"""HTTP endpoints for accounts, admin overrules and the message board.
Data rules live in community.py; this is request handling, auth checks,
input validation and rate limits."""

import re
from typing import Optional
from urllib.parse import urlparse

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

import db
from backend import avatars, characters, community
from backend.schemas import (
    AuthIn, LikeOut, MatchupOut, MeOut, OverrideIn, OverrideOut, PostIn, PostListOut,
    PostOut, RulingOut, ThreadOut, UserOut,
)

router = APIRouter()

COOKIE = "ps_session"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MAX_POST_CHARS = 500

register_limit = community.RateLimiter(limit=5, window=3600)     # per IP
login_limit = community.RateLimiter(limit=10, window=300)        # per IP
post_limit = community.RateLimiter(limit=10, window=60)          # per user
like_limit = community.RateLimiter(limit=60, window=60)          # per user
avatar_limit = community.RateLimiter(limit=10, window=3600)      # per user


# --- request helpers ----------------------------------------------------------------

def client_ip(request: Request) -> str:
    # Behind Render's proxy the real client is the LAST X-Forwarded-For
    # entry (the one the proxy itself appended); earlier ones are whatever
    # the client chose to send, so they can't be trusted for rate limits.
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if forwarded:
        return forwarded[-1]
    return request.client.host if request.client else "unknown"


def same_origin(request: Request) -> None:
    """Blocks cross-site form posts. The session cookie is SameSite=Lax
    (not sent on cross-site POSTs) and every write takes a JSON body, so
    this is a second layer: reject any write whose Origin isn't this site."""
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="Cross-site request refused")


def current_user(request: Request) -> Optional[dict]:
    return community.user_for_token(request.cookies.get(COOKIE))


def require_user(user: Optional[dict] = Depends(current_user)) -> dict:
    if user is None:
        raise HTTPException(status_code=401, detail="Log in first")
    return user


