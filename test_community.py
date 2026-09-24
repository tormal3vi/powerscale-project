"""Tests for accounts, admin overrules and the message board data layer
(backend/community.py), against a throwaway SQLite database.

Run with: ./venv/bin/python3 test_community.py
"""

import os
import tempfile

_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.name}"
os.environ["ADMIN_USERNAMES"] = "Boss, other_admin"

from backend import community  # noqa: E402  (must import after DATABASE_URL is set)

community.init()


def test_password_hash_is_salted_and_verifies():
    a, b = community.hash_password("correct horse"), community.hash_password("correct horse")
    assert a != b  # per-hash random salt
    assert "correct horse" not in a
    assert community.verify_password("correct horse", a)
    assert not community.verify_password("wrong horse", a)
    assert not community.verify_password("anything", "not-a-valid-hash")


def test_usernames_are_unique_case_insensitively():
    community.create_user("Alice", "password123")
    try:
        community.create_user("alice", "password456")
    except community.UsernameTaken:
        pass
    else:
        raise AssertionError("duplicate username accepted")
    assert community.authenticate("ALICE", "password123")["username"] == "Alice"
    assert community.authenticate("alice", "nope") is None
    assert community.authenticate("nobody", "password123") is None


def test_sessions_store_only_a_hash_and_can_be_revoked():
    user = community.create_user("sessiontest", "password123")
    token = community.create_session(user["id"])
    with community.engine.connect() as conn:
        stored = [r[0] for r in conn.execute(community.sessions.select().with_only_columns(community.sessions.c.token_hash))]
    assert token not in stored
    assert community.user_for_token(token)["username"] == "sessiontest"
    community.delete_session(token)
    assert community.user_for_token(token) is None
    assert community.user_for_token(None) is None


def test_admins_come_from_the_setting_case_insensitively():
    assert community.is_admin("boss") and community.is_admin("OTHER_ADMIN")
    assert not community.is_admin("alice")


def test_overrides_match_either_order_but_only_the_exact_forms():
    admin = community.create_user("Boss", "password123")
    community.set_override(10, 20, "Base", "Final Form", winner_id=20, note="group vote", admin_id=admin["id"])
    assert community.get_override(20, 10, "Final Form", "Base")["winner_id"] == 20  # reversed order
    assert community.get_override(10, 20, "Base", "Other Form") is None           # different form
    community.set_override(20, 10, "Final Form", "Base", winner_id=10, note="changed", admin_id=admin["id"])
    assert community.get_override(10, 20, "Base", "Final Form")["note"] == "changed"  # updated, not duplicated
    assert community.delete_override(10, 20, "Base", "Final Form")
    assert community.get_override(10, 20, "Base", "Final Form") is None


def test_posts_replies_likes_and_cascading_delete():
    u = community.create_user("poster", "password123")
    top = community.create_post(u["id"], "hello", char_a=1, char_b=2, form_a="Base", form_b="Base")
    reply = community.create_post(u["id"], "reply", parent_id=top)
    nested = community.create_post(u["id"], "reply to reply", parent_id=reply)
    thread = community.get_thread(top, u["id"])
    assert [r["id"] for r in thread["replies"]] == [reply, nested]  # one level: joins the same thread
    assert thread["post"]["reply_count"] == 2
    assert community.toggle_like(top, u["id"]) is True
    assert community.get_post_view(top, u["id"])["liked_by_me"]
    assert community.toggle_like(top, u["id"]) is False
    assert top in [p["id"] for p in community.list_posts(None)]
    community.toggle_like(reply, u["id"])
    community.delete_post(top)
    assert community.get_post(top) is None and community.get_post(reply) is None and community.get_post(nested) is None


def test_init_adds_new_post_columns_to_an_existing_database():
    # A database made before posts had kind/ruling_winner (like the live
    # one) gains them on startup, keeping its rows.
    from sqlalchemy import create_engine, inspect, text
    old_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    old_engine = create_engine(f"sqlite:///{old_db.name}")
    with old_engine.begin() as conn:
        conn.execute(text("CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, parent_id INTEGER, "
                          "body TEXT NOT NULL, char_a INTEGER, char_b INTEGER, form_a VARCHAR(200), "
                          "form_b VARCHAR(200), created_at DATETIME NOT NULL)"))
        conn.execute(text("INSERT INTO posts (user_id, body, created_at) VALUES (1, 'old post', '2026-01-01')"))
    real_engine = community.engine
    community.engine = old_engine
    try:
        community.init()
        community.init()  # and a second startup is a no-op
    finally:
        community.engine = real_engine
    cols = {c["name"] for c in inspect(old_engine).get_columns("posts")}
    assert {"kind", "ruling_winner"} <= cols
    with old_engine.connect() as conn:
        assert conn.execute(text("SELECT body, kind FROM posts")).all() == [("old post", None)]
    old_engine.dispose()
    os.unlink(old_db.name)


