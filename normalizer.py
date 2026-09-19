"""Converts raw CharacterStats text fields into numeric NormalizedStats.

Scale design (approved by user before the full ladder was written):
- The score for a Tier is log10(destructive energy in joules), anchored
  to real physics where VS Battles Wiki's tier definitions actually have
  one (roughly 10-C through 3-A - e.g. 5-B "Planet level" is anchored to
  Earth's real gravitational binding energy, ~2.2e32 J). This is a raw,
  physically-interpretable number, not rescaled to a bounded range, and
  the gaps between tiers are deliberately uneven because the real energy
  gaps are uneven (the jump from 5-B to 3-A is far bigger than 9-B to 8-B).
- Above 3-A ("Universe level"), tiers describe multiversal/dimensional
  concepts the wiki itself never assigns a joule value to. Those scores
  continue the same log-style progression using a fixed, deliberately
  large synthetic step (_ABSTRACT_STEP) per tier - ordinal and
  documented as such, not a real energy claim.
- Attack Potency and Durability are NOT given their own ladder: VS
  Battles Wiki defines a character's overall Tier as derived from AP and
  Durability, so all three share the exact same vocabulary and scale
  (TIER_LADDER) - just expressed as a code ("9-B") in the Tier field vs.
  a descriptive name ("Wall level") in the AP/Durability fields. Both
  forms are indexed into the same lookup table.
- Speed uses unrelated vocabulary ("Subsonic", "Massively FTL+") and
  gets its own ladder (SPEED_LADDER), anchored to log10(m/s) where a
  real velocity applies, with synthetic sentinel scores for the
  immeasurable/infinite top end for the same reason as above.

Vocabulary gap sweep additions (Phase 4 prep, user-reviewed and decided
- see _TIER_ALIASES / _SPEED_ALIASES and the "Speed of Light" anchor
  below for what changed and why):
- Both ladders support ALIASES: alternate real-world phrasings that map
  to an EXISTING entry's score rather than getting their own value
  (wiki-side typos like "Relavistic" for "Relativistic", or informal
  synonyms like "Human level" for 10-C). Aliases never override a
  ladder key that already has its own independently-computed score -
  same collision-safety rule as the tier ladder's two-pass build.
- "Speed of Light" is its own explicit SPEED_LADDER anchor at
  log10(299,792,458 m/s) - the precise physical speed of light, not a
  synonym for the nearby "Massively Relativistic" entry - positioned as
  the exact boundary between sub-light and FTL tiers.
- "Omnipresent" deliberately gets NO ladder score at all. Existing
  everywhere at once isn't a point on a "how fast" scale - it's a
  categorically different kind of claim, so forcing a number onto it
  would misrepresent what the wiki text is actually saying. Instead,
  NormalizedStats carries a separate `is_omnipresent` boolean, detected
  independently of the speed ladder lookup; that character's numeric
  speed baseline/peak stay None unless some other, real speed token is
  also present in the same field.
"""

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from parser import DEFAULT_FORM_NAME, CharacterForm, CharacterStats, StatBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier ladder (shared by Tier, Attack Potency, Durability)
# ---------------------------------------------------------------------------