def require_admin(user: dict = Depends(require_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admins only")
    return user


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        COOKIE, token, max_age=community.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=https, path="/",
    )


def avatar_url(username: str, updated_at: Optional[datetime]) -> Optional[str]:
    # Versioned by upload time, so the image itself can be cached for good.
    return f"/api/avatars/{username}?v={int(updated_at.timestamp())}" if updated_at else None


def _user_out(user: dict) -> UserOut:
    return UserOut(username=user["username"], is_admin=community.is_admin(user["username"]),
                   avatar_url=avatar_url(user["username"], community.avatar_updated_at(user["id"])))


# --- accounts ------------------------------------------------------------------------

@router.get("/api/auth/me", response_model=MeOut)
def me(user: Optional[dict] = Depends(current_user)):
    return MeOut(user=_user_out(user) if user else None)


@router.post("/api/auth/register", response_model=UserOut, dependencies=[Depends(same_origin)])
def register(payload: AuthIn, request: Request, response: Response):
    if not register_limit.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many sign-ups from here - try again later")
    username = payload.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Username must be 3-20 letters, numbers or underscores")
    if not 8 <= len(payload.password) <= 128:
        raise HTTPException(status_code=400, detail="Password must be 8-128 characters")
    try:
        user = community.create_user(username, payload.password)
    except community.UsernameTaken:
        raise HTTPException(status_code=409, detail="That username is taken")
    _set_session_cookie(request, response, community.create_session(user["id"]))
    return _user_out(user)


@router.post("/api/auth/login", response_model=UserOut, dependencies=[Depends(same_origin)])
def login(payload: AuthIn, request: Request, response: Response):
    if not login_limit.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many attempts - wait a few minutes")
    user = community.authenticate(payload.username.strip(), payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Wrong username or password")
    _set_session_cookie(request, response, community.create_session(user["id"]))
    return _user_out(user)


@router.post("/api/auth/logout", dependencies=[Depends(same_origin)])
def logout(request: Request, response: Response):
    community.delete_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# --- profile pictures -------------------------------------------------------------------

@router.put("/api/me/avatar", response_model=UserOut, dependencies=[Depends(same_origin)])
async def upload_avatar(request: Request, user: dict = Depends(require_user)):
    # The raw image is the request body. A cross-site page can't send an
    # image/* body without a CORS preflight (which fails), so together with
    # same_origin and the SameSite cookie this can't be triggered from
    # another site.
    image = await _read_image_upload(request, user)
    await run_in_threadpool(community.set_avatar, user["id"], image)
    return _user_out(user)


async def _read_image_upload(request: Request, user: dict) -> bytes:
    """The raw image request body, size-capped, re-encoded by avatars.py."""
    if not request.headers.get("content-type", "").startswith("image/"):
        raise HTTPException(status_code=415, detail="Upload an image file")
    if not avatar_limit.allow(f"user:{user['id']}"):
        raise HTTPException(status_code=429, detail="Too many picture changes - try again in an hour")
    too_big = HTTPException(status_code=413, detail="That image is over 5 MB")
    if int(request.headers.get("content-length") or 0) > avatars.MAX_UPLOAD_BYTES:
        raise too_big
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > avatars.MAX_UPLOAD_BYTES:
            raise too_big
    try:
        return await run_in_threadpool(avatars.process, bytes(body))
    except avatars.BadImage as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/me/avatar", response_model=UserOut, dependencies=[Depends(same_origin)])
def remove_avatar(user: dict = Depends(require_user)):
    community.delete_avatar(user["id"])
    return _user_out(user)


@router.get("/api/avatars/{username}")
def get_avatar(username: str):
    image = community.get_avatar(username)
    if image is None:
        raise HTTPException(status_code=404, detail="No picture")
    return Response(content=image, media_type="image/webp", headers={
        "Cache-Control": "public, max-age=31536000, immutable",  # URLs carry ?v=<upload time>
        "X-Content-Type-Options": "nosniff",
    })


# --- character pictures (admin replacements) -------------------------------------------

def character_image_url(char_id: int, updated_at: Optional[datetime]) -> Optional[str]:
    return f"/api/character-images/{char_id}?v={int(updated_at.timestamp())}" if updated_at else None


@router.put("/api/characters/{char_id}/image", dependencies=[Depends(same_origin)])
async def replace_character_image(char_id: int, request: Request, admin: dict = Depends(require_admin)):
    if db.get_character_by_id(char_id) is None:
        raise HTTPException(status_code=404, detail=f"No character with id {char_id}")
    image = await _read_image_upload(request, admin)
    await run_in_threadpool(community.set_character_image, char_id, image, admin["id"])
    return {"image_url": character_image_url(char_id, community.character_image_versions().get(char_id))}


@router.delete("/api/characters/{char_id}/image", dependencies=[Depends(same_origin)])
def reset_character_image(char_id: int, admin: dict = Depends(require_admin)):
    community.delete_character_image(char_id)
    return {"ok": True}


@router.get("/api/character-images/{char_id}")
def get_character_image(char_id: int):
    image = community.get_character_image(char_id)
    if image is None:
        raise HTTPException(status_code=404, detail="No replacement picture")
    return Response(content=image, media_type="image/webp", headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })


# --- admin overrules --------------------------------------------------------------------

def override_out(char_a: int, char_b: int, form_a: str, form_b: str) -> Optional[OverrideOut]:
    ov = community.get_override(char_a, char_b, form_a, form_b)
    if ov is None:
        return None
    winner_name = characters.display_name_for_id(ov["winner_id"]) or "Unknown"
    return OverrideOut(winner_id=ov["winner_id"], winner_name=winner_name, note=ov["note"],
                       admin=ov["admin"], created_at=ov["created_at"])


@router.post("/api/overrides", response_model=OverrideOut, dependencies=[Depends(same_origin)])
def set_override(payload: OverrideIn, admin: dict = Depends(require_admin)):
    if payload.char_a == payload.char_b:
        raise HTTPException(status_code=400, detail="A matchup needs two different characters")
    if payload.winner_id not in (payload.char_a, payload.char_b):
        raise HTTPException(status_code=400, detail="The winner must be one of the two characters")
    for cid in (payload.char_a, payload.char_b):
        if db.get_character_by_id(cid) is None:
            raise HTTPException(status_code=404, detail=f"No character with id {cid}")
    note = payload.note.strip()
    before = community.get_override(payload.char_a, payload.char_b, payload.form_a, payload.form_b)
    community.set_override(payload.char_a, payload.char_b, payload.form_a, payload.form_b,
                           payload.winner_id, note, admin["id"])
    # Every ruling goes straight onto the board - unless this save changed
    # nothing (same winner, same reason), which would only be a duplicate.
    if before is None or before["winner_id"] != payload.winner_id or before["note"] != note:
        community.create_post(admin["id"], note, None, payload.char_a, payload.char_b,
                              payload.form_a, payload.form_b, kind="overrule", ruling_winner=payload.winner_id)
    return override_out(payload.char_a, payload.char_b, payload.form_a, payload.form_b)


@router.delete("/api/overrides", dependencies=[Depends(same_origin)])
def remove_override(a: int, b: int, fa: str, fb: str, admin: dict = Depends(require_admin)):
    if not community.delete_override(a, b, fa, fb):
        raise HTTPException(status_code=404, detail="No overrule on that matchup")
    return {"ok": True}


# --- message board ------------------------------------------------------------------------

def _matchup_out(row: dict, cache: dict) -> Optional[MatchupOut]:
    if row["char_a"] is None or row["char_b"] is None:
        return None
    key = (row["char_a"], row["char_b"], row["form_a"], row["form_b"])
    if key in cache:
        return cache[key]
    try:
        v = characters.run_compare(row["char_a"], row["char_b"], row["form_a"], row["form_b"])
    except ValueError:
        cache[key] = None  # a character was removed since the post was made
        return None
    ov = community.get_override(row["char_a"], row["char_b"], v.form_a, v.form_b)
    label_a, label_b = _bare(v.character_a), _bare(v.character_b)
    if v.composite is None:
        calc = "Not enough data for a verdict"
    elif v.favored:
        calc = f"{label_a if v.favored == v.character_a else label_b} favored — {v.label}"
    else:
        calc = v.label
    winner = winner_id = None
    if ov is not None:
        winner_id = ov["winner_id"]
        winner = label_a if winner_id == row["char_a"] else label_b
    cat_a = (db.get_character_by_id(row["char_a"]) or {}).get("category") or ""
    cat_b = (db.get_character_by_id(row["char_b"]) or {}).get("category") or ""
    cache[key] = MatchupOut(
        char_a=row["char_a"], char_b=row["char_b"], form_a=v.form_a, form_b=v.form_b,
        name_a=v.character_a, name_b=v.character_b, label_a=label_a, label_b=label_b,
        category_a=cat_a, category_b=cat_b, calc_verdict=calc, overruled_winner=winner,
        overruled_winner_id=winner_id,
    )
    return cache[key]


def _bare(name: str) -> str:
    """"Dante (Devil May Cry)" / "Genos, Demon Cyborg" -> "Dante" / "Genos"."""
    return re.sub(r"\s*\([^)]*\)", "", characters.short_name(name)).strip() or name


@router.get("/api/matchups/preview", response_model=MatchupOut)
def matchup_preview(a: int, b: int, fa: Optional[str] = None, fb: Optional[str] = None):
    """The matchup card a post would carry, built exactly as the feed builds
    it - so the board's composer can show it before anything is posted."""
    if a == b:
        raise HTTPException(status_code=400, detail="Pick two different characters")
    m = _matchup_out({"char_a": a, "char_b": b, "form_a": fa, "form_b": fb}, {})
    if m is None:
        raise HTTPException(status_code=404, detail="Character or form not found")
    return m


def _ruling_out(row: dict, matchup: Optional[MatchupOut]) -> Optional[RulingOut]:
    if row.get("kind") != "overrule" or matchup is None or row.get("ruling_winner") is None:
        return None
    winner = matchup.label_a if row["ruling_winner"] == matchup.char_a else matchup.label_b
    if matchup.overruled_winner_id is None:
        status = "lifted"
    elif matchup.overruled_winner_id != row["ruling_winner"]:
        status = "changed"
    else:
        status = "current"
    return RulingOut(winner=winner, status=status)


def _post_out(row: dict, viewer: Optional[dict], cache: dict) -> PostOut:
    can_delete = viewer is not None and (viewer["id"] == row["user_id"] or viewer["is_admin"])
    matchup = _matchup_out(row, cache)
    return PostOut(
        id=row["id"], parent_id=row["parent_id"], author=row["username"],
        author_is_admin=community.is_admin(row["username"]),
        author_avatar=avatar_url(row["username"], row.get("avatar_at")), kind=row.get("kind"),
        ruling=_ruling_out(row, matchup), body=row["body"],
        created_at=row["created_at"], like_count=row["like_count"], reply_count=row["reply_count"],
        liked_by_me=row["liked_by_me"], can_delete=can_delete, matchup=matchup,
    )


@router.get("/api/posts", response_model=PostListOut)
def list_posts(before: Optional[int] = None, limit: int = 20, viewer: Optional[dict] = Depends(current_user)):
    limit = max(1, min(limit, 50))
    rows = community.list_posts(viewer["id"] if viewer else None, before, limit)
    cache: dict = {}
    return PostListOut(
        posts=[_post_out(r, viewer, cache) for r in rows],
        next_before=rows[-1]["id"] if len(rows) == limit else None,
    )


@router.get("/api/posts/{post_id}", response_model=ThreadOut)
def get_thread(post_id: int, viewer: Optional[dict] = Depends(current_user)):
    thread = community.get_thread(post_id, viewer["id"] if viewer else None)
    if thread is None:
        raise HTTPException(status_code=404, detail="Post not found")
    cache: dict = {}
    return ThreadOut(post=_post_out(thread["post"], viewer, cache),
                     replies=[_post_out(r, viewer, cache) for r in thread["replies"]])


@router.post("/api/posts", response_model=PostOut, dependencies=[Depends(same_origin)])
def create_post(payload: PostIn, user: dict = Depends(require_user)):
    if not post_limit.allow(f"user:{user['id']}"):
        raise HTTPException(status_code=429, detail="You're posting too fast - wait a minute")
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Write something first")
    if len(body) > MAX_POST_CHARS:
        raise HTTPException(status_code=400, detail=f"Posts are limited to {MAX_POST_CHARS} characters")
    has_matchup = payload.char_a is not None and payload.char_b is not None
    if has_matchup:
        if payload.char_a == payload.char_b:
            raise HTTPException(status_code=400, detail="Pick two different characters")
        for cid in (payload.char_a, payload.char_b):
            if db.get_character_by_id(cid) is None:
                raise HTTPException(status_code=404, detail=f"No character with id {cid}")
    try:
        post_id = community.create_post(
            user["id"], body, payload.parent_id,
            payload.char_a if has_matchup else None, payload.char_b if has_matchup else None,
            payload.form_a if has_matchup else None, payload.form_b if has_matchup else None,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="The post you're replying to no longer exists")
    return _post_out(community.get_post_view(post_id, user["id"]), user, {})


@router.delete("/api/posts/{post_id}", dependencies=[Depends(same_origin)])
def delete_post(post_id: int, user: dict = Depends(require_user)):
    post = community.get_post(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post["user_id"] != user["id"] and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="You can only delete your own posts")
    community.delete_post(post_id)
    return {"ok": True}


@router.post("/api/posts/{post_id}/like", response_model=LikeOut, dependencies=[Depends(same_origin)])
def like_post(post_id: int, user: dict = Depends(require_user)):
    if not like_limit.allow(f"user:{user['id']}"):
        raise HTTPException(status_code=429, detail="Slow down a little")
    if community.get_post(post_id) is None:
        raise HTTPException(status_code=404, detail="Post not found")
    liked = community.toggle_like(post_id, user["id"])
    return LikeOut(liked=liked, like_count=community.get_post_view(post_id, user["id"])["like_count"])
