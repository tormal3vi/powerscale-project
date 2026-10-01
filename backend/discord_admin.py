"""/admin: describe channel, role and permission changes in plain words
and the bot makes them - after showing exactly what it will do and
waiting for a Confirm.

  /admin request: "make #updates read-only for everyone except Admins"

1. Discord gets a "thinking..." reply at once (it allows 3 seconds; the
   model takes longer). In the background the bot reads the server's
   channels and roles, and Claude turns the request into a list of
   actions.
2. The actions are checked against the server (do those channels and
   roles exist, are those real permissions) and written out from the
   server's own names, not the model's - that preview is what the admin
   approves. Granting Administrator is never planned: do that by hand.
3. Confirm applies them one by one, each with an audit log entry naming
   who asked; Cancel drops the plan. Only the person who asked can press
   either, and a plan lasts 14 minutes (Discord's reply token lasts 15).

Only server members with Administrator see or can use the command, and
the bot can't do more than its own role allows (Manage Channels, Manage
Roles, and only roles below its own).

Settings (the host's environment, never committed):
  ANTHROPIC_API_KEY   console.anthropic.com -> API keys. Without it
                      /admin says it isn't set up.
  ADMIN_BOT_MODEL     optional; the Claude model to use.
"""

import json
import os
import secrets
import threading
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

import requests

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://powerscale.online, 1.0)"
EPHEMERAL = 64
ADMINISTRATOR = 1 << 3
PLAN_SECONDS = 14 * 60
DEFAULT_MODEL = "claude-sonnet-5-5"

PERMISSIONS = {name: 1 << bit for bit, name in enumerate([
    "CREATE_INSTANT_INVITE", "KICK_MEMBERS", "BAN_MEMBERS", "ADMINISTRATOR", "MANAGE_CHANNELS", "MANAGE_GUILD",
    "ADD_REACTIONS", "VIEW_AUDIT_LOG", "PRIORITY_SPEAKER", "STREAM", "VIEW_CHANNEL", "SEND_MESSAGES",
    "SEND_TTS_MESSAGES", "MANAGE_MESSAGES", "EMBED_LINKS", "ATTACH_FILES", "READ_MESSAGE_HISTORY",
    "MENTION_EVERYONE", "USE_EXTERNAL_EMOJIS", "VIEW_GUILD_INSIGHTS", "CONNECT", "SPEAK", "MUTE_MEMBERS",
    "DEAFEN_MEMBERS", "MOVE_MEMBERS", "USE_VAD", "CHANGE_NICKNAME", "MANAGE_NICKNAMES", "MANAGE_ROLES",
    "MANAGE_WEBHOOKS", "MANAGE_GUILD_EXPRESSIONS", "USE_APPLICATION_COMMANDS", "REQUEST_TO_SPEAK",
    "MANAGE_EVENTS", "MANAGE_THREADS", "CREATE_PUBLIC_THREADS", "CREATE_PRIVATE_THREADS",
    "USE_EXTERNAL_STICKERS", "SEND_MESSAGES_IN_THREADS", "USE_EMBEDDED_ACTIVITIES", "MODERATE_MEMBERS",
    "VIEW_CREATOR_MONETIZATION_ANALYTICS", "USE_SOUNDBOARD", "CREATE_GUILD_EXPRESSIONS", "CREATE_EVENTS",
    "USE_EXTERNAL_SOUNDS", "SEND_VOICE_MESSAGES"])}
PERMISSIONS.update(SEND_POLLS=1 << 49, USE_EXTERNAL_APPS=1 << 50)

CHANNEL_TYPES = {"text": 0, "voice": 2, "category": 4, "announcement": 5, "stage": 13, "forum": 15}
TYPE_NAMES = {v: k for k, v in CHANNEL_TYPES.items()}

COMMAND = {
    "name": "admin", "description": "Describe channel, role or permission changes; the bot shows a plan to confirm.",
    "integration_types": [0], "contexts": [0], "default_member_permissions": str(ADMINISTRATOR),
    "options": [{"type": 3, "name": "request", "required": True, "max_length": 1500,
                 "description": "e.g. make #updates read-only for everyone except Admins"}],
}

