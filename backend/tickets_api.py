"""HTTP endpoints for tickets (rules and storage: tickets.py)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from backend import characters, community, tickets
from backend.community_api import (
    _matchup_out, avatar_url, post_limit, require_admin, require_user, same_origin,
)
from backend.schemas import (
    MyTicketOut, TicketAnswerIn, TicketBanIn, TicketBanOut, TicketIn, TicketListOut, TicketOut,
)

router = APIRouter()


def _run(fn, *args):
    try:
        return fn(*args)
    except tickets.TicketError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


def _forms(a: int, b: int, form_a: Optional[str], form_b: Optional[str]):
    """The matchup's canonical form names (as overrules store them) - and
    a 404 for a character that doesn't exist."""
    if a == b:
        raise HTTPException(status_code=400, detail="A matchup needs two different characters")
    try:
        v = characters.run_compare(a, b, form_a, form_b)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return v.form_a, v.form_b


def _ticket_out(t: dict, cache: dict, admin_view: bool = False) -> TicketOut:
    row = {"char_a": t["char_low"], "char_b": t["char_high"], "form_a": t["form_low"], "form_b": t["form_high"]}
    m = _matchup_out(row, cache)
    winner = (m.label_a if t["winner_id"] == m.char_a else m.label_b) if m else "?"
    return TicketOut(
        id=t["id"], matchup=m, winner_id=t["winner_id"], winner=winner, reason=t["reason"], status=t["status"],
        outcome=t.get("outcome"), response=t.get("response"), admin=t.get("admin"),
        created_at=t["created_at"], answered_at=t.get("answered_at"),
        author=t.get("username") or "", author_avatar=avatar_url(t["username"], t.get("avatar_at")) if t.get("username") else None,
        author_banned=bool(t.get("banned")) if admin_view else False,
    )


def _one(ticket_id: int, admin_view: bool = False) -> TicketOut:
    return _ticket_out(tickets.one(ticket_id), {}, admin_view)


# --- writing one ------------------------------------------------------------------------------

@router.get("/api/tickets/mine", response_model=MyTicketOut)
def my_ticket(a: int, b: int, response: Response, fa: Optional[str] = None, fb: Optional[str] = None,
              user: dict = Depends(require_user)):
    """Your ticket about this exact matchup, if any - and whether you may
    write one."""
    response.headers["Cache-Control"] = "no-store"
    form_a, form_b = _forms(a, b, fa, fb)
    t = tickets.mine(user["id"], a, b, form_a, form_b)
    ban = tickets.ban_of(user["id"])
    out = MyTicketOut(banned=ban is not None, ban_reason=ban["reason"] if ban else None)
    if t:
        out.ticket = _one(t["id"])
    return out


@router.post("/api/tickets", response_model=TicketOut, dependencies=[Depends(same_origin)])
def write_ticket(payload: TicketIn, user: dict = Depends(require_user)):
    if not post_limit.allow(f"user:{user['id']}"):
        raise HTTPException(status_code=429, detail="You're sending too fast - wait a minute")
    form_a, form_b = _forms(payload.char_a, payload.char_b, payload.form_a, payload.form_b)
    reason = " ".join(payload.reason.split())
    ticket_id = _run(tickets.create, user["id"], payload.char_a, payload.char_b, form_a, form_b,
                     payload.winner_id, reason)
    return _one(ticket_id)


# --- admins --------------------------------------------------------------------------------------

@router.get("/api/admin/tickets", response_model=TicketListOut)
def admin_tickets(response: Response, status: str = "open", admin: dict = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    cache: dict = {}
    rows = tickets.listing(status=status if status in ("open", "answered") else None, limit=200)
    return TicketListOut(tickets=[_ticket_out(t, cache, admin_view=True) for t in rows],
                         open_count=tickets.open_count())


@router.get("/api/admin/tickets/count")
def admin_ticket_count(response: Response, admin: dict = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    return {"open": tickets.open_count()}


@router.post("/api/admin/tickets/{ticket_id}/answer", response_model=TicketOut, dependencies=[Depends(same_origin)])
def answer_ticket(ticket_id: int, payload: TicketAnswerIn, admin: dict = Depends(require_admin)):
    """Replies and closes it; the verdict stands."""
    if tickets.get(ticket_id) is None:
        raise HTTPException(status_code=404, detail="No such ticket")
    _run(tickets.answer, ticket_id, admin["id"], payload.response.strip(), "kept")
    return _one(ticket_id, admin_view=True)


@router.post("/api/admin/tickets/{ticket_id}/overrule", response_model=TicketOut, dependencies=[Depends(same_origin)])
def overrule_from_ticket(ticket_id: int, payload: TicketAnswerIn, admin: dict = Depends(require_admin)):
    """Overrules the matchup in the ticket writer's favor - its Board post
    credits them - and closes the ticket. The note (or, left empty, the
    writer's own reason) becomes the overrule's reason."""
    t = tickets.get(ticket_id)
    if t is None:
        raise HTTPException(status_code=404, detail="No such ticket")
    if t["status"] != "open":
        raise HTTPException(status_code=409, detail="That ticket was already answered")
    note = (payload.response.strip() or t["reason"])[:300]
    a, b, fa, fb = t["char_low"], t["char_high"], t["form_low"], t["form_high"]
    community.set_override(a, b, fa, fb, t["winner_id"], note, admin["id"])
    community.create_post(admin["id"], note, None, a, b, fa, fb, kind="overrule", ruling_winner=t["winner_id"],
                          credit_user_id=t["user_id"])
    _run(tickets.answer, ticket_id, admin["id"], payload.response.strip(), "overruled")
    return _one(ticket_id, admin_view=True)


@router.get("/api/admin/ticket-bans", response_model=list)
def ticket_bans(response: Response, admin: dict = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    return [TicketBanOut(username=b["username"], admin=b["admin"], reason=b["reason"], created_at=b["created_at"])
            for b in tickets.bans()]


@router.post("/api/admin/ticket-bans", dependencies=[Depends(same_origin)])
def ban_from_tickets(payload: TicketBanIn, admin: dict = Depends(require_admin)):
    profile = community.get_profile(username=payload.username.strip())
    if profile is None:
        raise HTTPException(status_code=404, detail="No such user")
    if community.is_admin(profile["username"]):
        raise HTTPException(status_code=403, detail="Admins can't be banned from tickets")
    tickets.ban(profile["id"], admin["id"], " ".join(payload.reason.split())[:300])
    return {"ok": True}


@router.delete("/api/admin/ticket-bans/{username}", dependencies=[Depends(same_origin)])
def unban_from_tickets(username: str, admin: dict = Depends(require_admin)):
    profile = community.get_profile(username=username)
    if profile is None or not tickets.unban(profile["id"]):
        raise HTTPException(status_code=404, detail="That user isn't banned from tickets")
    return {"ok": True}
