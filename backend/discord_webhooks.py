"""Posts site events into Discord channels through webhooks.

A webhook is a channel's secret posting URL (Discord: channel settings ->
Integrations -> Webhooks). Set them as environment variables, never in
code: DISCORD_WEBHOOK_OVERRULES gets admin overrules, DISCORD_WEBHOOK_DUELS
finished duels, DISCORD_WEBHOOK_LOBBY open games looking for players and
DISCORD_WEBHOOK_LEADERBOARD the weekly leaderboard (both else the duels
channel), DISCORD_WEBHOOK_UPDATES the site's update notes;
DISCORD_WEBHOOK_URL is used for whichever isn't set.
With none set (local runs, tests) nothing is sent.

Posting happens on a background thread, a moment after the event, so a
slow or unreachable Discord never slows the site down - and the message
is built from what's saved, after the request that caused it is done.
"""

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from datetime import time as clock
from pathlib import Path
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


def _request(method: str, url: str, embed: Optional[dict] = None, message: Optional[dict] = None) -> Optional[dict]:
    """One call to a webhook - an embed, or a whole `message` (e.g. with a
    poll); its JSON reply (if any), None when it failed."""
    if embed is not None:
        message = {"embeds": [embed]}
    # No pings, ever: a username or an overrule's reason could contain
    # "@everyone".
    body = json.dumps({**message, "allowed_mentions": {"parse": []}}).encode() if message else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Content-Type": "application/json", "User-Agent": scraper.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read()
        return json.loads(text) if text else {}
    except Exception:  # noqa: BLE001 - a missed announcement isn't worth an error page
        return None


def _send(url: str, embed: dict) -> None:
    _request("POST", url, embed)


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