_plans: Dict[str, dict] = {}
_lock = threading.Lock()


# --- talking to Discord ---------------------------------------------------------------------------

def _headers(reason: Optional[str] = None) -> dict:
    h = {"Authorization": f"Bot {os.environ.get('DISCORD_BOT_TOKEN', '').strip()}", "User-Agent": USER_AGENT}
    if reason:
        h["X-Audit-Log-Reason"] = urllib.parse.quote(reason[:400])
    return h


def _discord(method: str, path: str, body=None, reason: Optional[str] = None):
    r = requests.request(method, API + path, headers=_headers(reason), json=body, timeout=20)
    if r.status_code == 429:  # rate limited: wait as told, once
        time.sleep(min(float((r.json() or {}).get("retry_after", 1)), 10))
        r = requests.request(method, API + path, headers=_headers(reason), json=body, timeout=20)
    if not r.ok:
        try:
            detail = r.json().get("message") or r.text
        except ValueError:
            detail = r.text
        raise RuntimeError(f"Discord said: {detail[:200]}")
    return r.json() if r.content else None


def _edit_reply(interaction: dict, message: dict) -> None:
    url = f"{API}/webhooks/{interaction['application_id']}/{interaction['token']}/messages/@original"
    try:
        requests.patch(url, json={"allowed_mentions": {"parse": []}, **message},
                       headers={"User-Agent": USER_AGENT}, timeout=20)
    except requests.RequestException:
        pass


# --- the server as the model sees it --------------------------------------------------------------

def _state(guild_id: str) -> dict:
    roles = _discord("GET", f"/guilds/{guild_id}/roles")
    channels = _discord("GET", f"/guilds/{guild_id}/channels")
    return {"guild_id": guild_id,
            "roles": {r["id"]: r for r in roles},
            "channels": {c["id"]: c for c in channels}}


def _perm_names(bits) -> List[str]:
    bits = int(bits or 0)
    return [n for n, b in PERMISSIONS.items() if bits & b]


def _describe_server(state: dict) -> str:
    roles = sorted(state["roles"].values(), key=lambda r: -r["position"])
    lines = ["ROLES (highest first; @everyone's id is the server id):"]
    for r in roles:
        extra = " [managed by an integration]" if r.get("managed") else ""
        lines.append(f"- {r['name']} id={r['id']} position={r['position']}{extra} "
                     f"permissions={','.join(_perm_names(r['permissions'])) or 'none'}")
    lines.append("\nCHANNELS:")
    for c in sorted(state["channels"].values(), key=lambda c: (c.get("parent_id") or "", c.get("position", 0))):
        kind = TYPE_NAMES.get(c["type"], f"type {c['type']}")
        parent = state["channels"].get(c.get("parent_id") or "")
        head = f"- #{c['name']} id={c['id']} {kind}" + (f" in category '{parent['name']}'" if parent else "")
        if c.get("topic"):
            head += f" topic={json.dumps(c['topic'][:120])}"
        lines.append(head)
        for o in c.get("permission_overwrites") or []:
            who = state["roles"].get(o["id"], {}).get("name", "?") if o["type"] == 0 else f"member {o['id']}"
            lines.append(f"    overwrite {who} (id={o['id']}): allow={','.join(_perm_names(o['allow'])) or '-'} "
                         f"deny={','.join(_perm_names(o['deny'])) or '-'}")
    return "\n".join(lines)


