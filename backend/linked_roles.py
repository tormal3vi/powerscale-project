"""Discord Linked Roles: server roles that unlock from Powerscale stats.

A server owner sets up a role in Server Settings -> Roles -> Links, picks
Powerscale and a requirement ("Duel wins at least 50"); members claim it
from the server's Linked Roles menu. Discord sends them to the
verification URL below, they sign in with Discord (and with Powerscale,
if they aren't), and the site tells Discord their numbers. It keeps them
current afterwards: when a duel finishes, and every few hours for the rest.

Signing in with Discord here also connects the two accounts, as /link does.

Settings (the host's environment, never committed):
  DISCORD_CLIENT_SECRET   OAuth2 -> Client Secret. Turns the feature on.
  (and DISCORD_APPLICATION_ID, DISCORD_BOT_TOKEN, as for the app)
On the app's page at discord.com/developers/applications:
  General Information -> Linked Roles Verification URL:
      https://powerscale.online/discord/linked-role
  OAuth2 -> Redirects:
      https://powerscale.online/discord/linked-role/callback

The OAuth tokens are stored encrypted (a key derived from the client
secret), and deleted with the account or when Discord is disconnected.
"""

import hashlib
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from html import escape
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, insert, select, update

from backend import community, titles
from backend.community import _aware, _now, discord_role_tokens, users
from backend.community_api import current_user

router = APIRouter()

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://powerscale.online, 1.0)"
# What a role can require (at most 5). Types: 2 = number at least, 7 = yes/no.
METADATA = [
    {"key": "duel_wins", "name": "Duel wins", "description": "Duels won on Powerscale (at least this many)", "type": 2},
    {"key": "overrules", "name": "Approved overrules",
     "description": "Overrule suggestions the admins approved (at least this many)", "type": 2},
    {"key": "weekly_champion", "name": "Weekly champion", "description": "Last week's #1 on the duel leaderboard", "type": 7},
    {"key": "founder", "name": "Founder", "description": "One of Powerscale's first 100 accounts", "type": 7},
]
REFRESH_HOURS = 6
STATE_MINUTES = 10

_states: Dict[str, Tuple[int, float]] = {}  # OAuth state -> (Powerscale user id, expiry)
_states_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="linked-roles")


def _setting(name: str) -> str:
    return os.environ.get(name, "").strip()


def enabled() -> bool:
    return bool(_setting("DISCORD_CLIENT_SECRET") and _setting("DISCORD_APPLICATION_ID"))


def _redirect_uri(request: Request) -> str:
    from backend.main import _base_url  # the https address behind Render's proxy
    return f"{_base_url(request)}/discord/linked-role/callback"


# --- tokens at rest ----------------------------------------------------------------------

def _box():
    from nacl.secret import SecretBox
    key = hashlib.sha256(("powerscale-linked-roles:" + _setting("DISCORD_CLIENT_SECRET")).encode()).digest()
    return SecretBox(key)


def _seal(token: str) -> str:
    return _box().encrypt(token.encode()).hex()


def _open(sealed: str) -> Optional[str]:
    try:
        return _box().decrypt(bytes.fromhex(sealed)).decode()
    except Exception:  # noqa: BLE001 - the secret changed: the token is useless
        return None


# --- Discord calls ------------------------------------------------------------------------

_registration: dict = {"status": "not tried yet"}  # what Discord said, for /discord/linked-role/status


