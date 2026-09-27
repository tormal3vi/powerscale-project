"""A user's public profile page: who they are and everything they've
done on the site - posts, matchup comments, duels, the overrules their
tickets led to (and, for admins, their tickets and ticket ban)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from backend import characters, community, duels, tickets
from backend.community_api import _post_out, _profile_out, current_user
from backend.duels_api import _duel_out
from backend.schemas import ProfileCommentOut, PublicProfileOut
from backend.tickets_api import _ticket_out

router = APIRouter()


def _label(char_id: int) -> str:
    name = characters.display_name_for_id(char_id) or "Removed character"
    return characters.short_name(name)


@router.get("/api/users/{username}/page", response_model=PublicProfileOut)
def profile_page(username: str, response: Response, viewer: Optional[dict] = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    profile = community.get_profile(username=username)
    if profile is None:
        raise HTTPException(status_code=404, detail="No such user")
    uid = profile["id"]
    activity = community.user_activity(uid, viewer["id"] if viewer else None)
    cache: dict = {}
    out = PublicProfileOut(
        profile=_profile_out(profile, own=False),
        likes_received=profile["likes_received"], comment_count=activity["comment_count"],
        overrules_suggested=tickets.credited_overrules(uid),
        posts=[_post_out(r, viewer, cache) for r in activity["posts"]],
        credited=[_post_out(r, viewer, cache) for r in activity["credited"]],
        comments=[ProfileCommentOut(
            id=c["id"], body=c["body"], created_at=c["created_at"],
            label_a=_label(c["char_low"]), label_b=_label(c["char_high"]),
            compare_url=f"compare.html?a={c['char_low']}&b={c['char_high']}",
        ) for c in activity["comments"]],
    )
    board = duels.leaderboard(limit=1000)
    out.duel_rank = next((i + 1 for i, r in enumerate(board) if r["username"] == profile["username"]), None)
    recent = duels.recent_finished(uid)
    out.duels = [_duel_out(g, uid, recent, recent["people"], cache) for g in recent["games"]]
    if viewer and viewer["is_admin"]:
        out.tickets = [_ticket_out(t, cache, admin_view=True) for t in tickets.listing(user_id=uid, limit=50)]
        ban = tickets.ban_of(uid)
        out.ticket_banned = ban is not None
        out.ticket_ban_reason = ban["reason"] if ban else None
    return out
