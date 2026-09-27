"""Posts site events into Discord channels through webhooks.

A webhook is a channel's secret posting URL (Discord: channel settings ->
Integrations -> Webhooks). Set them as environment variables, never in
code: DISCORD_WEBHOOK_OVERRULES gets admin overrules, DISCORD_WEBHOOK_DUELS
finished duels, DISCORD_WEBHOOK_LEADERBOARD the weekly leaderboard (else
the duels channel); DISCORD_WEBHOOK_URL is used for whichever isn't set.
With none set (local runs, tests) nothing is sent.

Posting happens on a background thread, a moment after the event, so a
slow or unreachable Discord never slows the site down - and the message
is built from what's saved, after the request that caused it is done.
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from datetime import time as clock
from typing import Optional, Tuple

import db
import scraper

GOLD = 0xD9A441
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="discord")


def _site() -> str:
    return os.environ.get("SITE_URL", "https://powerscale.online").rstrip("/")


def _webhook(*kinds: str) -> Optional[str]:
    """The first of DISCORD_WEBHOOK_<kind>... that's set, else DISCORD_WEBHOOK_URL."""
    for name in [f"DISCORD_WEBHOOK_{k}" for k in kinds] + ["DISCORD_WEBHOOK_URL"]:
        url = os.environ.get(name, "").strip()
        if url:
            return url
    return None


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


# --- the weekly leaderboard ----------------------------------------------------------------------
# Every Monday at 18:00 Budapest time: the week's best duel players and
# the all-time top 10. A background loop checks every 10 minutes (the site
# is kept awake); the database remembers which week went out, so restarts
# and deploys never post it twice. A week missed while the site was down
# is posted when it's back, the same day or later.

WEEKLY_DAY, WEEKLY_HOUR = 0, 18  # Monday, 18:00


def _zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Budapest")
    except Exception:  # noqa: BLE001 - no time zone data: UTC is close enough
        return timezone.utc


def week_slot(now: datetime) -> Tuple[str, datetime, datetime]:
    """The latest posting time at or before `now`: (its ISO week, e.g.
    "2026-W40", and the week it covers as UTC [since, until))."""
    tz = _zone()
    local = now.astimezone(tz)
    day = local.date() - timedelta(days=(local.weekday() - WEEKLY_DAY) % 7)
    slot = datetime.combine(day, clock(WEEKLY_HOUR), tzinfo=tz)
    if local < slot:
        slot = datetime.combine(day - timedelta(days=7), clock(WEEKLY_HOUR), tzinfo=tz)
    since = datetime.combine(slot.date() - timedelta(days=7), clock(WEEKLY_HOUR), tzinfo=tz)
    return slot.strftime("%G-W%V"), since.astimezone(timezone.utc), slot.astimezone(timezone.utc)


def leaderboard_embed(since: datetime, until: datetime) -> Optional[dict]:
    from backend import duels
    everyone = duels.leaderboard(limit=10)
    if not everyone:
        return None  # nobody has finished a duel yet: nothing to show
    week = duels.leaderboard(limit=5, since=since, until=until)
    played = duels.finished_count(since, until)
    medal = {0: "🥇", 1: "🥈", 2: "🥉"}

    def lines(rows):
        return "\n".join(f"{medal.get(i, f'`{i + 1}.`')} **{r['username']}** — {r['wins']}–{r['draws']}–{r['losses']}"
                         for i, r in enumerate(rows))
    tz = _zone()
    first, last = since.astimezone(tz).date(), (until - timedelta(days=1)).astimezone(tz).date()
    span = f"{first:%b} {first.day} – {last:%b} {last.day}"
    embed = {
        "title": "🏆 Weekly duel leaderboard", "url": f"{_site()}/duels.html", "color": GOLD,
        "description": (f"**{played}** duel{'s' if played != 1 else ''} finished this week ({span})."
                        if played else f"No duels finished this week ({span}). Start one on the site!"),
        "fields": ([{"name": "This week", "value": lines(week)}] if week else [])
                  + [{"name": "All time", "value": lines(everyone)}],
        "footer": {"text": "Wins–draws–losses · powerscale.online"},
    }
    return embed


def weekly_leaderboard(now: Optional[datetime] = None) -> bool:
    """Posts this week's leaderboard if it's due and hasn't gone out yet.
    True when it posted."""
    url = _webhook("LEADERBOARD", "DUELS")
    if not url:
        return False
    from backend import community
    key, since, until = week_slot(now or datetime.now(timezone.utc))
    if not community.claim_mark("discord_leaderboard_week", key):
        return False
    embed = leaderboard_embed(since, until)
    if embed:
        _send(url, embed)
    return embed is not None


def start_weekly() -> None:
    if not _webhook("LEADERBOARD", "DUELS"):
        return

    def loop():
        while True:
            try:
                weekly_leaderboard()
            except Exception:  # noqa: BLE001 - try again next round
                pass
            time.sleep(600)
    threading.Thread(target=loop, name="discord-weekly", daemon=True).start()