def test_overrule_posts_keep_the_ruling_they_made():
    admin = community.create_user("ruler", "password123")
    pid = community.create_post(admin["id"], "Devil Trigger", None, 5, 6, "Base", "DMC 1",
                                kind="overrule", ruling_winner=6)
    view = community.get_post_view(pid, None)
    assert view["kind"] == "overrule" and view["ruling_winner"] == 6
    plain = community.create_post(admin["id"], "just a post")
    assert community.get_post_view(plain, None)["kind"] is None


def test_ruling_status_follows_the_live_overrule():
    from backend.community_api import _ruling_out
    from backend.schemas import MatchupOut

    def matchup(winner_id):
        return MatchupOut(char_a=5, char_b=6, name_a="Kratos", name_b="Dante (Devil May Cry)", label_a="Kratos",
                          label_b="Dante", category_a="God of War", category_b="Devil May Cry", calc_verdict="x",
                          overruled_winner={5: "Kratos", 6: "Dante"}.get(winner_id), overruled_winner_id=winner_id)

    row = {"kind": "overrule", "ruling_winner": 6}
    assert _ruling_out(row, matchup(6)).model_dump() == {"winner": "Dante", "status": "current"}
    assert _ruling_out(row, matchup(5)).status == "changed"
    assert _ruling_out(row, matchup(None)).status == "lifted"
    assert _ruling_out({"kind": None, "ruling_winner": None}, matchup(6)) is None


def test_avatar_uploads_are_re_encoded_to_a_small_square_webp():
    from io import BytesIO
    from PIL import Image
    from backend import avatars

    def encoded(fmt, size=(640, 360), mode="RGB"):
        buf = BytesIO()
        Image.new(mode, size, (200, 30, 30)).save(buf, fmt)
        return buf.getvalue()

    for fmt in ("PNG", "JPEG", "GIF", "WEBP"):
        out = Image.open(BytesIO(avatars.process(encoded(fmt, mode="RGB" if fmt == "JPEG" else "RGBA"))))
        assert (out.format, out.size) == ("WEBP", (avatars.SIZE, avatars.SIZE)), fmt
    # Anything that isn't a real raster image is refused, never stored as-is.
    for bad in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
                b"<html><body>hi</body></html>", b"", encoded("BMP")):
        try:
            avatars.process(bad)
            assert False, bad[:20]
        except avatars.BadImage:
            pass
    # Oversized dimensions are refused from the header, before decoding.
    limit = avatars.MAX_PIXELS
    avatars.MAX_PIXELS = 100
    try:
        avatars.process(encoded("PNG", size=(20, 20)))
        assert False, "should refuse"
    except avatars.BadImage as exc:
        assert "too large" in str(exc)
    finally:
        avatars.MAX_PIXELS = limit


def test_avatars_store_replace_and_show_on_posts():
    u = community.create_user("picture_person", "password123")
    assert community.avatar_updated_at(u["id"]) is None
    community.set_avatar(u["id"], b"first")
    community.set_avatar(u["id"], b"second")  # replaces, doesn't add
    assert community.get_avatar("PICTURE_PERSON") == b"second"  # usernames are case-insensitive
    pid = community.create_post(u["id"], "hi")
    assert community.get_post_view(pid, None)["avatar_at"] is not None
    community.delete_avatar(u["id"])
    assert community.get_avatar("picture_person") is None
    assert community.get_post_view(pid, None)["avatar_at"] is None


