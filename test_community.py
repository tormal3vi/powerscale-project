"""Tests for accounts, admin overrules and the message board data layer
(backend/community.py), against a throwaway SQLite database.

Run with: ./venv/bin/python3 test_community.py
"""

import os
import tempfile

_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.name}"
os.environ["ADMIN_USERNAMES"] = "Boss, other_admin"
os.environ["PREWARM_PICTURES"] = "0"  # duels would fetch pictures from the wiki

from backend import community  # noqa: E402  (must import after DATABASE_URL is set)
from backend import duels  # noqa: E402  (registers its tables before init creates them)
from backend import tickets  # noqa: E402

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


def test_character_data_is_stored_compressed():
    import json, sqlite3
    from pathlib import Path
    import db
    path = Path(tempfile.mkdtemp()) / "old.db"
    old = sqlite3.connect(path)  # a copy from before compression: TEXT columns, plain JSON
    old.execute("""CREATE TABLE characters (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        source_url TEXT NOT NULL UNIQUE, category TEXT, last_scraped_at TEXT NOT NULL, raw_json TEXT NOT NULL,
        normalized_json TEXT NOT NULL, image_url TEXT, subseries TEXT)""")
    old.execute("INSERT INTO characters VALUES (40, 'Old', 'u/old', 'S', 'now', ?, ?, NULL, NULL)",
                (json.dumps({"name": "Old"}), json.dumps({"forms": []})))
    old.execute("DELETE FROM characters WHERE id = 40")  # a deleted character: its id stays used
    old.execute("INSERT INTO characters VALUES (7, 'Kept', 'u/kept', 'S', 'now', ?, ?, NULL, 'Part')",
                (json.dumps({"name": "Kept", "note": "é"}), json.dumps({"forms": [{"name": "Base"}]})))
    old.commit()
    old.close()
    db.init_db(path)
    kept = db.get_character_by_id(7, path)
    assert json.loads(kept["raw_json"]) == {"name": "Kept", "note": "é"} and kept["subseries"] == "Part"
    db.upsert_character("New", "u/new", "S", {"name": "New"}, {"forms": []}, db_path=path)
    new = db.get_character("u/new", path)
    assert new["id"] == 41 and json.loads(new["normalized_json"]) == {"forms": []}  # never reuses id 40
    raw = sqlite3.connect(path).execute("SELECT raw_json FROM characters WHERE id = 41").fetchone()[0]
    assert isinstance(raw, bytes) and raw[:1] == b"x"  # compressed on disk


def test_display_names_prefer_the_name_people_use():
    from backend import characters as C
    wiki = "https://vsbattles.fandom.com/wiki/"
    cases = [('Jackson "Jax" Briggs, Major Briggs', wiki + "Jax_(Second_Timeline)", "Jax"),
             ('Jacqueline Sonya "Jacqui" Briggs', wiki + "Jacqui_Briggs", "Jacqui Briggs"),
             ("Grey Cloud (real name), Nightwolf", wiki + "Nightwolf_(Second_Timeline)", "Nightwolf"),
             ('"Alien", Xenomorph', wiki + "Alien_(Mortal_Kombat)", "Alien"),
             ('Monkey D. Luffy, "Straw Hat Luffy"', wiki + "Monkey_D._Luffy_(Emperor)", "Monkey D. Luffy"),  # unchanged
             ("Joker (Real name is unknown)", wiki + "Joker_(Post-Crisis)", "Joker")]
    for name, url, shown in cases:
        assert C.short_name(C.base_name(name, url)) == shown, (name, C.base_name(name, url))


def test_title_named_series_lead_with_the_page_title():
    from batch_scrape import titled_name
    assert titled_name("Doctor Doom", "Victor Von Doom") == "Doctor Doom, Victor Von Doom"
    assert titled_name("Black Cat (Marvel Comics)", "Black Cat/Felicia Hardy") == "Black Cat, Black Cat/Felicia Hardy"
    assert titled_name("Silver Surfer (Marvel Comics)", "Silver Surfer, Norrin Radd") == "Silver Surfer, Norrin Radd"
    assert titled_name("Nova (Sam Alexander)", None) == "Nova"
    assert titled_name("Whiplash (Anton Vanko)", "Anton Igorevich Vanko, Whiplash") == "Whiplash, Anton Igorevich Vanko"


def test_duels_show_the_picture_of_the_form_they_use():
    from backend import characters, duels_api
    dimple = 4804  # God Dimple (his default, strongest form) has its own picture
    main = db_row_image(dimple)
    assert "God_Dimple" in characters.form_picture(dimple) and characters.form_picture(dimple) != main
    assert characters.form_picture(dimple, "Full Power") == main  # no picture of its own: the character's
    side = duels_api._side(dimple, None, {})
    assert side.form == "God Dimple" and "God_Dimple" in side.image_url


def db_row_image(char_id):
    import db
    return db.get_character_by_id(char_id)["image_url"]


def test_page_addresses_keep_a_question_mark_in_the_title():
    import scraper
    title = "Hank Pym (Marvel Cinematic Universe: What If...?)"
    url = scraper.page_url(title)
    assert url.endswith("What_If...%3F)") and scraper.page_title(url) == title
    # addresses stored before encoding still read whole
    assert scraper.page_title("https://vsbattles.fandom.com/wiki/Hank_Pym_(Marvel_Cinematic_Universe:_What_If...?)") == title
    assert scraper.page_title(scraper.page_url("Cloak & Dagger (Marvel Rivals)")) == "Cloak & Dagger (Marvel Rivals)"


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
        "Kid Invincible"]  # unique title, and the name fans use