def register_metadata() -> None:
    """Tells Discord which numbers roles can check (on startup; idempotent)."""
    app_id, token = _setting("DISCORD_APPLICATION_ID"), _setting("DISCORD_BOT_TOKEN")
    if not enabled():
        return
    if not token:
        _registration.update(status="no DISCORD_BOT_TOKEN set")
        return
    try:
        r = requests.put(f"{API}/applications/{app_id}/role-connections/metadata", json=METADATA, timeout=15,
                         headers={"Authorization": f"Bot {token}", "User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        _registration.update(status=f"couldn't reach Discord ({type(exc).__name__})")
        return
    if r.ok:
        _registration.update(status="registered", fields=[m["key"] for m in r.json()])
    else:  # Discord's error says why (no secrets in it)
        _registration.update(status=f"Discord said {r.status_code}", detail=r.text[:500])


def _token_request(data: dict) -> Optional[dict]:
    body = {"client_id": _setting("DISCORD_APPLICATION_ID"), "client_secret": _setting("DISCORD_CLIENT_SECRET"), **data}
    try:
        r = requests.post(f"{API}/oauth2/token", data=body, timeout=15, headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        return None
    return r.json() if r.ok else None


def _metadata(username: str) -> dict:
    p = titles.progress(username)
    return {"duel_wins": p["wins"], "overrules": p["overrules"],
            "weekly_champion": 1 if p["champion"] else 0, "founder": 1 if p["founder"] else 0}


def _save_tokens(user_id: int, discord_id: str, grant: dict) -> None:
    values = {"discord_id": str(discord_id), "access_token": _seal(grant["access_token"]),
              "refresh_token": _seal(grant["refresh_token"]),
              "expires_at": _now() + timedelta(seconds=int(grant.get("expires_in", 604800)) - 60)}
    with community.engine.begin() as conn:
        if not conn.execute(update(discord_role_tokens).where(discord_role_tokens.c.user_id == user_id)
                            .values(**values, pushed=None)).rowcount:
            conn.execute(insert(discord_role_tokens).values(user_id=user_id, **values))


def push(user_id: int, force: bool = False) -> bool:
    """Sends Discord this user's current numbers (refreshing the token if it
    expired). Skips unchanged ones unless `force`. True when Discord took it."""
    with community.reader.connect() as conn:
        row = conn.execute(select(discord_role_tokens, users.c.username).join(
            users, users.c.id == discord_role_tokens.c.user_id).where(
            discord_role_tokens.c.user_id == user_id)).mappings().first()
    if row is None:
        return False
    meta = _metadata(row["username"])
    payload = json.dumps(meta, sort_keys=True)
    if not force and row["pushed"] == payload:
        return True
    access = _open(row["access_token"])
    if access is None or _aware(row["expires_at"]) <= _now():
        refresh = _open(row["refresh_token"])
        grant = _token_request({"grant_type": "refresh_token", "refresh_token": refresh}) if refresh else None
        if grant is None:  # revoked or expired for good: they can connect again from Discord
            with community.engine.begin() as conn:
                conn.execute(delete(discord_role_tokens).where(discord_role_tokens.c.user_id == user_id))
            return False
        _save_tokens(user_id, row["discord_id"], grant)
        access = grant["access_token"]
    try:
        r = requests.put(f"{API}/users/@me/applications/{_setting('DISCORD_APPLICATION_ID')}/role-connection",
                         json={"platform_name": "Powerscale", "platform_username": row["username"], "metadata": meta},
                         headers={"Authorization": f"Bearer {access}", "User-Agent": USER_AGENT}, timeout=15)
    except requests.RequestException:
        return False
    if r.status_code == 401:
        with community.engine.begin() as conn:
            conn.execute(delete(discord_role_tokens).where(discord_role_tokens.c.user_id == user_id))
        return False
    if r.ok:
        with community.engine.begin() as conn:
            conn.execute(update(discord_role_tokens).where(discord_role_tokens.c.user_id == user_id).values(pushed=payload))
    return r.ok


def refresh_later(user_ids) -> None:
    """After a duel finishes: its players' numbers, in the background."""
    if not enabled():
        return
    ids = [u for u in user_ids if u is not None]

    def run():
        titles.forget()
        for uid in ids:
            try:
                push(uid)
            except Exception:  # noqa: BLE001 - never let this break a duel
                pass
    _pool.submit(run)


def start() -> None:
    """On startup: register the metadata, then every few hours bring
    everyone's numbers up to date (overrules, the weekly champion)."""
    if not enabled():
        return

    def loop():
        register_metadata()
        while True:
            time.sleep(REFRESH_HOURS * 3600)
            try:
                with community.reader.connect() as conn:
                    ids = [r[0] for r in conn.execute(select(discord_role_tokens.c.user_id))]
                titles.forget()
                for uid in ids:
                    push(uid)
            except Exception:  # noqa: BLE001 - try again next time
                pass
    threading.Thread(target=loop, name="linked-roles", daemon=True).start()


# --- the verification URL ------------------------------------------------------------------

def _page(title: str, text: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(status_code=status, content=f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)} — Powerscale</title>
<link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Public+Sans:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="/styles.css">
<meta name="robots" content="noindex"></head><body><div class="page"><main class="page-content lr-page">
<div class="lr-card"><img src="/favicon.svg?v=2" alt="" width="48" height="48"><h1 class="lr-title">{escape(title)}</h1>
<p class="lr-text">{text}</p><a class="btn-gold" href="/">Go to Powerscale</a></div></main></div></body></html>""")


@router.get("/discord/linked-role/status", include_in_schema=False)
def status():
    """Whether Linked Roles is on and Discord took the stats it can check."""
    return {"enabled": enabled(), "metadata": _registration}


@router.get("/discord/linked-role", include_in_schema=False)
def start_linking(request: Request, user: Optional[dict] = Depends(current_user)):
    if not enabled():
        return _page("Not set up yet", "Linked roles aren't switched on for this site yet.", 503)
    if user is None:
        return RedirectResponse("/login.html?" + urlencode({"next": "/discord/linked-role"}), status_code=302)
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _states_lock:
        for old in [s for s, (_, exp) in _states.items() if exp < now]:
            del _states[old]
        _states[state] = (user["id"], now + STATE_MINUTES * 60)
    query = urlencode({"client_id": _setting("DISCORD_APPLICATION_ID"), "redirect_uri": _redirect_uri(request),
                       "response_type": "code", "scope": "role_connections.write identify", "state": state,
                       "prompt": "consent"})
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{query}", status_code=302)


@router.get("/discord/linked-role/callback", include_in_schema=False)
def finish_linking(request: Request, code: str = "", state: str = "", error: str = "",
                   user: Optional[dict] = Depends(current_user)):
    with _states_lock:
        owner = _states.pop(state, None)
    if error:
        return _page("Not connected", "You didn't allow it on Discord, so nothing changed.")
    if owner is None or owner[1] < time.time() or user is None or owner[0] != user["id"] or not code:
        return _page("Link expired", "That link expired or was opened in another browser. Start again from Discord "
                     "(Server Settings or the server's Linked Roles menu).", 400)
    grant = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(request)})
    if grant is None or "role_connections.write" not in (grant.get("scope") or ""):
        return _page("Couldn't connect", "Discord didn't accept the sign-in. Please try again.", 502)
    try:
        me = requests.get(f"{API}/users/@me", timeout=15,
                          headers={"Authorization": f"Bearer {grant['access_token']}", "User-Agent": USER_AGENT}).json()
    except (requests.RequestException, ValueError):
        return _page("Couldn't connect", "Discord didn't answer. Please try again.", 502)
    if not me.get("id"):
        return _page("Couldn't connect", "Discord didn't say who you are. Please try again.", 502)
    community.set_discord(user["id"], me["id"], me.get("global_name") or me.get("username") or "Discord user")
    _save_tokens(user["id"], me["id"], grant)
    titles.forget()
    pushed = push(user["id"], force=True)
    name = escape(user["username"])
    if not pushed:
        return _page("Almost there", f"Connected to <b>{name}</b>, but Discord didn't take your stats yet. "
                     "They'll update within a few hours.")
    return _page("You're connected", f"Discord now knows <b>{name}</b>'s Powerscale stats, and your roles will "
                 "update as you win duels. You can close this tab and go back to Discord.")