def test_profile_update_rename_and_counts():
    a = community.create_user("settings_amy", "password123")
    b = community.create_user("settings_bob", "password123")
    community.update_profile(a["id"], "Amy_Renamed", "Hax over stats.", 915)
    p = community.get_profile(username="amy_renamed")  # case-insensitive
    assert (p["username"], p["bio"], p["favorite_char_id"]) == ("Amy_Renamed", "Hax over stats.", 915)
    try:
        community.update_profile(a["id"], "SETTINGS_BOB", "", None)  # someone else's, any case
        assert False, "should be taken"
    except community.UsernameTaken:
        pass
    community.update_profile(a["id"], "amy_renamed", "", None)  # own name, new case: fine; clears
    assert community.get_profile(user_id=a["id"])["favorite_char_id"] is None
    top = community.create_post(a["id"], "mine")
    community.create_post(b["id"], "reply", parent_id=top)
    community.toggle_like(top, b["id"])
    p = community.get_profile(user_id=a["id"])
    assert (p["post_count"], p["likes_received"]) == (1, 1)  # replies aren't "posts"


def test_password_change_and_other_sessions():
    u = community.create_user("pw_person", "password123")
    keep, other = community.create_session(u["id"]), community.create_session(u["id"])
    assert not community.change_password(u["id"], "wrong-password", "newpassword1")
    assert community.change_password(u["id"], "password123", "newpassword1")
    assert community.authenticate("pw_person", "newpassword1") and not community.authenticate("pw_person", "password123")
    assert community.delete_other_sessions(u["id"], keep) == 1
    assert community.user_for_token(keep) and community.user_for_token(other) is None


def test_delete_account_removes_only_what_the_user_wrote():
    gone = community.create_user("leaving_user", "password123")
    stays = community.create_user("staying_user", "password123")
    their_post = community.create_post(gone["id"], "bye")
    reply_to_them = community.create_post(stays["id"], "reply to leaver", parent_id=their_post)
    other_post = community.create_post(stays["id"], "unrelated")
    their_reply = community.create_post(gone["id"], "leaver's reply", parent_id=other_post)
    other_reply = community.create_post(stays["id"], "kept reply", parent_id=other_post)
    community.toggle_like(other_post, gone["id"])
    community.toggle_like(other_post, stays["id"])
    community.set_avatar(gone["id"], b"img")
    token = community.create_session(gone["id"])
    assert not community.has_admin_records(gone["id"])
    community.delete_account(gone["id"])
    for pid in (their_post, reply_to_them, their_reply):
        assert community.get_post(pid) is None
    assert community.get_post(other_post) and community.get_post(other_reply)
    assert community.get_post_view(other_post, None)["like_count"] == 1  # only the leaver's like went
    assert community.get_profile(user_id=gone["id"]) is None
    assert community.user_for_token(token) is None and community.get_avatar("leaving_user") is None
    # Admin records pin an account (they reference it by id).
    admin = community.create_user("record_keeper", "password123")
    community.set_override(1, 2, "Base", "Base", 1, "", admin["id"])
    assert community.has_admin_records(admin["id"])


def test_display_names_label_versions_that_share_a_page_title():
    # Invincible's Comics and TV pages spell the Name field differently, so
    # only their page titles show they're two versions of one character.
    import sqlite3
    from backend import characters as C
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE characters (name TEXT, source_url TEXT)")
    wiki = "https://vsbattles.fandom.com/wiki/"
    rows = [("Nowl-Ahn, Nolan Grayson, Omni-Man", wiki + "Omni-Man_(Comics)"),
            ('Nolan Grayson, "Omni-Man" (Hero Name)', wiki + "Omni-Man_(TV_Series)"),
            ('Markus Murphy, Marky, Mark, "Kid Invincible" (Temporary)', wiki + "Kid_Invincible_(Comics)")]
    conn.executemany("INSERT INTO characters VALUES (?, ?)", rows)
    col = C.colliding_names(conn)
    assert [C.display_name(n, u, col) for n, u in rows] == [
        "Omni-Man (Comics)", "Omni-Man (TV Series)",
        'Markus Murphy, Marky, Mark, "Kid Invincible" (Temporary)']  # unique title: keeps its name


def test_rate_limiter_blocks_after_the_limit_per_key():
    rl = community.RateLimiter(limit=2, window=60)
    assert rl.allow("ip1") and rl.allow("ip1")
    assert not rl.allow("ip1")
    assert rl.allow("ip2")  # separate key, separate budget


if __name__ == "__main__":
    import sys

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    os.unlink(_DB.name)
    sys.exit(1 if failures else 0)
