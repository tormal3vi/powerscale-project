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
    DuelCreateIn, DuelJoinIn, DuelListOut, DuelOut, DuelPickIn, DuelPickOut, DuelPlayerOut,
    DuelResultRoundOut, DuelRoundOut, DuelRoundPickOut, DuelSideOut, LeaderboardOut, LeaderboardRowOut,
)

router = APIRouter()


def _run(fn, *args):
    try:
        return fn(*args)
    except duels.DuelError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


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


def _round_out(game_id: int, r: dict) -> DuelRoundOut:
    cache: dict = {}
    return DuelRoundOut(game_id=game_id, round_no=r["round_no"], total=duels.ROUNDS, seconds_left=r["seconds_left"],
                        a=_side(r["char_a"], r["form_a"], cache), b=_side(r["char_b"], r["form_b"], cache))


def _duel_out(g: dict, me: Optional[int], data: dict, people: Dict[int, dict], cache: dict) -> DuelOut:
    members = data["members"].get(g["id"], [])
    invited = data["invites"].get(g["id"], [])
    picks, now = data["picks"], data["now"]
    teams, size = duels.shape(g)
    done = g["status"] == "done"
    rounds = data["rounds"].get(g["id"], []) if done else []
    score = {m["user_id"]: sum(duels.pick_of(picks, g["id"], m["user_id"], r["round_no"], r["answer_id"])[1]
                               for r in rounds) for m in members}
    member_ids = {m["user_id"] for m in members}
    mine = next((m for m in members if m["user_id"] == me), None)
    free = duels.open_teams(g, members)

    def person(uid):
        p = people.get(uid, {"username": "deleted", "avatar_at": None})
        return p["username"], avatar_url(p["username"], p["avatar_at"])

    out_players = []
    for m in sorted(members, key=lambda m: (m["team"], m["joined_at"])):
        name, avatar = person(m["user_id"])
        out_players.append(DuelPlayerOut(
            username=name, is_admin=community.is_admin(name), avatar_url=avatar, team=m["team"],
            played=duels.played(picks, g["id"], m["user_id"], now), me=m["user_id"] == me,
            score=score[m["user_id"]] if done else None, outcome=m["outcome"] if done else None,
        ))
    can_join = (me is not None and g["status"] == "open" and me not in member_ids and bool(free)
                and (not invited or me in invited))
    out = DuelOut(
        id=g["id"], status=g["status"], created_at=g["created_at"], format=duels.format_name(g),
        teams=teams, team_size=size, creator=person(g["creator_id"])[0], players=out_players,
        invited=[person(u)[0] for u in invited if u not in member_ids], private=bool(invited),
        seats_left=teams * size - len(members) if g["status"] == "open" else 0,
        picked=g.get("picked"), excluded=[s for s in (g.get("excluded") or "").split("|") if s],
        my_team=mine["team"] if mine else None,
        my_played=duels.played(picks, g["id"], me, now) if mine else 0, total=duels.ROUNDS,
        can_play=me is not None and duels.can_play(g, me, members),
        can_join=can_join, join_teams=free if can_join else [],
        can_leave=bool(mine) and g["status"] == "open" and me != g["creator_id"]
        and not any(p["game_id"] == g["id"] and p["user_id"] == me for p in picks),
        can_decline=me is not None and g["status"] == "open" and me in invited and me not in member_ids,
        can_cancel=g["status"] == "open" and me == g["creator_id"],
    )
    if done:
        out.outcome = mine["outcome"] if mine else None
        out.team_scores = [sum(score[m["user_id"]] for m in members if m["team"] == t) for t in range(1, teams + 1)]
        for r in rounds:
            round_picks = []
            for m in sorted(members, key=lambda m: (m["team"], m["joined_at"])):
                pick_id, ok = duels.pick_of(picks, g["id"], m["user_id"], r["round_no"], r["answer_id"])
                round_picks.append(DuelRoundPickOut(username=person(m["user_id"])[0], team=m["team"],
                                                    pick_id=pick_id, correct=ok))
            out.rounds.append(DuelResultRoundOut(
                round_no=r["round_no"], a=_side(r["char_a"], r["form_a"], cache),
                b=_side(r["char_b"], r["form_b"], cache), answer_id=r["answer_id"], verdict=r["verdict"],
                picked=r["picked"], picks=round_picks, compare_url=f"compare.html?a={r['char_a']}&b={r['char_b']}",
            ))
    return out


