"""The Powerscale Discord app: /compare, /character, /random, /leaderboard, /profile.

Discord delivers each slash command to this site as a signed POST (its
"interactions endpoint"), and the reply goes back in the response. No
bot process has to stay connected, so it runs inside the web service at
no extra cost. It can be added to a server, or to a user's own account
and used anywhere.

Settings (the host's environment, never committed), from the app's page
at discord.com/developers/applications:
  DISCORD_PUBLIC_KEY      General Information -> Public Key. Checks that a
                          request really comes from Discord; without it
                          the endpoint doesn't exist.
  DISCORD_APPLICATION_ID  General Information -> Application ID.
  DISCORD_BOT_TOKEN       Bot -> Reset Token. With the application id,
                          lets the site register the commands with
                          Discord when it starts (only when they changed).
"""

import json
import os
import random
import re
import threading
import time
import unicodedata
import urllib.parse
from typing import List, Optional

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

import calculator
import db
from backend import characters, community, community_api, duels, tickets
from backend.discord_webhooks import GOLD, _clip, _md, _site

router = APIRouter()

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://powerscale.online, 1.0)"
EPHEMERAL = 64  # message flag: only the person who asked sees it
STATS = (("tier", "Tier"), ("attack_potency", "Attack Potency"), ("speed", "Speed"), ("durability", "Durability"))

# Option types (Discord's numbers)
STRING = 3
# Everywhere: a server it's added to, a user's own install, DMs.
EVERYWHERE = {"integration_types": [0, 1], "contexts": [0, 1, 2]}


def _character_option(name: str, description: str, required: bool = True) -> dict:
    return {"type": STRING, "name": name, "description": description, "required": required, "autocomplete": True}


COMMANDS = [
    {"name": "compare", "description": "Who would win? The site's verdict on two characters.", **EVERYWHERE,
     "options": [_character_option("a", "First character"), _character_option("b", "Second character"),
                 _character_option("form_a", "First character's form (default: their strongest)", False),
                 _character_option("form_b", "Second character's form (default: their strongest)", False)]},
    {"name": "character", "description": "A character's tier, attack potency, speed and durability.", **EVERYWHERE,
     "options": [_character_option("name", "Character"),
                 _character_option("form", "Form (default: their strongest)", False)]},
    {"name": "random", "description": "A random matchup between characters of similar tiers.", **EVERYWHERE},
    {"name": "leaderboard", "description": "The top duel players on the site.", **EVERYWHERE},
    {"name": "profile", "description": "Someone's Powerscale profile: posts, duel record, favorite character.",
     **EVERYWHERE, "options": [{"type": STRING, "name": "username", "description": "Their username on powerscale.online",
                                "required": True, "autocomplete": True}]},
]


# --- checking it's Discord --------------------------------------------------------------------

def verify(public_key: str, signature: str, timestamp: str, body: bytes) -> bool:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False


@router.post("/api/discord/interactions", include_in_schema=False)
async def interactions(request: Request):
    key = os.environ.get("DISCORD_PUBLIC_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=404, detail="Not Found")
    body = await request.body()
    if not verify(key, request.headers.get("X-Signature-Ed25519", ""),
                  request.headers.get("X-Signature-Timestamp", ""), body):
        # Discord also sends bad signatures on purpose, to check they're refused.
        return Response("invalid request signature", status_code=401)
    return JSONResponse(await run_in_threadpool(handle, json.loads(body)))


def handle(interaction: dict) -> dict:
    kind = interaction.get("type")
    if kind == 1:  # PING: Discord checking the endpoint
        return {"type": 1}
    data = interaction.get("data") or {}
    options = {o["name"]: o for o in data.get("options") or []}
    if kind == 4:  # typing in an option with suggestions
        return {"type": 8, "data": {"choices": _suggest(options)}}
    if kind == 2:  # a slash command
        try:
            reply = {"compare": _compare, "character": _character, "random": _random, "leaderboard": _leaderboard,
                     "profile": _profile}[data.get("name")]({k: o.get("value") for k, o in options.items()})
        except KeyError:
            reply = _oops("I don't know that command.")
        except Exception:  # noqa: BLE001 - Discord shows its own vague error otherwise
            reply = _oops("Something went wrong on the site. Try again in a moment.")
        return {"type": 4, "data": {"allowed_mentions": {"parse": []}, **reply}}
    return {"type": 4, "data": _oops("Unsupported interaction.")}


def _oops(text: str) -> dict:
    return {"content": text, "flags": EPHEMERAL}


# --- finding characters ------------------------------------------------------------------------

_index: dict = {"at": 0.0, "rows": []}
_index_lock = threading.Lock()


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())


