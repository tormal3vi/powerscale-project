"""The matchup of the day: the same pair for everyone on a given day
(Budapest time) - Browse's "Matchup of the day" button and the Discord
post. Two characters of similar tiers from different series, whose
verdict isn't a walkover, so there's something to argue about.
"""

import json
import random
import threading
from datetime import date, datetime, timezone
from typing import Dict, Tuple

import db
from backend import characters, duels

_picked: Dict[date, Tuple[int, int]] = {}
_lock = threading.Lock()


def _zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Budapest")
    except Exception:  # noqa: BLE001 - no time zone data: UTC is close enough
        return timezone.utc


_forms: Dict[int, int] = {}


def _form_counts() -> Dict[int, int]:
    with _lock:
        if _forms:
            return _forms
    with db.connect() as conn:
        counts = {r["id"]: len(json.loads(r["normalized_json"]).get("forms") or []) or 1
                  for r in conn.execute("SELECT id, normalized_json FROM characters")}
    with _lock:
        _forms.update(counts)
    return counts


def today() -> date:
    return datetime.now(timezone.utc).astimezone(_zone()).date()


def pick(day: date) -> Tuple[int, int]:
    """(character a, character b) for `day` - seeded by the date, so it's
    the same for everyone (kept for the day once chosen, even if the
    roster grows meanwhile)."""
    with _lock:
        if day in _picked:
            return _picked[day]
    everyone = sorted(characters.scorable_pool())  # (id, tier, series), in a fixed order
    forms = _form_counts()
    rnd = random.Random(f"powerscale-daily-{day.isoformat()}")
    chosen = None
    # Well-known characters first: main characters tend to have several
    # forms (451 have 3+), one-off side characters just one.
    for least in (3, 2, 1):
        pool = [c for c in everyone if forms.get(c[0], 1) >= least]
        for _ in range(40):
            a, tier, series = rnd.choice(pool)
            near = [cid for cid, t, s in pool if s != series and abs(t - tier) <= duels.RANDOM_TIER_SPREAD]
            if not near:
                continue
            b = rnd.choice(near)
            v = characters.run_compare(a, b, None, None)
            if v.composite is None or v.label == "Overwhelming favorite":
                continue  # no verdict, or nothing to argue about
            chosen = (a, b)
            break
        if chosen:
            break
    if chosen is None:  # an unlucky day: any two
        chosen = tuple(cid for cid, _, _ in rnd.sample(everyone, 2))
    with _lock:
        _picked[day] = chosen
        for old in [d for d in _picked if d < day]:
            del _picked[old]
    return chosen