# (code, descriptive name, plain-grade score = log10(joules), approximate
# real-world anchor noted where one exists).
_TIER_ANCHORS: List[Tuple[str, str, float]] = [
    ("10-C", "Below Average level", 0.5),
    ("10-B", "Athletic Human level", 2.0),
    ("10-A", "Athlete level", 2.8),
    ("9-C", "Street level", 3.2),
    ("9-B", "Wall level", 3.6),
    ("9-A", "Small Building level", 4.6),
    ("8-C", "Building level", 6.5),
    ("8-B", "City Block level", 9.5),          # ~ tons of TNT
    ("8-A", "Multi-City Block level", 10.8),
    ("7-C", "Town level", 12.6),                # ~ kiloton
    ("7-B", "City level", 15.6),                 # ~ megaton
    ("7-A", "Mountain level", 17.5),
    ("6-C", "Island level", 19.5),
    ("6-B", "Country level", 21.5),
    ("6-A", "Continent level", 23.0),
    ("5-C", "Moon level", 29.1),                 # Moon's grav. binding energy
    ("5-B", "Planet level", 32.3),               # Earth's grav. binding energy
    ("5-A", "Large Planet level", 36.0),
    ("High 5-A", "Brown Dwarf level", 37.84),    # wiki's own published High 5-A lower bound, ~6.906e37 J
    ("4-C", "Star level", 41.8),                 # Sun's grav. binding energy
    ("4-B", "Solar System level", 47.5),
    ("4-A", "Multi-Solar System level", 53.2),
    ("3-C", "Galaxy level", 58.9),
    ("3-B", "Multi-Galaxy level", 64.6),
    ("3-A", "Universe level", 70.1),              # observable universe's E=mc^2
    # -- below here: no real energy value, ordinal only (see module docstring)
    ("2-C", "Low Multiverse level", 85.0),
    ("2-B", "Multiverse level", 100.0),
    ("2-A", "Multiverse level+", 115.0),
    ("1-C", "Low Complex Multiverse level", 130.0),
    ("1-B", "Complex Multiverse level", 145.0),
    ("1-A", "Outerverse level", 160.0),
]
_ABSTRACT_STEP = 15.0  # synthetic per-tier gap used above 3-A and for 1-A's own grading

# ---------------------------------------------------------------------------
# Speed ladder
# ---------------------------------------------------------------------------

# (descriptive name, plain-grade score = log10(m/s), approximated from a
# representative real velocity where one applies).
_SPEED_ANCHORS: List[Tuple[str, float]] = [
    ("Immobile", -5.0),
    ("Below Average Human", -0.3),
    ("Average Human", 0.15),
    ("Athletic Human", 0.9),
    ("Peak Human", 1.15),
    ("Superhuman", 1.7),
    ("Subsonic", 2.3),
    ("Transonic", 2.53),          # ~Mach 1
    ("Supersonic", 2.9),
    ("Hypersonic", 3.3),          # Mach 5+
    ("High Hypersonic", 4.2),
    ("Massively Hypersonic", 5.7),
    ("Sub-Relativistic", 7.0),
    ("Relativistic", 8.3),
    ("Massively Relativistic", 8.47),   # ~c (approaching, not touching, light speed)
    # Exactly c - the sub-light/FTL boundary. The ~10 phrasing variants
    # the vocabulary sweep found (e.g. "Speed of Light with Fairy Law",
    # "possibly Speed of Light as a Doppel Witch") all contain "Speed of
    # Light" as a contiguous substring, and _find_all_tokens matches
    # substrings anywhere in the text - so this single anchor key already
    # catches every one of them with no separate alias needed per
    # variant (unlike a strict whole-string-equality lookup, which
    # would require listing each one).
    ("Speed of Light", math.log10(299_792_458)),
    ("FTL", 9.0),
    ("Massively FTL", 11.5),
    # -- below here: no finite velocity, ordinal only (see module docstring)
    ("Infinite Speed", 20.0),
    ("Immeasurable", 30.0),
    ("Irrelevant", 35.0),
]
_SPEED_TOP_STEP = 10.0  # synthetic gap used for the last speed anchor's "+" grade


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _build_tier_ladder() -> Dict[str, float]:
    ladder: Dict[str, float] = {}
    n = len(_TIER_ANCHORS)

    # Pass 1: plain (unmodified) entries first. Some tier names are
    # *literally* "Low ..." (e.g. 2-C is "Low Multiverse level") - those
    # canonical names must win over a same-spelled synthetic grade
    # generated for a neighboring tier in pass 2 below (e.g. 2-B
    # "Multiverse level"'s synthesized "Low Multiverse level" grade).
    for code, name, score in _TIER_ANCHORS:
        ladder[_normalize_label(code)] = score
        ladder[_normalize_label(name)] = score

    # Pass 2: synthesized Low/High/+ grades, never clobbering a pass-1
    # (or earlier pass-2) entry.
    for i, (code, name, score) in enumerate(_TIER_ANCHORS):
        gap_after = (_TIER_ANCHORS[i + 1][2] - score) if i + 1 < n else _ABSTRACT_STEP
        gap_before = (score - _TIER_ANCHORS[i - 1][2]) if i > 0 else gap_after

        graded = {
            "low ": score - 0.40 * gap_before,
            "high ": score + 0.25 * gap_after,
            "+": score + 0.40 * gap_after,
        }
        for label in (code, name):
            for prefix_or_suffix, graded_score in graded.items():
                key = _normalize_label(f"{label}+" if prefix_or_suffix == "+" else f"{prefix_or_suffix}{label}")
                if key not in ladder:
                    ladder[key] = graded_score
                else:
                    logger.debug("normalizer: skipping tier ladder key collision %r", key)
    return ladder