PLAN_TOOL = {
    "name": "propose_plan",
    "description": "The changes to make on the Discord server, in order. An admin reviews them before anything happens.",
    "input_schema": {
        "type": "object", "required": ["actions"],
        "properties": {
            "note": {"type": "string", "description": "Anything the admin should know: assumptions made, or why "
                                                      "something asked for can't or shouldn't be done. Short."},
            "actions": {"type": "array", "items": {
                "type": "object", "required": ["op"],
                "properties": {
                    "op": {"type": "string", "enum": ["create_channel", "edit_channel", "delete_channel",
                                                      "set_permissions", "remove_overwrite",
                                                      "create_role", "edit_role", "delete_role"]},
                    "ref": {"type": "string", "description": "create_channel/create_role: a short label so later "
                                                             "actions can point at it as 'new:<ref>'"},
                    "channel_id": {"type": "string", "description": "an existing channel's id, or 'new:<ref>'"},
                    "role_id": {"type": "string", "description": "an existing role's id, or 'new:<ref>'"},
                    "target_id": {"type": "string", "description": "set_permissions/remove_overwrite: the role "
                                                                   "(or 'new:<ref>') or member id the overwrite is for"},
                    "target_type": {"type": "string", "enum": ["role", "member"]},
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": list(CHANNEL_TYPES)},
                    "parent_id": {"type": "string", "description": "a category's id or 'new:<ref>'; "
                                                                   "'none' takes a channel out of its category"},
                    "topic": {"type": "string"},
                    "slowmode_seconds": {"type": "integer", "minimum": 0, "maximum": 21600},
                    "nsfw": {"type": "boolean"},
                    "sync_with_category": {"type": "boolean", "description": "edit_channel: copy the category's "
                                                                             "overwrites onto the channel"},
                    "allow": {"type": "array", "items": {"type": "string", "enum": list(PERMISSIONS)},
                              "description": "set_permissions: explicitly allow; create_role: its permissions; "
                                             "edit_role: permissions to add"},
                    "deny": {"type": "array", "items": {"type": "string", "enum": list(PERMISSIONS)},
                             "description": "set_permissions: explicitly deny; edit_role: permissions to remove"},
                    "neutral": {"type": "array", "items": {"type": "string", "enum": list(PERMISSIONS)},
                                "description": "set_permissions: back to inherited (neither allowed nor denied)"},
                    "color": {"type": "string", "description": "roles: hex like #D9A441"},
                    "hoist": {"type": "boolean", "description": "roles: shown separately in the member list"},
                    "mentionable": {"type": "boolean"},
                }}},
        }},
}

SYSTEM = """You plan changes to a Discord server for its administrator. You get the server's current roles and
channels and a request in plain words, and you answer only by calling propose_plan.

- Use the ids given. To act on something created earlier in the same plan, give it a ref and use 'new:<ref>'.
- set_permissions changes only the permissions you list: others in that overwrite stay as they are.
- "Read-only" usually means: deny SEND_MESSAGES, SEND_MESSAGES_IN_THREADS, CREATE_PUBLIC_THREADS,
  CREATE_PRIVATE_THREADS and ADD_REACTIONS for @everyone, and allow SEND_MESSAGES for the roles that should post.
  "Hidden"/"private" means deny VIEW_CHANNEL for @everyone and allow it for the roles that should see it.
- Never grant ADMINISTRATOR, and don't touch roles marked managed. Don't delete anything that wasn't clearly asked
  to be deleted.
- Do the smallest set of changes that does what was asked. If the request is unclear or impossible, return no
  actions and explain in note."""


def _plan_with_claude(request_text: str, state: dict) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
        "model": os.environ.get("ADMIN_BOT_MODEL", "").strip() or DEFAULT_MODEL,
        "max_tokens": 4096, "system": SYSTEM, "tools": [PLAN_TOOL],
        "tool_choice": {"type": "tool", "name": "propose_plan"},
        "messages": [{"role": "user", "content": f"{_describe_server(state)}\n\nREQUEST:\n{request_text}"}]})
    if not r.ok:
        raise RuntimeError(f"Claude API error {r.status_code}")
    for block in r.json().get("content") or []:
        if block.get("type") == "tool_use":
            return block.get("input") or {}
    raise RuntimeError("Claude didn't return a plan")


# --- checking and describing a plan ---------------------------------------------------------------

