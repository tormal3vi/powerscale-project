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
