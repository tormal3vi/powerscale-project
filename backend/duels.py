"""Prediction duels: players each call the same five matchups against the
clock; whoever (or whichever team) matches the site's verdict most often
wins.

Formats run from 1v1 to free-for-alls (1v1v1, 1v1v1v1) and teams (2v2,
2v2v2, 3v3). A game is open to anyone, or private - the challenger invites
exactly enough players to fill it. Everyone plays on their own time, from
the moment they join. A team's score is its members' right answers added
up; the top team wins, and teams sharing the top score draw.

The five matchups - picked by the challenger or drawn at random from
close-ish tiers - are fixed when the game is created, and so is each
answer: the admin overrule if there is one, else the calculator's favorite
("too close to call" matchups are never used). A round's 20-second clock
starts on the server when the round is shown, so reloading doesn't reset
it. Nobody sees answers or anyone else's picks until the game is over.

Draft mode instead deals each player a hand of HAND_SIZE characters per
round - every hand in a round drawn from around the same tier, so they're
comparable - and each picks the one they think is strongest, against the
clock. Picks then fight every opponent's pick of that round (the site's
verdict, or its lean when too close to call; no pick loses to any pick):
a player's score is their head-to-head wins.

Stored in the community database (Neon), next to accounts and posts."""

import random
import re
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Table, and_, case, delete, func, insert, or_,
    select, update,
)

from sqlalchemy.exc import IntegrityError

from backend import characters, community, prewarm
from backend.community import _aware, _now, avatars, engine, metadata, users

ROUNDS = 5
ROUND_SECONDS = 20
GRACE_SECONDS = 3  # network lag between the clock running out and the pick arriving
OPEN_DAYS = 7  # a game that never fills expires after this
FORFEIT_HOURS = 72  # once a game is full and someone's done, the rest have this long
RANDOM_TIER_SPREAD = 3.0  # random rounds pair characters within this Tier distance (~one tier)
HAND_SIZE = 4  # draft: characters dealt to each player per round (a 2x2 grid on phones)
MODES = ("predict", "draft")

# (teams, players per team)
FORMATS = {"1v1": (2, 1), "1v1v1": (3, 1), "1v1v1v1": (4, 1), "2v2": (2, 2), "2v2v2": (3, 2), "3v3": (2, 3)}