def _check(actions: List[dict], state: dict) -> Tuple[List[dict], List[str]]:
    """(the actions that can be done, why the others can't)."""
    ok, problems = [], []
    new_channels, new_roles = set(), set()

    def channel(cid, kinds=None):
        if cid and cid.startswith("new:"):
            return cid[4:] in new_channels
        c = state["channels"].get(cid or "")
        return c is not None and (kinds is None or c["type"] in kinds)

    def role(rid):
        return rid[4:] in new_roles if rid and rid.startswith("new:") else rid in state["roles"]

    for i, a in enumerate(actions, 1):
        op = a.get("op")
        why = None
        if op in ("create_channel", "create_role", "edit_role") and "ADMINISTRATOR" in (a.get("allow") or []):
            why = "won't grant Administrator - do that by hand"
        elif op == "set_permissions" and "ADMINISTRATOR" in (a.get("allow") or []):
            why = "won't grant Administrator - do that by hand"
        elif op == "create_channel":
            if not a.get("name"):
                why = "no name"
            elif a.get("parent_id") and a["parent_id"] != "none" and not channel(a["parent_id"], {4}):
                why = "that category doesn't exist"
            elif a.get("ref"):
                new_channels.add(a["ref"])
        elif op in ("edit_channel", "delete_channel", "set_permissions", "remove_overwrite"):
            if not channel(a.get("channel_id")):
                why = "that channel doesn't exist"
            elif op == "edit_channel" and a.get("parent_id") not in (None, "none") and not channel(a["parent_id"], {4}):
                why = "that category doesn't exist"
            elif op in ("set_permissions", "remove_overwrite"):
                if a.get("target_type", "role") == "role" and not role(a.get("target_id")):
                    why = "that role doesn't exist"
                elif not a.get("target_id"):
                    why = "no role or member given"
        elif op == "create_role":
            if not a.get("name"):
                why = "no name"
            elif a.get("ref"):
                new_roles.add(a["ref"])
        elif op in ("edit_role", "delete_role"):
            rid = a.get("role_id")
            if not role(rid):
                why = "that role doesn't exist"
            elif rid == state["guild_id"] and op == "delete_role":
                why = "@everyone can't be deleted"
            elif state["roles"].get(rid, {}).get("managed"):
                why = "that role belongs to an integration"
        else:
            why = f"unknown action {op!r}"
        if why:
            problems.append(f"Step {i} skipped: {why}.")
        else:
            ok.append(a)
    return ok, problems


def _name_of(state: dict, refs: dict, cid: Optional[str], role_like: bool = False, kind: str = "role") -> str:
    if not cid:
        return "?"
    if cid.startswith("new:"):
        label = refs.get(cid, cid[4:])
        return f"#{label} (new)" if not role_like else f"@{label} (new)"
    if role_like:
        if kind == "member":
            return f"<@{cid}>"
        r = state["roles"].get(cid)
        return "@everyone" if cid == state["guild_id"] else (f"@{r['name']}" if r else cid)
    c = state["channels"].get(cid)
    return f"#{c['name']}" if c else cid


def _pretty_perms(names) -> str:
    return ", ".join(n.replace("_", " ").title() for n in names or [])


