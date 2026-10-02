"""/admin: channel, role and permission changes from a plan, applied
only after the bot shows exactly what it will do and you press Confirm.

The admin describes the change to Claude Code, which writes the plan (one
line of JSON - the format is under "reading a plan" below) to paste:

  /admin plan: {"summary": "...", "actions": [...]}

/admin with no plan shows the server's channels, roles and overrides,
to paste to Claude Code when it needs to see them; "/admin plan: #bug-reports
#suggestions" shows just those channels.

1. The bot reads the server's channels and roles and checks every step
   (do those channels and roles exist, are those real permissions).
2. The preview is written from the server's own names - that's what the
   admin approves. Granting Administrator is refused: do that by hand.
3. Confirm applies the steps one by one, each with an audit log entry
   naming who asked; Cancel drops the plan. Only the person who asked can
   press either, and a plan lasts 14 minutes (Discord's reply token lasts 15).

Only server members with Administrator see or can use the command, and
the bot can't do more than its own role allows (Manage Channels, Manage
Roles, and only roles below its own). No AI runs on the site, so it costs
nothing.
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
    "name": "admin", "description": "Apply a channel/role plan from Claude Code, after a preview (no plan: show the server).",
    "integration_types": [0], "contexts": [0], "default_member_permissions": str(ADMINISTRATOR),
    "options": [{"type": 3, "name": "plan", "required": False, "max_length": 6000,
                 "description": "The plan Claude Code wrote. Empty: list the server; #channel: just that channel"}],
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
    try:
        automod = _discord("GET", f"/guilds/{guild_id}/auto-moderation/rules")
    except (RuntimeError, requests.RequestException):
        automod = []  # needs Manage Server; the rest works without it
    return {"guild_id": guild_id,
            "roles": {r["id"]: r for r in roles},
            "channels": {c["id"]: c for c in channels},
            "automod": {r["id"]: r for r in automod}}


def _perm_names(bits) -> List[str]:
    bits = int(bits or 0)
    return [n for n, b in PERMISSIONS.items() if bits & b]


def _describe_server(state: dict, only: Optional[set] = None) -> str:
    """The server for Claude Code to read; `only`: just those channel ids."""
    lines = []
    if not only:
        lines.append("ROLES (highest first; @everyone's id is the server id):")
        for r in sorted(state["roles"].values(), key=lambda r: -r["position"]):
            extra = " [managed by an integration]" if r.get("managed") else ""
            perms = "ADMINISTRATOR (everything)" if int(r["permissions"]) & ADMINISTRATOR \
                else ",".join(_perm_names(r["permissions"])) or "none"
            lines.append(f"- {r['name']} id={r['id']} position={r['position']}{extra} permissions={perms}")
        lines.append("")
    lines.append("CHANNELS:")
    for c in sorted(state["channels"].values(), key=lambda c: (c.get("parent_id") or "", c.get("position", 0))):
        if only and c["id"] not in only:
            continue
        kind = TYPE_NAMES.get(c["type"], f"type {c['type']}")
        parent = state["channels"].get(c.get("parent_id") or "")
        head = f"- #{c['name']} id={c['id']} {kind}" + (f" in category '{parent['name']}'" if parent else "")
        if c.get("topic"):
            head += f" topic={json.dumps(c['topic'][:120])}"
        lines.append(head)
        if c.get("available_tags"):
            lines.append("    tags: " + ", ".join(t["name"] + (" (mods only)" if t.get("moderated") else "")
                                         for t in c["available_tags"]))
        for o in c.get("permission_overwrites") or []:
            who = state["roles"].get(o["id"], {}).get("name", "?") if o["type"] == 0 else f"member {o['id']}"
            lines.append(f"    overwrite {who} (id={o['id']}): allow={','.join(_perm_names(o['allow'])) or '-'} "
                         f"deny={','.join(_perm_names(o['deny'])) or '-'}")
    if state.get("automod"):
        lines.append("\nAUTOMOD RULES:")
        for r in state["automod"].values():
            meta = r.get("trigger_metadata") or {}
            lines.append(f"- {r['name']!r} id={r['id']} {'on' if r.get('enabled') else 'off'} "
                         f"keywords={meta.get('keyword_filter') or []} regex={meta.get('regex_patterns') or []} "
                         f"exempt roles={len(r.get('exempt_roles') or [])} channels={len(r.get('exempt_channels') or [])}")
    return "\n".join(lines)


# --- reading a plan ------------------------------------------------------------------------------
#
# A plan is one line of JSON (Discord's text box is a single line), usually
# written by Claude Code from the admin's description:
#
#   {"summary": "updates read-only except Admins", "actions": [
#     {"op": "set_permissions", "channel": "#updates", "target": "@everyone",
#      "deny": ["SEND_MESSAGES", "ADD_REACTIONS"]},
#     {"op": "set_permissions", "channel": "#updates", "target": "@Admin", "allow": ["SEND_MESSAGES"]}]}
#
# ops: create_channel (name, type text|voice|category|announcement|stage|forum,
#        parent, topic, slowmode_seconds, nsfw, ref)
#      edit_channel (channel, name, topic, parent ("none" to take it out), slowmode_seconds,
#        nsfw, sync_with_category; forums also: tags [{"name", "emoji"}] - the full list,
#        tags already there keep their posts -, require_tag, default_reaction "🔥",
#        sort "latest"|"created", layout "list"|"gallery". A forum's topic is its
#        post guidelines)
#      delete_channel (channel)
#      set_permissions (channel, target, target_type role|member, allow, deny, neutral):
#        only the listed permissions change; "neutral" resets them to inherited
#      remove_overwrite (channel, target, target_type)
#      create_role (name, color "#D9A441", hoist, mentionable, allow = its permissions, ref)
#      edit_role (role, name, color, hoist, mentionable, allow = add, deny = remove)
#      delete_role (role)
#      create_automod_rule (name, regex [...] and/or keywords [...], only_in [channels] or
#        exempt_channels [channels], exempt_roles [roles], message): blocks matching messages.
#        only_in exempts every other channel and category as they are now, so a channel
#        made later is covered by the rule until it's run again. Slash commands aren't
#        messages, so a rule matching everything ("regex": [".+"]) leaves them working.
#      delete_automod_rule (rule: its name or id)
# Channels are "#name" or an id, roles "@name", "@everyone" or an id, members
# an id or <@id>. Something made earlier in the plan is "new:<its ref>".
# Permissions are Discord's names (SEND_MESSAGES, VIEW_CHANNEL...), any case.

KEYS = {"channel": "channel_id", "parent": "parent_id", "role": "role_id", "target": "target_id"}


def _find_channel(value: str, state: dict, categories: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """(id, None) or (None, why not)."""
    if value in state["channels"] or value.startswith("new:") or value == "none":
        return value, None
    name = value.strip().lstrip("#").strip().lower()
    hits = [c["id"] for c in state["channels"].values() if c["name"].lower() == name
            and (not categories or c["type"] == 4)]
    if len(hits) == 1:
        return hits[0], None
    return None, (f"two channels are called #{name} - use its id" if hits
                  else f"no {'category' if categories else 'channel'} called {value}")


def _find_role(value: str, state: dict) -> Tuple[Optional[str], Optional[str]]:
    if value in state["roles"] or value.startswith("new:"):
        return value, None
    name = value.strip().lstrip("@").strip().lower()
    if name == "everyone":
        return state["guild_id"], None
    hits = [r["id"] for r in state["roles"].values() if r["name"].lower() == name]
    if len(hits) == 1:
        return hits[0], None
    return None, f"two roles are called @{name} - use its id" if hits else f"no role called {value}"


def _normalize(action: dict, state: dict) -> Tuple[Optional[dict], Optional[str]]:
    """Names to ids and permission names to Discord's, or why it can't be read."""
    if not isinstance(action, dict):
        return None, "not an action"
    a = {KEYS.get(k, k): v for k, v in action.items()}
    for key in ("allow", "deny", "neutral"):
        names = a.get(key) or []
        if isinstance(names, str):
            names = [names]
        fixed = [str(n).strip().upper().replace(" ", "_") for n in names]
        unknown = [n for n in fixed if n not in PERMISSIONS]
        if unknown:
            return None, "unknown permission " + ", ".join(unknown)
        if fixed:
            a[key] = fixed
    for key in ("channel_id", "parent_id"):
        if a.get(key) is not None:
            found, why = _find_channel(str(a[key]), state, categories=key == "parent_id")
            if why:
                return None, why
            a[key] = found
    if a.get("role_id") is not None:
        found, why = _find_role(str(a["role_id"]), state)
        if why:
            return None, why
        a["role_id"] = found
    if a.get("target_id") is not None:
        target = str(a["target_id"]).strip()
        if a.get("target_type") == "member" or target.startswith("<@"):
            a["target_type"], a["target_id"] = "member", target.strip("<@!>")
        else:
            found, why = _find_role(target, state)
            if why:
                return None, why
            a["target_id"] = found
    for key in ("only_in", "exempt_channels"):
        if a.get(key) is not None:
            ids = []
            for value in a[key] if isinstance(a[key], list) else [a[key]]:
                found, why = _find_channel(str(value), state)
                if why:
                    return None, why
                ids.append(found)
            a[key] = ids
    if a.get("exempt_roles") is not None:
        ids = []
        for value in a["exempt_roles"] if isinstance(a["exempt_roles"], list) else [a["exempt_roles"]]:
            found, why = _find_role(str(value), state)
            if why:
                return None, why
            ids.append(found)
        a["exempt_roles"] = ids
    if a.get("rule") is not None:
        rules = state.get("automod") or {}
        rule = str(a["rule"])
        hits = [r["id"] for r in rules.values() if r["id"] == rule or r["name"].lower() == rule.lower()]
        if len(hits) != 1:
            return None, f"no AutoMod rule called {rule!r}" if not hits else f"two AutoMod rules are called {rule!r}"
        a["rule"] = hits[0]
    if a.get("type") is not None and a["type"] not in CHANNEL_TYPES:
        return None, f"unknown channel type {a['type']!r}"
    return a, None


def _parse(text: str) -> Tuple[dict, Optional[str]]:
    text = (text or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:]
    try:
        plan = json.loads(text)
    except ValueError as exc:
        return {}, f"That isn't a plan I can read ({exc.msg}, at character {exc.pos})."
    if isinstance(plan, list):
        plan = {"actions": plan}
    if not isinstance(plan, dict) or not isinstance(plan.get("actions"), list):
        return {}, "A plan needs a list of actions."
    return plan, None


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

    for i, raw in enumerate(actions, 1):
        a, why = _normalize(raw, state)
        if why:
            problems.append(f"Step {i} skipped: {why}.")
            continue
        op = a.get("op")
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
            elif op == "edit_channel" and any(k in a for k in ("tags", "require_tag", "default_reaction", "sort", "layout")) \
                    and not channel(a.get("channel_id"), {15, 16}):
                why = "tags, default reaction, sort and layout are for forum channels"
            elif op == "edit_channel" and (len(a.get("tags") or []) > 20
                                           or any(not isinstance(t, dict) or not t.get("name") for t in a.get("tags") or [])):
                why = "tags need a name each, 20 at most"
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
        elif op == "create_automod_rule":
            if not a.get("name"):
                why = "no name"
            elif not (a.get("regex") or a.get("keywords")):
                why = "no regex or keywords to match"
            elif a.get("only_in") and a.get("exempt_channels"):
                why = "give only_in or exempt_channels, not both"
            elif any(str(c).startswith("new:") for c in (a.get("only_in") or []) + (a.get("exempt_channels") or [])):
                why = "AutoMod rules can't use channels made in the same plan - run it again after"
            elif len(a.get("exempt_roles") or []) > 20:
                why = "Discord allows 20 exempt roles at most"
        elif op == "delete_automod_rule":
            if not a.get("rule"):
                why = "which rule? give its name"
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
    ch_name = lambda cid: _name_of(state, refs, cid)  # noqa: E731
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
        if a.get("tags") is not None:
            parts.append("tags: " + (", ".join(f"{t.get('emoji', '')} {t['name']}".strip() for t in a["tags"]) or "none"))
        if "require_tag" in a:
            parts.append("posts need a tag" if a["require_tag"] else "tags optional")
        if "default_reaction" in a:
            parts.append(f"default reaction {a['default_reaction'] or '(none)'}")
        if a.get("sort"):
            parts.append(f"sorted by {'newest activity' if a['sort'] == 'latest' else 'creation date'}")
        if a.get("layout"):
            parts.append(f"{a['layout']} view")
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
    if op == "create_automod_rule":
        match = ", ".join([f"`{x}`" for x in a.get("regex") or []] + [f"“{x}”" for x in a.get("keywords") or []])
        where = (" everywhere except " + ", ".join(ch_name(c) for c in a["exempt_channels"])) if a.get("exempt_channels") \
            else (" only in " + ", ".join(ch_name(c) for c in a["only_in"])) if a.get("only_in") else " in every channel"
        roles = ", ".join(_name_of(state, refs, r, role_like=True) for r in a.get("exempt_roles") or [])
        return (f"AutoMod rule **{a['name']}**: block messages matching {match}{where}"
                + (f"; {roles} not affected" if roles else "")
                + (f"; tells them: {a['message'][:150]}" if a.get("message") else ""))
    if op == "delete_automod_rule":
        rule = (state.get("automod") or {}).get(a["rule"], {})
        return f"⚠️ **Delete AutoMod rule {rule.get('name', a['rule'])}**"
    return op


def _refs(actions: List[dict]) -> dict:
    return {f"new:{a['ref']}": a.get("name") or a["ref"] for a in actions if a.get("ref")}


def _plan_message(plan: dict) -> dict:
    lines = [f"**Plan:** {plan['request'][:300]}", ""]
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
        body.update(_forum_fields(a, cid))
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
    elif op == "create_automod_rule":
        exempt = list(a.get("exempt_channels") or [])
        if a.get("only_in"):
            exempt = _all_but(_discord("GET", f"/guilds/{guild_id}/channels"), set(a["only_in"]))
        if len(exempt) > 50:
            raise RuntimeError("Discord allows 50 exempt channels at most")
        action = {"type": 1}  # block the message
        if a.get("message"):
            action["metadata"] = {"custom_message": str(a["message"])[:150]}
        _discord("POST", f"/guilds/{guild_id}/auto-moderation/rules", {
            "name": str(a["name"])[:100], "event_type": 1, "trigger_type": 1, "enabled": True,
            "trigger_metadata": {"keyword_filter": list(a.get("keywords") or []),
                                 "regex_patterns": list(a.get("regex") or [])},
            "actions": [action], "exempt_roles": [real(r) for r in a.get("exempt_roles") or []],
            "exempt_channels": exempt}, reason)
    elif op == "delete_automod_rule":
        _discord("DELETE", f"/guilds/{guild_id}/auto-moderation/rules/{a['rule']}", None, reason)


def _forum_fields(a: dict, cid: str) -> dict:
    body = {}
    if a.get("tags") is not None or "require_tag" in a:
        channel = _discord("GET", f"/channels/{cid}")
        if a.get("tags") is not None:
            # Same name: the same tag (its id), so posts keep it.
            old = {t["name"].lower(): t for t in channel.get("available_tags") or []}
            tags = []
            for t in a["tags"][:20]:
                tag = {"name": str(t["name"])[:20], "moderated": bool(t.get("moderated"))}
                if t.get("emoji"):
                    tag["emoji_name"] = t["emoji"]
                if tag["name"].lower() in old:
                    tag["id"] = old[tag["name"].lower()]["id"]
                tags.append(tag)
            body["available_tags"] = tags
        if "require_tag" in a:
            body["flags"] = (channel.get("flags") or 0) & ~16 | (16 if a["require_tag"] else 0)
    if "default_reaction" in a:
        body["default_reaction_emoji"] = {"emoji_name": a["default_reaction"]} if a["default_reaction"] else None
    if a.get("sort"):
        body["default_sort_order"] = 1 if a["sort"] == "created" else 0
    if a.get("layout"):
        body["default_forum_layout"] = 2 if a["layout"] == "gallery" else 1
    return body


def _all_but(channels: List[dict], keep: set) -> List[str]:
    """Every channel the rule should leave alone: whole categories where
    possible (fewer entries, and new channels in them are covered too),
    single channels in the categories that hold one of `keep`."""
    keep_parents = {c.get("parent_id") for c in channels if c["id"] in keep}
    exempt = []
    for c in channels:
        if c["id"] in keep or c["type"] in (10, 11, 12):  # threads follow their channel
            continue
        if c["type"] == 4:
            if c["id"] not in keep_parents:
                exempt.append(c["id"])
        elif c.get("parent_id") in keep_parents or not c.get("parent_id"):
            exempt.append(c["id"])
    return exempt


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


def command(interaction: dict, plan_text: Optional[str]) -> dict:
    """/admin plan:<json> -> a preview to confirm. Without a plan: the
    server's channels and roles, to paste to Claude Code."""
    if not interaction.get("guild_id"):
        return _private("Use /admin in a server.")
    if not _is_admin(interaction):
        return _private("Only server administrators can use /admin.")

    def work():
        try:
            state = _state(interaction["guild_id"])
            asked = (plan_text or "").strip()
            if not asked or asked.startswith("#"):
                only = set()
                for name in asked.replace(",", " ").split():
                    found, why = _find_channel(name, state)
                    if why:
                        _edit_reply(interaction, {"content": why})
                        return
                    only.add(found)
                text = _describe_server(state, only)
                if len(text) > 3900:
                    text = text[:3900] + "\n... (cut short - try /admin plan: #channel to see one channel)"
                _edit_reply(interaction, {"content": "", "embeds": [{
                    "title": "This server, for Claude Code", "color": 0xD9A441,
                    "description": f"```\n{text}\n```",
                    "footer": {"text": "Describe what you want changed to Claude Code; "
                                       "it writes a plan to paste into /admin plan:"}}]})
                return
            proposal, why = _parse(plan_text)
            if why:
                _edit_reply(interaction, {"content": why})
                return
            actions, problems = _check(proposal["actions"][:25], state)
            summary = " ".join(str(proposal.get("summary") or "").split()) or f"{len(actions)} change(s)"
            plan = {"id": secrets.token_urlsafe(9), "user": _who(interaction)[0], "guild": interaction["guild_id"],
                    "request": summary, "actions": actions, "problems": problems,
                    "note": proposal.get("note"), "state": state, "refs": _refs(actions),
                    "until": time.time() + PLAN_SECONDS}
            with _lock:
                for old in [k for k, p in _plans.items() if p["until"] < time.time()]:
                    del _plans[old]
                _plans[plan["id"]] = plan
            _edit_reply(interaction, _plan_message(plan))
        except Exception as exc:  # noqa: BLE001 - tell the admin rather than leave "thinking..." forever
            _edit_reply(interaction, {"content": f"Couldn't read the server: {str(exc)[:300]}"})

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