def _build_speed_ladder() -> Dict[str, float]:
    ladder: Dict[str, float] = {}
    n = len(_SPEED_ANCHORS)
    for i, (name, score) in enumerate(_SPEED_ANCHORS):
        gap_after = (_SPEED_ANCHORS[i + 1][1] - score) if i + 1 < n else _SPEED_TOP_STEP
        ladder[_normalize_label(name)] = score
        ladder[_normalize_label(f"{name}+")] = score + 0.5 * gap_after
    return ladder


def _apply_aliases(ladder: Dict[str, float], aliases: Dict[str, str]) -> None:
    """Add each alias key pointing to the SAME score as its target key
    (which must already exist in `ladder`). Never overrides an existing
    entry - if an alias string happens to already be its own
    independently-scored key, that entry wins untouched (same
    collision-safety rule as the tier ladder's two-pass build above)."""
    for alias, target in aliases.items():
        target_key = _normalize_label(target)
        if target_key not in ladder:
            raise KeyError(f"normalizer: alias target {target!r} not found in ladder (typo in alias table?)")
        alias_key = _normalize_label(alias)
        if alias_key not in ladder:
            ladder[alias_key] = ladder[target_key]
        else:
            logger.debug("normalizer: skipping alias %r - already a distinct ladder entry", alias)


# Wiki-side typos of existing terms - same class of fix as the "LargeTown
# level" catch in Phase 3, found by the Phase 4 vocabulary gap sweep.
_TIER_TYPO_ALIASES: Dict[str, str] = {}
_SPEED_TYPO_ALIASES: Dict[str, str] = {
    "Sub-Relatvistic+": "Sub-Relativistic+",  # missing 'i' - Jackal, Fairy Tail
    "Relavistic": "Relativistic",             # missing 'ti' - Tsukuyo Amane, Puella Magi Verse
}

# Informal synonyms for an EXISTING tier/speed entry (not typos, not new
# tiers of their own) - user-reviewed decisions from the vocabulary gap
# sweep, not independent judgment calls.
_TIER_TERM_ALIASES: Dict[str, str] = {
    # 10-C is named "Below Average level" (no "Human") in the anchor
    # table, but real pages commonly phrase this informally with
    # "Human" inserted, or drop "Below" entirely - all map to the same
    # 10-C score. (10-B "Athletic Human level" already says "Human" in
    # its canonical name, so it doesn't need this fix - audited per the
    # sweep review request.)
    "Below Average Human level": "Below Average level",
    "Human level": "Below Average level",
    "Average Human": "Below Average level",
    # 10-A's canonical name was renamed from "Peak Human level" to
    # "Athlete level" (batch2 vocab sweep, Dragon Ball/JoJo scrape) to
    # match the wiki's current Tiering System / Attack Potency page
    # naming - "Peak Human level" doesn't appear as live wiki
    # terminology anymore, but kept as an alias since older pages may
    # still use it and the score (2.8) is unchanged either way.
    "Peak Human level": "Athlete level",
    # Adjective form of the existing 1-A "Outerverse level" anchor, not
    # a distinct concept - found manually entering Varga Kolos (Crimson
    # Cross), whose Attack Potency/Durability said "...Outerversal
    # (true ceiling)" while Tier said "High 1-A" for the same claim.
    # Only Tier matched (the code form), so AP/Durability's peak
    # silently collapsed to the baseline value instead of the intended
    # ~1-A ceiling - a ~130-point internal inconsistency between Tier
    # and the two fields it's supposed to be derived from.
    "Outerversal": "Outerverse level",
}
_SPEED_TERM_ALIASES: Dict[str, str] = {
    # Standalone shorthand for the existing "Infinite Speed" entry.
    "Infinite": "Infinite Speed",
}