def _md(text: str) -> str:
    """Text shown as-is in Discord markdown: "tier_climber" stays un-italic."""
    return re.sub(r"([\\_*~|`\[\]])", r"\\\1", text)


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
# the all-time top 10 - skipped in a week with no finished duels. A
# background loop checks every 10 minutes (the site is kept awake); the
# database remembers which week went out, so restarts and deploys never
# post it twice. A week missed while the site was down is posted when it's
# back, the same day or later.

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
    played = duels.finished_count(since, until)
    if not played:
        return None  # a quiet week: no post
    everyone = duels.leaderboard(limit=10)
    week = duels.leaderboard(limit=5, since=since, until=until)
    medal = {0: "🥇", 1: "🥈", 2: "🥉"}

    def lines(rows):
        return "\n".join(f"{medal.get(i, f'`{i + 1}.`')} **{r['username']}** — {r['wins']}–{r['draws']}–{r['losses']}"
                         for i, r in enumerate(rows))
    tz = _zone()
    first, last = since.astimezone(tz).date(), (until - timedelta(days=1)).astimezone(tz).date()
    span = f"{first:%b} {first.day} – {last:%b} {last.day}"
    embed = {
        "title": "🏆 Weekly duel leaderboard", "url": f"{_site()}/duels.html", "color": GOLD,
        "description": f"**{played}** duel{'s' if played != 1 else ''} finished this week ({span}).",
        "fields": [{"name": "This week", "value": lines(week)}, {"name": "All time", "value": lines(everyone)}],
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


def start_schedule() -> None:
    """Every 10 minutes: the weekly leaderboard and the matchup of the
    day, each when it's due and its channel is set up."""
    tasks = [task for task, channel in ((weekly_leaderboard, _webhook("LEADERBOARD", "DUELS")),
                                        (daily_matchup, _webhook("DAILY"))) if channel]
    if not tasks:
        return

    def loop():
        while True:
            for task in tasks:
                try:
                    task()
                except Exception:  # noqa: BLE001 - try again next round
                    pass
            time.sleep(600)
    threading.Thread(target=loop, name="discord-schedule", daemon=True).start()


# --- the matchup of the day ------------------------------------------------------------------------
# Every day at 17:00 Budapest time (DISCORD_WEBHOOK_DAILY): the site's
# matchup of the day as a 24-hour Discord poll, and yesterday's revealed -
# what the site says next to how the server voted. A day the site was down
# at 17:00 is posted when it's back, the same day; never a past day.

DAILY_HOUR = 17


def _character_line(char_id: int) -> str:
    from backend import characters
    row = db.get_character_by_id(char_id) or {}
    name = characters.short_name(characters.display_name_for_id(char_id) or "?")
    series = (row.get("category") or "") + (f" · {row['subseries']}" if row.get("subseries") else "")
    return f"**[{_md(name)}]({_site()}/character.html?id={char_id})** ({_md(series)})"


def _verdict_line(a: int, b: int) -> str:
    from backend import characters, community
    v = characters.run_compare(a, b, None, None)
    ov = community.get_override(a, b, v.form_a, v.form_b)
    if ov is not None:
        winner = v.character_a if ov["winner_id"] == a else v.character_b
        return f"{characters.short_name(winner)} wins — overruled by admins"
    if v.favored:
        return f"{characters.short_name(v.favored)} favored — {v.label}"
    return v.label


def _vote_line(message: Optional[dict], names: Tuple[str, str]) -> Optional[str]:
    counts = {c["id"]: c["count"] for c in ((message or {}).get("poll") or {}).get("results", {}).get("answer_counts", [])}
    total = sum(counts.values())
    if not total:
        return None
    first = round(100 * counts.get(1, 0) / total)
    return f"{first}% {names[0]}, {100 - first}% {names[1]} ({total} vote{'s' if total != 1 else ''})"


def daily_matchup(now: Optional[datetime] = None) -> bool:
    """Posts today's matchup poll if it's 17:00 or later and it hasn't gone
    out yet. True when it posted."""
    url = _webhook("DAILY")
    if not url:
        return False
    from backend import characters, community, daily
    local = (now or datetime.now(timezone.utc)).astimezone(_zone())
    if local.hour < DAILY_HOUR:
        return False
    day = local.date()
    before = community.get_mark("discord_daily_post")  # "YYYY-MM-DD|message id" of the last one
    if not community.claim_mark("discord_daily", day.isoformat()):
        return False
    a, b = daily.pick(day)
    names = tuple(_clip(characters.short_name(characters.display_name_for_id(c) or "?"), 55) for c in (a, b))
    embed = {"title": "⚔️ Matchup of the day", "color": GOLD, "url": f"{_site()}/browse.html",
             "description": f"{_character_line(a)}\nvs\n{_character_line(b)}\n\n"
                            "Vote below. The site's verdict comes out tomorrow.",
             "footer": {"text": "powerscale.online"}}
    pic = _picture(a)
    if pic:
        embed["thumbnail"] = {"url": pic}
    if before and "|" in before:  # yesterday's, revealed
        prev_day, prev_id = before.split("|", 1)
        pa, pb = daily.pick(datetime.strptime(prev_day, "%Y-%m-%d").date())
        prev_names = tuple(characters.short_name(characters.display_name_for_id(c) or "?") for c in (pa, pb))
        lines = [f"The site says: **{_md(_verdict_line(pa, pb))}**"]
        votes = _vote_line(_request("GET", _message_url(url, prev_id)), prev_names)
        if votes:
            lines.append(f"You voted: {votes}")
        lines.append(f"[Open the matchup]({_site()}/compare.html?a={pa}&b={pb})")
        embed["fields"] = [{"name": _clip(f"Yesterday: {prev_names[0]} vs {prev_names[1]}", 256), "value": "\n".join(lines)}]
    poll = {"question": {"text": _clip(f"Who would win: {names[0]} or {names[1]}?", 300)},
            "answers": [{"poll_media": {"text": n}} for n in names], "duration": 24, "allow_multiselect": False}
    sep = "&" if "?" in url else "?"
    posted = _request("POST", f"{url}{sep}wait=true", message={"embeds": [embed], "poll": poll})
    if posted is None:  # if Discord won't take a poll with an embed, post them one after the other
        _request("POST", url, embed)
        posted = _request("POST", f"{url}{sep}wait=true", message={"poll": poll})
    if posted and posted.get("id"):
        community.set_mark("discord_daily_post", f"{day.isoformat()}|{posted['id']}")
    return True


# --- update notes --------------------------------------------------------------------------------
# frontend/updates.json holds a plain-language note for each update
# (newest first): {"id": "2026-09-28.1", "date": "2026-09-28", "title":
# "...", "changes": ["...", ...]}. Ids sort by date, then a counter. When a
# new version of the site starts, the notes it hasn't announced yet go to
# the update channel - so a post appears once the changes are live. The
# very first run announces only the newest note, not the whole history.

UPDATES_FILE = Path(__file__).parent.parent / "frontend" / "updates.json"
MAX_NOTES_AT_ONCE = 5


def update_embed(note: dict) -> dict:
    changes = "\n".join(f"• {c}" for c in note.get("changes") or [])
    day = datetime.strptime(note["date"], "%Y-%m-%d")
    return {"title": _clip(f"🆕 {note['title']}", 256), "url": _site(), "color": GOLD,
            "description": _clip(changes, 4000), "footer": {"text": f"powerscale.online · {day:%b} {day.day}, {day.year}"}}


def announce_updates() -> int:
    """Posts the update notes not announced yet; how many it posted."""
    url = _webhook("UPDATES")
    if not url or not UPDATES_FILE.exists():
        return 0
    from backend import community
    notes = sorted(json.loads(UPDATES_FILE.read_text(encoding="utf-8")), key=lambda n: n["id"])
    if not notes:
        return 0
    before = community.get_mark("discord_updates")
    if not community.claim_mark("discord_updates", notes[-1]["id"]):
        return 0  # nothing new, or another copy of the site got there first
    fresh = [n for n in notes if before is None or n["id"] > before]
    fresh = fresh[-1:] if before is None else fresh[-MAX_NOTES_AT_ONCE:]
    for note in fresh:
        _send(url, update_embed(note))
    return len(fresh)


def start_updates() -> None:
    if not _webhook("UPDATES"):
        return

    def run():
        time.sleep(5)  # let the site finish starting
        try:
            announce_updates()
        except Exception:  # noqa: BLE001 - tried again on the next start
            pass
    threading.Thread(target=run, name="discord-updates", daemon=True).start()


# --- open games looking for players ---------------------------------------------------------
# A game open to anyone gets a "wants to duel" post with a link to join.
# It's kept current - seats left - and deleted once the game is full,
# cancelled or expired, so the channel only ever shows games you can
# still join. At most one post per player every 10 minutes. The calls
# run one at a time, in order, so a game that fills a moment after it's
# created still has its post removed.

LOBBY_EVERY = 600  # seconds between one player's posts
_lobby = ThreadPoolExecutor(max_workers=1, thread_name_prefix="discord-lobby")
_last_lobby_post: dict = {}


def _message_url(webhook: str, message_id: str) -> str:
    base, _, query = webhook.partition("?")
    return f"{base}/messages/{message_id}" + (f"?{query}" if query else "")


def lobby_embed(g) -> dict:
    kind = f"{'Draft' if g.mode == 'draft' else 'Prediction'} duel · {g.format}"
    how = ("Everyone is dealt 4 characters and picks the strongest, five rounds."
           if g.mode == "draft" else "Five matchups, 20 seconds each: call who the site says wins.")
    joined = [_md(p.username) for p in g.players]
    lines = [f"**{kind}** · {g.seats_left} seat{'s' if g.seats_left != 1 else ''} left", how]
    if len(joined) > 1:
        lines.append("In: " + ", ".join(joined))
    url = f"{_site()}/duels.html?game={g.id}"
    lines.append(f"**[Join the game →]({url})**")
    embed = {"title": _clip(f"⚔️ {g.creator} wants to duel", 256), "url": url, "color": GOLD,
             "description": "\n".join(lines),
             "footer": {"text": "Open to anyone · powerscale.online"}}
    creator = next((p for p in g.players if p.username == g.creator), None)
    if creator and creator.avatar_url:
        embed["thumbnail"] = {"url": _site() + creator.avatar_url}
    return embed


def _set_message(game_id: int, message_id: Optional[str]) -> None:
    from sqlalchemy import update
    from backend import community, duels
    with community.engine.begin() as conn:
        conn.execute(update(duels.games).where(duels.games.c.id == game_id).values(discord_msg=message_id))


def _lobby_post(url: str, game_id: int) -> None:
    from backend import duels_api
    g = duels_api._one(game_id, None)
    if g.status != "open" or g.private:
        return
    now = time.time()
    if now - _last_lobby_post.get(g.creator, 0) < LOBBY_EVERY:
        return
    _last_lobby_post[g.creator] = now
    sep = "&" if "?" in url else "?"
    message = _request("POST", f"{url}{sep}wait=true", lobby_embed(g))  # wait: Discord sends the message back
    if message and message.get("id"):
        _set_message(game_id, str(message["id"]))


def _lobby_refresh(url: str, game_id: int) -> None:
    from sqlalchemy import select
    from backend import community, duels, duels_api
    with community.reader.connect() as conn:
        message_id = conn.execute(select(duels.games.c.discord_msg).where(duels.games.c.id == game_id)).scalar()
    if not message_id:
        return
    g = duels_api._one(game_id, None)
    if g.status == "open":
        _request("PATCH", _message_url(url, message_id), lobby_embed(g))
    else:
        _request("DELETE", _message_url(url, message_id))
        _set_message(game_id, None)


def _lobby_task(fn, game_id: int) -> None:
    url = _webhook("LOBBY", "DUELS")
    if not url:
        return

    def run():
        try:
            fn(url, game_id)
        except Exception:  # noqa: BLE001 - the game works the same without its post
            pass
    _lobby.submit(run)


_lobby_channel: dict = {}


def lobby_channel_id() -> Optional[str]:
    """The channel open games are posted in (asked of Discord once)."""
    url = _webhook("LOBBY", "DUELS")
    if not url:
        return None
    if url not in _lobby_channel:
        info = _request("GET", url.partition("?")[0]) or {}
        _lobby_channel[url] = info.get("channel_id")
    return _lobby_channel[url]


def lobby_open(game_id: int) -> None:
    _lobby_task(_lobby_post, game_id)


def lobby_update(game_id: int) -> None:
    """After a join, leave, cancel or expiry: update the post, or remove it."""
    _lobby_task(_lobby_refresh, game_id)
