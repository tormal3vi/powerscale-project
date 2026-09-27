"""Tickets: users asking admins to look at a verdict they disagree with.

A ticket is about one matchup - the exact pair of forms, as overrules are
- and says who the user thinks wins, and why. Each user gets one ticket
per matchup, ever: while it's open they wait, and once an admin has
answered it (with a reply, or by overruling the verdict in the user's
favor) that matchup is settled for them. Admins can ban ticket spammers
from writing any more.

Stored in the community database (Neon), next to accounts and posts."""

from typing import Dict, List, Optional

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint, and_, delete, func, insert,
    select, update,
)
from sqlalchemy.exc import IntegrityError

from backend import community
from backend.community import _aware, _now, avatars, engine, metadata, users

MAX_REASON_CHARS = 1000
MAX_RESPONSE_CHARS = 1000

tickets = Table(
    "tickets", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    # The matchup as overrules store it: lower character id first, forms
    # following their character.
    Column("char_low", Integer, nullable=False),
    Column("char_high", Integer, nullable=False),
    Column("form_low", String(200), nullable=False),
    Column("form_high", String(200), nullable=False),
    Column("winner_id", Integer, nullable=False),  # who the user says wins
    Column("reason", Text, nullable=False),
    Column("status", String(12), nullable=False),  # open | answered
    Column("outcome", String(12), nullable=True),  # overruled | kept, once answered
    Column("response", Text, nullable=True),  # the admin's reply
    Column("admin_id", Integer, ForeignKey("users.id"), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("answered_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("user_id", "char_low", "char_high", "form_low", "form_high", name="uq_ticket_per_matchup"),
)
ticket_bans = Table(
    "ticket_bans", metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("admin_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("reason", String(300), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class TicketError(Exception):
    """A request the rules don't allow; the message is shown as is."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _key(a: int, b: int, form_a: str, form_b: str) -> dict:
    if a <= b:
        return {"char_low": a, "char_high": b, "form_low": form_a, "form_high": form_b}
    return {"char_low": b, "char_high": a, "form_low": form_b, "form_high": form_a}


def _match(key: dict):
    return and_(*(getattr(tickets.c, k) == v for k, v in key.items()))


def _row(r) -> dict:
    d = dict(r)
    for k in ("created_at", "answered_at"):
        if d.get(k):
            d[k] = _aware(d[k])
    return d


# --- bans --------------------------------------------------------------------------------

def ban_of(user_id: int) -> Optional[dict]:
    with community.reader.connect() as conn:
        r = conn.execute(select(ticket_bans).where(ticket_bans.c.user_id == user_id)).mappings().first()
    return _row(r) if r else None


def ban(user_id: int, admin_id: int, reason: str) -> None:
    with engine.begin() as conn:
        conn.execute(delete(ticket_bans).where(ticket_bans.c.user_id == user_id))
        conn.execute(insert(ticket_bans).values(user_id=user_id, admin_id=admin_id, reason=reason, created_at=_now()))


def unban(user_id: int) -> bool:
    with engine.begin() as conn:
        return conn.execute(delete(ticket_bans).where(ticket_bans.c.user_id == user_id)).rowcount > 0


def bans() -> List[dict]:
    admin = users.alias("admin")
    with community.reader.connect() as conn:
        rows = conn.execute(
            select(ticket_bans, users.c.username, admin.c.username.label("admin"))
            .join(users, users.c.id == ticket_bans.c.user_id)
            .join(admin, admin.c.id == ticket_bans.c.admin_id)
            .order_by(ticket_bans.c.created_at.desc())).mappings().all()
    return [_row(r) for r in rows]


# --- writing and answering ------------------------------------------------------------------

def mine(user_id: int, a: int, b: int, form_a: str, form_b: str) -> Optional[dict]:
    """This user's ticket about this exact matchup, if they wrote one."""
    with community.reader.connect() as conn:
        r = conn.execute(select(tickets).where(and_(tickets.c.user_id == user_id,
                                                    _match(_key(a, b, form_a, form_b))))).mappings().first()
    return _row(r) if r else None


def create(user_id: int, a: int, b: int, form_a: str, form_b: str, winner_id: int, reason: str) -> int:
    if winner_id not in (a, b):
        raise TicketError("Pick one of the two characters as the winner")
    if not reason:
        raise TicketError("Say why you think the verdict is wrong")
    if len(reason) > MAX_REASON_CHARS:
        raise TicketError(f"Keep it under {MAX_REASON_CHARS} characters")
    if ban_of(user_id):
        raise TicketError("You can't send tickets - an admin has blocked you from it", 403)
    try:
        with engine.begin() as conn:
            return conn.execute(insert(tickets).values(
                user_id=user_id, **_key(a, b, form_a, form_b), winner_id=winner_id, reason=reason,
                status="open", created_at=_now(),
            )).inserted_primary_key[0]
    except IntegrityError:
        raise TicketError("You already sent a ticket about this matchup", 409)


def get(ticket_id: int) -> Optional[dict]:
    with community.reader.connect() as conn:
        r = conn.execute(select(tickets).where(tickets.c.id == ticket_id)).mappings().first()
    return _row(r) if r else None


def answer(ticket_id: int, admin_id: int, response: str, outcome: str) -> dict:
    """Closes an open ticket: `outcome` is "kept" (a reply, verdict stands)
    or "overruled" (the admin overruled in the user's favor)."""
    if len(response) > MAX_RESPONSE_CHARS:
        raise TicketError(f"Keep the reply under {MAX_RESPONSE_CHARS} characters")
    with engine.begin() as conn:
        done = conn.execute(update(tickets).where(and_(tickets.c.id == ticket_id, tickets.c.status == "open"))
                            .values(status="answered", outcome=outcome, response=response or None,
                                    admin_id=admin_id, answered_at=_now())).rowcount
    if not done:
        raise TicketError("That ticket was already answered", 409)
    return get(ticket_id)


# --- reading ----------------------------------------------------------------------------------

def listing(status: Optional[str] = None, user_id: Optional[int] = None, limit: int = 100,
            ticket_id: Optional[int] = None) -> List[dict]:
    """Tickets with their writer's name and picture (and the answering
    admin's name), newest first; open ones oldest first - the queue."""
    admin = users.alias("admin")
    q = (select(tickets, users.c.username, avatars.c.updated_at.label("avatar_at"), admin.c.username.label("admin"),
                (select(ticket_bans.c.user_id).where(ticket_bans.c.user_id == tickets.c.user_id).exists()).label("banned"))
         .join(users, users.c.id == tickets.c.user_id)
         .outerjoin(avatars, avatars.c.user_id == tickets.c.user_id)
         .outerjoin(admin, admin.c.id == tickets.c.admin_id))
    if status:
        q = q.where(tickets.c.status == status)
    if user_id is not None:
        q = q.where(tickets.c.user_id == user_id)
    if ticket_id is not None:
        q = q.where(tickets.c.id == ticket_id)
    q = q.order_by(tickets.c.id.asc() if status == "open" else tickets.c.id.desc()).limit(limit)
    with community.reader.connect() as conn:
        rows = conn.execute(q).mappings().all()
    return [{**_row(r), "avatar_at": _aware(r["avatar_at"]) if r["avatar_at"] else None, "banned": bool(r["banned"])}
            for r in rows]


def one(ticket_id: int) -> Optional[dict]:
    """A ticket in listing() form: with its writer's name, picture, ban."""
    rows = listing(ticket_id=ticket_id, limit=1)
    return rows[0] if rows else None


def open_count() -> int:
    with community.reader.connect() as conn:
        return conn.execute(select(func.count()).select_from(tickets).where(tickets.c.status == "open")).scalar()


def credited_overrules(user_id: int) -> int:
    with community.reader.connect() as conn:
        return conn.execute(select(func.count()).select_from(tickets).where(and_(
            tickets.c.user_id == user_id, tickets.c.outcome == "overruled"))).scalar()


def delete_user(user_id: int) -> None:
    """Their tickets and any ban on them - when they delete their account."""
    with engine.begin() as conn:
        conn.execute(delete(tickets).where(tickets.c.user_id == user_id))
        conn.execute(delete(ticket_bans).where(ticket_bans.c.user_id == user_id))


def usernames(ids) -> Dict[int, str]:
    ids = [i for i in set(ids) if i is not None]
    if not ids:
        return {}
    with community.reader.connect() as conn:
        return {r[0]: r[1] for r in conn.execute(select(users.c.id, users.c.username).where(users.c.id.in_(ids)))}