def test_display_names_prefer_the_page_title_over_a_real_name_lead():
    from backend import characters as C
    wiki = "https://vsbattles.fandom.com/wiki/"
    cases = [
        ('John ("Jack"), Naked Snake, Big Boss, Saladin', "Big_Boss", "Big Boss"),
        ("Real name unknown, known as The Sorrow", "The_Sorrow", "The Sorrow"),
        ("Ocelot/Revolver Ocelot (code-name), ADAM (his CIA code-name)", "Revolver_Ocelot", "Revolver Ocelot"),
        ("Unknown, impersonated Captain Tennille", "Impostor_Captain_Tennille", "Impostor Captain Tennille"),
        ("Unknown. Aliases include the Phantom Stranger, the Grey Walker", "The_Phantom_Stranger_(Post-Crisis)",
         "The Phantom Stranger"),
        ("Real name unknown. Referred as Prometheus", "Prometheus_(Post-Crisis)", "Prometheus"),
        ("Joker (Real name is unknown)", "Joker_(2008_Graphic_Novel)", "Joker"),
        ("Rudeus Greyrat (Pre-reincarnation name unknown); Rudi", "Rudeus_Greyrat", "Rudeus Greyrat"),
        ("Mistral (Her codename, true name is unknown)", "Mistral", "Mistral"),
        ("Raiden (雷電?) (Birth name unknown, but was given the name Jack)", "Raiden_(Metal_Gear)", "Raiden"),
        # Kept: the page is titled "Real name (Hero name)"...
        ("Metal Bat, Bad", "Bad_(Metal_Bat)", "Metal Bat, Bad"),
        # ...the lead already contains the whole title...
        ("Son Goku, Kakarot", "Goku", "Son Goku, Kakarot"),
        # ...or the title's words aren't all in the Name field.
        ("Asa Mitaka, War Devil (Yoru)", "War_Fiend", "Asa Mitaka, War Devil (Yoru)"),
    ]
    assert [C.base_name(n, wiki + t) for n, t, _ in cases] == [want for _, _, want in cases]
    shown = [  # what the site shows: the short name
        ('Unknown (Only known as "Flam·Rouge" some time after being adopted), Flamberge', "Flamberge", "Flamberge"),
        ("Has no actual name but is referred to as Demise or Tyrannical Being", "Demise", "Demise"),
        ("President Max Proffit Haltmann (English name), Gains Income Haltmann", "President_Haltmann",
         "President Max Proffit Haltmann"),
        ("Mr. Bright (The sun-like character) & Mr. Shine (The moon-like character)", "Mr._Bright_and_Mr._Shine",
         "Mr. Bright & Mr. Shine"),
        ('Grand Doomer (American name)/"Grand Lowper"', "Grand_Doomer", "Grand Doomer"),
        ('Team Kirby / "Kirby Hunters"', "Team_Kirby", "Team Kirby"),
        ("Ocelot/Revolver Ocelot", "Ocelot", "Ocelot/Revolver Ocelot"),  # a slash inside a name stays
    ]
    assert [C.short_name(C.base_name(n, wiki + t)) for n, t, _ in shown] == [want for _, _, want in shown]


def test_board_version_moves_on_every_board_write():
    user = community.create_user("live_fan", "password123")
    seen = [community.board_version()]
    def moved():
        seen.append(community.board_version())
        return seen[-1] != seen[-2]
    assert not moved()  # reading alone changes nothing
    post_id = community.create_post(user["id"], "first!")
    assert moved()
    community.create_post(user["id"], "a reply", parent_id=post_id)
    assert moved()
    community.toggle_like(post_id, user["id"])
    assert moved()
    community.list_posts(user["id"])
    assert not moved()
    community.delete_post(post_id)
    assert moved()
    community.delete_account(user["id"])
    assert moved()


def test_subseries_survives_a_re_parse_and_can_be_refiled():
    import tempfile
    from pathlib import Path
    import db
    path = Path(tempfile.mkdtemp()) / "chars.db"
    db.init_db(path)
    url = "https://vsbattles.fandom.com/wiki/Batman_(Arkham)"
    db.upsert_character("Batman", url, "DC", {}, {}, db_path=path, subseries="Arkham")
    db.upsert_character("Batman", url, "DC", {}, {}, db_path=path)  # a re-parse passes no subseries
    assert db.get_character(url, path)["subseries"] == "Arkham"
    db.set_series(url, "DC", "Comics", db_path=path)
    assert db.get_character(url, path)["subseries"] == "Comics"


def test_matchup_comments_are_shared_by_both_orders_and_leave_with_the_account():
    u = community.create_user("commenter", "password123")
    c1 = community.add_matchup_comment(u["id"], 12, 7, "Superman stomps")
    community.add_matchup_comment(u["id"], 7, 12, "Agreed")
    assert [c["body"] for c in community.list_matchup_comments(7, 12)] == ["Superman stomps", "Agreed"]
    assert community.list_matchup_comments(7, 13) == []
    community.delete_matchup_comment(c1)
    assert [c["body"] for c in community.list_matchup_comments(12, 7)] == ["Agreed"]
    community.delete_account(u["id"])
    assert community.list_matchup_comments(7, 12) == []


def _play(game_id, user_id, right):
    """Plays every round, picking the answer (right=True) or the other side."""
    r = duels.next_round(game_id, user_id)
    while r is not None:
        wrong = r["char_b"] if r["answer_id"] == r["char_a"] else r["char_a"]
        in_time, r_next = duels.pick(game_id, user_id, r["round_no"], r["answer_id"] if right else wrong)
        assert in_time
        r = r_next  # the next round comes back with the pick


def _users(*names):
    return [community.create_user(n, "password123") for n in names]


def test_duel_best_of_five_decides_a_winner_and_keeps_records():
    alice, bob = _users("duel_alice", "duel_bob")
    game_id = duels.create(alice["id"], "1v1", [], [])
    _play(game_id, alice["id"], right=True)  # the creator can play before anyone joins
    assert duels.game(game_id)["games"][0]["status"] == "open"
    duels.join(game_id, bob["id"])
    assert duels.game(game_id)["games"][0]["status"] == "active"
    _play(game_id, bob["id"], right=False)
    g = duels.game(game_id)["games"][0]
    assert g["status"] == "done" and g["winning_team"] == 1
    recs = duels.records([alice["id"], bob["id"]])
    assert recs[alice["id"]] == {"wins": 1, "draws": 0, "losses": 0}
    assert recs[bob["id"]] == {"wins": 0, "draws": 0, "losses": 1}
    assert duels.next_round(game_id, alice["id"]) is None  # nothing left to play


def test_duel_late_picks_count_as_wrong_and_reloads_keep_the_clock():
    from datetime import timedelta
    from sqlalchemy import update
    (carol,) = _users("duel_carol")
    game_id = duels.create(carol["id"], "1v1", [], [])
    first = duels.next_round(game_id, carol["id"])
    again = duels.next_round(game_id, carol["id"])
    assert again["round_no"] == first["round_no"] and again["seconds_left"] <= first["seconds_left"]
    with community.engine.begin() as conn:  # the round was shown a minute ago
        conn.execute(update(duels.game_picks).where(duels.game_picks.c.game_id == game_id)
                     .values(started_at=community._now() - timedelta(seconds=60)))
    in_time, nxt = duels.pick(game_id, carol["id"], first["round_no"], first["answer_id"])
    assert in_time is False and nxt["round_no"] == first["round_no"] + 1


def _raises(fn, status=None, text=None):
    try:
        fn()
    except duels.DuelError as exc:
        assert status is None or exc.status == status, exc.status
        assert text is None or text in str(exc), str(exc)
        return
    assert False, "expected a DuelError"


def test_duel_seats_invites_and_leaving():
    dan, eve, fay = _users("duel_dan", "duel_eve", "duel_fay")
    open_game = duels.create(dan["id"], "1v1", [], [])
    _raises(lambda: duels.join(open_game, dan["id"]), 409)  # already in it: made it
    duels.join(open_game, eve["id"])
    _raises(lambda: duels.join(open_game, fay["id"]), 409)  # full
    private = duels.create(dan["id"], "1v1", ["DUEL_EVE"], [])  # usernames match any case
    _raises(lambda: duels.join(private, fay["id"]), 403)
    duels.decline(private, eve["id"])
    assert duels.game(private)["games"][0]["status"] == "declined"
    _raises(lambda: duels.create(dan["id"], "2v2", ["duel_eve"], []), text="needs 3 invited")
    lobby = duels.create(dan["id"], "1v1v1", [], [])
    duels.join(lobby, fay["id"])
    duels.leave(lobby, fay["id"])  # before playing, a joiner can still back out
    assert [m["user_id"] for m in duels.game(lobby)["members"][lobby]] == [dan["id"]]