def _characters() -> List[dict]:
    """Every character, ready to search: rebuilt every 10 minutes at most
    (characters only change when someone scrapes)."""
    with _index_lock:
        if _index["rows"] and time.time() - _index["at"] < 600:
            return _index["rows"]
        colliding = characters.all_collisions()
        rows = []
        with db.connect() as conn:
            for r in conn.execute("SELECT id, name, source_url, category, subseries, normalized_json FROM characters"):
                display = characters.short_name(characters.display_name(r["name"], r["source_url"], colliding))
                series = (r["category"] or "Uncategorized") + (f" · {r['subseries']}" if r["subseries"] else "")
                forms = [f.get("name") or "Base" for f in json.loads(r["normalized_json"]).get("forms") or []]
                rows.append({"id": r["id"], "display": display, "series": series, "forms": forms,
                             "key": _fold(display), "name": _fold(re.sub(r"\([^)]*\)", " ", display)),
                             "all": _fold(f"{r['name']} {display} {series}")})
        rows.sort(key=lambda c: c["key"])
        _index.update(at=time.time(), rows=rows)
        return rows


def search(query: str, limit: int = 25) -> List[dict]:
    q = _fold(query)
    rows = _characters()
    if not q:
        return rows[:limit]
    words = q.split()
    ranked = []
    for c in rows:
        key, name = c["key"], c["name"]  # name: without the "(Qualifier)"
        if q in (key, name):
            rank = 0
        elif f" {q} " in f" {name} ":  # a whole word of the name: "goku" finds Son Goku
            rank = 1
        elif name.startswith(q):
            rank = 2
        elif any(w.startswith(q) for w in key.split()) or f" {q}" in f" {key}" or f" {q} " in f" {c['all']} ":
            rank = 3  # ...or a whole alias: "all might" finds Toshinori Yagi
        elif all(w in c["all"] for w in words):
            rank = 4
        else:
            continue
        # Among equal matches, main characters (many forms) before one-offs.
        ranked.append((rank, -min(len(c["forms"]), 5), len(key), key, c))
    ranked.sort(key=lambda t: t[:4])
    return [t[4] for t in ranked[:limit]]


def _find(value) -> Optional[dict]:
    """A picked suggestion carries the id; typed text gets the best match."""
    value = str(value or "").strip()
    if value.isdigit():
        hit = next((c for c in _characters() if c["id"] == int(value)), None)
        if hit:
            return hit
    found = search(value, 1)
    return found[0] if found else None


def _find_form(character: dict, value) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    value, forms = str(value).strip(), character["forms"]
    if value.isdigit() and int(value) < len(forms):
        return forms[int(value)]
    q = _fold(value)
    return next((f for f in forms if _fold(f) == q), None) or next((f for f in forms if q in _fold(f)), None)


def _suggest(options: dict) -> List[dict]:
    focused = next((o for o in options.values() if o.get("focused")), None)
    if focused is None:
        return []
    name = focused["name"]
    if name == "username":
        return [{"name": u, "value": u} for u in community.search_usernames(str(focused.get("value") or ""))]
    if name.startswith("form"):
        owner = _find((options.get({"form_a": "a", "form_b": "b", "form": "name"}[name]) or {}).get("value"))
        if owner is None:
            return []
        q = _fold(str(focused.get("value") or ""))
        return [{"name": _clip(f, 100), "value": str(i)} for i, f in enumerate(owner["forms"])
                if not q or q in _fold(f)][:25]
    return [{"name": _clip(f"{c['display']} · {c['series']}", 100), "value": str(c["id"])}
            for c in search(str(focused.get("value") or ""))]


