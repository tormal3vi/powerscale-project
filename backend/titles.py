"""Titles: earned, colored labels shown next to a username.

- Duel wins: Rookie (1) up to Legend (1,000).
- Approved overrule suggestions (a ticket that led to an overrule):
  Scout (1) up to Oracle (30).
- Weekly Champion: last week's #1 on the duel leaderboard (the week the
  Monday Discord post covers), held until the next one.
- Founder: one of the site's first 100 accounts.

Everyone shows their rarest title unless they pick one in Profile
settings (or pick none). Worked out for every user at once and kept for a
minute: a handful of grouped queries, whatever the page shows.
"""

import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select, update

from backend import community

# (needed, name, color); rarer ones last.
DUEL_TITLES = [
    (1, "Rookie", "slate"), (5, "Challenger", "green"), (10, "Contender", "teal"), (25, "Fighter", "sky"),
    (50, "Veteran", "blue"), (100, "Champion", "violet"), (200, "Elite", "purple"), (300, "Master", "pink"),
    (500, "Grandmaster", "orange"), (750, "Mythic", "red"), (1000, "Legend", "legend"),
]
OVERRULE_TITLES = [(1, "Scout", "teal"), (5, "Analyst", "blue"), (15, "Arbiter", "purple"), (30, "Oracle", "gold")]
FOUNDERS = 100

# How rare each title is, for picking the one shown by default.
_RANK = {f"duels:{n}": 10 + i * 10 for i, (n, _, _) in enumerate(DUEL_TITLES)}
_RANK.update({"overrules:1": 25, "overrules:5": 55, "overrules:15": 85, "overrules:30": 105,
              "founder": 45, "champion": 95})
_NAMES = {f"duels:{n}": (name, color) for n, name, color in DUEL_TITLES}
_NAMES.update({f"overrules:{n}": (name, color) for n, name, color in OVERRULE_TITLES})
_NAMES.update({"founder": ("Founder", "founder"), "champion": ("Weekly Champion", "gold")})
HIDDEN = "none"  # chosen: show no title

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "users": None}
TTL = 60


def title(key: str) -> dict:
    name, color = _NAMES[key]
    return {"key": key, "name": name, "color": color}


def _weekly_champion(conn) -> Optional[str]:
    """Last posted week's #1 (username), unless it was a tie at the top."""
    from backend import discord_webhooks, duels
    _, since, until = discord_webhooks.week_slot(datetime.now(timezone.utc))
    top = duels.leaderboard(limit=2, since=since, until=until)
    if not top:
        return None
    key = lambda r: (r["wins"], -r["losses"], r["draws"])  # noqa: E731
    return top[0]["username"] if len(top) == 1 or key(top[0]) != key(top[1]) else None


def _compute() -> Dict[str, dict]:
    """{lowercased username: {"id", "earned": [keys], "chosen", "wins", "overrules"}}."""
    from backend import duels
    users, posts = community.users, community.posts
    with community.reader.connect() as conn:
        people = {r["id"]: r for r in conn.execute(
            select(users.c.id, users.c.username, users.c.title)).mappings()}
        credits = dict(conn.execute(select(posts.c.credit_user_id, func.count()).where(
            posts.c.credit_user_id.isnot(None)).group_by(posts.c.credit_user_id)).all())
        champion = _weekly_champion(conn)
    founders = set(sorted(people)[:FOUNDERS])
    records = duels.records()
    out = {}
    for uid, p in people.items():
        wins = records.get(uid, {}).get("wins", 0)
        overrules = credits.get(uid, 0)
        earned = [f"duels:{n}" for n, _, _ in DUEL_TITLES if wins >= n]
        earned += [f"overrules:{n}" for n, _, _ in OVERRULE_TITLES if overrules >= n]
        if uid in founders:
            earned.append("founder")
        if champion and p["username"] == champion:
            earned.append("champion")
        out[p["username"].lower()] = {"id": uid, "earned": earned, "chosen": p["title"],
                                      "wins": wins, "overrules": overrules}
    return out


def _everyone() -> Dict[str, dict]:
    with _lock:
        if _cache["users"] is not None and time.monotonic() - _cache["at"] < TTL:
            return _cache["users"]
    users = _compute()
    with _lock:
        _cache.update(users=users, at=time.monotonic())
    return users


def forget() -> None:
    """After something that changes titles: a finished duel, an overrule,
    someone picking their title."""
    with _lock:
        _cache["users"] = None


def shown(username: Optional[str]) -> Optional[dict]:
    """The title next to this name, or None."""
    u = _everyone().get((username or "").lower())
    if not u or not u["earned"] or u["chosen"] == HIDDEN:
        return None
    key = u["chosen"] if u["chosen"] in u["earned"] else max(u["earned"], key=lambda k: _RANK[k])
    return title(key)


def summary(username: str) -> dict:
    """Everything a profile shows: the title shown, every one earned
    (rarest first), the next ones to work towards, and the choice made."""
    u = _everyone().get(username.lower())
    if u is None:
        return {"shown": None, "earned": [], "next": [], "chosen": None}
    upcoming = []
    for kind, ladder, have, word in (("duels", DUEL_TITLES, u["wins"], "duel wins"),
                                     ("overrules", OVERRULE_TITLES, u["overrules"], "approved overrule suggestions")):
        nxt = next(((n, name, color) for n, name, color in ladder if have < n), None)
        if nxt:
            upcoming.append({**title(f"{kind}:{nxt[0]}"), "have": have, "need": nxt[0], "what": word})
    return {"shown": shown(username), "chosen": u["chosen"],
            "earned": [title(k) for k in sorted(u["earned"], key=lambda k: -_RANK[k])], "next": upcoming}


def choose(user_id: int, key: Optional[str]) -> None:
    """None: the rarest one automatically; HIDDEN: none; else one of theirs."""
    if key not in (None, HIDDEN) and key not in _NAMES:
        raise ValueError("Unknown title")
    with community.engine.begin() as conn:
        name = conn.execute(select(community.users.c.username).where(community.users.c.id == user_id)).scalar()
        if key not in (None, HIDDEN) and key not in (_everyone().get((name or "").lower()) or {}).get("earned", []):
            raise ValueError("You haven't earned that title yet")
        conn.execute(update(community.users).where(community.users.c.id == user_id).values(title=key))
    forget()