def test_link_only_games_are_joinable_but_not_listed_or_posted():
    from backend import discord_webhooks, duels_api
    gus, hal, ida = _users("link_gus", "link_hal", "link_ida")
    listed = duels.create(gus["id"], "1v1", [], [])
    friends = duels.create(gus["id"], "1v1", [], [], link_only=True)
    private = duels.create(gus["id"], "1v1", ["link_hal"], [], link_only=True)  # invite-only anyway
    assert [g["id"] for g in duels.overview(ida["id"])["open"] if g["creator_id"] == gus["id"]] == [listed]
    assert duels_api._one(friends, gus["id"]).link_only and not duels_api._one(listed, gus["id"]).link_only
    assert not duels_api._one(private, gus["id"]).link_only
    posted = []
    real, discord_webhooks._request = discord_webhooks._request, lambda *a, **k: posted.append(a) or None
    try:
        discord_webhooks._lobby_post("https://discord.invalid/webhook", friends)
        assert posted == []  # a challenge to a friend isn't announced
        discord_webhooks._last_lobby_post.pop("link_gus", None)
        discord_webhooks._lobby_post("https://discord.invalid/webhook", listed)
        assert len(posted) == 1
    finally:
        discord_webhooks._request = real
    duels.join(friends, hal["id"])  # whoever has the link can join
    assert duels.game(friends)["games"][0]["status"] == "active"


def test_gauntlet_runs_end_at_the_first_loss_or_draw():
    from backend import gauntlet
    real = gauntlet.fight
    outcomes = iter(["win", "win", "even", "win", "loss"])
    gauntlet.fight = lambda c, o, form=None: {"outcome": next(outcomes), "verdict": "", "form": None}
    try:
        r = gauntlet.run(1, [11, 12, 13, 14, 15])
    finally:
        gauntlet.fight = real
    assert r["climbed"] == 2 and r["total"] == 5
    assert [f["reached"] for f in r["fights"]] == [True, True, True, False, False]
    assert [duels.gauntlet_points(g, 4) for g in (4, 5, 2, 7, None)] == [3, 2, 1, 0, 0]


def test_gauntlet_duels_are_called_fight_by_fight():
    from datetime import timedelta
    from sqlalchemy import select, update
    ann, ben, cy = _users("gd_ann", "gd_ben", "gd_cy")

    def play(game_id, uid, right=True, slow=0):
        r = duels.next_round(game_id, uid)
        assert r["seconds"] == duels.GAUNTLET_SECONDS and r["call"]["rung"] == 1
        while r is not None:
            if slow:  # as if they'd taken `slow` seconds on this call
                with community.engine.begin() as conn:
                    conn.execute(update(duels.game_picks).where(
                        (duels.game_picks.c.game_id == game_id) & (duels.game_picks.c.user_id == uid)
                        & (duels.game_picks.c.round_no == r["round_no"])).values(
                        started_at=r["started_at"] - timedelta(seconds=slow)))
            call = r["answer_id"] if right else 1 - r["answer_id"]
            in_time, nxt = duels.pick(game_id, uid, r["round_no"], call)
            assert in_time and nxt is None  # the result shows before the next call starts
            res = duels.call_result(game_id, r["round_no"])
            assert res["beat"] == bool(r["answer_id"])
            r = duels.next_round(game_id, uid)

    game_id = duels.create(ann["id"], "1v1", [], [], [], "gauntlet", gauntlet_opts={"source": "random"})
    g = duels.game(game_id)["games"][0]
    assert g["rounds_total"] >= duels.GAUNTLETS  # at least one call per gauntlet
    duels.join(game_id, ben["id"])
    play(game_id, ann["id"], right=True)
    play(game_id, ben["id"], right=False)
    done = duels.game(game_id)
    scores = {m["user_id"]: m["score"] for m in done["members"][game_id]}
    # 1 for each right "beats them", 3 for each knockout called right.
    expected = sum(duels.call_points(r["answer_id"]) for r in done["rounds"][game_id])
    assert expected >= g["rounds_total"]  # more when a gauntlet ends in a knockout (it can clear instead)
    assert scores == {ann["id"]: expected, ben["id"]: 0}
    assert {m["user_id"]: m["outcome"] for m in done["members"][game_id]}[ann["id"]] == "win"

    # Level on calls: the faster player wins.
    game_id = duels.create(ann["id"], "1v1", [], [], [], "gauntlet", gauntlet_opts={"source": "random"})
    duels.join(game_id, cy["id"])
    play(game_id, ann["id"], right=True)
    play(game_id, cy["id"], right=True, slow=5)
    done = duels.game(game_id)
    outcomes = {m["user_id"]: m["outcome"] for m in done["members"][game_id]}
    assert outcomes == {ann["id"]: "win", cy["id"]: "loss"}
    assert len({m["score"] for m in done["members"][game_id]}) == 1
    duels.delete_user_games(ann["id"])
    with community.reader.connect() as conn:
        assert not conn.execute(select(duels.game_ladders).where(duels.game_ladders.c.game_id == game_id)).first()


def test_titles_come_from_wins_and_can_be_picked_or_hidden():
    from backend import titles
    kim, lou = _users("title_kim", "title_lou")
    for _ in range(5):  # kim wins five duels
        game_id = duels.create(kim["id"], "1v1", [], [])
        duels.join(game_id, lou["id"])
        _play(game_id, kim["id"], right=True)
        _play(game_id, lou["id"], right=False)
    titles.forget()
    s = titles.summary("TITLE_KIM")  # any case
    keys = [t["key"] for t in s["earned"]]
    assert "duels:5" in keys and "duels:1" not in keys  # Challenger replaced Rookie
    assert s["next"][0]["name"] == "Contender" and s["next"][0]["have"] == 5
    founder = "founder" in keys  # the test database's first 100 accounts
    assert titles.shown("title_kim")["name"] == ("Challenger" if not founder or titles._RANK["duels:5"] > titles._RANK["founder"] else "Founder")
    titles.choose(kim["id"], "duels:5")
    assert titles.shown("title_kim")["name"] == "Challenger"
    try:
        titles.choose(kim["id"], "duels:1")
        assert False, "a replaced title can't be picked"
    except ValueError:
        pass
    titles.choose(kim["id"], titles.HIDDEN)
    assert titles.shown("title_kim") is None
    try:
        titles.choose(kim["id"], "duels:1000")
        assert False, "a title not earned can't be picked"
    except ValueError:
        pass
    titles.choose(kim["id"], None)
    assert titles.shown("title_kim")["key"] == max(keys, key=lambda k: titles._RANK[k])
    assert titles.shown("nobody_at_all") is None
    # Admins make overrules: no overrule titles, and no progress towards them.
    existing = community.get_profile(username="other_admin")  # an admin (ADMIN_USERNAMES)
    boss = existing or community.create_user("other_admin", "password123")
    with community.engine.begin() as conn:
        conn.execute(community.posts.insert().values(user_id=boss["id"], body="x", created_at=community._now(),
                                                    credit_user_id=boss["id"]))
    titles.forget()
    s = titles.summary("other_admin")
    assert not [t for t in s["earned"] if t["key"].startswith("overrules")]
    assert [t["what"] for t in s["next"]] == ["wins"]