TIER_LADDER: Dict[str, float] = _build_tier_ladder()
_apply_aliases(TIER_LADDER, _TIER_TYPO_ALIASES)
_apply_aliases(TIER_LADDER, _TIER_TERM_ALIASES)

SPEED_LADDER: Dict[str, float] = _build_speed_ladder()
_apply_aliases(SPEED_LADDER, _SPEED_TYPO_ALIASES)
_apply_aliases(SPEED_LADDER, _SPEED_TERM_ALIASES)

# Attack Potency and Durability use the exact same vocabulary/scale as Tier.
AP_DURABILITY_LADDER = TIER_LADDER


# ---------------------------------------------------------------------------
# Parsing: qualifiers, parentheticals, and a tokenizer over known labels
# ---------------------------------------------------------------------------

_QUALIFIER_PHRASES = [
    "up to at least",
    "up to possibly",
    "up to",
    "at least",
    "at most",
    "possibly",
    "likely",
    "probably",
    "around",
    "roughly",
    "approximately",
]


def _strip_parentheses(text: str) -> str:
    """Remove all parenthesized content (any depth) - it's justification
    text, never itself a tier token."""
    result = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            result.append(ch)
    return "".join(result)


def _qualifier_before(lowered_text: str, match_start: int, window: int = 25) -> Optional[str]:
    segment = lowered_text[max(0, match_start - window):match_start].strip(" ,|")
    for phrase in _QUALIFIER_PHRASES:
        if segment.endswith(phrase):
            return phrase
    return None


def _find_all_tokens(text: str, ladder: Dict[str, float]) -> List[Tuple[Optional[str], str, float]]:
    """Scan left to right for every occurrence of a known ladder label in
    `text`, longest match first, respecting word boundaries. Returns a
    list of (qualifier_or_None, matched_label, score)."""
    lowered = text.lower()
    labels_by_len = sorted(ladder.keys(), key=len, reverse=True)
    results = []
    i, n = 0, len(lowered)
    while i < n:
        matched = None
        for label in labels_by_len:
            end = i + len(label)
            if lowered.startswith(label, i):
                before_ok = i == 0 or not lowered[i - 1].isalnum()
                after_ok = end >= n or not lowered[end].isalnum()
                if before_ok and after_ok:
                    matched = label
                    break
        if matched:
            qualifier = _qualifier_before(lowered, i)
            results.append((qualifier, matched, ladder[matched]))
            i += len(matched)
        else:
            i += 1
    return results


@dataclass
class NormalizedRange:
    raw: Optional[str] = None
    baseline: Optional[float] = None
    baseline_qualifier: Optional[str] = None
    baseline_label: Optional[str] = None
    peak: Optional[float] = None
    peak_qualifier: Optional[str] = None
    peak_label: Optional[str] = None


def parse_range(text: Optional[str], ladder: Dict[str, float]) -> NormalizedRange:
    """Parse a raw wiki stat string (possibly with '|'-separated forms,
    ','-separated progressions, qualifier words, and parenthetical
    justifications) into a baseline/peak NormalizedRange. Falls back to
    an all-None range (with a logged warning) if nothing recognizable is
    found, rather than raising."""
    result = NormalizedRange(raw=text)
    if not text:
        return result

    cleaned = _strip_parentheses(text)
    tokens = _find_all_tokens(cleaned, ladder)
    if not tokens:
        logger.warning("normalizer: no recognizable tier/speed token in %r", text)
        return result

    tokens.sort(key=lambda t: t[2])
    lo_qualifier, lo_label, lo_score = tokens[0]
    hi_qualifier, hi_label, hi_score = tokens[-1]

    result.baseline = lo_score
    result.baseline_qualifier = lo_qualifier
    result.baseline_label = lo_label
    result.peak = hi_score
    result.peak_qualifier = hi_qualifier
    result.peak_label = hi_label
    return result


