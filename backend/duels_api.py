"""HTTP endpoints for prediction duels (rules and storage: duels.py)."""

import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response

import calculator
import db
from backend import characters, community, duels
from backend.community_api import (
    avatar_url, character_image_url, current_user, post_limit, require_user, same_origin,
)
from backend.schemas import (
    DuelCreateIn, DuelListOut, DuelNextOut, DuelOut, DuelPickIn, DuelPlayerOut, DuelResultRoundOut,
    DuelRoundOut, DuelSideOut, LeaderboardOut, LeaderboardRowOut,
)

router = APIRouter()


def _run(fn, *args):
    try:
        return fn(*args)
    except duels.DuelError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


def _player(uid: Optional[int], players: Dict[int, dict]) -> Optional[DuelPlayerOut]:
    p = players.get(uid)
    if p is None:
        return None
    return DuelPlayerOut(username=p["username"], is_admin=community.is_admin(p["username"]),
                         avatar_url=avatar_url(p["username"], p["avatar_at"]))


def _side(char_id: int, form: str, cache: dict) -> DuelSideOut:
    """One character as a round shows it: picture, name, series - never
    its stats, which would give the answer away."""
    key = (char_id, form)
    if key not in cache:
        row = db.get_character_by_id(char_id) or {}
        normalized = json.loads(row.get("normalized_json") or "{}")
        default = calculator.select_form(normalized).get("name") if normalized.get("forms") else None
        replaced = character_image_url(char_id, community.character_image_versions().get(char_id))
        series = row.get("category") or ""
        if row.get("subseries"):
            series += f" · {row['subseries']}"
        cache[key] = DuelSideOut(
            id=char_id, name=characters.display_name_for_id(char_id) or "Removed character", series=series,
            form=form if form != default else None, image_url=replaced or row.get("image_url"),
        )
    return cache[key]


def _duel_out(g: dict, me: Optional[int], picks: List[dict], details: dict, players: Dict[int, dict],
              now, cache: dict) -> DuelOut:
    is_creator = me == g["creator_id"]
    other = g["opponent_id"] if is_creator else g["creator_id"]
    opponent_id = g["opponent_id"] or g["invited_id"]
    out = DuelOut(
        id=g["id"], status=g["status"], created_at=g["created_at"],
        creator=_player(g["creator_id"], players), opponent=_player(opponent_id, players),
        open_to_anyone=g["invited_id"] is None, me_is_creator=is_creator, total=duels.ROUNDS,
        my_played=duels.played(picks, g["id"], me, now), their_played=duels.played(picks, g["id"], other, now),
        can_play=me is not None and duels._can_play(g, me),
        can_accept=me is not None and g["status"] == "open" and not is_creator
        and g["invited_id"] in (None, me),
        can_decline=g["status"] == "open" and g["invited_id"] == me and me is not None,
        can_cancel=g["status"] == "open" and is_creator,
    )
    if g["status"] == "done":
        mine_score = theirs_score = 0
        for r in details.get(g["id"], []):
            my_pick, my_ok = duels.correct(picks, g["id"], me, r["round_no"], r["answer_id"])
            their_pick, their_ok = duels.correct(picks, g["id"], other, r["round_no"], r["answer_id"])
            mine_score += my_ok
            theirs_score += their_ok
            out.rounds.append(DuelResultRoundOut(
                round_no=r["round_no"], a=_side(r["char_a"], r["form_a"], cache), b=_side(r["char_b"], r["form_b"], cache),
                answer_id=r["answer_id"], verdict=r["verdict"], picked=r["picked"],
                my_pick=my_pick, their_pick=their_pick, my_correct=my_ok, their_correct=their_ok,
                compare_url=f"compare.html?a={r['char_a']}&b={r['char_b']}",
            ))
        if me in (g["creator_id"], g["opponent_id"]):
            out.my_score, out.their_score = mine_score, theirs_score
            out.outcome = "draw" if g["winner_id"] is None else "win" if g["winner_id"] == me else "loss"
        else:  # someone else's finished game: creator's view
            out.my_score, out.their_score = mine_score, theirs_score
    return out