def test_linked_roles_push_numbers_and_forget_revoked_tokens():
    from datetime import timedelta
    from sqlalchemy import select
    from backend import linked_roles
    os.environ.update(DISCORD_CLIENT_SECRET="test-secret", DISCORD_APPLICATION_ID="123")
    sent = []

    class Reply:
        def __init__(self, status, body=None):
            self.status_code, self.ok, self._body = status, 200 <= status < 300, body or {}

        def json(self):
            return self._body
    real_put, real_post = linked_roles.requests.put, linked_roles.requests.post
    linked_roles.requests.put = lambda url, json=None, headers=None, timeout=None: (
        sent.append((url, json, headers["Authorization"])) or Reply(sent_status[0]))
    linked_roles.requests.post = lambda url, data=None, headers=None, timeout=None: Reply(
        200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})
    sent_status = [200]
    try:
        max_, = _users("lr_max")
        assert linked_roles._open(linked_roles._seal("tok")) == "tok"
        community.set_discord(max_["id"], "555", "max#1")
        linked_roles._save_tokens(max_["id"], "555", {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600})
        with community.reader.connect() as conn:
            stored = conn.execute(select(community.discord_role_tokens)).mappings().first()
        assert "acc" not in stored["access_token"]  # encrypted at rest
        assert linked_roles.push(max_["id"])
        url, body, auth = sent[-1]
        assert url.endswith("/users/@me/applications/123/role-connection") and auth == "Bearer acc"
        assert body["platform_username"] == "lr_max" and body["metadata"]["duel_wins"] == 0
        assert linked_roles.push(max_["id"]) and len(sent) == 1  # unchanged: not sent again
        with community.engine.begin() as conn:  # an expired token is refreshed first
            conn.execute(community.discord_role_tokens.update().values(expires_at=community._now() - timedelta(minutes=1)))
        assert linked_roles.push(max_["id"], force=True) and sent[-1][2] == "Bearer new-access"
        sent_status[0] = 401  # revoked on Discord: forgotten
        assert not linked_roles.push(max_["id"], force=True)
        with community.reader.connect() as conn:
            assert conn.execute(select(community.discord_role_tokens)).first() is None
        linked_roles._save_tokens(max_["id"], "555", {"access_token": "a", "refresh_token": "r"})
        community.delete_account(max_["id"])  # tokens go with the account
        with community.reader.connect() as conn:
            assert conn.execute(select(community.discord_role_tokens)).first() is None
    finally:
        linked_roles.requests.put, linked_roles.requests.post = real_put, real_post
        for k in ("DISCORD_CLIENT_SECRET", "DISCORD_APPLICATION_ID"):
            os.environ.pop(k, None)


def test_team_games_add_up_members_and_share_the_result():
    a1, a2, b1, b2 = _users("team_a1", "team_a2", "team_b1", "team_b2")
    game_id = duels.create(a1["id"], "2v2", [], [])
    duels.join(game_id, a2["id"], 1)
    _raises(lambda: duels.join(game_id, b1["id"], 1), 409, "team is full")
    duels.join(game_id, b1["id"])  # placed on the team with room
    duels.join(game_id, b2["id"], 2)
    assert duels.game(game_id)["games"][0]["status"] == "active"
    _play(game_id, a1["id"], right=True)
    _play(game_id, a2["id"], right=False)  # team 1: 5 + 0
    _play(game_id, b1["id"], right=True)
    _play(game_id, b2["id"], right=True)  # team 2: 5 + 5
    g = duels.game(game_id)["games"][0]
    assert g["status"] == "done" and g["winning_team"] == 2
    recs = duels.records([a1["id"], b2["id"]])
    assert recs[a1["id"]]["losses"] == 1 and recs[b2["id"]]["wins"] == 1


def test_free_for_all_ties_at_the_top_are_draws():
    x, y, z = _users("ffa_x", "ffa_y", "ffa_z")
    game_id = duels.create(x["id"], "1v1v1", [], [])
    duels.join(game_id, y["id"])
    duels.join(game_id, z["id"])
    for u, right in ((x, True), (y, True), (z, False)):
        _play(game_id, u["id"], right)
    recs = duels.records([x["id"], y["id"], z["id"]])
    assert recs[x["id"]]["draws"] == 1 and recs[y["id"]]["draws"] == 1 and recs[z["id"]]["losses"] == 1
    assert duels.game(game_id)["games"][0]["winning_team"] is None


def test_random_rounds_leave_out_excluded_series():
    from sqlalchemy import select
    from backend import characters
    (hana,) = _users("duel_hana")
    series = {cid: s for cid, _, s in characters.scorable_pool()}
    biggest = max(set(series.values()), key=lambda s: sum(1 for x in series.values() if x == s))
    game_id = duels.create(hana["id"], "1v1", [], [], [biggest])
    with community.engine.connect() as conn:
        rounds = conn.execute(select(duels.game_rounds).where(duels.game_rounds.c.game_id == game_id)).mappings().all()
    assert all(series[r["char_a"]] != biggest and series[r["char_b"]] != biggest for r in rounds)
    assert duels.game(game_id)["games"][0]["excluded"] == biggest
    everything = sorted(set(series.values()))
    _raises(lambda: duels.create(hana["id"], "1v1", [], [], everything), text="exclude fewer")


def test_scores_pending_count_and_leaderboard():
    ivy, jon, kai = _users("sc_ivy", "sc_jon", "sc_kai")
    game_id = duels.create(ivy["id"], "1v1", [], [])
    private = duels.create(ivy["id"], "1v1", ["sc_kai"], [])
    assert duels.pending_count(kai["id"]) == 1  # the invite
    assert duels.pending_count(ivy["id"]) == 2  # two games to play
    duels.join(game_id, jon["id"])
    _play(game_id, ivy["id"], right=True)
    assert duels.pending_count(ivy["id"]) == 1  # only the private one left
    _play(game_id, jon["id"], right=False)
    members = {m["user_id"]: m for m in duels.game(game_id)["members"][game_id]}
    assert members[ivy["id"]]["score"] == 5 and members[jon["id"]]["score"] == 0  # stored when it finished
    rows = {r["username"]: r for r in duels.leaderboard(100)}
    assert rows["sc_ivy"]["wins"] == 1 and rows["sc_jon"]["losses"] == 1
    listed = duels.overview(ivy["id"])  # a list doesn't load finished games' rounds
    assert game_id in [g["id"] for g in listed["mine"]] and game_id not in listed["rounds"]
    assert private in [g["id"] for g in listed["mine"]]