def describe(a: dict, state: dict, refs: dict) -> str:
    op = a["op"]
    ch = lambda key="channel_id": _name_of(state, refs, a.get(key))  # noqa: E731
    if op == "create_channel":
        where = f" in {ch('parent_id')}" if a.get("parent_id") not in (None, "none") else ""
        return f"Create {a.get('type', 'text')} channel **#{a['name']}**{where}" + \
            (f" — topic: {a['topic'][:80]}" if a.get("topic") else "")
    if op == "edit_channel":
        parts = []
        if a.get("name"):
            parts.append(f"rename to **#{a['name']}**")
        if "topic" in a:
            parts.append(f"topic: {a['topic'][:80] or '(none)'}")
        if a.get("parent_id"):
            parts.append("take out of its category" if a["parent_id"] == "none" else f"move into {ch('parent_id')}")
        if "slowmode_seconds" in a:
            parts.append(f"slowmode {a['slowmode_seconds']}s")
        if "nsfw" in a:
            parts.append("age-restricted" if a["nsfw"] else "not age-restricted")
        if a.get("sync_with_category"):
            parts.append("sync permissions with its category")
        return f"Edit **{ch()}**: " + ("; ".join(parts) or "no changes")
    if op == "delete_channel":
        return f"⚠️ **Delete {ch()}** (its messages are gone for good)"
    if op in ("set_permissions", "remove_overwrite"):
        who = _name_of(state, refs, a.get("target_id"), role_like=True, kind=a.get("target_type", "role"))
        if op == "remove_overwrite":
            return f"In **{ch()}**, remove the permission override for **{who}**"
        parts = [f"allow {_pretty_perms(a.get('allow'))}" if a.get("allow") else "",
                 f"deny {_pretty_perms(a.get('deny'))}" if a.get("deny") else "",
                 f"reset {_pretty_perms(a.get('neutral'))}" if a.get("neutral") else ""]
        return f"In **{ch()}**, for **{who}**: " + "; ".join(p for p in parts if p)
    role = _name_of(state, refs, a.get("role_id"), role_like=True)
    if op == "create_role":
        extra = [f"color {a['color']}" if a.get("color") else "", "shown separately" if a.get("hoist") else "",
                 f"permissions: {_pretty_perms(a.get('allow'))}" if a.get("allow") else ""]
        return f"Create role **@{a['name']}**" + "".join(f"; {e}" for e in extra if e)
    if op == "edit_role":
        parts = [f"rename to **@{a['name']}**" if a.get("name") else "",
                 f"color {a['color']}" if a.get("color") else "",
                 ("shown separately" if a["hoist"] else "not shown separately") if "hoist" in a else "",
                 ("mentionable" if a["mentionable"] else "not mentionable") if "mentionable" in a else "",
                 f"add {_pretty_perms(a.get('allow'))}" if a.get("allow") else "",
                 f"remove {_pretty_perms(a.get('deny'))}" if a.get("deny") else ""]
        return f"Edit role **{role}**: " + ("; ".join(p for p in parts if p) or "no changes")
    if op == "delete_role":
        return f"⚠️ **Delete role {role}**"
    return op


def _refs(actions: List[dict]) -> dict:
    return {f"new:{a['ref']}": a.get("name") or a["ref"] for a in actions if a.get("ref")}


def _plan_message(plan: dict) -> dict:
    lines = [f"**You asked:** {plan['request'][:300]}", ""]
    if plan["actions"]:
        lines += [f"`{i}.` {describe(a, plan['state'], plan['refs'])}" for i, a in enumerate(plan["actions"], 1)]
    else:
        lines.append("Nothing to do.")
    if plan["problems"]:
        lines += [""] + [f"❌ {p}" for p in plan["problems"]]
    if plan.get("note"):
        lines += ["", f"📝 {plan['note'][:600]}"]
    text = "\n".join(lines)
    message = {"embeds": [{"title": "Plan — nothing changes until you confirm", "color": 0xD9A441,
                           "description": text[:4000],
                           "footer": {"text": "Only you can see this · expires in 14 minutes"}}]}
    if plan["actions"]:
        message["components"] = [{"type": 1, "components": [
            {"type": 2, "style": 3, "label": f"Confirm ({len(plan['actions'])})", "custom_id": f"admin:yes:{plan['id']}"},
            {"type": 2, "style": 2, "label": "Cancel", "custom_id": f"admin:no:{plan['id']}"}]}]
    else:
        message["components"] = []
    return message


# --- doing it ------------------------------------------------------------------------------------

def _bits(names) -> int:
    total = 0
    for n in names or []:
        total |= PERMISSIONS.get(n, 0)
    return total


def _color(value: Optional[str]) -> Optional[int]:
    try:
        return int(str(value).lstrip("#"), 16) if value else None
    except ValueError:
        return None


