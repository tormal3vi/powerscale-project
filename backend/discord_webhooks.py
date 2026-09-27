"""Posts site events into Discord channels through webhooks.

A webhook is a channel's secret posting URL (Discord: channel settings ->
Integrations -> Webhooks). Set them as environment variables, never in
code: DISCORD_WEBHOOK_OVERRULES gets admin overrules, DISCORD_WEBHOOK_DUELS
finished duels; DISCORD_WEBHOOK_URL is used for whichever of those isn't
set. With none set (local runs, tests) nothing is sent.

Posting happens on a background thread, a moment after the event, so a
slow or unreachable Discord never slows the site down - and the message
is built from what's saved, after the request that caused it is done.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import db
import scraper

GOLD = 0xD9A441
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="discord")


def _site() -> str:
    return os.environ.get("SITE_URL", "https://powerscale.online").rstrip("/")


def _webhook(kind: str) -> Optional[str]:
    return (os.environ.get(f"DISCORD_WEBHOOK_{kind}") or os.environ.get("DISCORD_WEBHOOK_URL") or "").strip() or None


def _send(url: str, embed: dict) -> None:
    # No pings, ever: a username or an overrule's reason could contain
    # "@everyone".
    body = json.dumps({"embeds": [embed], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": scraper.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception:  # noqa: BLE001 - a missed announcement isn't worth an error page
        pass


def _later(kind: str, build) -> None:
    url = _webhook(kind)
    if not url:
        return

    def run():
        time.sleep(1.5)  # let the request that caused it finish saving
        try:
            embed = build()
        except Exception:  # noqa: BLE001
            return
        if embed:
            _send(url, embed)
    _pool.submit(run)


def _picture(char_id: int) -> Optional[str]:
    row = db.get_character_by_id(char_id) or {}
    url = row.get("image_url")
    if not url or not url.startswith("https://static.wikia.nocookie.net/"):
        return None
    path, _, query = url.partition("?")
    return f"{path}/top-crop/width/200/height/200" + (f"?{query}" if query else "")


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


# --- events ---------------------------------------------------------------------------------

def overrule(char_a: int, char_b: int, form_a: str, form_b: str, winner_id: int, note: str,
             admin: str, credit: Optional[str] = None) -> None:
    def build():
        from backend import characters
        v = characters.run_compare(char_a, char_b, form_a, form_b)
        winner = characters.short_name(v.character_a if winner_id == char_a else v.character_b)
        calc = (f"{characters.short_name(v.favored)} favored — {v.label}" if v.favored
                else v.label if v.composite is not None else "not enough data")
        lines = [f"**{characters.short_name(v.character_a)}** vs **{characters.short_name(v.character_b)}**"
                 + (f" · {v.form_a} vs {v.form_b}" if (v.form_a, v.form_b) != ("Base", "Base") else ""),
                 f"Calculator's estimate: {calc}"]
        if note:
            lines.append("> " + _clip(" ".join(note.split()), 300))
        if credit:
            lines.append(f"Suggested by **{credit}** — their ticket led to this call.")
        embed = {"title": _clip(f"{winner} wins — overruled by admins", 250), "color": GOLD,
                 "url": f"{_site()}/compare.html?a={char_a}&b={char_b}&fa={urllib.parse.quote(form_a)}"
                        f"&fb={urllib.parse.quote(form_b)}",
                 "description": "\n".join(lines), "footer": {"text": f"Ruled by {admin}"}}
        pic = _picture(winner_id)
        if pic:
            embed["thumbnail"] = {"url": pic}
        return embed
    _later("OVERRULES", build)


def duel_finished(game_id: int) -> None:
    def build():
        from backend import duels_api
        g = duels_api._one(game_id, None)
        if g.status != "done":
            return None
        teams = []
        for t in range(1, g.teams + 1):
            names = [p.username for p in g.players if p.team == t]
            teams.append((" & ".join(names), g.team_scores[t - 1] if g.team_scores else 0,
                          any(p.outcome == "win" for p in g.players if p.team == t)))
        winners = [t for t in teams if t[2]]
        kind = f"{'draft' if g.mode == 'draft' else 'prediction'} duel · {g.format}"
        title = f"{winners[0][0]} won a {kind}" if winners else f"A {kind} ended in a draw"
        ranked = sorted(teams, key=lambda t: -t[1])
        embed = {"title": _clip(title, 250), "color": GOLD if winners else 0x7A7264,
                 "url": f"{_site()}/duels.html?game={game_id}",
                 "description": "\n".join(f"{'**' if w else ''}{name}{'**' if w else ''} — {score}"
                                          for name, score, w in ranked)}
        winner = next((p for p in g.players if p.outcome == "win"), None)
        if winner and winner.avatar_url:
            embed["thumbnail"] = {"url": _site() + winner.avatar_url}
        return embed
    _later("DUELS", build)