games = Table(
    "games", metadata,
    Column("id", Integer, primary_key=True),
    Column("creator_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    # opponent_id, invited_id and winner_id are from when every game was
    # 1v1: kept for old rows (see _migrate), unused since game_players.
    Column("opponent_id", Integer, ForeignKey("users.id"), nullable=True, index=True),
    Column("invited_id", Integer, ForeignKey("users.id"), nullable=True, index=True),
    # open (seats left) | active (full) | done | expired | declined | cancelled
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=True),  # when the last seat filled
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("winner_id", Integer, nullable=True),
    Column("teams", Integer, nullable=True),  # NULL on old rows: 2
    Column("team_size", Integer, nullable=True),  # NULL on old rows: 1
    Column("winning_team", Integer, nullable=True),  # NULL once done: a draw at the top
    Column("picked", Integer, nullable=True),  # how many rounds the creator chose (NULL on old rows)
    Column("excluded", String(1000), nullable=True),  # series left out of the random rounds, "|"-joined
    Column("mode", String(12), nullable=True),  # predict (NULL on old rows) | draft
)
game_players = Table(
    "game_players", metadata,
    Column("game_id", Integer, ForeignKey("games.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True, index=True),
    Column("team", Integer, nullable=False),  # 1-based
    Column("joined_at", DateTime(timezone=True), nullable=False),
    Column("outcome", String(8), nullable=True),  # win | draw | loss, once done
    Column("score", Integer, nullable=True),  # right answers (draft: head-to-head wins), once done
    Column("seat", Integer, nullable=True),  # draft: which dealt hands are yours (0-based; NULL otherwise)
)
# Draft games: each seat's hand for each round, dealt when the game is made.
game_hands = Table(
    "game_hands", metadata,
    Column("game_id", Integer, ForeignKey("games.id"), primary_key=True),
    Column("round_no", Integer, primary_key=True),
    Column("seat", Integer, primary_key=True),
    Column("slot", Integer, primary_key=True),
    Column("char_id", Integer, nullable=False),
)
# Private games: who may take the seats.
game_invites = Table(
    "game_invites", metadata,
    Column("game_id", Integer, ForeignKey("games.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True, index=True),
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


@community.on_init
def _migrate() -> None:
    """Games from before team play (all 1v1): add the new columns, and give
    each old game its player rows, invite and outcomes."""
    with engine.begin() as conn:
        community.add_missing_columns(conn, "games", [
            ("teams", "INTEGER"), ("team_size", "INTEGER"), ("winning_team", "INTEGER"),
            ("picked", "INTEGER"), ("excluded", "VARCHAR(1000)")])
        community.add_missing_columns(conn, "games", [("mode", "VARCHAR(12)")])
        community.add_missing_columns(conn, "game_players", [("score", "INTEGER"), ("seat", "INTEGER")])
        has_players = select(game_players.c.game_id).where(game_players.c.game_id == games.c.id).exists()
        old = conn.execute(select(games).where(~has_players)).mappings().all()
        for g in old:
            done = g["status"] == "done"
            for team, uid in ((1, g["creator_id"]), (2, g["opponent_id"])):
                if uid is None:
                    continue
                outcome = None
                if done:
                    outcome = "draw" if g["winner_id"] is None else "win" if g["winner_id"] == uid else "loss"
                conn.execute(insert(game_players).values(game_id=g["id"], user_id=uid, team=team,
                                                         joined_at=g["accepted_at"] or g["created_at"], outcome=outcome))
            if g["invited_id"] is not None:
                conn.execute(insert(game_invites).values(game_id=g["id"], user_id=g["invited_id"]))
            winning = None
            if done and g["winner_id"] is not None:
                winning = 1 if g["winner_id"] == g["creator_id"] else 2
            conn.execute(update(games).where(games.c.id == g["id"]).values(teams=2, team_size=1, winning_team=winning))


class DuelError(Exception):
    """A request the rules don't allow; the message is shown as is."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- "anything new?" ---------------------------------------------------------------
# The Duels page polls this, like the Board's version: bumped by every
# write here, answered from memory. (One server process, as for _memo.)

_version_lock = threading.Lock()
_changes = 0


def version() -> str:
    with _version_lock:
        return f"{community._board_boot}.{_changes}"


def _changed() -> None:
    global _changes
    with _version_lock:
        _changes += 1


# --- matchups and answers -------------------------------------------------------------

def _bare(name: Optional[str]) -> str:
    short = characters.short_name(name or "Unknown")
    return re.sub(r"\s*\([^)]*\)", "", short).strip() or short


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


def _random_rounds(n: int, taken: set, excluded: frozenset = frozenset()) -> List[dict]:
    pool = [(cid, tier) for cid, tier, series in characters.scorable_pool() if series not in excluded]
    if n and len(pool) < 20:
        raise DuelError("Too few characters left to draw from - exclude fewer series")
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


def _deal(seats: int, excluded: frozenset) -> List[dict]:
    """Every seat's hand for every round (game_hands rows). A round's
    hands come from around one randomly chosen tier, widening the band if
    it's too thin; no character is dealt twice in a game."""
    pool = [(cid, tier) for cid, tier, series in characters.scorable_pool() if series not in excluded]
    need = seats * HAND_SIZE
    if len(pool) < need * ROUNDS:
        raise DuelError("Too few characters left to deal from - exclude fewer series")
    used: set = set()
    rows = []
    for round_no in range(1, ROUNDS + 1):
        center = random.choice(pool)[1]
        for spread in (RANDOM_TIER_SPREAD, RANDOM_TIER_SPREAD * 2, RANDOM_TIER_SPREAD * 4, 1e9):
            band = [cid for cid, t in pool if abs(t - center) <= spread and cid not in used]
            if len(band) >= need:
                break
        dealt = random.sample(band, need)
        used.update(dealt)
        for i, cid in enumerate(dealt):
            rows.append({"round_no": round_no, "seat": i // HAND_SIZE, "slot": i % HAND_SIZE, "char_id": cid})
    return rows


def _stronger(a: Optional[int], b: Optional[int]) -> Optional[int]:
    """Draft: which of two picks wins - an admin overrule, else the
    calculator (its lean when it's too close to call). No pick loses to any
    pick; None when neither picked, or it's dead even."""
    if a is None or b is None:
        return a if b is None else b
    try:
        v = characters.run_compare(a, b, None, None)
    except ValueError:
        return None
    ov = community.get_override(a, b, v.form_a, v.form_b)
    if ov is not None:
        return ov["winner_id"]
    if v.composite:
        return a if v.composite > 0 else b
    return None


def draft_points(members: List[dict], picked: Dict[Tuple[int, int], Optional[int]]) -> Dict[Tuple[int, int], int]:
    """{(round, user id): head-to-head wins} - each player's pick against
    every opponent's (other teams only) pick that round. `picked` maps
    (round, user id) to the character they picked in time (or None)."""
    out: Dict[Tuple[int, int], int] = {}
    for n in range(1, ROUNDS + 1):
        for i, x in enumerate(members):
            out.setdefault((n, x["user_id"]), 0)
            for y in members[i + 1:]:
                if x["team"] == y["team"]:
                    continue
                cx, cy = picked.get((n, x["user_id"])), picked.get((n, y["user_id"]))
                w = _stronger(cx, cy)
                if w is not None and cx != cy:
                    winner = x if w == cx else y
                    out[(n, winner["user_id"])] = out.get((n, winner["user_id"]), 0) + 1
    return out


# --- creating, joining, leaving ---------------------------------------------------------

def shape(g: dict) -> Tuple[int, int]:
    """(teams, players per team)."""
    return g["teams"] or 2, g["team_size"] or 1


def format_name(g: dict) -> str:
    teams, size = shape(g)
    return "v".join([str(size)] * teams)


def create(creator_id: int, fmt: str, invite: List[str], picked: List[dict], exclude: List[str] = (),
           mode: str = "predict") -> int:
    """`exclude`: series whose characters the random rounds (or draft
    hands) leave out - picked matchups are the creator's own choice."""
    if fmt not in FORMATS:
        raise DuelError("Unknown format")
    if mode not in MODES:
        raise DuelError("Unknown mode")
    excluded = frozenset(s.strip() for s in exclude if s and s.strip())
    teams, size = FORMATS[fmt]
    seats = teams * size - 1
    names = [n.strip() for n in invite if n and n.strip()]
    invitees: List[int] = []
    if names:
        if len(names) != seats:
            raise DuelError(f"A private {fmt} needs {seats} invited player{'s' if seats > 1 else ''} - "
                            f"or open it to anyone")
        with community.reader.connect() as conn:
            found = {r["username_lower"]: r["id"] for r in conn.execute(
                select(users.c.id, users.c.username_lower)
                .where(users.c.username_lower.in_([n.lower() for n in names]))).mappings()}
        for n in names:
            uid = found.get(n.lower())
            if uid is None:
                raise DuelError(f"No user named {n}", 404)
            if uid == creator_id:
                raise DuelError("You can't invite yourself")
            if uid in invitees:
                raise DuelError(f"{n} is invited twice")
            invitees.append(uid)
    rounds, taken = [], set()
    hands: List[dict] = []
    if mode == "draft":
        picked = []  # hands are dealt, never chosen
        hands = _deal(teams * size, excluded)
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
    n_picked = len(rounds)
    if mode == "draft":
        # Placeholder rounds: the clock and picks hang off them, the hands
        # live in game_hands.
        rounds = [{"char_a": 0, "char_b": 0, "form_a": "", "form_b": "", "answer_id": 0, "verdict": "", "picked": False}
                  for _ in range(ROUNDS)]
    else:
        rounds += _random_rounds(ROUNDS - len(rounds), taken, excluded)
        random.shuffle(rounds)
    now = _now()
    with engine.begin() as conn:
        game_id = conn.execute(insert(games).values(
            creator_id=creator_id, status="open", created_at=now, teams=teams, team_size=size,
            picked=n_picked, excluded="|".join(sorted(excluded)) or None, mode=mode,
        )).inserted_primary_key[0]
        conn.execute(insert(game_players).values(game_id=game_id, user_id=creator_id, team=1, joined_at=now,
                                                 seat=0 if mode == "draft" else None))
        if hands:
            conn.execute(insert(game_hands), [{"game_id": game_id, **h} for h in hands])
        if invitees:
            conn.execute(insert(game_invites), [{"game_id": game_id, "user_id": u} for u in invitees])
        conn.execute(insert(game_rounds), [{"game_id": game_id, "round_no": i, **r}
                                           for i, r in enumerate(rounds, start=1)])
    _changed()
    if hands:
        by_round: Dict[int, List[int]] = {}
        for h in sorted(hands, key=lambda h: (h["round_no"], h["seat"], h["slot"])):
            by_round.setdefault(h["round_no"], []).append(h["char_id"])
        prewarm.round_pictures([tuple(ids) for _, ids in sorted(by_round.items())])
    else:
        prewarm.round_pictures([(r["char_a"], r["char_b"]) for r in rounds])
    return game_id


def _game(conn, game_id: int, lock: bool = False) -> dict:
    q = select(games).where(games.c.id == game_id)
    if lock:
        q = q.with_for_update()  # Postgres: joins to one game queue up (SQLite ignores it)
    row = conn.execute(q).mappings().first()
    if row is None:
        raise DuelError("No such game", 404)
    return dict(row)


def _members(conn, game_ids: List[int], people: Optional[Dict[int, dict]] = None) -> Dict[int, List[dict]]:
    """Each game's players, in joining order. With `people`, also records
    each one's name and picture there - same query, no extra round trip."""
    out: Dict[int, List[dict]] = {}
    if game_ids:
        q = (select(game_players, users.c.username, avatars.c.updated_at.label("avatar_at"))
             .join(users, users.c.id == game_players.c.user_id)
             .outerjoin(avatars, avatars.c.user_id == game_players.c.user_id)
             .where(game_players.c.game_id.in_(game_ids)).order_by(game_players.c.joined_at))
        for p in conn.execute(q).mappings():
            p = dict(p)
            if people is not None:
                people[p["user_id"]] = {"username": p["username"],
                                        "avatar_at": _aware(p["avatar_at"]) if p["avatar_at"] else None}
            out.setdefault(p["game_id"], []).append(p)
    return out


def _invites(conn, game_ids: List[int], people: Optional[Dict[int, dict]] = None) -> Dict[int, List[int]]:
    out: Dict[int, List[int]] = {}
    if game_ids:
        q = (select(game_invites, users.c.username).join(users, users.c.id == game_invites.c.user_id)
             .where(game_invites.c.game_id.in_(game_ids)))
        for r in conn.execute(q).mappings():
            if people is not None:
                people.setdefault(r["user_id"], {"username": r["username"], "avatar_at": None})
            out.setdefault(r["game_id"], []).append(r["user_id"])
    return out


def open_teams(g: dict, members: List[dict]) -> List[int]:
    teams, size = shape(g)
    return [t for t in range(1, teams + 1) if sum(1 for m in members if m["team"] == t) < size]


def join(game_id: int, user_id: int, team: Optional[int] = None) -> None:
    with engine.begin() as conn:
        g = _game(conn, game_id, lock=True)
        if g["status"] != "open":
            raise DuelError("This game is full or over", 409)
        members = _members(conn, [game_id]).get(game_id, [])
        if any(m["user_id"] == user_id for m in members):
            raise DuelError("You're already in this game", 409)
        invited = _invites(conn, [game_id]).get(game_id, [])
        if invited and user_id not in invited:
            raise DuelError("This game is private - only invited players can join", 403)
        free = open_teams(g, members)
        if not free:
            raise DuelError("This game is full", 409)
        if team is None:
            team = min(free, key=lambda t: (sum(1 for m in members if m["team"] == t), t))
        elif team not in free:
            raise DuelError("That team is full - pick another", 409)
        now = _now()
        seat = None
        if g.get("mode") == "draft":  # the lowest free seat: a leaver's seat is reused
            taken = {m["seat"] for m in members}
            seat = next(s for s in range(len(members) + 1) if s not in taken)
        conn.execute(insert(game_players).values(game_id=game_id, user_id=user_id, team=team, joined_at=now, seat=seat))
        teams, size = shape(g)
        if len(members) + 1 == teams * size:
            conn.execute(update(games).where(games.c.id == game_id).values(status="active", accepted_at=now))
    _changed()


def leave(game_id: int, user_id: int) -> None:
    """Out of a game that hasn't filled yet - only before playing any round."""
    with engine.begin() as conn:
        g = _game(conn, game_id, lock=True)
        if g["status"] != "open" or g["creator_id"] == user_id:
            raise DuelError("You can't leave this game", 403)
        started = conn.execute(select(func.count()).select_from(game_picks).where(and_(
            game_picks.c.game_id == game_id, game_picks.c.user_id == user_id))).scalar()
        if started:
            raise DuelError("You've already played in this game", 409)
        gone = conn.execute(delete(game_players).where(and_(
            game_players.c.game_id == game_id, game_players.c.user_id == user_id))).rowcount
        if not gone:
            raise DuelError("You're not in this game", 404)
    _changed()


def decline(game_id: int, user_id: int) -> None:
    """An invited player says no - a private game can't fill without them."""
    with engine.begin() as conn:
        g = _game(conn, game_id)
        invited = _invites(conn, [game_id]).get(game_id, [])
        members = _members(conn, [game_id]).get(game_id, [])
        if g["status"] != "open" or user_id not in invited or any(m["user_id"] == user_id for m in members):
            raise DuelError("You can't decline this game", 403)
        conn.execute(update(games).where(games.c.id == game_id).values(status="declined", finished_at=_now()))
    _changed()


def cancel(game_id: int, user_id: int) -> None:
    with engine.begin() as conn:
        g = _game(conn, game_id)
        if g["creator_id"] != user_id or g["status"] != "open":
            raise DuelError("Only a game you made that hasn't filled can be cancelled", 403)
        conn.execute(update(games).where(games.c.id == game_id).values(status="cancelled", finished_at=_now()))
    _changed()


# --- playing ------------------------------------------------------------------------------

def _deadline(started_at: datetime) -> datetime:
    return _aware(started_at) + timedelta(seconds=ROUND_SECONDS)


def _settled(pick: dict, now: datetime) -> bool:
    """Answered, or its clock has run out."""
    return pick["answered_at"] is not None or now > _deadline(pick["started_at"]) + timedelta(seconds=GRACE_SECONDS)


def _in_time(pick: Optional[dict]) -> bool:
    return bool(pick and pick["answered_at"] is not None
                and _aware(pick["answered_at"]) <= _deadline(pick["started_at"]) + timedelta(seconds=GRACE_SECONDS))


def _correct(pick: Optional[dict], answer_id: int) -> bool:
    return _in_time(pick) and pick["pick_id"] == answer_id


def can_play(g: dict, user_id: Optional[int], members: List[dict]) -> bool:
    """Members play from the moment they join, full or not."""
    return g["status"] in ("open", "active") and any(m["user_id"] == user_id for m in members)


def _state(conn, game_id: int, user_id: int) -> List[dict]:
    """In one query - each round trip to Neon costs ~0.2s, and this runs
    between every two rounds: the game's five rounds, with this player's
    pick on each (if started), the game's status and whether they're in it."""
    member = select(game_players.c.user_id).where(and_(
        game_players.c.game_id == game_id, game_players.c.user_id == user_id)).exists()
    seat = select(game_players.c.seat).where(and_(
        game_players.c.game_id == game_id, game_players.c.user_id == user_id)).scalar_subquery()
    rows = conn.execute(
        select(games.c.status, games.c.mode, game_rounds, game_picks.c.started_at, game_picks.c.pick_id,
               game_picks.c.answered_at, member.label("member"), seat.label("seat"))
        .select_from(games.join(game_rounds, game_rounds.c.game_id == games.c.id).outerjoin(
            game_picks, and_(game_picks.c.game_id == game_rounds.c.game_id,
                             game_picks.c.round_no == game_rounds.c.round_no, game_picks.c.user_id == user_id)))
        .where(games.c.id == game_id).order_by(game_rounds.c.round_no)).mappings().all()
    if not rows:
        raise DuelError("No such game", 404)
    return [dict(r) for r in rows]


def _next(conn, state: List[dict], user_id: int, now: datetime) -> Optional[dict]:
    """The round this player is on, starting its clock if it wasn't shown
    yet; None when they've played them all."""
    for r in state:
        if r["started_at"] is not None and _settled(r, now):
            continue
        if r["started_at"] is None:
            try:
                conn.execute(insert(game_picks).values(game_id=r["game_id"], round_no=r["round_no"],
                                                       user_id=user_id, started_at=now))
            except IntegrityError:
                # Started a moment ago by this player's other request (a
                # double tap, two tabs): the clock that counts is that one.
                started = conn.execute(select(game_picks.c.started_at).where(and_(
                    game_picks.c.game_id == r["game_id"], game_picks.c.round_no == r["round_no"],
                    game_picks.c.user_id == user_id))).scalar()
                r["started_at"] = started
            else:
                r["started_at"] = now
                _changed()
        left = (_deadline(r["started_at"]) - now).total_seconds()
        return {**r, "seconds_left": max(0.0, left)}
    return None


def _hand(conn, game_id: int, round_no: int, seat: Optional[int]) -> List[int]:
    """A draft player's hand for one round, in dealt order."""
    if seat is None:
        return []
    return [r[0] for r in conn.execute(select(game_hands.c.char_id).where(and_(
        game_hands.c.game_id == game_id, game_hands.c.round_no == round_no, game_hands.c.seat == seat))
        .order_by(game_hands.c.slot))]


def _with_hand(conn, r: Optional[dict]) -> Optional[dict]:
    if r is not None and r.get("mode") == "draft":
        r["hand"] = _hand(conn, r["game_id"], r["round_no"], r["seat"])
    return r


def _finish_if_done(conn, game_id: int, now: datetime) -> None:
    """After a player's last round: maybe they were the last one."""
    _settle(conn, [_game(conn, game_id)], now)


# Playing runs in autocommit too (community.reader), without a transaction:
# BEGIN and COMMIT were two of the four round trips behind the Play
# button. Each write guards itself instead - a round starts with a single
# insert (the primary key stops a second), and an answer is only written
# over no answer.

def next_round(game_id: int, user_id: int) -> Optional[dict]:
    now = _now()
    with community.reader.connect() as conn:
        state = _state(conn, game_id, user_id)
        if not state[0]["member"]:
            raise DuelError("Join the game first", 403)
        if state[0]["status"] not in ("open", "active"):
            return None
        r = _next(conn, state, user_id, now)
        if r is None:
            _finish_if_done(conn, game_id, now)
        return _with_hand(conn, r)


def pick(game_id: int, user_id: int, round_no: int, pick_id: int) -> Tuple[bool, Optional[dict]]:
    """Records a pick and hands out the next round in the same request.
    Returns (in time, next round or None). A late pick counts as wrong.
    Three statements in all: read, update, start the next round."""
    now = _now()
    with community.reader.connect() as conn:
        state = _state(conn, game_id, user_id)
        r = next((x for x in state if x["round_no"] == round_no), None)
        if r is None or r["started_at"] is None:
            raise DuelError("That round hasn't started", 409)
        if r["mode"] == "draft":
            if pick_id not in _hand(conn, game_id, round_no, r["seat"]):
                raise DuelError("Pick one of the characters in your hand")
        elif pick_id not in (r["char_a"], r["char_b"]):
            raise DuelError("Pick one of the two characters")
        if r["answered_at"] is not None:
            raise DuelError("You already answered this round", 409)
        answered = conn.execute(update(game_picks).where(and_(
            game_picks.c.game_id == game_id, game_picks.c.round_no == round_no, game_picks.c.user_id == user_id,
            game_picks.c.answered_at.is_(None),
        )).values(pick_id=pick_id, answered_at=now)).rowcount
        if not answered:
            raise DuelError("You already answered this round", 409)
        _changed()
        r.update(pick_id=pick_id, answered_at=now)
        in_time = now <= _deadline(r["started_at"]) + timedelta(seconds=GRACE_SECONDS)
        nxt = _next(conn, state, user_id, now) if state[0]["status"] in ("open", "active") else None
        if nxt is None:
            _finish_if_done(conn, game_id, now)
        nxt = _with_hand(conn, nxt)
    return in_time, nxt


# --- settling -------------------------------------------------------------------------------

def _settle(conn, gs: List[dict], now: datetime, picks: Optional[List[dict]] = None,
            members: Optional[Dict[int, List[dict]]] = None) -> None:
    """Moves games on as time passes: expires games that never filled, and
    finishes full games once every player is done - or once someone is
    done and the rest have let FORFEIT_HOURS go by (their unplayed rounds
    count as wrong). Updates the dicts in `gs` and `members` in place."""
    for g in gs:
        if g["status"] == "open" and now - _aware(g["created_at"]) > timedelta(days=OPEN_DAYS):
            g.update(status="expired", finished_at=now)
            conn.execute(update(games).where(games.c.id == g["id"]).values(status="expired", finished_at=now))
            _changed()
    active = [g for g in gs if g["status"] == "active"]
    if not active:
        return
    ids = [g["id"] for g in active]
    if members is None:
        members = _members(conn, ids)
    if picks is None:
        picks = [dict(p) for p in conn.execute(select(game_picks).where(game_picks.c.game_id.in_(ids))).mappings()]
    by_player: Dict[Tuple[int, int], List[dict]] = {}
    for p in picks:
        by_player.setdefault((p["game_id"], p["user_id"]), []).append(p)
    ready = []
    for g in active:
        done_at = []
        for m in members.get(g["id"], []):
            mine = [p for p in by_player.get((g["id"], m["user_id"]), []) if _settled(p, now)]
            if len(mine) == ROUNDS:
                done_at.append(max(_aware(p["answered_at"]) if p["answered_at"] else _deadline(p["started_at"])
                                   for p in mine))
        if len(done_at) < len(members.get(g["id"], [])):
            if not done_at or now - max(done_at + [_aware(g["accepted_at"])]) < timedelta(hours=FORFEIT_HOURS):
                continue
        ready.append(g)
    if not ready:
        return
    answers = {(r["game_id"], r["round_no"]): r["answer_id"] for r in conn.execute(
        select(game_rounds.c.game_id, game_rounds.c.round_no, game_rounds.c.answer_id)
        .where(game_rounds.c.game_id.in_([g["id"] for g in ready]))).mappings()}
    for g in ready:
        team_score: Dict[int, int] = {}
        if g.get("mode") == "draft":
            points = draft_points(members[g["id"]], picks_in_time(g["id"], picks, members[g["id"]]))
            for m in members[g["id"]]:
                m["score"] = sum(points.get((n, m["user_id"]), 0) for n in range(1, ROUNDS + 1))
                team_score[m["team"]] = team_score.get(m["team"], 0) + m["score"]
        for m in members[g["id"]] if g.get("mode") != "draft" else []:
            m["score"] = sum(_correct(next((p for p in by_player.get((g["id"], m["user_id"]), []) if p["round_no"] == n), None),
                                      answers[(g["id"], n)]) for n in range(1, ROUNDS + 1))
            team_score[m["team"]] = team_score.get(m["team"], 0) + m["score"]
        top = max(team_score.values())
        leaders = [t for t, s in team_score.items() if s == top]
        winning = leaders[0] if len(leaders) == 1 else None
        # Players first, the game's status last: reads may run outside a
        # transaction, and a settle cut off halfway is then just redone.
        for m in members[g["id"]]:
            m["outcome"] = ("win" if len(leaders) == 1 else "draw") if m["team"] in leaders else "loss"
            conn.execute(update(game_players).where(and_(game_players.c.game_id == g["id"],
                                                         game_players.c.user_id == m["user_id"]))
                         .values(outcome=m["outcome"], score=m["score"]))
        g.update(status="done", finished_at=now, winning_team=winning)
        conn.execute(update(games).where(games.c.id == g["id"]).values(status="done", finished_at=now,
                                                                        winning_team=winning))
        _changed()


# --- reading ---------------------------------------------------------------------------------
# Reads run in autocommit (community.reader): no BEGIN/ROLLBACK round trips.
# A settle they trigger writes each row on its own, players before the
# game's status (see _settle).

def _load(conn, gs: List[dict], now: datetime, rounds_for_done: bool) -> dict:
    """The rest of what `gs` needs: players (with names and pictures),
    invites, picks, and - with rounds_for_done - the rounds of finished
    games (answers only leave the server once a game is over). Lists skip
    those: a finished game's scores are stored with its players."""
    people: Dict[int, dict] = {}
    ids = [g["id"] for g in gs]
    members = _members(conn, ids, people)
    live = [g["id"] for g in gs if g["status"] in ("open", "active")]
    done = [g["id"] for g in gs if g["status"] == "done"]
    # Finished before scores were stored: they still need their rounds.
    unscored = [gid for gid in done if any(m["score"] is None for m in members.get(gid, []))]
    need_picks = ids if rounds_for_done else live + unscored
    picks = [dict(p) for p in conn.execute(select(game_picks).where(
        game_picks.c.game_id.in_(need_picks))).mappings()] if need_picks else []
    _settle(conn, gs, now, [p for p in picks if p["game_id"] in live], members)
    done = [g["id"] for g in gs if g["status"] == "done"]  # settling may have finished some
    want_rounds = done if rounds_for_done else [gid for gid in done if any(m["score"] is None for m in members.get(gid, []))]
    rounds: Dict[int, List[dict]] = {}
    if want_rounds:
        for r in conn.execute(select(game_rounds).where(game_rounds.c.game_id.in_(want_rounds))
                              .order_by(game_rounds.c.round_no)).mappings():
            rounds.setdefault(r["game_id"], []).append(dict(r))
    invites = _invites(conn, [g["id"] for g in gs if g["status"] == "open"], people)
    hands: Dict[int, Dict[Tuple[int, int], List[int]]] = {}
    draft_done = [gid for gid in want_rounds if next(g for g in gs if g["id"] == gid).get("mode") == "draft"]
    if draft_done:
        for h in conn.execute(select(game_hands).where(game_hands.c.game_id.in_(draft_done))
                              .order_by(game_hands.c.slot)).mappings():
            hands.setdefault(h["game_id"], {}).setdefault((h["round_no"], h["seat"]), []).append(h["char_id"])
    return {"games": gs, "members": members, "invites": invites, "picks": picks, "rounds": rounds,
            "hands": hands, "people": people, "now": now}


def overview(user_id: int) -> dict:
    """The player's own games (joined or invited to), newest first, then
    other people's open games with a free seat. Four queries in all."""
    now = _now()
    joined = select(game_players.c.game_id).where(game_players.c.user_id == user_id)
    invited = select(game_invites.c.game_id).where(game_invites.c.user_id == user_id)
    private = select(game_invites.c.game_id).where(game_invites.c.game_id == games.c.id).exists()
    mine_q = or_(games.c.id.in_(joined), games.c.id.in_(invited))
    open_q = and_(games.c.status == "open", ~private, games.c.creator_id != user_id)
    with community.reader.connect() as conn:
        gs = [dict(g) for g in conn.execute(select(games, mine_q.label("is_mine")).where(or_(mine_q, open_q))
                                            .order_by(games.c.id.desc()).limit(80)).mappings()]
        mine = [g for g in gs if g["is_mine"]][:60]
        others = [g for g in gs if not g["is_mine"]][:20]
        data = _load(conn, mine + others, now, rounds_for_done=False)
    data["mine"] = mine
    data["open"] = [g for g in others if g["status"] == "open"]
    return data


def recent_finished(user_id: int, limit: int = 8) -> dict:
    """A player's latest finished games, for their public profile."""
    now = _now()
    joined = select(game_players.c.game_id).where(game_players.c.user_id == user_id)
    with community.reader.connect() as conn:
        gs = [dict(g) for g in conn.execute(select(games).where(and_(games.c.id.in_(joined), games.c.status == "done"))
                                            .order_by(games.c.id.desc()).limit(limit)).mappings()]
        return _load(conn, gs, now, rounds_for_done=False)


def game(game_id: int) -> dict:
    now = _now()
    with community.reader.connect() as conn:
        gs = [dict(g) for g in conn.execute(select(games).where(games.c.id == game_id)).mappings()]
        if not gs:
            raise DuelError("No such game", 404)
        return _load(conn, gs, now, rounds_for_done=True)


def players(ids) -> Dict[int, dict]:
    """{user id: {"username", "avatar_at"}} in one query."""
    ids = [i for i in set(ids) if i is not None]
    if not ids:
        return {}
    with community.reader.connect() as conn:
        rows = conn.execute(select(users.c.id, users.c.username, avatars.c.updated_at.label("avatar_at"))
                            .outerjoin(avatars, avatars.c.user_id == users.c.id)
                            .where(users.c.id.in_(ids))).mappings()
        return {r["id"]: {"username": r["username"], "avatar_at": _aware(r["avatar_at"]) if r["avatar_at"] else None}
                for r in rows}


def picks_in_time(game_id: int, picks: List[dict], members: List[dict]) -> Dict[Tuple[int, int], Optional[int]]:
    """Draft: {(round, user id): the character picked in time, or None}."""
    member_ids = {m["user_id"] for m in members}
    out: Dict[Tuple[int, int], Optional[int]] = {}
    for p in picks:
        if p["game_id"] == game_id and p["user_id"] in member_ids:
            out[(p["round_no"], p["user_id"])] = p["pick_id"] if _in_time(p) else None
    return out


def played(picks: List[dict], game_id: int, user_id: Optional[int], now: datetime) -> int:
    return sum(1 for p in picks if p["game_id"] == game_id and p["user_id"] == user_id and _settled(p, now))


def pick_of(picks: List[dict], game_id: int, user_id: int, round_no: int, answer_id: int) -> Tuple[Optional[int], bool]:
    p = next((p for p in picks if p["game_id"] == game_id and p["user_id"] == user_id
              and p["round_no"] == round_no), None)
    return (p["pick_id"] if p else None), _correct(p, answer_id)


def pending_count(user_id: int) -> int:
    """Invites waiting on this player, plus games with rounds they haven't
    played - for the dot on the Duels link, on every page. One query."""
    live = games.c.status.in_(["open", "active"])
    member = select(game_players.c.game_id).where(and_(
        game_players.c.game_id == games.c.id, game_players.c.user_id == user_id)).exists()
    cutoff = _now() - timedelta(seconds=ROUND_SECONDS + GRACE_SECONDS)
    my_done = (select(func.count()).select_from(game_picks).where(and_(
        game_picks.c.game_id == games.c.id, game_picks.c.user_id == user_id,
        or_(game_picks.c.answered_at.isnot(None), game_picks.c.started_at < cutoff))).scalar_subquery())
    invites = (select(func.count()).select_from(game_invites.join(games, games.c.id == game_invites.c.game_id))
               .where(and_(game_invites.c.user_id == user_id, games.c.status == "open", ~member)).scalar_subquery())
    to_play = (select(func.count()).select_from(games).where(and_(live, member, my_done < ROUNDS)).scalar_subquery())
    with community.reader.connect() as conn:
        a, b = conn.execute(select(invites, to_play)).one()
    return a + b


# --- records ---------------------------------------------------------------------------------

def records(user_ids: Optional[List[int]] = None) -> Dict[int, dict]:
    """{user id: {"wins", "draws", "losses"}} over finished games."""
    q = select(game_players.c.user_id, game_players.c.outcome, func.count()) \
        .where(game_players.c.outcome.isnot(None)).group_by(game_players.c.user_id, game_players.c.outcome)
    if user_ids is not None:
        q = q.where(game_players.c.user_id.in_(user_ids or [0]))
    key = {"win": "wins", "draw": "draws", "loss": "losses"}
    out: Dict[int, dict] = {}
    with community.reader.connect() as conn:
        for uid, outcome, n in conn.execute(q):
            out.setdefault(uid, {"wins": 0, "draws": 0, "losses": 0})[key[outcome]] += n
    return out


def leaderboard(limit: int = 25) -> List[dict]:
    """Top players by wins (then fewest losses, most draws), with their
    names and pictures - one query."""
    wins = func.sum(case((game_players.c.outcome == "win", 1), else_=0))
    draws = func.sum(case((game_players.c.outcome == "draw", 1), else_=0))
    losses = func.sum(case((game_players.c.outcome == "loss", 1), else_=0))
    q = (select(users.c.username, avatars.c.updated_at.label("avatar_at"),
                wins.label("wins"), draws.label("draws"), losses.label("losses"))
         .select_from(game_players.join(users, users.c.id == game_players.c.user_id)
                      .outerjoin(avatars, avatars.c.user_id == game_players.c.user_id))
         .where(game_players.c.outcome.isnot(None))
         .group_by(users.c.id, users.c.username, avatars.c.updated_at)
         .order_by(wins.desc(), losses.asc(), draws.desc(), users.c.username).limit(limit))
    with community.reader.connect() as conn:
        return [{**r, "avatar_at": _aware(r["avatar_at"]) if r["avatar_at"] else None,
                 "wins": int(r["wins"]), "draws": int(r["draws"]), "losses": int(r["losses"])}
                for r in conn.execute(q).mappings()]


def delete_user_games(user_id: int) -> None:
    """Every game the user made, joined or was invited to - called when
    they delete their account. (The other players lose those results too:
    a game can't be scored with a player missing.)"""
    with engine.begin() as conn:
        ids = {r[0] for r in conn.execute(select(game_players.c.game_id).where(game_players.c.user_id == user_id))}
        ids |= {r[0] for r in conn.execute(select(game_invites.c.game_id).where(game_invites.c.user_id == user_id))}
        ids |= {r[0] for r in conn.execute(select(games.c.id).where(or_(
            games.c.creator_id == user_id, games.c.opponent_id == user_id, games.c.invited_id == user_id)))}
        if ids:
            for table in (game_picks, game_hands, game_rounds, game_players, game_invites):
                conn.execute(delete(table).where(table.c.game_id.in_(ids)))
            conn.execute(delete(games).where(games.c.id.in_(ids)))
    _changed()