def _apply_one(a: dict, guild_id: str, made: dict, reason: str) -> None:
    def real(x):
        return made.get(x, x) if x and x.startswith("new:") else x

    op = a["op"]
    if op == "create_channel":
        body = {"name": a["name"], "type": CHANNEL_TYPES.get(a.get("type") or "text", 0)}
        if a.get("parent_id") not in (None, "none"):
            body["parent_id"] = real(a["parent_id"])
        for key, field in (("topic", "topic"), ("slowmode_seconds", "rate_limit_per_user"), ("nsfw", "nsfw")):
            if key in a:
                body[field] = a[key]
        c = _discord("POST", f"/guilds/{guild_id}/channels", body, reason)
        if a.get("ref"):
            made[f"new:{a['ref']}"] = c["id"]
    elif op == "edit_channel":
        cid = real(a["channel_id"])
        body = {}
        for key, field in (("name", "name"), ("topic", "topic"), ("slowmode_seconds", "rate_limit_per_user"),
                           ("nsfw", "nsfw")):
            if key in a:
                body[field] = a[key]
        if a.get("parent_id"):
            body["parent_id"] = None if a["parent_id"] == "none" else real(a["parent_id"])
        if a.get("sync_with_category"):
            channel = _discord("GET", f"/channels/{cid}")
            parent_id = body.get("parent_id", channel.get("parent_id"))
            if parent_id:
                body["permission_overwrites"] = _discord("GET", f"/channels/{parent_id}").get("permission_overwrites", [])
        if body:
            _discord("PATCH", f"/channels/{cid}", body, reason)
    elif op == "delete_channel":
        _discord("DELETE", f"/channels/{real(a['channel_id'])}", None, reason)
    elif op == "set_permissions":
        cid, target = real(a["channel_id"]), real(a["target_id"])
        kind = 1 if a.get("target_type") == "member" else 0
        current = next((o for o in _discord("GET", f"/channels/{cid}").get("permission_overwrites") or []
                        if o["id"] == target), {"allow": "0", "deny": "0"})
        allow, deny, neutral = _bits(a.get("allow")), _bits(a.get("deny")), _bits(a.get("neutral"))
        new_allow = (int(current["allow"]) | allow) & ~deny & ~neutral
        new_deny = (int(current["deny"]) | deny) & ~allow & ~neutral
        _discord("PUT", f"/channels/{cid}/permissions/{target}",
                 {"type": kind, "allow": str(new_allow), "deny": str(new_deny)}, reason)
    elif op == "remove_overwrite":
        _discord("DELETE", f"/channels/{real(a['channel_id'])}/permissions/{real(a['target_id'])}", None, reason)
    elif op == "create_role":
        body = {"name": a["name"], "permissions": str(_bits(a.get("allow")))}
        for key in ("hoist", "mentionable"):
            if key in a:
                body[key] = a[key]
        if _color(a.get("color")) is not None:
            body["color"] = _color(a["color"])
        r = _discord("POST", f"/guilds/{guild_id}/roles", body, reason)
        if a.get("ref"):
            made[f"new:{a['ref']}"] = r["id"]
    elif op == "edit_role":
        rid = real(a["role_id"])
        body = {}
        if a.get("name"):
            body["name"] = a["name"]
        for key in ("hoist", "mentionable"):
            if key in a:
                body[key] = a[key]
        if _color(a.get("color")) is not None:
            body["color"] = _color(a["color"])
        if a.get("allow") or a.get("deny"):
            role = next((r for r in _discord("GET", f"/guilds/{guild_id}/roles") if r["id"] == rid), None)
            if role is None:
                raise RuntimeError("the role is gone")
            body["permissions"] = str((int(role["permissions"]) | _bits(a.get("allow"))) & ~_bits(a.get("deny")))
        if body:
            _discord("PATCH", f"/guilds/{guild_id}/roles/{rid}", body, reason)
    elif op == "delete_role":
        _discord("DELETE", f"/guilds/{guild_id}/roles/{real(a['role_id'])}", None, reason)


# --- the interaction ------------------------------------------------------------------------------

def _who(interaction: dict) -> Tuple[Optional[str], str]:
    user = (interaction.get("member") or {}).get("user") or interaction.get("user") or {}
    return (str(user["id"]) if user.get("id") else None), user.get("username") or "someone"