def test_draft_deals_hands_by_seat_and_scores_head_to_head():
    from sqlalchemy import select
    ann, ben, cal = _users("dr_ann", "dr_ben", "dr_cal")
    game_id = duels.create(ann["id"], "1v1", [], [], [], "draft")
    with community.engine.connect() as conn:
        hands = conn.execute(select(duels.game_hands).where(duels.game_hands.c.game_id == game_id)).mappings().all()
    assert len(hands) == duels.ROUNDS * 2 * duels.HAND_SIZE
    assert len({h["char_id"] for h in hands}) == len(hands)  # nobody sees a character twice
    lobby = duels.create(ann["id"], "1v1v1", [], [], [], "draft")
    duels.join(lobby, cal["id"])
    duels.leave(lobby, cal["id"])  # before playing
    duels.join(lobby, ben["id"])  # gets the freed seat
    assert {m["user_id"]: m["seat"] for m in duels.game(lobby)["members"][lobby]} == {ann["id"]: 0, ben["id"]: 1}
    duels.join(game_id, ben["id"])
    r = duels.next_round(game_id, ann["id"])
    assert len(r["hand"]) == duels.HAND_SIZE and r["hand"] == [h["char_id"] for h in sorted(
        (h for h in hands if h["round_no"] == r["round_no"] and h["seat"] == 0), key=lambda h: h["slot"])]
    other_seat = [h["char_id"] for h in hands if h["round_no"] == r["round_no"] and h["seat"] == 1]
    _raises(lambda: duels.pick(game_id, ann["id"], r["round_no"], other_seat[0]), text="in your hand")
    chosen = {}
    for u, idx in ((ann, 0), (ben, -1)):
        r = duels.next_round(game_id, u["id"])
        while r:
            chosen[(r["round_no"], u["id"])] = r["hand"][idx]
            _, r = duels.pick(game_id, u["id"], r["round_no"], r["hand"][idx])
    with community.engine.connect() as conn:
        took = {(p["round_no"], p["user_id"]): (p["answered_at"] - p["started_at"]).total_seconds() for p in conn.execute(
            select(duels.game_picks).where(duels.game_picks.c.game_id == game_id)).mappings()}
    expected = {ann["id"]: 0, ben["id"]: 0}
    for n in range(1, duels.ROUNDS + 1):
        a, b = chosen[(n, ann["id"])], chosen[(n, ben["id"])]
        w = duels._stronger(a, b)
        if w is not None:
            expected[ann["id"] if w == a else ben["id"]] += 1
        elif took[(n, ann["id"])] != took[(n, ben["id"])]:  # dead even: the faster pick
            expected[ann["id"] if took[(n, ann["id"])] < took[(n, ben["id"])] else ben["id"]] += 1
    members = {m["user_id"]: m for m in duels.game(game_id)["members"][game_id]}
    assert {uid: members[uid]["score"] for uid in expected} == expected
    assert duels.game(game_id)["games"][0]["status"] == "done"
    duels.delete_user_games(ann["id"])  # an account deletion takes the hands too
    with community.engine.connect() as conn:
        assert not conn.execute(select(duels.game_hands).where(duels.game_hands.c.game_id.in_([game_id, lobby]))).first()


def test_draft_dead_even_picks_go_to_the_faster_one():
    members = [{"user_id": 1, "team": 1}, {"user_id": 2, "team": 2}, {"user_id": 3, "team": 2}]
    picked = {(1, 1): 10, (1, 2): 20, (1, 3): None}
    real = duels._stronger
    duels._stronger = lambda a, b, lean=False: real(a, b) if a is None or b is None else None  # every pair dead even
    try:
        bouts = [b for b in duels.draft_bouts(members, picked, {(1, 1): 7.5, (1, 2): 3.0}) if b["round_no"] == 1]
        assert [(b["y"], b["winner"], b["by_speed"]) for b in bouts] == [(2, 2, True), (3, 1, False)]  # no pick loses
        assert duels.draft_points(members, picked, {(1, 1): 7.5, (1, 2): 3.0})[(1, 2)] == 1
        assert duels.draft_bouts(members, picked)[0]["winner"] is None  # no times: a tie
    finally:
        duels._stronger = real


def test_discord_app_checks_signatures_and_answers_commands():
    from nacl.signing import SigningKey
    from backend import discord_bot
    key = SigningKey.generate()
    public = key.verify_key.encode().hex()
    body, ts = b'{"type": 1}', "1700000000"
    signed = key.sign(ts.encode() + body).signature.hex()
    assert discord_bot.verify(public, signed, ts, body)
    assert not discord_bot.verify(public, signed, ts, b'{"type": 2}')  # tampered
    assert not discord_bot.verify(public, "zz", ts, body)  # not even hex
    assert discord_bot.handle({"type": 1}) == {"type": 1}

    # Suggestions while typing: the character first, then its forms.
    choices = discord_bot.handle({"type": 4, "data": {"name": "compare", "options": [
        {"name": "a", "type": 3, "value": "kratos", "focused": True}]}})["data"]["choices"]
    assert choices and all(len(c["name"]) <= 100 for c in choices) and "Kratos" in choices[0]["name"]
    kratos = int(choices[0]["value"])
    forms = discord_bot.handle({"type": 4, "data": {"name": "compare", "options": [
        {"name": "a", "type": 3, "value": str(kratos)}, {"name": "form_a", "type": 3, "value": "", "focused": True}]}})
    assert forms["data"]["choices"][0]["value"] == "0"

    def run(command, **opts):
        return discord_bot.handle({"type": 2, "data": {"name": command, "options": [
            {"name": k, "type": 3, "value": v} for k, v in opts.items()]}})["data"]
    other = discord_bot.search("superman")[0]["id"]
    reply = run("compare", a=str(kratos), b=str(other))
    embed = reply["embeds"][0]
    assert " vs " in embed["title"] and embed["url"].startswith("https://powerscale.online/compare.html?a=")
    assert len(embed["fields"]) == 2 and "Tier" in embed["fields"][0]["value"]
    assert reply["allowed_mentions"] == {"parse": []} and reply["components"][0]["components"][0]["style"] == 5
    assert run("compare", a="kratos", b="superman")["embeds"]  # typed names work too
    assert run("compare", a=str(kratos), b=str(kratos))["flags"] == discord_bot.EPHEMERAL
    assert run("compare", a="zzqqxx", b="superman")["flags"] == discord_bot.EPHEMERAL
    assert run("compare", a=str(kratos), b=str(other), form_a="no such form")["flags"] == discord_bot.EPHEMERAL
    assert "Attack Potency" in run("character", name=str(kratos))["embeds"][0]["description"]
    assert " vs " in run("random")["embeds"][0]["title"]
    assert run("leaderboard")["embeds"][0]["title"] == "Duel leaderboard"

    # /profile: suggestions by name, the public profile, and unknown names.
    (fan,) = _users("tier_fan_99")
    community.update_profile(fan["id"], "tier_fan_99", "Kratos *always* wins", kratos)
    names = discord_bot.handle({"type": 4, "data": {"name": "profile", "options": [
        {"name": "username", "type": 3, "value": "TIER_F", "focused": True}]}})["data"]["choices"]
    assert {"name": "tier_fan_99", "value": "tier_fan_99"} in names
    assert discord_bot.handle({"type": 4, "data": {"name": "profile", "options": [
        {"name": "username", "type": 3, "value": "_", "focused": True}]}})["data"]["choices"]  # "_" isn't a wildcard
    card = run("profile", username="@Tier_Fan_99")["embeds"][0]  # any case, with or without @
    fields = {f["name"]: f["value"] for f in card["fields"]}
    assert card["title"] == "tier_fan_99" and card["url"].endswith("/user.html?u=tier_fan_99")
    assert "Kratos \\*always\\* wins" in card["description"] and "Member since" in card["description"]
    assert fields["Duels"] == "None yet" and fields["Posts"] == "0" and "character.html?id=" in fields["Favorite character"]
    assert run("profile", username="nobody_here")["flags"] == discord_bot.EPHEMERAL


