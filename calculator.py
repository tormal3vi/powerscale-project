"""Phase 5: the "who would win" calculator.

Combines the Tier/Attack Potency/Speed/Durability normalization
(Phase 2) and the multi-form data model (Phase 4 follow-up) into a
weighted stat comparison between two characters, plus a curated-
keyword ability/weakness flag layer that is surfaced alongside the
verdict but never feeds into its number. The whole design below -
weights, the missing-stat policy, the confidence phrasing, and the
decision to keep ability flags score-free - was reviewed and signed
off before this was written; see README's "Who would win" calculator
section for the full rationale.

Design summary:
- Four scored axes: attack_potency, durability, speed, tier. Weighted
  35/25/25/15 - AP highest because nothing else matters if you can't
  hurt the opponent; Durability and Speed equal and second; Tier
  lowest because the wiki defines Tier *as a function of* AP and
  Durability, so weighting it heavily would double-count that signal.
- Each axis's raw delta (in that axis's own log10 units) is squashed
  through tanh(delta / TANH_SCALE) into a bounded [-1, +1] "advantage"
  before weighting, so one absurdly lopsided axis can't mathematically
  drown out the others just by having more zeros.
- Missing stats: baseline first, falling back to peak (flagged); if
  neither side has a value for an axis, that axis is dropped and its
  weight is redistributed across the axes that do have data. Fewer
  than 2 comparable axes -> no verdict at all, not a guess. 2-3 axes
  -> verdict still computes, but capped below the top confidence band.
- Ability/weakness text is scanned for a small curated tag list
  (regeneration, immortality, reality warping, ...) and surfaced as
  flags + the full raw text - never as a numeric modifier. A keyword
  match can't tell "Low-Godly regeneration" from a throwaway heal, or
  whether the opponent's kit already counters it, so treating a match
  as a score bump would be pretending to a precision this system
  doesn't have.
- Known limitation, stated up front rather than papered over:
  powers_and_abilities/weaknesses live only on the flat top-level
  character record (see parser.py's CharacterForm), not per form - so
  ability flags describe the character's whole page, not specifically
  the form selected for the numeric comparison.
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import db

# --- tunables (see module docstring / README for the reasoning) ------------

AXES: Tuple[str, ...] = ("attack_potency", "durability", "speed", "tier")

AXIS_WEIGHTS: Dict[str, float] = {
    "attack_potency": 0.35,
    "durability": 0.25,
    "speed": 0.25,
    "tier": 0.15,
}

TANH_SCALE = 5.0            # per-axis delta (log10 units) that saturates advantage
MIN_AXES_FOR_VERDICT = 2    # fewer comparable axes than this -> no verdict at all

_LABEL_BANDS: List[Tuple[float, str]] = [
    (0.80, "Overwhelming favorite"),
    (0.55, "Clear favorite"),
    (0.30, "Favored"),
    (0.10, "Slight edge"),
    (0.0, "Too close to call"),
]
_CONFIDENCE_HINTS: Dict[str, str] = {
    "Overwhelming favorite": "~90%+",
    "Clear favorite": "~80%",
    "Favored": "~65%",
    "Slight edge": "~55%",
    "Too close to call": "toss-up",
}

# (display label, keyword variants to match, case-insensitively, as substrings)
ABILITY_TAGS: List[Tuple[str, List[str]]] = [
    ("Regeneration", ["regeneration"]),
    ("Immortality", ["immortality"]),
    ("Reality Warping", ["reality warping"]),
    ("Acausality", ["acausality"]),
    ("Non-Corporeal", ["non-corporeal", "non corporeal", "incorporeal"]),
    ("BFR (Battlefield Removal)", ["bfr", "battlefield removal"]),
    ("Existence Erasure", ["existence erasure"]),
    ("Petrification", ["petrification"]),
    ("Durability Negation", ["durability negation", "ignores durability", "ignore durability", "ignoring durability"]),
    ("One-Hit-Kill / Instant Death", ["one-hit-kill", "one hit kill", "instant death"]),
    ("Probability Manipulation", ["probability manipulation"]),
    ("Resistance", ["resistance to"]),
]


# --- result types ------------------------------------------------------------

@dataclass
class AxisComparison:
    axis: str
    a_value: Optional[float] = None
    a_source: str = "missing"   # "baseline" | "peak" | "missing"
    b_value: Optional[float] = None
    b_source: str = "missing"
    delta: Optional[float] = None        # a_value - b_value, raw log10 units
    advantage: Optional[float] = None    # tanh(delta / TANH_SCALE); None if axis excluded
    weight_used: Optional[float] = None  # effective (redistributed) weight actually applied


@dataclass
class AbilityFlag:
    tag: str
    characters: List[str] = field(default_factory=list)  # names this tag matched on


@dataclass
class Verdict:
    character_a: str
    character_b: str
    form_a: str
    form_b: str
    axis_comparisons: List[AxisComparison]
    axes_used: int
    composite: Optional[float]   # weighted sum in [-1, 1]; None if insufficient data
    label: str                   # band name, or "Insufficient data"
    confidence_hint: str         # e.g. "~80%" or "toss-up" or "n/a"
    favored: Optional[str]       # character name, or None (tie / insufficient data)
    partial_data: bool
    ability_flags: List[AbilityFlag]
    notes: List[str]


# --- loading real characters from the DB ------------------------------------

CharacterIdentifier = Union[int, str]


def _parse_identifier(text: str) -> CharacterIdentifier:
    try:
        return int(text)
    except ValueError:
        return text


def load_character(identifier: CharacterIdentifier, db_path: Path = db.DB_PATH) -> Tuple[dict, dict]:
    """Returns (raw_dict, normalized_dict) for one character, looked up by
    DB id (int) or exact name (str, case-insensitive). Names can collide
    across distinct source pages, so an ambiguous name raises rather than
    silently picking one - the caller must retry with an id."""
    if isinstance(identifier, int):
        row = db.get_character_by_id(identifier, db_path)
        if row is None:
            raise ValueError(f"No character with id {identifier}")
    else:
        matches = db.find_characters_by_name(identifier, db_path)
        if not matches:
            raise ValueError(f"No character found matching {identifier!r}")
        if len(matches) > 1:
            options = "; ".join(f"id={m['id']} [{m['category']}] {m['source_url']}" for m in matches)
            raise ValueError(
                f"{identifier!r} matches {len(matches)} characters - specify an id instead: {options}"
            )
        row = db.get_character_by_id(matches[0]["id"], db_path)

    return json.loads(row["raw_json"]), json.loads(row["normalized_json"])


# --- form selection ----------------------------------------------------------

def select_form(normalized: dict, form_name: Optional[str] = None) -> dict:
    """Picks a form dict from normalized["forms"]. Explicit form_name wins
    if given (exact match, or raises listing what's available). Otherwise
    defaults to the highest tier.baseline - deliberately not "the fairest
    form for a matchup", which has no objectively correct answer. Forms
    with no Tier score at all sort last, never chosen by default unless
    every form is equally unscored."""
    forms = normalized.get("forms") or []
    if not forms:
        name = normalized.get("name") or "this character"
        raise ValueError(f"{name} has no forms at all - a data problem, not a valid input")

    if form_name is not None:
        for f in forms:
            if f.get("name") == form_name:
                return f
        available = ", ".join(f.get("name", "?") for f in forms)
        name = normalized.get("name") or "this character"
        raise ValueError(f"{name} has no form named {form_name!r}. Available forms: {available}")

    def _tier_baseline(f: dict) -> float:
        tier = f.get("tier") or {}
        baseline = tier.get("baseline")
        return baseline if baseline is not None else float("-inf")

    return max(forms, key=_tier_baseline)


# --- stat comparison -----------------------------------------------------

def _axis_value(form: dict, axis: str) -> Tuple[Optional[float], str]:
    rng = form.get(axis) or {}
    if rng.get("baseline") is not None:
        return rng["baseline"], "baseline"
    if rng.get("peak") is not None:
        return rng["peak"], "peak"
    return None, "missing"


def _compare_axis(axis: str, form_a: dict, form_b: dict) -> AxisComparison:
    a_val, a_src = _axis_value(form_a, axis)
    b_val, b_src = _axis_value(form_b, axis)
    comp = AxisComparison(axis=axis, a_value=a_val, a_source=a_src, b_value=b_val, b_source=b_src)
    if a_val is not None and b_val is not None:
        comp.delta = a_val - b_val
        comp.advantage = math.tanh(comp.delta / TANH_SCALE)
    return comp


def _band_for(magnitude: float) -> str:
    for threshold, label in _LABEL_BANDS:
        if magnitude >= threshold:
            return label
    return _LABEL_BANDS[-1][1]


def compare_forms(name_a: str, form_a: dict, name_b: str, form_b: dict) -> Verdict:
    """Pure stat comparison between two already-selected forms - no DB
    access, no ability text. This is the core scoring logic; see
    compare_characters() for the DB-loading + ability-flag wrapper."""
    comparisons = [_compare_axis(axis, form_a, form_b) for axis in AXES]
    usable = [c for c in comparisons if c.advantage is not None]
    axes_used = len(usable)

    notes: List[str] = []
    for c in comparisons:
        if c.a_source == "peak":
            notes.append(f"{name_a}'s {c.axis} has no baseline score - used its peak value instead.")
        if c.b_source == "peak":
            notes.append(f"{name_b}'s {c.axis} has no baseline score - used its peak value instead.")
    if form_a.get("is_omnipresent"):
        notes.append(
            f"{name_a} is flagged Omnipresent - existing everywhere at once isn't a numeric Speed "
            f"value, so this comparison's Speed axis (if used) doesn't capture that at all."
        )
    if form_b.get("is_omnipresent"):
        notes.append(
            f"{name_b} is flagged Omnipresent - existing everywhere at once isn't a numeric Speed "
            f"value, so this comparison's Speed axis (if used) doesn't capture that at all."
        )

    form_a_name = form_a.get("name", "?")
    form_b_name = form_b.get("name", "?")

    if axes_used < MIN_AXES_FOR_VERDICT:
        return Verdict(
            character_a=name_a, character_b=name_b,
            form_a=form_a_name, form_b=form_b_name,
            axis_comparisons=comparisons, axes_used=axes_used,
            composite=None, label="Insufficient data", confidence_hint="n/a",
            favored=None, partial_data=True,
            ability_flags=[], notes=notes,
        )

    total_weight = sum(AXIS_WEIGHTS[c.axis] for c in usable)
    composite = 0.0
    for c in usable:
        c.weight_used = AXIS_WEIGHTS[c.axis] / total_weight
        composite += c.weight_used * c.advantage

    partial_data = axes_used < len(AXES)
    band = _band_for(abs(composite))
    if partial_data and band == _LABEL_BANDS[0][1]:
        # Can't reach the top confidence band off partial data - one or two
        # extreme axes shouldn't be able to buy a maximally-confident verdict.
        band = _LABEL_BANDS[1][1]

    favored = None
    if band != "Too close to call":
        favored = name_a if composite > 0 else name_b

    return Verdict(
        character_a=name_a, character_b=name_b,
        form_a=form_a_name, form_b=form_b_name,
        axis_comparisons=comparisons, axes_used=axes_used,
        composite=composite, label=band, confidence_hint=_CONFIDENCE_HINTS[band],
        favored=favored, partial_data=partial_data,
        ability_flags=[], notes=notes,
    )


# --- ability/weakness flags (score-free, see module docstring) -------------

def _character_text_blob(raw: dict) -> str:
    parts = list(raw.get("powers_and_abilities") or [])
    weaknesses = raw.get("weaknesses")
    if weaknesses:
        parts.append(weaknesses)
    return " ".join(parts).lower()


def ability_flags(
    raw_a: dict, raw_b: dict, name_a: Optional[str] = None, name_b: Optional[str] = None,
) -> List[AbilityFlag]:
    """Curated-keyword scan only - flags presence, never scores it. See
    module docstring for why this deliberately doesn't touch compare_forms's
    numeric output, and its real limitations (no notion of degree, no
    counter-matching, not scoped to the form used in the comparison)."""
    blob_a = _character_text_blob(raw_a)
    blob_b = _character_text_blob(raw_b)
    name_a = name_a or raw_a.get("name") or "Character A"
    name_b = name_b or raw_b.get("name") or "Character B"

    flags: List[AbilityFlag] = []
    for tag, keywords in ABILITY_TAGS:
        on_a = any(kw in blob_a for kw in keywords)
        on_b = any(kw in blob_b for kw in keywords)
        if not on_a and not on_b:
            continue
        characters = []
        if on_a:
            characters.append(name_a)
        if on_b:
            characters.append(name_b)
        flags.append(AbilityFlag(tag=tag, characters=characters))
    return flags


# --- full DB-backed entry point ----------------------------------------------

def compare_characters(
    char_a: CharacterIdentifier,
    char_b: CharacterIdentifier,
    form_a: Optional[str] = None,
    form_b: Optional[str] = None,
    db_path: Path = db.DB_PATH,
    name_a: Optional[str] = None,
    name_b: Optional[str] = None,
) -> Verdict:
    """name_a/name_b override the names shown in the verdict - for when
    two different characters share the same stored name (e.g. three
    "Ichigo Kurosaki" pages), which would otherwise read as "Ichigo
    Kurosaki favored" with no way to tell which one."""
    raw_a, norm_a = load_character(char_a, db_path)
    raw_b, norm_b = load_character(char_b, db_path)

    selected_a = select_form(norm_a, form_a)
    selected_b = select_form(norm_b, form_b)

    name_a = name_a or norm_a.get("name") or raw_a.get("name") or str(char_a)
    name_b = name_b or norm_b.get("name") or raw_b.get("name") or str(char_b)

    verdict = compare_forms(name_a, selected_a, name_b, selected_b)
    verdict.ability_flags = ability_flags(raw_a, raw_b, name_a, name_b)
    if verdict.ability_flags:
        verdict.notes.append(
            "Ability/weakness flags reflect each character's whole-page text, not specifically the "
            "form selected above - abilities aren't tracked per-form in the current data model."
        )
    return verdict


# --- formatting ----------------------------------------------------------

def format_verdict(v: Verdict) -> str:
    lines: List[str] = []
    lines.append(f"{v.character_a} ({v.form_a})  vs  {v.character_b} ({v.form_b})")
    lines.append("=" * 60)

    if v.composite is None:
        lines.append(f"Verdict: {v.label} - fewer than {MIN_AXES_FOR_VERDICT} stats are comparable "
                      f"between these two ({v.axes_used}/{len(AXES)} usable). No meaningful verdict.")
    else:
        who = v.favored or "Neither side"
        partial = "  (based on partial data - see below)" if v.partial_data else ""
        lines.append(f"Verdict: {who} favored - {v.label} ({v.confidence_hint}){partial}")
        lines.append("(Heuristic estimate from normalized stats - not a calibrated win probability.)")

    lines.append("")
    lines.append(f"Stat breakdown ({v.axes_used}/{len(AXES)} axes used):")
    for c in v.axis_comparisons:
        a_str = "unscored" if c.a_value is None else f"{c.a_value:.2f} ({c.a_source})"
        b_str = "unscored" if c.b_value is None else f"{c.b_value:.2f} ({c.b_source})"
        if c.advantage is None:
            lines.append(f"  - {c.axis:16s} A={a_str:20s} B={b_str:20s} [excluded - missing on one side]")
        else:
            lines.append(
                f"  - {c.axis:16s} A={a_str:20s} B={b_str:20s} "
                f"advantage(A)={c.advantage:+.2f}  weight={c.weight_used:.2f}"
            )

    if v.notes:
        lines.append("")
        lines.append("Notes:")
        for n in v.notes:
            lines.append(f"  - {n}")

    if v.ability_flags:
        lines.append("")
        lines.append("Ability flags (NOT factored into the verdict above - read before trusting it):")
        for f in v.ability_flags:
            lines.append(f"  - {f.tag}: {', '.join(f.characters)}")

    return "\n".join(lines)


def format_ability_text(raw: dict) -> str:
    lines = [f"{raw.get('name', '?')} - Powers and Abilities:"]
    for p in raw.get("powers_and_abilities") or []:
        lines.append(f"  - {p}")
    if raw.get("weaknesses"):
        lines.append(f"  Weaknesses: {raw['weaknesses']}")
    return "\n".join(lines)


# --- CLI -----------------------------------------------------------------

def main() -> int:
    argp = argparse.ArgumentParser(description="Phase 5: who-would-win calculator (stats-based estimate).")
    argp.add_argument("character_a", help="Name or DB id")
    argp.add_argument("character_b", help="Name or DB id")
    argp.add_argument("--form-a", help="Form name for character A (default: highest-Tier form)")
    argp.add_argument("--form-b", help="Form name for character B (default: highest-Tier form)")
    argp.add_argument("--show-abilities", action="store_true", help="Also print full raw abilities/weaknesses text")
    args = argp.parse_args()

    try:
        char_a = _parse_identifier(args.character_a)
        char_b = _parse_identifier(args.character_b)
        verdict = compare_characters(char_a, char_b, args.form_a, args.form_b)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(format_verdict(verdict))

    if args.show_abilities:
        raw_a, _ = load_character(_parse_identifier(args.character_a))
        raw_b, _ = load_character(_parse_identifier(args.character_b))
        print()
        print(format_ability_text(raw_a))
        print()
        print(format_ability_text(raw_b))

    return 0


if __name__ == "__main__":
    sys.exit(main())