def _is_admin(interaction: dict) -> bool:
    try:
        return bool(int((interaction.get("member") or {}).get("permissions") or 0) & ADMINISTRATOR)
    except ValueError:
        return False


def _private(text: str) -> dict:
    return {"type": 4, "data": {"content": text, "flags": EPHEMERAL}}


def command(interaction: dict, request_text: str) -> dict:
    """The /admin command: answered with "thinking...", planned in the background."""
    if not interaction.get("guild_id"):
        return _private("Use /admin in a server.")
    if not _is_admin(interaction):
        return _private("Only server administrators can use /admin.")
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return _private("/admin isn't set up yet: the site needs an ANTHROPIC_API_KEY.")
    request_text = " ".join(str(request_text or "").split())
    if not request_text:
        return _private("Describe what to change.")

    def work():
        try:
            state = _state(interaction["guild_id"])
            proposal = _plan_with_claude(request_text, state)
            actions, problems = _check(list(proposal.get("actions") or [])[:25], state)
            plan = {"id": secrets.token_urlsafe(9), "user": _who(interaction)[0], "guild": interaction["guild_id"],
                    "request": request_text, "actions": actions, "problems": problems,
                    "note": proposal.get("note"), "state": state, "refs": _refs(actions),
                    "until": time.time() + PLAN_SECONDS}
            with _lock:
                for old in [k for k, p in _plans.items() if p["until"] < time.time()]:
                    del _plans[old]
                _plans[plan["id"]] = plan
            _edit_reply(interaction, _plan_message(plan))
        except Exception as exc:  # noqa: BLE001 - tell the admin rather than leave "thinking..." forever
            _edit_reply(interaction, {"content": f"Couldn't make a plan: {str(exc)[:300]}"})

    threading.Thread(target=work, name="discord-admin-plan", daemon=True).start()
    return {"type": 5, "data": {"flags": EPHEMERAL}}  # "Powerscale is thinking..."


def button(interaction: dict, custom_id: str) -> dict:
    """Confirm / Cancel under a plan."""
    _, answer, plan_id = (custom_id.split(":") + ["", ""])[:3]
    with _lock:
        plan = _plans.get(plan_id)
    if plan is None or plan["until"] < time.time():
        return {"type": 7, "data": {"content": "This plan expired - run /admin again.", "embeds": [], "components": []}}
    user, name = _who(interaction)
    if user != plan["user"] or not _is_admin(interaction):
        return _private("Only the admin who asked can confirm this plan.")
    with _lock:
        _plans.pop(plan_id, None)  # one press only
    if answer != "yes":
        return {"type": 7, "data": {"content": "Cancelled - nothing was changed.", "embeds": [], "components": []}}

    def work():
        made, lines = {}, []
        reason = f"/admin by {name}: {plan['request']}"
        for i, a in enumerate(plan["actions"], 1):
            text = describe(a, plan["state"], plan["refs"])
            try:
                _apply_one(a, plan["guild"], made, reason)
                lines.append(f"✅ `{i}.` {text}")
            except Exception as exc:  # noqa: BLE001 - keep going, report each step
                lines.append(f"❌ `{i}.` {text}\n  ↳ {str(exc)[:200]}")
        failed = sum(1 for line in lines if line.startswith("❌"))
        title = "Done" if not failed else f"Done, {failed} step{'s' if failed > 1 else ''} failed"
        if failed:
            lines.append("\nFailures are usually the bot's role: it needs Manage Channels and Manage Roles, and "
                         "can only change roles below its own.")
        _edit_reply(interaction, {"content": "", "components": [], "embeds": [{
            "title": title, "color": 0x3BA55D if not failed else 0xE15252, "description": "\n".join(lines)[:4000]}]})

    threading.Thread(target=work, name="discord-admin-apply", daemon=True).start()
    return {"type": 7, "data": {"content": "", "components": [], "embeds": [{
        "title": "Applying…", "color": 0xD9A441,
        "description": "\n".join(f"`{i}.` {describe(a, plan['state'], plan['refs'])}"
                                 for i, a in enumerate(plan["actions"], 1))[:4000]}]}}