def test_weekly_leaderboard_posts_once_a_week():
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import insert
    from backend import discord_webhooks as hooks
    utc = timezone.utc
    # Mondays 18:00 Budapest: 16:00 UTC in summer time, 17:00 UTC in winter.
    key, since, until = hooks.week_slot(datetime(2026, 9, 28, 16, 0, tzinfo=utc))
    assert key == "2026-W40" and until == datetime(2026, 9, 28, 16, 0, tzinfo=utc) and until - since == timedelta(days=7)
    assert hooks.week_slot(datetime(2026, 9, 28, 15, 59, tzinfo=utc))[0] == "2026-W39"  # a minute early: last week's
    assert hooks.week_slot(datetime(2026, 10, 4, 12, 0, tzinfo=utc))[0] == "2026-W40"  # Sunday: still this one
    assert hooks.week_slot(datetime(2026, 11, 2, 17, 0, tzinfo=utc))[2] == datetime(2026, 11, 2, 17, 0, tzinfo=utc)

    # Two finished games: one in the week, one the week before. (A week in
    # 2020, which no other test's games can land in.)
    key, since, until = hooks.week_slot(datetime(2020, 9, 28, 16, 0, tzinfo=utc))
    win, lose = _users("wk_win", "wk_lose")
    with community.engine.begin() as conn:
        for finished in (since + timedelta(hours=1), since - timedelta(days=1)):
            gid = conn.execute(insert(duels.games).values(
                creator_id=win["id"], status="done", created_at=finished, finished_at=finished,
                teams=2, team_size=1, winning_team=1)).inserted_primary_key[0]
            conn.execute(insert(duels.game_players).values(game_id=gid, user_id=win["id"], team=1, outcome="win",
                                                           score=3, joined_at=finished))
            conn.execute(insert(duels.game_players).values(game_id=gid, user_id=lose["id"], team=2, outcome="loss",
                                                           score=1, joined_at=finished))
    week = {r["username"]: r for r in duels.leaderboard(since=since, until=until)}
    assert week["wk_win"]["wins"] == 1 and week["wk_lose"]["losses"] == 1
    assert {r["username"]: r["wins"] for r in duels.leaderboard()}["wk_win"] >= 2
    assert duels.finished_count(since, until) >= 1
    embed = hooks.leaderboard_embed(since, until)
    assert "Sep 21 – Sep 27" in embed["description"] and [f["name"] for f in embed["fields"]] == ["This week", "All time"]

    sent = []
    real_send = hooks._send
    hooks._send = lambda url, embed: sent.append(embed)
    os.environ["DISCORD_WEBHOOK_DUELS"] = "https://example.invalid/hook"
    try:
        now = datetime(2020, 9, 28, 16, 30, tzinfo=utc)
        assert hooks.weekly_leaderboard(now) and not hooks.weekly_leaderboard(now)  # once per week
        assert not hooks.weekly_leaderboard(now - timedelta(days=7))  # an older week never goes out after
        assert not hooks.weekly_leaderboard(now + timedelta(days=7)) and len(sent) == 1  # no duels that week
    finally:
        hooks._send = real_send
        del os.environ["DISCORD_WEBHOOK_DUELS"]
    assert not hooks.weekly_leaderboard(now + timedelta(days=14))  # no channel set: nothing
    assert hooks.leaderboard_embed(until, until + timedelta(days=7)) is None  # a quiet week: no post


def test_update_notes_are_announced_once():
    import json
    from pathlib import Path
    from backend import discord_webhooks as hooks
    notes = [{"id": "2026-01-02.1", "date": "2026-01-02", "title": "Second", "changes": ["b"]},
             {"id": "2026-01-01.1", "date": "2026-01-01", "title": "First", "changes": ["a"]}]
    path = Path(tempfile.mkdtemp()) / "updates.json"
    path.write_text(json.dumps(notes))
    sent = []
    real_send, real_file = hooks._send, hooks.UPDATES_FILE
    hooks._send, hooks.UPDATES_FILE = (lambda url, embed: sent.append(embed["title"])), path
    try:
        assert hooks.announce_updates() == 0  # no update channel set
        os.environ["DISCORD_WEBHOOK_UPDATES"] = "https://example.invalid/hook"
        assert hooks.announce_updates() == 1 and sent == ["🆕 Second"]  # first run: only the newest
        assert hooks.announce_updates() == 0  # a restart: nothing new
        notes[:0] = [{"id": "2026-01-03.1", "date": "2026-01-03", "title": "Third", "changes": ["c"]},
                     {"id": "2026-01-02.2", "date": "2026-01-02", "title": "Also second", "changes": ["d"]}]
        path.write_text(json.dumps(notes))
        assert hooks.announce_updates() == 2 and sent[1:] == ["🆕 Also second", "🆕 Third"]  # oldest first
    finally:
        hooks._send, hooks.UPDATES_FILE = real_send, real_file
        os.environ.pop("DISCORD_WEBHOOK_UPDATES", None)
    real = json.loads(real_file.read_text(encoding="utf-8"))  # the site's own notes are well-formed
    assert real and all({"id", "date", "title", "changes"} <= set(n) for n in real)
    assert len({n["id"] for n in real}) == len(real) and hooks.update_embed(real[0])["description"]


