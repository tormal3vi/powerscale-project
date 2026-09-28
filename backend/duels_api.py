"""HTTP endpoints for prediction duels (rules and storage: duels.py)."""

import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response

import calculator
import db
from backend import characters, community, discord_webhooks, duels
from backend.community_api import (
    avatar_url, character_image_url, current_user, post_limit, require_user, same_origin,
)
from backend.schemas import (
    DuelBoutOut, DuelCreateIn, DuelJoinIn, DuelListOut, DuelOut, DuelPickIn, DuelPickOut, DuelPlayerOut,
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
            form=form or default, image_url=replaced or characters.form_picture(char_id, form or default),
        )
    return cache[key]


def _round_out(game_id: int, r: dict) -> DuelRoundOut:
    cache: dict = {}
    out = DuelRoundOut(game_id=game_id, round_no=r["round_no"], total=duels.ROUNDS, seconds_left=r["seconds_left"])
    if r.get("mode") == "draft":
        out.mode = "draft"
        out.hand = [_side(cid, None, cache) for cid in r.get("hand", [])]
    else:
        out.a, out.b = _side(r["char_a"], r["form_a"], cache), _side(r["char_b"], r["form_b"], cache)
    return out


def _tier_of() -> Dict[int, float]:
    return {cid: tier for cid, tier, _ in characters.scorable_pool()}