def parse_tier_range(text: Optional[str]) -> NormalizedRange:
    return parse_range(text, TIER_LADDER)


def parse_speed_range(text: Optional[str]) -> NormalizedRange:
    return parse_range(text, SPEED_LADDER)


_OMNIPRESENT_RE = re.compile(r"\bomnipresent\b", re.IGNORECASE)


def _detect_omnipresent(speed_text: Optional[str]) -> bool:
    """"Omnipresent" is deliberately excluded from SPEED_LADDER (see
    module docstring) - existing everywhere at once isn't a point on a
    speed scale. Detected independently via substring search instead of
    a ladder lookup, so it never contributes a numeric score."""
    if not speed_text:
        return False
    return bool(_OMNIPRESENT_RE.search(speed_text))


@dataclass
class NormalizedForm:
    """Mirrors parser.CharacterForm, normalized. Only Tier, Attack
    Potency, Speed, and Durability are normalized (same scope as
    NormalizedStats below) - Lifting Strength/Striking Strength/
    Stamina/Range stay raw text on CharacterForm.stats, unnormalized."""
    name: str
    tier: NormalizedRange = field(default_factory=NormalizedRange)
    attack_potency: NormalizedRange = field(default_factory=NormalizedRange)
    speed: NormalizedRange = field(default_factory=NormalizedRange)
    durability: NormalizedRange = field(default_factory=NormalizedRange)
    is_omnipresent: bool = False


def _normalize_form(form: CharacterForm) -> NormalizedForm:
    return NormalizedForm(
        name=form.name,
        tier=parse_tier_range(form.tier),
        attack_potency=parse_tier_range(form.stats.attack_potency),
        speed=parse_speed_range(form.stats.speed),
        durability=parse_tier_range(form.stats.durability),
        is_omnipresent=_detect_omnipresent(form.stats.speed),
    )


@dataclass
class NormalizedStats:
    name: Optional[str] = None
    source: Optional[str] = None
    tier: NormalizedRange = field(default_factory=NormalizedRange)
    attack_potency: NormalizedRange = field(default_factory=NormalizedRange)
    speed: NormalizedRange = field(default_factory=NormalizedRange)
    durability: NormalizedRange = field(default_factory=NormalizedRange)
    is_omnipresent: bool = False
    # Always non-empty, mirroring CharacterStats.forms (see parser.py's
    # module docstring): one synthetic NormalizedForm for ordinary
    # characters (matching the flat fields above exactly - see
    # test_normalizer.py's consistency checks), or one per tab for
    # multi-form characters.
    forms: List[NormalizedForm] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_character(stats: CharacterStats) -> NormalizedStats:
    """Build a NormalizedStats from a parsed CharacterStats. Only Tier,
    Attack Potency, Speed, and Durability are normalized here - abilities
    and weaknesses need categorical (not numeric) normalization, planned
    for a later phase."""
    # stats.forms is always non-empty when built by parser.parse_character,
    # so this normally never branches - the fallback below only matters
    # for a CharacterStats built by hand (e.g. tests) without going
    # through parse_character.
    forms_source = stats.forms or [CharacterForm(
        name=DEFAULT_FORM_NAME,
        tier=stats.tier,
        stats=StatBlock(
            attack_potency=stats.attack_potency,
            speed=stats.speed,
            lifting_strength=stats.lifting_strength,
            striking_strength=stats.striking_strength,
            durability=stats.durability,
            stamina=stats.stamina,
            range=stats.range,
        ),
    )]
    forms = [_normalize_form(f) for f in forms_source]
    return NormalizedStats(
        name=stats.name,
        source=stats.source,
        tier=parse_tier_range(stats.tier),
        attack_potency=parse_tier_range(stats.attack_potency),
        speed=parse_speed_range(stats.speed),
        durability=parse_tier_range(stats.durability),
        is_omnipresent=_detect_omnipresent(stats.speed),
        forms=forms,
    )
