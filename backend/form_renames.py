"""Moves community data onto a character's new form names.

Re-parsing a page can rename a character's forms - the wiki page changed,
or the parser learned to split it. Overrules, Board posts, tickets and
duel rounds store form names, so rows that still name a form the
character no longer has would silently stop matching (an overrule would
stop applying). Each batch below moves them, once, when the site starts:
the database remembers the last batch applied (site_marks), and applying
twice changes nothing anyway.
"""

import json
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, update
from sqlalchemy.exc import IntegrityError

import calculator
import db
from backend import community, duels, tickets

# batch id -> (character id, old form, new form - None: the character's
# default form, the one a page shows first).
RENAMES: Dict[str, List[Tuple[int, str, Optional[str]]]] = {
    # The parser learned "Keys:" (plural): these 32 went from one "Base"
    # form to their real ones (Johnny Joestar's Act 1-4, Frieza's sagas...).
    "2026-09-28": [(cid, "Base", None) for cid in (
        580, 583, 728, 809, 934, 941, 2649, 2660, 2830, 4908, 5023, 5472, 5480, 5790, 5877, 5880,
        6196, 6243, 6312, 6342, 6343, 6376, 6517, 6821, 6837, 6878, 6906, 6917, 6957, 6977, 6986, 7011)],
}

# (table, character column, form column) - every place a form name is kept.
_COLUMNS = [
    (community.overrides, "char_low", "form_low"), (community.overrides, "char_high", "form_high"),
    (community.posts, "char_a", "form_a"), (community.posts, "char_b", "form_b"),
    (tickets.tickets, "char_low", "form_low"), (tickets.tickets, "char_high", "form_high"),
    (duels.game_rounds, "char_a", "form_a"), (duels.game_rounds, "char_b", "form_b"),
]


def _forms(char_id: int) -> Tuple[List[str], Optional[str]]:
    row = db.get_character_by_id(char_id)
    normalized = json.loads(row["normalized_json"]) if row else {}
    if not normalized.get("forms"):
        return [], None
    return [f.get("name") for f in normalized["forms"]], calculator.select_form(normalized).get("name")


def rename(char_id: int, old: str, new: str) -> int:
    """Moves every stored `old` form of `char_id` to `new`; rows moved."""
    moved = 0
    for table, char_col, form_col in _COLUMNS:
        try:
            with community.engine.begin() as conn:
                moved += conn.execute(update(table).where(and_(
                    table.c[char_col] == char_id, table.c[form_col] == old)).values({form_col: new})).rowcount
        except IntegrityError:
            pass  # that matchup already exists under the new name: keep both as they are
    return moved


def apply() -> int:
    """Applies the batches not applied yet; rows moved."""
    done = community.get_mark("form_renames") or ""
    moved = 0
    for batch in sorted(b for b in RENAMES if b > done):
        for char_id, old, new in RENAMES[batch]:
            forms, default = _forms(char_id)
            if not forms or old in forms:
                continue  # nothing to move: the old name is still a form (or the character is gone)
            target = new or default
            if target and target in forms:
                moved += rename(char_id, old, target)
        community.claim_mark("form_renames", batch)
    if moved:
        community._forget("overrides")
    return moved