def _duel_out(g: dict, me: Optional[int], data: dict, people: Dict[int, dict], cache: dict) -> DuelOut:
    members = data["members"].get(g["id"], [])
    invited = data["invites"].get(g["id"], [])
    picks, now = data["picks"], data["now"]
    teams, size = duels.shape(g)
    done = g["status"] == "done"
    rounds = data["rounds"].get(g["id"], []) if done else []
    # Stored when the game finished; games from before that are scored from
    # their rounds (loaded for exactly those).
    score = {m["user_id"]: m["score"] if m.get("score") is not None else
             sum(duels.pick_of(picks, g["id"], m["user_id"], r["round_no"], r["answer_id"])[1] for r in rounds)
             for m in members}
    # Lists don't load a finished game's picks: everyone has played it out.
    have_picks = not done or any(p["game_id"] == g["id"] for p in picks)
    played = lambda uid: duels.played(picks, g["id"], uid, now) if have_picks else duels.ROUNDS  # noqa: E731
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
            played=played(m["user_id"]), me=m["user_id"] == me,
            score=score[m["user_id"]] if done else None, outcome=m["outcome"] if done else None,
        ))
    can_join = (me is not None and g["status"] == "open" and me not in member_ids and bool(free)
                and (not invited or me in invited))
    out = DuelOut(
        id=g["id"], status=g["status"], created_at=g["created_at"], format=duels.format_name(g),
        mode=g.get("mode") or "predict",
        teams=teams, team_size=size, creator=person(g["creator_id"])[0], players=out_players,
        invited=[person(u)[0] for u in invited if u not in member_ids], private=bool(invited),
        seats_left=teams * size - len(members) if g["status"] == "open" else 0,
        picked=g.get("picked"), excluded=[s for s in (g.get("excluded") or "").split("|") if s],
        my_team=mine["team"] if mine else None,
        my_played=played(me) if mine else 0, total=duels.ROUNDS,
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
        draft = g.get("mode") == "draft"
        if draft and rounds:
            picked = duels.picks_in_time(g["id"], picks, members)
            bouts = duels.draft_bouts(members, picked, duels.pick_seconds(g["id"], picks, members))
            points = duels.draft_points(members, picked, bouts=bouts)
            if any(m.get("score") is not None and m["score"] != sum(points[(n, m["user_id"])] for n in range(1, duels.ROUNDS + 1))
                   for m in members):
                # Finished before dead-even picks went to the faster one:
                # show it as it was scored.
                bouts = duels.draft_bouts(members, picked)
                points = duels.draft_points(members, picked, bouts=bouts)
            hands = data.get("hands", {}).get(g["id"], {})
            tier = _tier_of()
        for r in rounds:
            round_picks = []
            for m in sorted(members, key=lambda m: (m["team"], m["joined_at"])):
                if draft:
                    hand = hands.get((r["round_no"], m["seat"]), [])
                    round_picks.append(DuelRoundPickOut(
                        username=person(m["user_id"])[0], team=m["team"], pick_id=picked.get((r["round_no"], m["user_id"])),
                        points=points.get((r["round_no"], m["user_id"]), 0),
                        hand=[_side(cid, None, cache) for cid in hand],
                        best_id=max(hand, key=lambda cid: tier.get(cid, -1)) if hand else None,
                    ))
                    continue
                pick_id, ok = duels.pick_of(picks, g["id"], m["user_id"], r["round_no"], r["answer_id"])
                round_picks.append(DuelRoundPickOut(username=person(m["user_id"])[0], team=m["team"],
                                                    pick_id=pick_id, correct=ok))
            if draft:
                round_bouts = []
                for bout in (b for b in bouts if b["round_no"] == r["round_no"]):
                    xa, ya = bout["x_pick"], bout["y_pick"]
                    round_bouts.append(DuelBoutOut(
                        user_a=person(bout["x"])[0], user_b=person(bout["y"])[0],
                        a=_side(xa, None, cache) if xa else None, b=_side(ya, None, cache) if ya else None,
                        winner=person(bout["winner"])[0] if bout["winner"] is not None else None,
                        by_speed=bout["by_speed"],
                        compare_url=f"compare.html?a={xa}&b={ya}" if xa and ya else None,
                    ))
                out.rounds.append(DuelResultRoundOut(round_no=r["round_no"], answer_id=0, verdict="", picked=False,
                                                     picks=round_picks, compare_url="", bouts=round_bouts))
                continue
            out.rounds.append(DuelResultRoundOut(
                round_no=r["round_no"], a=_side(r["char_a"], r["form_a"], cache),
                b=_side(r["char_b"], r["form_b"], cache), answer_id=r["answer_id"], verdict=r["verdict"],
                picked=r["picked"], picks=round_picks, compare_url=f"compare.html?a={r['char_a']}&b={r['char_b']}",
            ))
    return out


def _one(game_id: int, me: Optional[int]) -> DuelOut:
    data = _run(duels.game, game_id)
    return _duel_out(data["games"][0], me, data, data["people"], {})


@router.get("/api/games", response_model=DuelListOut)
def list_games(response: Response, user: dict = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    data = duels.overview(user["id"])
    people, cache = data["people"], {}
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
                   payload.exclude, payload.mode)
    discord_webhooks.lobby_open(game_id)  # open to anyone: "wants to duel" on Discord
    return _one(game_id, user["id"])


@router.post("/api/games/{game_id}/join", response_model=DuelOut, dependencies=[Depends(same_origin)])
def join_game(game_id: int, payload: DuelJoinIn, user: dict = Depends(require_user)):
    _run(duels.join, game_id, user["id"], payload.team)
    discord_webhooks.lobby_update(game_id)
    return _one(game_id, user["id"])


@router.post("/api/games/{game_id}/leave", dependencies=[Depends(same_origin)])
def leave_game(game_id: int, user: dict = Depends(require_user)):
    _run(duels.leave, game_id, user["id"])
    discord_webhooks.lobby_update(game_id)
    return {"ok": True}


@router.post("/api/games/{game_id}/decline", dependencies=[Depends(same_origin)])
def decline_game(game_id: int, user: dict = Depends(require_user)):
    _run(duels.decline, game_id, user["id"])
    return {"ok": True}


@router.post("/api/games/{game_id}/cancel", dependencies=[Depends(same_origin)])
def cancel_game(game_id: int, user: dict = Depends(require_user)):
    _run(duels.cancel, game_id, user["id"])
    discord_webhooks.lobby_update(game_id)
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
    return LeaderboardOut(rows=[
        LeaderboardRowOut(username=r["username"], is_admin=community.is_admin(r["username"]),
                          avatar_url=avatar_url(r["username"], r["avatar_at"]),
                          wins=r["wins"], draws=r["draws"], losses=r["losses"])
        for r in duels.leaderboard()
    ])
