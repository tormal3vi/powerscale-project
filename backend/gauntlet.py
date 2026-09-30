"""The gauntlet: how far one character climbs a ladder of ever-stronger
opponents. It fights them in order, lowest Tier first, and the run ends at
its first loss or at a fight too close to call - a gauntlet takes clear
wins. The result is how many it beat.

Opponents come from one of three places:
- random: one from each tenth of the whole roster by Tier, so rung 1 is
  among the weakest characters on the site and rung 10 among the
  strongest - the same yardstick for everyone;
- series: that series' best-known characters (most forms first, as the
  matchup of the day judges it), up to ten;
- custom: up to ten characters someone picked.
Every opponent is one the calculator can compare the character with.
"""

import functools
import random
from typing import Dict, List, Optional, Sequence

import db
from backend import characters, community

RUNGS = 10


class GauntletError(Exception):
    pass


def _ranked() -> tuple:
    """The scorable roster, weakest to strongest: (id, tier, series)."""
    return _sorted(characters.scorable_pool())


@functools.lru_cache(maxsize=2)
def _sorted(pool: tuple) -> tuple:  # keyed on the roster itself: a new roster, a new order
    return tuple(sorted(pool, key=lambda c: (c[1], c[0])))


def _tiers() -> Dict[int, float]:
    return {cid: tier for cid, tier, _ in _ranked()}


def fight(char_id: int, opponent: int, form: Optional[str] = None) -> dict:
    """One fight, from the challenger's side: outcome win | loss | even
    (too close to call) | none (not enough stats), and what decided it."""
    try:
        v = characters.run_compare(char_id, opponent, form, None)
    except ValueError:
        return {"outcome": "none", "verdict": "Not enough stats", "form": None}
    ov = community.get_override(char_id, opponent, v.form_a, v.form_b)
    if ov is not None:
        won = ov["winner_id"] == char_id
        name = v.character_a if won else v.character_b
        return {"outcome": "win" if won else "loss", "form": v.form_b,
                "verdict": f"{characters.short_name(name)} wins — overruled by admins"}
    if v.composite is None:
        return {"outcome": "none", "verdict": "Not enough stats to compare", "form": v.form_b}
    if v.favored is None:
        return {"outcome": "even", "verdict": v.label, "form": v.form_b}
    return {"outcome": "win" if v.composite > 0 else "loss", "form": v.form_b,
            "verdict": f"{characters.short_name(v.favored)} favored — {v.label}"}


def _title(cid: int) -> str:
    """The page title without its "(...)" version: one key per character."""
    row = db.get_character_by_id(cid) or {}
    return characters._title_key(row.get("source_url") or "") or f"id:{cid}"


def _comparable(char_id: int, opponent: int, form: Optional[str]) -> bool:
    return opponent != char_id and fight(char_id, opponent, form)["outcome"] != "none"


def ladder(char_id: int, source: str = "random", series: Optional[str] = None, custom: Sequence[int] = (),
           seed: Optional[int] = None, form: Optional[str] = None, excluded: frozenset = frozenset()) -> List[int]:
    """The opponents, lowest Tier first."""
    tiers = _tiers()
    if source == "random":
        rnd = random.Random(seed)
        pool = [c for c in _ranked() if c[2] not in excluded and c[0] != char_id]
        if len(pool) < RUNGS * 3:
            raise GauntletError("Too few characters to build a gauntlet from")
        size = len(pool) / RUNGS
        out = []
        for rung in range(RUNGS):
            bucket = [c[0] for c in pool[int(rung * size):int((rung + 1) * size)]]
            for _ in range(8):
                pick = rnd.choice(bucket)
                if pick not in out and _comparable(char_id, pick, form):
                    out.append(pick)
                    break
        return sorted(out, key=lambda cid: (tiers.get(cid, 0), cid))
    if source == "series":
        from backend import daily
        forms = daily._form_counts()
        members = [cid for cid, _, s in _ranked() if s == series and cid != char_id]
        if not members:
            raise GauntletError(f"No characters to fight in {series or 'that series'}")
        known = sorted(members, key=lambda cid: (-forms.get(cid, 1), cid))
        out, seen = [], set()
        for cid in known:
            if len(out) == RUNGS:
                break
            key = _title(cid)  # one Link, not every game's: Link (Ocarina of Time), Link (Wind Waker)...
            if key not in seen and _comparable(char_id, cid, form):
                out.append(cid)
                seen.add(key)
        return sorted(out, key=lambda cid: (tiers.get(cid, 0), -forms.get(cid, 1)))
    if source == "custom":
        out = []
        for cid in custom:
            if cid not in out and cid in tiers and _comparable(char_id, cid, form):
                out.append(cid)
        if not out:
            raise GauntletError("Pick opponents the character can be compared with")
        return sorted(out[:RUNGS], key=lambda cid: (tiers.get(cid, 0), cid))
    raise GauntletError("Unknown opponent source")


def run(char_id: int, opponents: Sequence[int], form: Optional[str] = None) -> dict:
    """{"fights": [...], "climbed": wins before the first loss or draw}.
    Every fight is worked out, including the ones after the run ended
    (reached: False), so a result can show what was still ahead."""
    fights, climbed, alive = [], 0, True
    for rung, opponent in enumerate(opponents, start=1):
        f = fight(char_id, opponent, form)
        fights.append({"rung": rung, "opponent": opponent, **f, "reached": alive})
        if alive:
            if f["outcome"] == "win":
                climbed += 1
            else:
                alive = False
    return {"fights": fights, "climbed": climbed, "total": len(opponents)}


def random_challenger(seed: Optional[int] = None, excluded: frozenset = frozenset()) -> int:
    """Someone well known enough to guess about: 2+ forms when possible."""
    from backend import daily
    rnd = random.Random(seed)
    forms = daily._form_counts()
    pool = [cid for cid, _, s in _ranked() if s not in excluded]
    known = [cid for cid in pool if forms.get(cid, 1) >= 2]
    return rnd.choice(known or pool)