# --- replies -------------------------------------------------------------------------------------

def _pretty(label: Optional[str]) -> str:
    """Tier codes upper-cased ("8-C", "High 7-A"), words title-cased - as the site shows them."""
    if not label:
        return "Unknown"
    if any(ch.isdigit() for ch in label):
        return label.upper()
    return re.sub(r"\b(m?ftl)\b", lambda m: m.group(1).upper(), label.title(), flags=re.I)  # "Massively FTL+"


def _stat(form: dict, axis: str) -> str:
    r = form.get(axis) or {}
    base, peak = r.get("baseline_label"), r.get("peak_label")
    if not base and not peak:
        return "Unknown"
    text = _pretty(base or peak)
    if r.get("baseline_qualifier") and base:
        text = f"{r['baseline_qualifier'].capitalize()} {text}"
    if base and peak and peak != base:
        text += f", up to {_pretty(peak)}"
    return text


def _stat_lines(char_id: int, form_name: Optional[str]) -> str:
    row = db.get_character_by_id(char_id) or {}
    normalized = json.loads(row.get("normalized_json") or "{}")
    if not normalized.get("forms"):
        return "No stats on its page."
    form = calculator.select_form(normalized, form_name)
    return "\n".join(f"**{label}:** {_clip(_stat(form, axis), 90)}" for axis, label in STATS)


def _picture(char_id: int, px: int = 200) -> Optional[str]:
    replaced = community.character_image_versions().get(char_id)
    url = community_api.character_image_url(char_id, replaced) if replaced else None
    if url:
        return _site() + url if url.startswith("/") else url
    url = (db.get_character_by_id(char_id) or {}).get("image_url")
    if not url or not url.startswith("https://static.wikia.nocookie.net/"):
        return None
    path, _, query = url.partition("?")
    return f"{path}/top-crop/width/{px}/height/{px}" + (f"?{query}" if query else "")


def _link_button(url: str, label: str = "Open on Powerscale") -> List[dict]:
    return [{"type": 1, "components": [{"type": 2, "style": 5, "label": label, "url": url}]}]


def _compare_reply(a: int, b: int, form_a: Optional[str] = None, form_b: Optional[str] = None) -> dict:
    v = characters.run_compare(a, b, form_a, form_b)
    name_a, name_b = characters.short_name(v.character_a), characters.short_name(v.character_b)
    ov = community.get_override(a, b, v.form_a, v.form_b)
    calc = (f"{characters.short_name(v.favored)} favored — {v.label}"
            + (f" ({v.confidence_hint})" if v.confidence_hint != "n/a" else "")) if v.favored else v.label
    pictured = a
    if ov is not None:
        winner = name_a if ov["winner_id"] == a else name_b
        pictured = ov["winner_id"]
        lines = [f"**{winner} wins — overruled by admins**"]
        if ov.get("note"):
            lines.append("> " + _clip(" ".join(ov["note"].split()), 300))
        lines.append(f"Calculator's estimate: {calc if v.composite is not None else 'not enough data'}")
    elif v.composite is None:
        lines = ["**Not enough data for a verdict**",
                 f"Only {v.axes_used} of 4 stats are comparable between these two."]
    else:
        lines = [f"**{calc}**"]
        if v.favored == v.character_b:
            pictured = b
        if v.partial_data:
            lines.append(f"Based on partial data: {v.axes_used}/4 stats were comparable.")
    counts = {c["id"]: len(c["forms"]) for c in _characters() if c["id"] in (a, b)}
    title_a = name_a + (f" · {v.form_a}" if counts.get(a, 1) > 1 else "")
    title_b = name_b + (f" · {v.form_b}" if counts.get(b, 1) > 1 else "")
    url = f"{_site()}/compare.html?" + urllib.parse.urlencode({"a": a, "b": b, "fa": v.form_a, "fb": v.form_b})
    embed = {
        "title": _clip(f"{name_a} vs {name_b}", 256), "url": url, "color": GOLD,
        "description": "\n".join(lines),
        "fields": [{"name": _clip(title_a, 256), "value": _stat_lines(a, v.form_a), "inline": True},
                   {"name": _clip(title_b, 256), "value": _stat_lines(b, v.form_b), "inline": True}],
        "footer": {"text": "Heuristic estimate from normalized VS Battles Wiki stats · powerscale.online"},
    }
    pic = _picture(pictured)
    if pic:
        embed["thumbnail"] = {"url": pic}
    return {"embeds": [embed], "components": _link_button(url)}