def test_renamed_forms_carry_overrules_and_posts_along():
    from backend import form_renames
    (boss,) = _users("rename_boss")
    johnny, other = 934, 116  # Johnny Joestar: "Base" became Act 1-4
    forms, default = form_renames._forms(johnny)
    assert "Base" not in forms and default in forms
    community.set_override(other, johnny, "Base", "Base", johnny, "Tusk wins", boss["id"])
    community.create_post(boss["id"], "Tusk wins", None, other, johnny, "Base", "Base", kind="overrule",
                          ruling_winner=johnny)
    community.claim_mark("form_renames", "")  # as on a site that never ran a batch
    assert form_renames.apply() >= 2
    assert community.get_override(other, johnny, "Base", "Base") is None
    assert community.get_override(other, johnny, "Base", default)["winner_id"] == johnny
    assert form_renames.apply() == 0  # once only


def test_a_finished_duel_is_announced_once():
    from backend import discord_webhooks
    announced = []
    real = discord_webhooks.duel_finished
    discord_webhooks.duel_finished = announced.append
    try:
        ann, ben = _users("once_ann", "once_ben")
        game_id = duels.create(ann["id"], "1v1", [], [])
        duels.join(game_id, ben["id"])
        stale = dict(duels.game(game_id)["games"][0])  # a page's view from before the end: still active
        _play(game_id, ann["id"], right=True)
        _play(game_id, ben["id"], right=False)  # the last pick finishes it
        assert announced == [game_id]
        with community.reader.connect() as conn:  # a second request settling the same game
            duels._settle(conn, [stale], community._now())
        assert announced == [game_id]
    finally:
        discord_webhooks.duel_finished = real


def test_matchup_of_the_day_poll_and_reveal():
    from datetime import datetime, timezone
    from backend import characters, daily, discord_webhooks as hooks
    calls = []

    def fake(method, url, embed=None, message=None):
        calls.append((method, url, message or {"embeds": [embed]}))
        if method == "GET":
            return {"poll": {"results": {"answer_counts": [{"id": 1, "count": 3}, {"id": 2, "count": 1}]}}}
        return {"id": f"msg{len(calls)}"} if "wait=true" in url else {}
    real = hooks._request
    hooks._request = fake
    os.environ["DISCORD_WEBHOOK_DAILY"] = "https://example.invalid/daily"
    try:
        utc = timezone.utc  # Budapest is UTC+2 in summer: 17:00 there is 15:00 UTC
        assert not hooks.daily_matchup(datetime(2021, 6, 1, 14, 59, tzinfo=utc))  # before 17:00
        assert hooks.daily_matchup(datetime(2021, 6, 1, 15, 5, tzinfo=utc))
        first = calls[-1][2]
        a, b = daily.pick(datetime(2021, 6, 1).date())
        for day in range(1, 15):  # never a toss-up or a walkover: the reveal names a winner
            v = characters.run_compare(*daily._draw(datetime(2021, 7, day).date()), None, None)
            assert v.favored is not None and v.label != "Overwhelming favorite", v.label
        assert a != b and [x["poll_media"]["text"] for x in first["poll"]["answers"]] and first["poll"]["duration"] == 24
        assert "fields" not in first["embeds"][0]  # nothing to reveal yet
        assert not hooks.daily_matchup(datetime(2021, 6, 1, 20, 0, tzinfo=utc))  # once a day
        daily._picked.clear()  # a restart, and a roster that grew: the day keeps its pair
        real_draw, daily._draw = daily._draw, lambda day: (1, 2)
        try:
            assert daily.pick(datetime(2021, 6, 1).date()) == (a, b)
        finally:
            daily._draw = real_draw
        assert hooks.daily_matchup(datetime(2021, 6, 2, 15, 5, tzinfo=utc))
        assert f"compare.html?a={a}&b={b}" in calls[-1][2]["embeds"][0]["fields"][0]["value"]  # the one posted
        assert calls[-2][0] == "GET" and calls[-2][1].endswith("/messages/msg1")  # yesterday's poll results
        reveal = calls[-1][2]["embeds"][0]["fields"][0]
        assert reveal["name"].startswith("Yesterday: ") and "The site says:" in reveal["value"]
        assert "75% " in reveal["value"] and "(4 votes)" in reveal["value"]
    finally:
        hooks._request = real
        del os.environ["DISCORD_WEBHOOK_DAILY"]


def test_discord_linking_and_duel_command():
    from datetime import timedelta
    from sqlalchemy import update
    from backend import discord_bot
    ann, ben = _users("link_ann", "link_ben")

    def as_discord(discord_id, handle):
        return {"member": {"user": {"id": discord_id, "username": handle}}, "channel_id": "555"}

    def run(command, who, **opts):
        return discord_bot.handle({"type": 2, "data": {"name": command, "options": [
            {"name": k, "type": 3, "value": v} for k, v in opts.items()]}, **who})["data"]

    # /link hands out a private one-time link; confirming it on the site links the account.
    reply = run("link", as_discord("111", "ann_dc"))
    assert reply["flags"] == discord_bot.EPHEMERAL and "profile.html?link=" in reply["content"]
    code = reply["content"].split("link=")[1].split(">")[0]
    assert community.discord_link_pending(code) == "ann_dc"
    assert community.redeem_discord_link(code, ann["id"]) == "ann_dc"
    assert community.user_by_discord("111")["username"] == "link_ann"
    assert community.get_profile(user_id=ann["id"])["discord_name"] == "ann_dc"
    for bad in (code, "nonsense"):  # used, or never existed
        try:
            community.redeem_discord_link(bad, ben["id"])
            assert False, "linked twice"
        except community.LinkError:
            pass
    late = community.discord_link_code("222", "ben_dc")
    with community.engine.begin() as conn:
        conn.execute(update(community.discord_links).values(expires_at=community._now() - timedelta(minutes=1)))
    assert community.discord_link_pending(late) is None
    community.redeem_discord_link(community.discord_link_code("222", "ben_dc"), ben["id"])

    # /duel as a linked user; a challenge pings the opponent and nobody else.
    assert run("duel", as_discord("999", "stranger"))["flags"] == discord_bot.EPHEMERAL  # not linked
    open_game = run("duel", as_discord("111", "ann_dc"), mode="draft", format="1v1v1")
    assert "wants to duel" in open_game["embeds"][0]["title"] and "Draft duel · 1v1v1" in open_game["embeds"][0]["description"]
    challenge = run("duel", as_discord("111", "ann_dc"), opponent="222")
    assert challenge["content"].startswith("<@222>") and challenge["allowed_mentions"] == {"users": ["222"]}
    assert "challenges link_ben" in challenge["embeds"][0]["title"]
    game_id = int(challenge["components"][0]["components"][0]["url"].split("game=")[1])
    assert duels.game(game_id)["games"][0]["status"] == "open"
    assert run("duel", as_discord("111", "ann_dc"), opponent="999")["flags"] == discord_bot.EPHEMERAL  # not linked
    assert run("duel", as_discord("111", "ann_dc"), opponent="222", format="2v2")["flags"] == discord_bot.EPHEMERAL

    # /profile by Discord user, or your own - with a Discord line.
    card = run("profile", as_discord("111", "ann_dc"), user="222")["embeds"][0]
    assert card["title"] == "link_ben" and {"name": "Discord", "value": "<@222>", "inline": True} in card["fields"]
    assert run("profile", as_discord("111", "ann_dc"))["embeds"][0]["title"] == "link_ann"
    right_click = discord_bot.handle({"type": 2, "data": {"type": 2, "name": "Powerscale profile", "target_id": "222"},
                                      **as_discord("111", "ann_dc")})["data"]
    assert right_click["embeds"][0]["title"] == "link_ben"

    # The public profile shows it, both ways - until they hide it.
    from backend import community_api
    public = community_api._profile_out(community.get_profile(user_id=ben["id"]), own=False)
    assert public.discord == "ben_dc" and public.discord_id == "222" and public.discord_shown is None
    community.show_discord(ben["id"], False)
    public = community_api._profile_out(community.get_profile(user_id=ben["id"]), own=False)
    assert public.discord is None and public.discord_id is None
    own = community_api._profile_out(community.get_profile(user_id=ben["id"]), own=True)
    assert own.discord == "ben_dc" and own.discord_shown is False
    hidden = run("profile", as_discord("111", "ann_dc"), user="222")
    assert hidden["flags"] == discord_bot.EPHEMERAL  # can't be found from Discord
    assert run("duel", as_discord("111", "ann_dc"), opponent="222")["flags"] == discord_bot.EPHEMERAL
    assert run("profile", as_discord("222", "ben_dc"))["embeds"][0]["title"] == "link_ben"  # themselves: still fine
    assert not any(f["name"] == "Discord" for f in run("profile", as_discord("222", "ben_dc"))["embeds"][0]["fields"])
    community.unlink_discord(ann["id"])
    assert community.user_by_discord("111") is None
    assert run("profile", as_discord("111", "ann_dc"))["flags"] == discord_bot.EPHEMERAL