@router.get("/api/games", response_model=DuelListOut)
def list_games(response: Response, user: dict = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    o = duels.overview(user["id"])
    gs = o["mine"] + o["open"]
    players = duels.players([uid for g in gs for uid in (g["creator_id"], g["opponent_id"], g["invited_id"])])
    cache: dict = {}
    return DuelListOut(
        mine=[_duel_out(g, user["id"], o["picks"], o["details"], players, o["now"], cache) for g in o["mine"]],
        open=[_duel_out(g, user["id"], o["picks"], o["details"], players, o["now"], cache) for g in o["open"]],
    )


@router.get("/api/games/pending")
def pending(response: Response, user: Optional[dict] = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return {"count": duels.pending_count(user["id"]) if user else 0}


@router.get("/api/games/{game_id}", response_model=DuelOut)
def get_game(game_id: int, response: Response, user: Optional[dict] = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    o = _run(duels.game, game_id, user["id"] if user else None)
    g = o["game"]
    players = duels.players([g["creator_id"], g["opponent_id"], g["invited_id"]])
    return _duel_out(g, user["id"] if user else None, o["picks"], o["details"], players, o["now"], {})


@router.post("/api/games", response_model=DuelOut, dependencies=[Depends(same_origin)])
def create_game(payload: DuelCreateIn, user: dict = Depends(require_user)):
    if not post_limit.allow(f"user:{user['id']}"):
        raise HTTPException(status_code=429, detail="You're making challenges too fast - wait a minute")
    for m in payload.matchups:
        for cid in (m.char_a, m.char_b):
            if db.get_character_by_id(cid) is None:
                raise HTTPException(status_code=404, detail=f"No character with id {cid}")
    game_id = _run(duels.create, user["id"], payload.opponent, [m.model_dump() for m in payload.matchups])
    return get_game(game_id, Response(), user)


@router.post("/api/games/{game_id}/accept", response_model=DuelOut, dependencies=[Depends(same_origin)])
def accept_game(game_id: int, user: dict = Depends(require_user)):
    _run(duels.accept, game_id, user["id"])
    return get_game(game_id, Response(), user)


@router.post("/api/games/{game_id}/decline", dependencies=[Depends(same_origin)])
def decline_game(game_id: int, user: dict = Depends(require_user)):
    _run(duels.decline, game_id, user["id"])
    return {"ok": True}


@router.post("/api/games/{game_id}/cancel", dependencies=[Depends(same_origin)])
def cancel_game(game_id: int, user: dict = Depends(require_user)):
    _run(duels.cancel, game_id, user["id"])
    return {"ok": True}


@router.post("/api/games/{game_id}/next", response_model=DuelNextOut, dependencies=[Depends(same_origin)])
def next_round(game_id: int, user: dict = Depends(require_user)):
    """Shows this player's current round, starting its clock."""
    r = _run(duels.next_round, game_id, user["id"])
    round_out = None
    if r is not None:
        cache: dict = {}
        round_out = DuelRoundOut(
            game_id=game_id, round_no=r["round_no"], total=duels.ROUNDS, seconds_left=r["seconds_left"],
            a=_side(r["char_a"], r["form_a"], cache), b=_side(r["char_b"], r["form_b"], cache),
        )
    return DuelNextOut(round=round_out, game=get_game(game_id, Response(), user))


@router.post("/api/games/{game_id}/pick", dependencies=[Depends(same_origin)])
def pick(game_id: int, payload: DuelPickIn, user: dict = Depends(require_user)):
    in_time = _run(duels.pick, game_id, user["id"], payload.round_no, payload.pick_id)
    return {"in_time": in_time}


@router.get("/api/leaderboard", response_model=LeaderboardOut)
def leaderboard(response: Response):
    response.headers["Cache-Control"] = "no-store"
    recs = duels.records()
    top = sorted(recs.items(), key=lambda kv: (-kv[1]["wins"], kv[1]["losses"], -kv[1]["draws"]))[:25]
    players = duels.players([uid for uid, _ in top])
    return LeaderboardOut(rows=[
        LeaderboardRowOut(username=players[uid]["username"], is_admin=community.is_admin(players[uid]["username"]),
                          avatar_url=avatar_url(players[uid]["username"], players[uid]["avatar_at"]), **rec)
        for uid, rec in top if uid in players
    ])
