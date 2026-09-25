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
    Column, DateTime, ForeignKey, Integer, LargeBinary, MetaData, String, Table, Text,
    UniqueConstraint, and_, create_engine, delete, exists, func, insert, inspect, literal, select, text,
    update,
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
    Column("bio", String(200), nullable=True),
    # A character id in powerscale.db (a separate database, so no FK).
    Column("favorite_char_id", Integer, nullable=True),
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
# Profile pictures, already re-encoded by backend/avatars.py (~10-20 KB
# each). Kept in the database rather than on disk: Render's free tier
# wipes the disk on every deploy.
avatars = Table(
    "avatars", metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("image", LargeBinary, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
# Pictures admins upload to replace a character's wiki picture (a bad
# crop, a spoiler, a missing one). Same processing as avatars. Keyed by
# the character's id in powerscale.db.
character_images = Table(
    "character_images", metadata,
    Column("char_id", Integer, primary_key=True),
    Column("image", LargeBinary, nullable=False),
    Column("admin_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
likes = Table(
    "likes", metadata,
    Column("post_id", Integer, ForeignKey("posts.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
)


# Columns added after the first deploy. create_all() only creates missing
# tables, never missing columns, so an existing database (the live Neon one)
# gets them here. Nullable, so adding them touches no existing row.
_ADDED_COLUMNS = {
    "posts": [("kind", "VARCHAR(16)"), ("ruling_winner", "INTEGER")],
    "users": [("bio", "VARCHAR(200)"), ("favorite_char_id", "INTEGER")],
}


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


# --- profile settings -------------------------------------------------------------

def get_profile(user_id: Optional[int] = None, username: Optional[str] = None) -> Optional[dict]:
    """A user's profile plus counts, by id or (case-insensitive) username."""
    where = users.c.id == user_id if user_id is not None else users.c.username_lower == (username or "").lower()
    with engine.connect() as conn:
        row = conn.execute(select(users.c.id, users.c.username, users.c.bio, users.c.favorite_char_id,
                                  users.c.created_at).where(where)).mappings().first()
        if row is None:
            return None
        post_count = conn.execute(select(func.count()).select_from(posts).where(
            and_(posts.c.user_id == row["id"], posts.c.parent_id.is_(None)))).scalar()
        likes_received = conn.execute(select(func.count()).select_from(likes).join(
            posts, posts.c.id == likes.c.post_id).where(posts.c.user_id == row["id"])).scalar()
    return {**row, "created_at": _aware(row["created_at"]), "post_count": post_count,
            "likes_received": likes_received}


def username_taken(username: str, except_user_id: Optional[int] = None) -> bool:
    with engine.connect() as conn:
        where = users.c.username_lower == username.lower()
        if except_user_id is not None:
            where = and_(where, users.c.id != except_user_id)
        return conn.execute(select(users.c.id).where(where)).first() is not None


def update_profile(user_id: int, username: str, bio: Optional[str], favorite_char_id: Optional[int]) -> None:
    """Raises UsernameTaken if another account has `username` (any case)."""
    with engine.begin() as conn:
        clash = conn.execute(select(users.c.id).where(and_(
            users.c.username_lower == username.lower(), users.c.id != user_id))).first()
        if clash:
            raise UsernameTaken(username)
        conn.execute(update(users).where(users.c.id == user_id).values(
            username=username, username_lower=username.lower(), bio=bio or None,
            favorite_char_id=favorite_char_id))


def change_password(user_id: int, current: str, new: str) -> bool:
    """False (and nothing changed) if `current` is wrong."""
    with engine.begin() as conn:
        stored = conn.execute(select(users.c.password_hash).where(users.c.id == user_id)).scalar()
        if stored is None or not verify_password(current, stored):
            return False
        conn.execute(update(users).where(users.c.id == user_id).values(password_hash=hash_password(new)))
    return True


def check_password(user_id: int, password: str) -> bool:
    with engine.connect() as conn:
        stored = conn.execute(select(users.c.password_hash).where(users.c.id == user_id)).scalar()
    return stored is not None and verify_password(password, stored)


def delete_other_sessions(user_id: int, keep_token: Optional[str]) -> int:
    keep = _token_hash(keep_token) if keep_token else ""
    with engine.begin() as conn:
        return conn.execute(delete(sessions).where(
            and_(sessions.c.user_id == user_id, sessions.c.token_hash != keep))).rowcount


def has_admin_records(user_id: int) -> bool:
    """Overrules or replaced pictures made by this account - they point at it
    by id, so it can't be deleted while they exist."""
    with engine.connect() as conn:
        return bool(conn.execute(select(overrides.c.id).where(overrides.c.admin_id == user_id)).first()
                    or conn.execute(select(character_images.c.char_id)
                                    .where(character_images.c.admin_id == user_id)).first())


def delete_account(user_id: int) -> None:
    """Removes the user and everything they wrote: their posts (with every
    reply to them), their replies elsewhere, their likes, picture and
    sessions."""
    with engine.begin() as conn:
        own = [r[0] for r in conn.execute(select(posts.c.id).where(posts.c.user_id == user_id))]
        replies_to_own = [r[0] for r in conn.execute(select(posts.c.id).where(posts.c.parent_id.in_(own)))] if own else []
        doomed = list(set(own) | set(replies_to_own))
        if doomed:
            conn.execute(delete(likes).where(likes.c.post_id.in_(doomed)))
            # Replies first: they point at their parent.
            conn.execute(delete(posts).where(and_(posts.c.id.in_(doomed), posts.c.parent_id.isnot(None))))
            conn.execute(delete(posts).where(posts.c.id.in_(doomed)))
        conn.execute(delete(likes).where(likes.c.user_id == user_id))
        conn.execute(delete(avatars).where(avatars.c.user_id == user_id))
        conn.execute(delete(sessions).where(sessions.c.user_id == user_id))
        conn.execute(delete(users).where(users.c.id == user_id))
    _board_changed()


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


# --- profile pictures -------------------------------------------------------------

def set_avatar(user_id: int, image: bytes) -> None:
    with engine.begin() as conn:
        conn.execute(delete(avatars).where(avatars.c.user_id == user_id))
        conn.execute(insert(avatars).values(user_id=user_id, image=image, updated_at=_now()))


def delete_avatar(user_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(delete(avatars).where(avatars.c.user_id == user_id))


def avatar_updated_at(user_id: int) -> Optional[datetime]:
    with engine.connect() as conn:
        at = conn.execute(select(avatars.c.updated_at).where(avatars.c.user_id == user_id)).scalar()
    return _aware(at) if at else None


def get_avatar(username: str) -> Optional[bytes]:
    with engine.connect() as conn:
        return conn.execute(
            select(avatars.c.image).join(users, users.c.id == avatars.c.user_id)
            .where(users.c.username_lower == username.lower())
        ).scalar()


# --- in-memory copies of small, rarely-changing tables -------------------------------
# Overrules and replaced pictures are read on almost every page (every
# matchup card, every character list) but change only when an admin acts.
# Each read was a round trip to Neon (~0.2s from Render). Render runs one
# server process, and every write to these tables goes through the
# functions below, which drop the copy - so it can't go stale.

_memo: Dict[str, object] = {}
_memo_lock = threading.Lock()


def _remembered(name: str, load):
    with _memo_lock:
        if name in _memo:
            return _memo[name]
    value = load()
    with _memo_lock:
        _memo[name] = value
    return value


def _forget(name: str) -> None:
    with _memo_lock:
        _memo.pop(name, None)


# --- "anything new on the Board?" -----------------------------------------------------
# An open Board asks every few seconds. Every write it shows (posts,
# replies, likes, deletions, overrules) goes through this module and bumps
# the counter, so answering "nothing new" never touches Neon - same one-
# process reasoning as _memo above. The random part changes on restart, so
# a Board left open across a deploy refreshes once rather than missing
# what happened while the server was down.

_board_boot = secrets.token_hex(4)
_board_changes = 0


def board_version() -> str:
    with _memo_lock:
        return f"{_board_boot}.{_board_changes}"


def _board_changed() -> None:
    global _board_changes
    with _memo_lock:
        _board_changes += 1


def set_character_image(char_id: int, image: bytes, admin_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(delete(character_images).where(character_images.c.char_id == char_id))
        conn.execute(insert(character_images).values(char_id=char_id, image=image, admin_id=admin_id,
                                                     updated_at=_now()))
    _forget("image_versions")


def delete_character_image(char_id: int) -> bool:
    with engine.begin() as conn:
        removed = conn.execute(delete(character_images).where(character_images.c.char_id == char_id)).rowcount > 0
    _forget("image_versions")
    return removed


def get_character_image(char_id: int) -> Optional[bytes]:
    with engine.connect() as conn:
        return conn.execute(select(character_images.c.image).where(character_images.c.char_id == char_id)).scalar()


def character_image_versions() -> Dict[int, datetime]:
    """{char_id: upload time} for every replaced picture - one query for
    the whole character list."""
    def load():
        with engine.connect() as conn:
            rows = conn.execute(select(character_images.c.char_id, character_images.c.updated_at)).all()
        return {cid: _aware(at) for cid, at in rows}
    return dict(_remembered("image_versions", load))


# --- admin overrules ---------------------------------------------------------------

def _matchup_key(a: int, b: int, form_a: str, form_b: str) -> dict:
    if a <= b:
        return {"char_low": a, "char_high": b, "form_low": form_a, "form_high": form_b}
    return {"char_low": b, "char_high": a, "form_low": form_b, "form_high": form_a}


def _where(key: dict):
    return and_(*(getattr(overrides.c, k) == v for k, v in key.items()))


def _all_overrides() -> Dict[tuple, dict]:
    def load():
        with engine.connect() as conn:
            rows = conn.execute(select(overrides, users.c.username.label("admin"))
                                .join(users, users.c.id == overrides.c.admin_id)).mappings().all()
        return {(r["char_low"], r["char_high"], r["form_low"], r["form_high"]):
                {"winner_id": r["winner_id"], "note": r["note"], "admin": r["admin"],
                 "created_at": _aware(r["created_at"])} for r in rows}
    return _remembered("overrides", load)


def get_override(a: int, b: int, form_a: str, form_b: str) -> Optional[dict]:
    key = _matchup_key(a, b, form_a, form_b)
    found = _all_overrides().get((key["char_low"], key["char_high"], key["form_low"], key["form_high"]))
    return dict(found) if found else None


def set_override(a: int, b: int, form_a: str, form_b: str, winner_id: int, note: str, admin_id: int) -> None:
    key = _matchup_key(a, b, form_a, form_b)
    values = {"winner_id": winner_id, "note": note, "admin_id": admin_id, "created_at": _now()}
    with engine.begin() as conn:
        if conn.execute(select(overrides.c.id).where(_where(key))).first():
            conn.execute(update(overrides).where(_where(key)).values(**values))
        else:
            conn.execute(insert(overrides).values(**key, **values))
    _forget("overrides")


def delete_override(a: int, b: int, form_a: str, form_b: str) -> bool:
    with engine.begin() as conn:
        removed = conn.execute(delete(overrides).where(_where(_matchup_key(a, b, form_a, form_b)))).rowcount > 0
    _forget("overrides")
    return removed


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
        post_id = conn.execute(insert(posts).values(
            user_id=user_id, parent_id=parent_id, body=body, char_a=char_a, char_b=char_b,
            form_a=form_a, form_b=form_b, created_at=_now(), kind=kind, ruling_winner=ruling_winner,
        )).inserted_primary_key[0]
    _board_changed()
    return post_id


def _post_rows(conn, where, viewer_id: Optional[int], order, limit: Optional[int] = None) -> List[dict]:
    # One query: counts and "liked by me" as subqueries rather than three
    # follow-up queries. Each round trip from Render to Neon costs ~0.2s,
    # and the Board took ~4.5s to load when these added up.
    replies = posts.alias("replies")
    like_count = select(func.count()).select_from(likes).where(likes.c.post_id == posts.c.id).scalar_subquery()
    reply_count = (select(func.count()).select_from(replies)
                   .where(replies.c.parent_id == posts.c.id).scalar_subquery())
    liked = (exists().where(and_(likes.c.post_id == posts.c.id, likes.c.user_id == viewer_id))
             if viewer_id is not None else literal(False))
    query = (select(posts, users.c.username, users.c.favorite_char_id,
                    avatars.c.updated_at.label("avatar_at"),
                    like_count.label("like_count"), reply_count.label("reply_count"),
                    liked.label("liked_by_me"))
             .join(users, users.c.id == posts.c.user_id)
             .outerjoin(avatars, avatars.c.user_id == posts.c.user_id)
             .where(where).order_by(order))
    if limit:
        query = query.limit(limit)
    rows = [dict(r) for r in conn.execute(query).mappings()]
    for r in rows:
        r["created_at"] = _aware(r["created_at"])
        r["avatar_at"] = _aware(r["avatar_at"]) if r["avatar_at"] else None
        r["like_count"] = r["like_count"] or 0
        r["reply_count"] = r["reply_count"] or 0
        r["liked_by_me"] = bool(r["liked_by_me"])
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
    _board_changed()


def toggle_like(post_id: int, user_id: int) -> bool:
    """Returns True if the post is now liked by this user."""
    with engine.begin() as conn:
        match = and_(likes.c.post_id == post_id, likes.c.user_id == user_id)
        liked = not conn.execute(select(likes.c.post_id).where(match)).first()
        if liked:
            conn.execute(insert(likes).values(post_id=post_id, user_id=user_id))
        else:
            conn.execute(delete(likes).where(match))
    _board_changed()
    return liked