def test_draft_too_close_to_call_is_even():
    from backend import characters
    pool = characters.scorable_pool()
    close = None
    for i, (a, _, _) in enumerate(pool[:400]):
        for b, _, _ in pool[i + 1:i + 30]:
            v = characters.run_compare(a, b, None, None)
            if v.favored is None and v.composite and community.get_override(a, b, v.form_a, v.form_b) is None:
                close = (a, b, v.composite)
                break
        if close:
            break
    a, b, lean = close  # the site says "too close to call", though it leans a little
    assert duels._stronger(a, b) is None  # even: the faster pick decides
    assert duels._stronger(a, b, lean=True) == (a if lean > 0 else b)  # how games finished before were scored
    members = [{"user_id": 1, "team": 1}, {"user_id": 2, "team": 2}]
    bout = duels.draft_bouts(members, {(1, 1): a, (1, 2): b}, {(1, 1): 9.0, (1, 2): 4.0})[0]
    assert bout["winner"] == 2 and bout["by_speed"]


def test_games_from_before_teams_get_players_and_outcomes():
    from sqlalchemy import insert
    old_a, old_b = _users("old_a", "old_b")
    now = community._now()
    with community.engine.begin() as conn:
        gid = conn.execute(insert(duels.games).values(
            creator_id=old_a["id"], opponent_id=old_b["id"], status="done", created_at=now,
            accepted_at=now, finished_at=now, winner_id=old_b["id"])).inserted_primary_key[0]
    duels._migrate()
    members = {m["user_id"]: m for m in duels.game(gid)["members"][gid]}
    assert members[old_a["id"]]["team"] == 1 and members[old_a["id"]]["outcome"] == "loss"
    assert members[old_b["id"]]["team"] == 2 and members[old_b["id"]]["outcome"] == "win"
    assert duels.game(gid)["games"][0]["winning_team"] == 2


def test_duel_picked_matchups_need_a_clear_winner():
    from backend import characters
    (gina,) = _users("duel_gina")
    pool = characters.scorable_pool()
    clear = close = None
    for i, (a, _, _) in enumerate(pool[:300]):
        for b, _, _ in pool[i + 1:i + 40]:
            v = characters.run_compare(a, b, None, None)
            if v.favored and not clear:
                clear = (a, b)
            # (skipping pairs an earlier test overruled: an overrule is a clear answer)
            if v.favored is None and v.composite is not None and not close \
                    and community.get_override(a, b, v.form_a, v.form_b) is None:
                close = (a, b)
        if clear and close:
            break
    game_id = duels.create(gina["id"], "1v1", [], [{"char_a": clear[0], "char_b": clear[1]}])
    from sqlalchemy import select
    with community.engine.connect() as conn:
        rounds = conn.execute(select(duels.game_rounds).where(duels.game_rounds.c.game_id == game_id)).mappings().all()
    assert len(rounds) == duels.ROUNDS and sum(r["picked"] for r in rounds) == 1
    _raises(lambda: duels.create(gina["id"], "1v1", [], [{"char_a": close[0], "char_b": close[1]}]),
            text="no clear winner")


def test_tickets_one_per_matchup_bans_and_answers():
    def raises(fn, status):
        try:
            fn()
        except tickets.TicketError as exc:
            assert exc.status == status, exc.status
            return
        assert False, "expected a TicketError"
    tia, admin = _users("tk_tia", "tk_boss")
    tid = tickets.create(tia["id"], 12, 7, "Base", "Post-Crisis", 7, "Superman outclasses him")
    assert tickets.mine(tia["id"], 7, 12, "Post-Crisis", "Base")["id"] == tid  # either order
    raises(lambda: tickets.create(tia["id"], 7, 12, "Post-Crisis", "Base", 12, "again"), 409)  # one per matchup
    other = tickets.create(tia["id"], 7, 12, "Pre-Crisis", "Base", 12, "a different form pair is its own matchup")
    assert tickets.open_count() == 2 and [t["id"] for t in tickets.listing(status="open")] == [tid, other]
    tickets.answer(tid, admin["id"], "The verdict stands.", "kept")
    raises(lambda: tickets.answer(tid, admin["id"], "twice", "kept"), 409)
    raises(lambda: tickets.create(tia["id"], 12, 7, "Base", "Post-Crisis", 7, "answered: settled"), 409)
    t = tickets.one(tid)
    assert t["status"] == "answered" and t["outcome"] == "kept" and t["admin"] == "tk_boss" and t["username"] == "tk_tia"
    tickets.ban(tia["id"], admin["id"], "spam")
    raises(lambda: tickets.create(tia["id"], 3, 4, "Base", "Base", 3, "banned"), 403)
    assert tickets.one(other)["banned"] and [b["username"] for b in tickets.bans()] == ["tk_tia"]
    assert tickets.unban(tia["id"]) and not tickets.ban_of(tia["id"])
    tickets.delete_user(tia["id"])
    assert tickets.listing(user_id=tia["id"]) == []


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