def _people(data: dict) -> Dict[int, dict]:
    ids = {g["creator_id"] for g in data["games"]}
    ids |= {m["user_id"] for ms in data["members"].values() for m in ms}
    ids |= {u for us in data["invites"].values() for u in us}
    return duels.players(ids)


def _one(game_id: int, me: Optional[int]) -> DuelOut:
    data = _run(duels.game, game_id)
    return _duel_out(data["games"][0], me, data, _people(data), {})


@router.get("/api/games", response_model=DuelListOut)
def list_games(response: Response, user: dict = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    data = duels.overview(user["id"])
    people, cache = _people(data), {}
    return DuelListOut(mine=[_duel_out(g, user["id"], data, people, cache) for g in data["mine"]],
                       open=[_duel_out(g, user["id"], data, people, cache) for g in data["open"]])


@router.get("/api/games/version")
def games_version(response: Response):
    """What an open Duels page polls, answered from memory."""
    response.headers["Cache-Control"] = "no-store"
    return {"version": duels.version()}


@router.get("/api/games/pending")
def pending(response: Response, user: Optional[dict] = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return {"count": duels.pending_count(user["id"]) if user else 0}


@router.get("/api/games/{game_id}", response_model=DuelOut)
def get_game(game_id: int, response: Response, user: Optional[dict] = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return _one(game_id, user["id"] if user else None)


@router.post("/api/games", response_model=DuelOut, dependencies=[Depends(same_origin)])
def create_game(payload: DuelCreateIn, user: dict = Depends(require_user)):
    if not post_limit.allow(f"user:{user['id']}"):
        raise HTTPException(status_code=429, detail="You're making games too fast - wait a minute")
    for m in payload.matchups:
        for cid in (m.char_a, m.char_b):
            if db.get_character_by_id(cid) is None:
                raise HTTPException(status_code=404, detail=f"No character with id {cid}")
    game_id = _run(duels.create, user["id"], payload.format, payload.invite, [m.model_dump() for m in payload.matchups],
                   payload.exclude)
    return _one(game_id, user["id"])


@router.post("/api/games/{game_id}/join", response_model=DuelOut, dependencies=[Depends(same_origin)])
def join_game(game_id: int, payload: DuelJoinIn, user: dict = Depends(require_user)):
    _run(duels.join, game_id, user["id"], payload.team)
    return _one(game_id, user["id"])


@router.post("/api/games/{game_id}/leave", dependencies=[Depends(same_origin)])
def leave_game(game_id: int, user: dict = Depends(require_user)):
    _run(duels.leave, game_id, user["id"])
    return {"ok": True}


@router.post("/api/games/{game_id}/decline", dependencies=[Depends(same_origin)])
def decline_game(game_id: int, user: dict = Depends(require_user)):
    _run(duels.decline, game_id, user["id"])
    return {"ok": True}


@router.post("/api/games/{game_id}/cancel", dependencies=[Depends(same_origin)])
def cancel_game(game_id: int, user: dict = Depends(require_user)):
    _run(duels.cancel, game_id, user["id"])
    return {"ok": True}


@router.post("/api/games/{game_id}/next", dependencies=[Depends(same_origin)])
def next_round(game_id: int, user: dict = Depends(require_user)):
    """This player's current round, starting its clock - {"round": null}
    once they've played them all."""
    r = _run(duels.next_round, game_id, user["id"])
    return {"round": _round_out(game_id, r) if r else None}


@router.post("/api/games/{game_id}/pick", response_model=DuelPickOut, dependencies=[Depends(same_origin)])
def pick(game_id: int, payload: DuelPickIn, user: dict = Depends(require_user)):
    """Locks in a pick and returns the next round (already started) in the
    same response: one round trip between rounds instead of two."""
    in_time, nxt = _run(duels.pick, game_id, user["id"], payload.round_no, payload.pick_id)
    return DuelPickOut(in_time=in_time, next=_round_out(game_id, nxt) if nxt else None)


@router.get("/api/leaderboard", response_model=LeaderboardOut)
def leaderboard(response: Response):
    response.headers["Cache-Control"] = "no-store"
    recs = duels.records()
    top = sorted(recs.items(), key=lambda kv: (-kv[1]["wins"], kv[1]["losses"], -kv[1]["draws"]))[:25]
    people = duels.players([uid for uid, _ in top])
    return LeaderboardOut(rows=[
        LeaderboardRowOut(username=people[uid]["username"], is_admin=community.is_admin(people[uid]["username"]),
                          avatar_url=avatar_url(people[uid]["username"], people[uid]["avatar_at"]), **rec)
        for uid, rec in top if uid in people
    ])
