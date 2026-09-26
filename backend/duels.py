"""Prediction duels: two users each call five matchups against the clock;
whoever matches the site's verdict more often wins.

A challenge is open to anyone or sent to one user. Its five matchups -
picked by the challenger or drawn at random from close-ish tiers - are
fixed when it's created, and so is each round's answer: the admin
overrule if there is one, else the calculator's favorite ("too close to
call" matchups are never used). Each player plays on their own time; a
round's 20-second clock starts on the server when that round is shown,
so reloading doesn't reset it. Nobody sees answers or the other's picks
until both have played every round.

Stored in the community database (Neon), next to accounts and posts."""

import random
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Table, and_, delete, insert, or_, select,
    update,
)

from backend import characters, community
from backend.community import _aware, _now, avatars, engine, metadata, users

ROUNDS = 5
ROUND_SECONDS = 20
GRACE_SECONDS = 3  # network lag between the clock running out and the pick arriving
OPEN_DAYS = 7  # an unaccepted challenge expires after this
FORFEIT_HOURS = 72  # once one player is done, the other has this long
RANDOM_TIER_SPREAD = 3.0  # random rounds pair characters within this Tier distance (~one tier)

games = Table(
    "games", metadata,
    Column("id", Integer, primary_key=True),
    Column("creator_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    # Set when someone accepts. For a challenge sent to one user,
    # invited_id names them from the start.
    Column("opponent_id", Integer, ForeignKey("users.id"), nullable=True, index=True),
    Column("invited_id", Integer, ForeignKey("users.id"), nullable=True, index=True),
    # open | active | done | expired | declined | cancelled
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("winner_id", Integer, nullable=True),  # NULL once done: a draw
)
game_rounds = Table(
    "game_rounds", metadata,
    Column("game_id", Integer, ForeignKey("games.id"), primary_key=True),
    Column("round_no", Integer, primary_key=True),
    Column("char_a", Integer, nullable=False),
    Column("char_b", Integer, nullable=False),
    Column("form_a", String(200), nullable=False),
    Column("form_b", String(200), nullable=False),
    Column("answer_id", Integer, nullable=False),
    Column("verdict", String(300), nullable=False),  # what decided it, for the results
    Column("picked", Boolean, nullable=False),  # chosen by the challenger, not drawn
)
game_picks = Table(
    "game_picks", metadata,
    Column("game_id", Integer, ForeignKey("games.id"), primary_key=True),
    Column("round_no", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("pick_id", Integer, nullable=True),
    Column("answered_at", DateTime(timezone=True), nullable=True),
)


class DuelError(Exception):
    """A request the rules don't allow; the message is shown as is."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- matchups and answers -------------------------------------------------------------

def _answer(a: int, b: int, form_a: Optional[str], form_b: Optional[str]) -> Optional[dict]:
    """The round a matchup would make, or None when there's no clear answer."""
    try:
        v = characters.run_compare(a, b, form_a, form_b)
    except ValueError:
        return None
    ov = community.get_override(a, b, v.form_a, v.form_b)
    if ov is not None:
        winner = ov["winner_id"]
        verdict = f"{_bare(characters.display_name_for_id(winner))} wins — overruled by admins"
    elif v.favored is not None and v.composite:
        winner = a if v.composite > 0 else b
        verdict = f"{_bare(v.favored)} favored — {v.label}"
    else:
        return None
    return {"char_a": a, "char_b": b, "form_a": v.form_a, "form_b": v.form_b,
            "answer_id": winner, "verdict": verdict}


def _bare(name: Optional[str]) -> str:
    short = characters.short_name(name or "Unknown")
    return re.sub(r"\s*\([^)]*\)", "", short).strip() or short


def _random_rounds(n: int, taken: set) -> List[dict]:
    pool = characters.scorable_pool()
    rounds, tries = [], 0
    while len(rounds) < n and tries < 60 * n:
        tries += 1
        a, tier_a = random.choice(pool)
        near = [cid for cid, t in pool if cid != a and abs(t - tier_a) <= RANDOM_TIER_SPREAD]
        if not near:
            continue
        b = random.choice(near)
        if frozenset((a, b)) in taken:
            continue
        r = _answer(a, b, None, None)
        if r:
            taken.add(frozenset((a, b)))
            rounds.append({**r, "picked": False})
    if len(rounds) < n:
        raise DuelError("Couldn't draw enough matchups - try again", 503)
    return rounds


# --- creating and joining -------------------------------------------------------------

def _user_by_name(username: str) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(select(users.c.id, users.c.username)
                           .where(users.c.username_lower == username.strip().lower())).mappings().first()
    return dict(row) if row else None


def create(creator_id: int, opponent: Optional[str], picked: List[dict]) -> int:
    invited = None
    if opponent and opponent.strip():
        invited = _user_by_name(opponent)
        if invited is None:
            raise DuelError("No user with that name", 404)
        if invited["id"] == creator_id:
            raise DuelError("You can't challenge yourself")
    rounds, taken = [], set()
    for i, m in enumerate(picked[:ROUNDS], start=1):
        if m["char_a"] == m["char_b"]:
            raise DuelError(f"Matchup {i} needs two different characters")
        r = _answer(m["char_a"], m["char_b"], m.get("form_a"), m.get("form_b"))
        if r is None:
            raise DuelError(f"Matchup {i} has no clear winner (too close to call, or not enough stats) - pick another")
        if frozenset((r["char_a"], r["char_b"])) in taken:
            raise DuelError(f"Matchup {i} is already in this challenge")
        taken.add(frozenset((r["char_a"], r["char_b"])))
        rounds.append({**r, "picked": True})
    rounds += _random_rounds(ROUNDS - len(rounds), taken)
    random.shuffle(rounds)
    with engine.begin() as conn:
        game_id = conn.execute(insert(games).values(
            creator_id=creator_id, invited_id=invited["id"] if invited else None,
            status="open", created_at=_now(),
        )).inserted_primary_key[0]
        conn.execute(insert(game_rounds), [{"game_id": game_id, "round_no": i, **r}
                                           for i, r in enumerate(rounds, start=1)])
    return game_id


def _game(conn, game_id: int) -> dict:
    row = conn.execute(select(games).where(games.c.id == game_id)).mappings().first()
    if row is None:
        raise DuelError("No such challenge", 404)
    return dict(row)


def accept(game_id: int, user_id: int) -> None:
    with engine.begin() as conn:
        g = _game(conn, game_id)
        if g["creator_id"] == user_id:
            raise DuelError("That's your own challenge")
        if g["invited_id"] is not None and g["invited_id"] != user_id:
            raise DuelError("This challenge was sent to someone else", 403)
        # Only if still open: two people accepting at once can't both get it.
        taken = conn.execute(update(games).where(and_(games.c.id == game_id, games.c.status == "open"))
                             .values(status="active", opponent_id=user_id, accepted_at=_now())).rowcount
    if not taken:
        raise DuelError("Someone else already took this challenge", 409)


def decline(game_id: int, user_id: int) -> None:
    with engine.begin() as conn:
        g = _game(conn, game_id)
        if g["invited_id"] != user_id or g["status"] != "open":
            raise DuelError("You can't decline this challenge", 403)
        conn.execute(update(games).where(games.c.id == game_id).values(status="declined", finished_at=_now()))


def cancel(game_id: int, user_id: int) -> None:
    with engine.begin() as conn:
        g = _game(conn, game_id)
        if g["creator_id"] != user_id or g["status"] != "open":
            raise DuelError("Only an open challenge you made can be cancelled", 403)
        conn.execute(update(games).where(games.c.id == game_id).values(status="cancelled", finished_at=_now()))


# --- playing ------------------------------------------------------------------------------

def _deadline(started_at: datetime) -> datetime:
    return _aware(started_at) + timedelta(seconds=ROUND_SECONDS)


def _settled(pick: dict, now: datetime) -> bool:
    """Answered, or its clock has run out."""
    return pick["answered_at"] is not None or now > _deadline(pick["started_at"]) + timedelta(seconds=GRACE_SECONDS)


def _correct(pick: Optional[dict], answer_id: int) -> bool:
    return bool(pick and pick["pick_id"] == answer_id and pick["answered_at"] is not None
                and _aware(pick["answered_at"]) <= _deadline(pick["started_at"]) + timedelta(seconds=GRACE_SECONDS))


def _can_play(g: dict, user_id: int) -> bool:
    return (g["status"] == "open" and g["creator_id"] == user_id) or \
        (g["status"] == "active" and user_id in (g["creator_id"], g["opponent_id"]))


def next_round(game_id: int, user_id: int) -> Optional[dict]:
    """The round this player is on - starting its clock if it wasn't shown
    yet - or None when they've played them all."""
    now = _now()
    with engine.begin() as conn:
        g = _game(conn, game_id)
        _settle(conn, [g], now)
        if not _can_play(g, user_id):
            if user_id not in (g["creator_id"], g["opponent_id"]):
                raise DuelError("Accept the challenge first", 403)
            return None
        picks = {p["round_no"]: dict(p) for p in conn.execute(select(game_picks).where(and_(
            game_picks.c.game_id == game_id, game_picks.c.user_id == user_id))).mappings()}
        for round_no in range(1, ROUNDS + 1):
            pick = picks.get(round_no)
            if pick and _settled(pick, now):
                continue
            if pick is None:
                pick = {"started_at": now}
                conn.execute(insert(game_picks).values(game_id=game_id, round_no=round_no,
                                                       user_id=user_id, started_at=now))
            r = conn.execute(select(game_rounds).where(and_(
                game_rounds.c.game_id == game_id, game_rounds.c.round_no == round_no))).mappings().first()
            left = (_deadline(pick["started_at"]) - now).total_seconds()
            return {**dict(r), "seconds_left": max(0.0, left)}
    return None


def pick(game_id: int, user_id: int, round_no: int, pick_id: int) -> bool:
    """Records a pick; False if it came in after the clock ran out (it
    then counts as wrong)."""
    now = _now()
    with engine.begin() as conn:
        r = conn.execute(select(game_rounds).where(and_(
            game_rounds.c.game_id == game_id, game_rounds.c.round_no == round_no))).mappings().first()
        if r is None:
            raise DuelError("No such round", 404)
        if pick_id not in (r["char_a"], r["char_b"]):
            raise DuelError("Pick one of the two characters")
        where = and_(game_picks.c.game_id == game_id, game_picks.c.round_no == round_no,
                     game_picks.c.user_id == user_id)
        p = conn.execute(select(game_picks).where(where)).mappings().first()
        if p is None:
            raise DuelError("That round hasn't started", 409)
        if p["answered_at"] is not None:
            raise DuelError("You already answered this round", 409)
        conn.execute(update(game_picks).where(where).values(pick_id=pick_id, answered_at=now))
        _settle(conn, [_game(conn, game_id)], now)
    return now <= _deadline(p["started_at"]) + timedelta(seconds=GRACE_SECONDS)


# --- settling -------------------------------------------------------------------------------

def _settle(conn, gs: List[dict], now: datetime, picks: Optional[List[dict]] = None) -> None:
    """Moves games on as time passes: expires old open challenges, and
    finishes games once both players are done - or once one is done and
    the other has let FORFEIT_HOURS go by (their unplayed rounds count as
    wrong). Updates the dicts in `gs` in place."""
    active = [g for g in gs if g["status"] == "active"]
    for g in gs:
        if g["status"] == "open" and now - _aware(g["created_at"]) > timedelta(days=OPEN_DAYS):
            g.update(status="expired", finished_at=now)
            conn.execute(update(games).where(games.c.id == g["id"]).values(status="expired", finished_at=now))
    if not active:
        return
    ids = [g["id"] for g in active]
    if picks is None:
        picks = [dict(p) for p in conn.execute(select(game_picks).where(game_picks.c.game_id.in_(ids))).mappings()]
    answers = {(r["game_id"], r["round_no"]): r["answer_id"] for r in conn.execute(
        select(game_rounds.c.game_id, game_rounds.c.round_no, game_rounds.c.answer_id)
        .where(game_rounds.c.game_id.in_(ids))).mappings()}
    by_player: Dict[Tuple[int, int], List[dict]] = {}
    for p in picks:
        by_player.setdefault((p["game_id"], p["user_id"]), []).append(p)
    for g in active:
        done_at = {}
        for uid in (g["creator_id"], g["opponent_id"]):
            mine = [p for p in by_player.get((g["id"], uid), []) if _settled(p, now)]
            if len(mine) == ROUNDS:
                done_at[uid] = max(_aware(p["answered_at"]) if p["answered_at"] else _deadline(p["started_at"])
                                   for p in mine)
        if len(done_at) < 2:
            if not done_at:
                continue
            waited_from = max(list(done_at.values()) + [_aware(g["accepted_at"])])
            if now - waited_from < timedelta(hours=FORFEIT_HOURS):
                continue
        score = {uid: sum(_correct(next((p for p in by_player.get((g["id"], uid), []) if p["round_no"] == n), None),
                                   answers[(g["id"], n)]) for n in range(1, ROUNDS + 1))
                 for uid in (g["creator_id"], g["opponent_id"])}
        a, b = score[g["creator_id"]], score[g["opponent_id"]]
        winner = g["creator_id"] if a > b else g["opponent_id"] if b > a else None
        g.update(status="done", finished_at=now, winner_id=winner)
        conn.execute(update(games).where(games.c.id == g["id"]).values(status="done", finished_at=now,
                                                                        winner_id=winner))


# --- reading ---------------------------------------------------------------------------------

def _load(conn, where, limit: Optional[int] = None) -> List[dict]:
    q = select(games).where(where).order_by(games.c.id.desc())
    if limit:
        q = q.limit(limit)
    return [dict(g) for g in conn.execute(q).mappings()]


def overview(user_id: int) -> dict:
    """The player's own games (made, accepted or invited to), newest first,
    and other people's open challenges they could take."""
    now = _now()
    with engine.begin() as conn:
        mine = _load(conn, or_(games.c.creator_id == user_id, games.c.opponent_id == user_id,
                               games.c.invited_id == user_id), limit=60)
        others = _load(conn, and_(games.c.status == "open", games.c.invited_id.is_(None),
                                  games.c.creator_id != user_id), limit=20)
        all_games = mine + others
        picks = [dict(p) for p in conn.execute(select(game_picks).where(
            game_picks.c.game_id.in_([g["id"] for g in all_games] or [0]))).mappings()]
        _settle(conn, all_games, now, [p for p in picks if p["game_id"] in {g["id"] for g in all_games}])
        others = [g for g in others if g["status"] == "open"]
        details = _details(conn, [g for g in mine if g["status"] == "done"])
    return {"mine": mine, "open": others, "picks": picks, "details": details, "now": now}


def game(game_id: int, user_id: Optional[int]) -> dict:
    now = _now()
    with engine.begin() as conn:
        g = _game(conn, game_id)
        picks = [dict(p) for p in conn.execute(select(game_picks).where(game_picks.c.game_id == game_id)).mappings()]
        _settle(conn, [g], now, picks)
        details = _details(conn, [g] if g["status"] == "done" else [])
    return {"game": g, "picks": picks, "details": details, "now": now}


def _details(conn, done: List[dict]) -> Dict[int, List[dict]]:
    """Rounds of finished games (answers included), by game id."""
    if not done:
        return {}
    out: Dict[int, List[dict]] = {}
    for r in conn.execute(select(game_rounds).where(game_rounds.c.game_id.in_([g["id"] for g in done]))
                          .order_by(game_rounds.c.round_no)).mappings():
        out.setdefault(r["game_id"], []).append(dict(r))
    return out


def players(ids) -> Dict[int, dict]:
    """{user id: {"username", "avatar_at"}} in one query."""
    ids = [i for i in set(ids) if i is not None]
    if not ids:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(select(users.c.id, users.c.username, avatars.c.updated_at.label("avatar_at"))
                            .outerjoin(avatars, avatars.c.user_id == users.c.id)
                            .where(users.c.id.in_(ids))).mappings()
        return {r["id"]: {"username": r["username"], "avatar_at": _aware(r["avatar_at"]) if r["avatar_at"] else None}
                for r in rows}


def pending_count(user_id: int) -> int:
    """Challenges sent to this player, plus games waiting on their rounds -
    for the dot on the Duels link."""
    now = _now()
    with engine.begin() as conn:
        gs = _load(conn, and_(games.c.status.in_(["open", "active"]), or_(
            games.c.creator_id == user_id, games.c.opponent_id == user_id, games.c.invited_id == user_id)))
        picks = [dict(p) for p in conn.execute(select(game_picks).where(and_(
            game_picks.c.game_id.in_([g["id"] for g in gs] or [0])))).mappings()]
        _settle(conn, gs, now, picks)
    n = 0
    for g in gs:
        if g["status"] == "open" and g["invited_id"] == user_id:
            n += 1
        elif _can_play(g, user_id) and played(picks, g["id"], user_id, now) < ROUNDS:
            n += 1
    return n


def played(picks: List[dict], game_id: int, user_id: Optional[int], now: datetime) -> int:
    return sum(1 for p in picks if p["game_id"] == game_id and p["user_id"] == user_id and _settled(p, now))


def correct(picks: List[dict], game_id: int, user_id: Optional[int], round_no: int, answer_id: int) -> Tuple[Optional[int], bool]:
    p = next((p for p in picks if p["game_id"] == game_id and p["user_id"] == user_id
              and p["round_no"] == round_no), None)
    return (p["pick_id"] if p else None), _correct(p, answer_id)


# --- records ---------------------------------------------------------------------------------

def records(user_ids: Optional[List[int]] = None) -> Dict[int, dict]:
    """{user id: {"wins", "draws", "losses"}} over finished games."""
    where = games.c.status == "done"
    if user_ids is not None:
        where = and_(where, or_(games.c.creator_id.in_(user_ids or [0]), games.c.opponent_id.in_(user_ids or [0])))
    with engine.connect() as conn:
        rows = conn.execute(select(games.c.creator_id, games.c.opponent_id, games.c.winner_id).where(where)).all()
    out: Dict[int, dict] = {}
    for creator, opponent, winner in rows:
        for uid in (creator, opponent):
            r = out.setdefault(uid, {"wins": 0, "draws": 0, "losses": 0})
            r["draws" if winner is None else "wins" if winner == uid else "losses"] += 1
    return out


def delete_user_games(user_id: int) -> None:
    """Every duel the user made, joined or was invited to - called when
    they delete their account."""
    with engine.begin() as conn:
        ids = [r[0] for r in conn.execute(select(games.c.id).where(or_(
            games.c.creator_id == user_id, games.c.opponent_id == user_id, games.c.invited_id == user_id)))]
        if ids:
            conn.execute(delete(game_picks).where(game_picks.c.game_id.in_(ids)))
            conn.execute(delete(game_rounds).where(game_rounds.c.game_id.in_(ids)))
            conn.execute(delete(games).where(games.c.id.in_(ids)))