def _compare(args: dict) -> dict:
    a, b = _find(args.get("a")), _find(args.get("b"))
    if a is None or b is None:
        missing = args.get("a") if a is None else args.get("b")
        return _oops(f"No character matches “{_clip(str(missing), 80)}”. Pick one from the suggestions as you type.")
    if a["id"] == b["id"]:
        return _oops("Pick two different characters.")
    forms = []
    for c, key in ((a, "form_a"), (b, "form_b")):
        form = _find_form(c, args.get(key))
        if args.get(key) and form is None:
            return _oops(f"{c['display']} has no form called “{_clip(str(args.get(key)), 60)}”.")
        forms.append(form)
    return _compare_reply(a["id"], b["id"], *forms)


def _character(args: dict) -> dict:
    c = _find(args.get("name"))
    if c is None:
        return _oops(f"No character matches “{_clip(str(args.get('name')), 80)}”. Pick one from the suggestions as you type.")
    form = _find_form(c, args.get("form"))
    if args.get("form") and form is None:
        return _oops(f"{c['display']} has no form called “{_clip(str(args.get('form')), 60)}”.")
    row = db.get_character_by_id(c["id"]) or {}
    normalized = json.loads(row.get("normalized_json") or "{}")
    shown = calculator.select_form(normalized, form).get("name") if normalized.get("forms") else None
    url = f"{_site()}/character.html?id={c['id']}"
    forms = c["forms"]
    description = [c["series"]]
    if len(forms) > 1:
        description.append(f"Form: **{shown}** (one of {len(forms)}: " + _clip(", ".join(forms), 200) + ")")
    embed = {"title": _clip(c["display"], 256), "url": url, "color": GOLD,
             "description": "\n".join(description) + "\n\n" + _stat_lines(c["id"], form),
             "footer": {"text": "From the VS Battles Wiki · powerscale.online"}}
    pic = _picture(c["id"], 400)
    if pic:
        embed["thumbnail"] = {"url": pic}
    return {"embeds": [embed], "components": _link_button(url)}


def _random(args: dict) -> dict:
    pool = characters.scorable_pool()
    a, tier, _ = random.choice(pool)
    near = [cid for cid, t, _ in pool if cid != a and abs(t - tier) <= duels.RANDOM_TIER_SPREAD]
    b = random.choice(near) if near else random.choice([cid for cid, _, _ in pool if cid != a])
    return _compare_reply(a, b)


def _leaderboard(args: dict) -> dict:
    rows = duels.leaderboard(limit=10)
    url = f"{_site()}/duels.html"
    if not rows:
        lines = ["No finished duels yet. Be the first!"]
    else:
        medal = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = [f"{medal.get(i, f'`{i + 1}.`')} **{r['username']}** — {r['wins']}–{r['draws']}–{r['losses']}"
                 for i, r in enumerate(rows)]
    embed = {"title": "Duel leaderboard", "url": url, "color": GOLD, "description": "\n".join(lines),
             "footer": {"text": "Wins–draws–losses · powerscale.online"}}
    return {"embeds": [embed], "components": _link_button(url, "Play a duel")}


def _recent_duels(user_id: int, limit: int = 3) -> str:
    from backend.duels_api import _duel_out
    recent = duels.recent_finished(user_id, limit=limit)
    lines, cache = [], {}
    for g in recent["games"]:
        d = _duel_out(g, user_id, recent, recent["people"], cache)
        word = {"win": "Won", "loss": "Lost", "draw": "Draw"}.get(d.outcome or "", "Finished")
        mine, scores = d.my_team, d.team_scores
        score = (f"{scores[mine - 1]}–{scores[2 - mine]}" if d.teams == 2 and mine and len(scores) == 2
                 else " · ".join(str(x) for x in scores))
        rivals = ", ".join(p.username for p in d.players if p.team != mine)
        kind = ("Draft · " if d.mode == "draft" else "") + d.format
        lines.append(f"[{word} {score}]({_site()}/duels.html?game={d.id}) vs {_md(rivals)} · {kind}")
    return "\n".join(lines)


