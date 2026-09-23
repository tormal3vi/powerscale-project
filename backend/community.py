"""User-generated data: accounts, sessions, admin overrules, message board.

Kept apart from powerscale.db on purpose: that file is a git-tracked
snapshot of scraped wiki data that every deploy overwrites, and Render's
free tier wipes its disk on every deploy/restart anyway. This lives in the
database named by DATABASE_URL - a hosted Postgres (Neon) in production -
or, when that's unset, a local gitignored SQLite file, so it runs locally
with no setup. SQLAlchemy Core keeps the SQL identical for both.
"""

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text,
    UniqueConstraint, and_, create_engine, delete, func, insert, inspect, select, text, update,
)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{Path(__file__).parent.parent / 'community.db'}"
    # Neon hands out postgres:// / postgresql:// URLs; SQLAlchemy needs the
    # driver named explicitly.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


engine = create_engine(_database_url(), pool_pre_ping=True)
metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(20), nullable=False),
    Column("username_lower", String(20), nullable=False, unique=True),
    Column("password_hash", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)
overrides = Table(
    "overrides", metadata,
    Column("id", Integer, primary_key=True),
    # Stored with the lower character id first, forms following their
    # character, so A-vs-B and B-vs-A are the same row.
    Column("char_low", Integer, nullable=False),
    Column("char_high", Integer, nullable=False),
    Column("form_low", String(200), nullable=False),
    Column("form_high", String(200), nullable=False),
    Column("winner_id", Integer, nullable=False),
    Column("note", String(300), nullable=False),
    Column("admin_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("char_low", "char_high", "form_low", "form_high", name="uq_override_matchup"),
)
posts = Table(
    "posts", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("parent_id", Integer, ForeignKey("posts.id"), nullable=True, index=True),
    Column("body", Text, nullable=False),
    Column("char_a", Integer, nullable=True),
    Column("char_b", Integer, nullable=True),
    Column("form_a", String(200), nullable=True),
    Column("form_b", String(200), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # "overrule" for the post an admin ruling makes automatically; NULL for
    # everything people write themselves.
    Column("kind", String(16), nullable=True),
    # The winner that ruling named, kept on the post itself: the overrule can
    # later be changed or lifted, and the post should still say what it said.
    Column("ruling_winner", Integer, nullable=True),
)
likes = Table(
    "likes", metadata,
    Column("post_id", Integer, ForeignKey("posts.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
)


# Columns added after the first deploy. create_all() only creates missing
# tables, never missing columns, so an existing database (the live Neon one)
# gets them here. Nullable, so adding them touches no existing row.
_ADDED_COLUMNS = {"posts": [("kind", "VARCHAR(16)"), ("ruling_winner", "INTEGER")]}


def init() -> None:
    metadata.create_all(engine)
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            have = {c["name"] for c in inspector.get_columns(table)}
            for name, sql_type in columns:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite hands timestamps back without tzinfo; Postgres keeps it.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- admins ------------------------------------------------------------------

def is_admin(username: str) -> bool:
    """Admins are listed by username in the ADMIN_USERNAMES setting
    (comma-separated) - changeable without code changes or a redeploy of
    any data."""
    admins = {u.strip().lower() for u in os.environ.get("ADMIN_USERNAMES", "").split(",") if u.strip()}
    return username.lower() in admins


# --- passwords -----------------------------------------------------------------

_PBKDF2_ITERATIONS = 600_000  # OWASP's current recommendation for PBKDF2-SHA256


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Checked against when a login names an unknown user, so that request
# costs the same time as a real one and can't be used to probe which
# usernames exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


# --- users & sessions ----------------------------------------------------------

SESSION_DAYS = 30


class UsernameTaken(Exception):
    pass


def create_user(username: str, password: str) -> dict:
    with engine.begin() as conn:
        exists = conn.execute(select(users.c.id).where(users.c.username_lower == username.lower())).first()
        if exists:
            raise UsernameTaken(username)
        result = conn.execute(insert(users).values(
            username=username, username_lower=username.lower(),
            password_hash=hash_password(password), created_at=_now(),
        ))
        return {"id": result.inserted_primary_key[0], "username": username}


def authenticate(username: str, password: str) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(select(users).where(users.c.username_lower == username.lower())).mappings().first()
    if row is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"]}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int) -> str:
    """Returns the raw token for the cookie; only its hash is stored, so a
    leaked database can't be used to log in as anyone."""
    token = secrets.token_urlsafe(32)
    with engine.begin() as conn:
        conn.execute(delete(sessions).where(sessions.c.expires_at < _now()))
        conn.execute(insert(sessions).values(
            token_hash=_token_hash(token), user_id=user_id, expires_at=_now() + timedelta(days=SESSION_DAYS),
        ))
    return token


def user_for_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            select(users.c.id, users.c.username, sessions.c.expires_at)
            .join(sessions, sessions.c.user_id == users.c.id)
            .where(sessions.c.token_hash == _token_hash(token))
        ).mappings().first()
    if row is None or _aware(row["expires_at"]) < _now():
        return None
    return {"id": row["id"], "username": row["username"], "is_admin": is_admin(row["username"])}


def delete_session(token: Optional[str]) -> None:
    if token:
        with engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.token_hash == _token_hash(token)))


# --- rate limiting --------------------------------------------------------------

class RateLimiter:
    """At most `limit` hits per `window` seconds per key (e.g. client IP).
    In-memory: fine for one server process, which is what Render's free
    tier runs. Resets on restart - it's there to blunt spam and password
    guessing, not as an exact quota."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: Dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


# --- admin overrules ---------------------------------------------------------------

def _matchup_key(a: int, b: int, form_a: str, form_b: str) -> dict:
    if a <= b:
        return {"char_low": a, "char_high": b, "form_low": form_a, "form_high": form_b}
    return {"char_low": b, "char_high": a, "form_low": form_b, "form_high": form_a}


def _where(key: dict):
    return and_(*(getattr(overrides.c, k) == v for k, v in key.items()))


def get_override(a: int, b: int, form_a: str, form_b: str) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(
            select(overrides, users.c.username.label("admin"))
            .join(users, users.c.id == overrides.c.admin_id)
            .where(_where(_matchup_key(a, b, form_a, form_b)))
        ).mappings().first()
    if row is None:
        return None
    return {"winner_id": row["winner_id"], "note": row["note"], "admin": row["admin"],
            "created_at": _aware(row["created_at"])}


def set_override(a: int, b: int, form_a: str, form_b: str, winner_id: int, note: str, admin_id: int) -> None:
    key = _matchup_key(a, b, form_a, form_b)
    values = {"winner_id": winner_id, "note": note, "admin_id": admin_id, "created_at": _now()}
    with engine.begin() as conn:
        if conn.execute(select(overrides.c.id).where(_where(key))).first():
            conn.execute(update(overrides).where(_where(key)).values(**values))
        else:
            conn.execute(insert(overrides).values(**key, **values))


def delete_override(a: int, b: int, form_a: str, form_b: str) -> bool:
    with engine.begin() as conn:
        return conn.execute(delete(overrides).where(_where(_matchup_key(a, b, form_a, form_b)))).rowcount > 0


# --- message board --------------------------------------------------------------------

def create_post(user_id: int, body: str, parent_id: Optional[int] = None, char_a: Optional[int] = None,
                char_b: Optional[int] = None, form_a: Optional[str] = None, form_b: Optional[str] = None,
                kind: Optional[str] = None, ruling_winner: Optional[int] = None) -> int:
    with engine.begin() as conn:
        if parent_id is not None:
            parent = conn.execute(select(posts.c.id, posts.c.parent_id).where(posts.c.id == parent_id)).first()
            if parent is None:
                raise LookupError(parent_id)
            # One level of replies: replying to a reply joins the same thread.
            parent_id = parent.parent_id or parent.id
        result = conn.execute(insert(posts).values(
            user_id=user_id, parent_id=parent_id, body=body, char_a=char_a, char_b=char_b,
            form_a=form_a, form_b=form_b, created_at=_now(), kind=kind, ruling_winner=ruling_winner,
        ))
        return result.inserted_primary_key[0]


def _post_rows(conn, where, viewer_id: Optional[int], order, limit: Optional[int] = None) -> List[dict]:
    query = (select(posts, users.c.username).join(users, users.c.id == posts.c.user_id).where(where).order_by(order))
    if limit:
        query = query.limit(limit)
    rows = [dict(r) for r in conn.execute(query).mappings()]
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    like_counts = dict(conn.execute(
        select(likes.c.post_id, func.count()).where(likes.c.post_id.in_(ids)).group_by(likes.c.post_id)).all())
    reply_counts = dict(conn.execute(
        select(posts.c.parent_id, func.count()).where(posts.c.parent_id.in_(ids)).group_by(posts.c.parent_id)).all())
    liked = set()
    if viewer_id is not None:
        liked = {r[0] for r in conn.execute(
            select(likes.c.post_id).where(and_(likes.c.post_id.in_(ids), likes.c.user_id == viewer_id)))}
    for r in rows:
        r["created_at"] = _aware(r["created_at"])
        r["like_count"] = like_counts.get(r["id"], 0)
        r["reply_count"] = reply_counts.get(r["id"], 0)
        r["liked_by_me"] = r["id"] in liked
    return rows


def list_posts(viewer_id: Optional[int], before_id: Optional[int] = None, limit: int = 20) -> List[dict]:
    where = posts.c.parent_id.is_(None)
    if before_id is not None:
        where = and_(where, posts.c.id < before_id)
    with engine.connect() as conn:
        return _post_rows(conn, where, viewer_id, posts.c.id.desc(), limit)


def get_thread(post_id: int, viewer_id: Optional[int]) -> Optional[dict]:
    with engine.connect() as conn:
        top = _post_rows(conn, and_(posts.c.id == post_id, posts.c.parent_id.is_(None)), viewer_id, posts.c.id)
        if not top:
            return None
        replies = _post_rows(conn, posts.c.parent_id == post_id, viewer_id, posts.c.id)
    return {"post": top[0], "replies": replies}


def get_post_view(post_id: int, viewer_id: Optional[int]) -> Optional[dict]:
    """One post (top-level or reply) with author, counts and liked-by-me."""
    with engine.connect() as conn:
        rows = _post_rows(conn, posts.c.id == post_id, viewer_id, posts.c.id)
    return rows[0] if rows else None


def get_post(post_id: int) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(select(posts).where(posts.c.id == post_id)).mappings().first()
    return dict(row) if row else None


def delete_post(post_id: int) -> None:
    """Removes the post, its replies, and every like on any of them."""
    with engine.begin() as conn:
        reply_ids = [r[0] for r in conn.execute(select(posts.c.id).where(posts.c.parent_id == post_id))]
        all_ids = reply_ids + [post_id]
        conn.execute(delete(likes).where(likes.c.post_id.in_(all_ids)))
        conn.execute(delete(posts).where(posts.c.id.in_(reply_ids)))
        conn.execute(delete(posts).where(posts.c.id == post_id))


def toggle_like(post_id: int, user_id: int) -> bool:
    """Returns True if the post is now liked by this user."""
    with engine.begin() as conn:
        match = and_(likes.c.post_id == post_id, likes.c.user_id == user_id)
        if conn.execute(select(likes.c.post_id).where(match)).first():
            conn.execute(delete(likes).where(match))
            return False
        conn.execute(insert(likes).values(post_id=post_id, user_id=user_id))
        return True