def _profile(args: dict) -> dict:
    name = str(args.get("username") or "").strip().lstrip("@")
    profile = community.get_profile(username=name) if name else None
    if profile is None:
        return _oops(f"No one called “{_clip(name, 40)}” on Powerscale. Pick a name from the suggestions as you type.")
    out = community_api._profile_out(profile, own=False)
    uid, rec = profile["id"], out.record
    played = rec.wins + rec.draws + rec.losses
    rank = next((i + 1 for i, r in enumerate(duels.leaderboard(limit=1000)) if r["username"] == out.username), None)
    url = f"{_site()}/user.html?" + urllib.parse.urlencode({"u": out.username})
    about = [_md(_clip(" ".join(out.bio.split()), 300))] if out.bio else []
    about.append(f"Member since {out.member_since:%B %Y}")
    fields = [
        {"name": "Posts", "value": str(out.post_count), "inline": True},
        {"name": "Likes received", "value": str(profile["likes_received"]), "inline": True},
        {"name": "Matchup comments", "value": str(community.comment_count(uid)), "inline": True},
        {"name": "Duels", "inline": True,
         "value": ((f"#{rank} · " if rank else "") + f"{rec.wins}–{rec.draws}–{rec.losses}") if played else "None yet"},
        {"name": "Overrules suggested", "value": str(tickets.credited_overrules(uid)), "inline": True},
    ]
    if out.favorite:
        fields.append({"name": "Favorite character", "inline": True,
                       "value": f"[{_md(_clip(out.favorite.name, 80))}]({_site()}/character.html?id={out.favorite.id})"})
    recent = _recent_duels(uid)
    if recent:
        fields.append({"name": "Recent duels", "value": recent, "inline": False})
    embed = {"title": _clip(out.username + (" · Admin" if out.is_admin else ""), 256), "url": url, "color": GOLD,
             "description": "\n".join(about), "fields": fields, "footer": {"text": "Wins–draws–losses · powerscale.online"}}
    picture = _site() + out.avatar_url if out.avatar_url else (_picture(out.favorite.id) if out.favorite else None)
    if picture:
        embed["thumbnail"] = {"url": picture}
    return {"embeds": [embed], "components": _link_button(url, "View profile")}


# --- registering the commands --------------------------------------------------------------------

def _shape(commands: List[dict]) -> list:
    """What matters for comparing registered commands with COMMANDS."""
    def opt(o):
        return (o["name"], o.get("description"), o["type"], bool(o.get("required")), bool(o.get("autocomplete")))
    return sorted((c["name"], c.get("description"), tuple(opt(o) for o in c.get("options") or []),
                   tuple(sorted(c.get("integration_types") or [])), tuple(sorted(c.get("contexts") or [])))
                  for c in commands)


def sync_commands() -> None:
    app_id = os.environ.get("DISCORD_APPLICATION_ID", "").strip()
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not app_id or not token:
        return
    url = f"{API}/applications/{app_id}/commands"
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
    try:
        current = requests.get(url, headers=headers, timeout=15)
        if current.ok and _shape(current.json()) == _shape(COMMANDS):
            return
        requests.put(url, headers=headers, json=COMMANDS, timeout=15)
    except requests.RequestException:
        pass  # tried again on the next start


def start() -> None:
    """On startup, in the background: register the commands if they
    changed, and build the search index so the first suggestions are quick."""
    if not os.environ.get("DISCORD_PUBLIC_KEY", "").strip():
        return

    def run():
        sync_commands()
        try:
            _characters()
        except Exception:  # noqa: BLE001 - built on first use instead
            pass
    threading.Thread(target=run, name="discord-start", daemon=True).start()
