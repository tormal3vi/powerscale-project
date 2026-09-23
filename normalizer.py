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
- "Omnipresent" scores the same as "Irrelevant", the top of the Speed
  ladder, and "Nigh-Omnipresent" the same as "Immeasurable". Existing
  everywhere at once isn't literally a speed, and it was first left
  unscored - but that left Ultimate Madoka, Akuma Homura, Infinite Zamasu
  and a few others with no Speed at all, so their strongest trait counted
  for nothing. For a "who's faster" comparison, being everywhere beats
  any finite speed. NormalizedStats also keeps a separate
  `is_omnipresent` flag for any page that mentions it.
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
    # 1-C/1-B names follow the wiki's current tiering, confirmed from how
    # real pages pair Tier codes with AP names: "1-C" <-> "Complex
    # Multiverse level" 72 times (vs. once for "Low Complex Multiverse
    # level"), and "Low 1-C" <-> "Low Complex Multiverse level" 30 times.
    # These used to be one step off (1-C = "Low Complex", 1-B =
    # "Complex"), so every "Complex Multiverse level" AP/Durability
    # scored a whole tier above the same page's own Tier (e.g. most of
    # God of War's pantheon: Tier 1-C, AP scored as 1-B).
    ("1-C", "Complex Multiverse level", 130.0),
    ("1-B", "Hyperverse level", 145.0),
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
_TIER_TYPO_ALIASES: Dict[str, str] = {
    # Found adding the Naruto category (occurrence counts at the time):
    "City-Block level": "City Block level",              # ~38, hyphenated
    # "At least City Block" with no "level" (Mundus, Golem Form). "Multi-City
    # Block" too, or its "City Block" would match inside it one tier low.
    "City Block": "City Block level",
    "Multi-City Block": "Multi-City Block level",
    "City-Block level+": "City Block level+",
    "Multi City-Block level": "Multi-City Block level",  # 5
    "County level": "Country level",                     # 2, missing 'r'
    # Found by the whole-DB audit (one or two occurrences each):
    "LargeTown level": "High 7-C",                       # missing space
    "Low Multiverse leve": "Low Multiverse level",       # truncated
}
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
    # Same adjective-form pattern, found adding Naruto (Hagoromo
    # Ōtsutsuki): "At least Universal+ level", "likely Low Complex
    # Multiversal level".
    "Universal+ level": "Low 2-C",
    "Universal+": "Low 2-C",
    "Low Complex Multiversal level": "Low 1-C",
    "Low Complex Multiversal": "Low 1-C",
    "Complex Multiversal": "Complex Multiverse level",   # Mimir (God of War)
    # The wiki's own descriptive names for Low/High sub-grades. The
    # ladder only held each tier's plain name, and the tokenizer matches
    # the longest KNOWN label, so e.g. "Multi-Continent level" silently
    # scored as plain "Continent level" (6-A instead of High 6-A) with no
    # warning at all - found diagnosing a Bambietta-vs-Ainz verdict, then
    # confirmed data-driven by scanning every stored AP/Durability string
    # for an unrecognized Multi-/Small/Large prefix on a known name (the
    # counts are real occurrences across the DB at the time). Each target
    # is the code the SAME page pairs it with in its Tier field (e.g.
    # "Small Country level" appears alongside "Low 6-B"), not guessed.
    "Multi-Continent level": "High 6-A",   # 1015 occurrences, 179 characters
    "Large Building level": "High 8-C",    # 268 / 51
    "Large Mountain level": "High 7-A",    # 250 / 60
    "Large Star level": "High 4-C",        # 202 / 33
    "Large Country level": "High 6-B",     # 190 / 39
    "Small Town level": "Low 7-C",         # 140 / 33
    "Large Town level": "High 7-C",        # 119 / 33
    "Large Island level": "High 6-C",      # 106 / 24
    "Small Star level": "Low 4-C",         # 100 / 17
    "Small City level": "Low 7-B",         # 82 / 25
    "Small Planet level": "Low 5-B",       # 18 / 3
    "Small Country level": "Low 6-B",      # 8 / 3
}
_SPEED_TERM_ALIASES: Dict[str, str] = {
    # Standalone shorthand for the existing "Infinite Speed" entry.
    "Infinite": "Infinite Speed",
    # Being everywhere beats any finite speed (see module docstring).
    "Omnipresent": "Irrelevant",
    "Nigh-Omnipresent": "Immeasurable",
    # AP-style wording used in a Speed field (Launch, Dragon Ball).
    "Human level": "Average Human",
}


TIER_LADDER: Dict[str, float] = _build_tier_ladder()
# "Universe level+" is the wiki's name for Low 2-C (pages pair them 18
# times), but pass 2 had already synthesized it as a "+" grade of 3-A
# with its own guessed score. It's a named sub-grade, like "Multi-
# Continent level", so it takes Low 2-C's score - overriding only that
# synthesized guess, never a real anchor.
TIER_LADDER[_normalize_label("Universe level+")] = TIER_LADDER[_normalize_label("Low 2-C")]
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


# Abilities that ARE the character's real fighting level, the way magic is
# for a caster: "Multi-Solar System level with Reality Overwrite" (Heaven
# Ascension DIO), "1-C via Plot Manipulation" (Arale). Deliberately a short
# list (user decision): a blanket "with <anything>" rule would also score
# transformations, one-off finishers and self-destructs as the baseline -
# 284 characters moved, some from 9-B to 1-C.
_POWER_ABILITIES = (
    "reality overwrite", "reality warping", "plot manipulation", "wish granting", "weather manipulation",
)
_POWER_RE = re.compile(r"(?:with|via|using)\s+(?:his |her |their |its )?(" + "|".join(_POWER_ABILITIES) + r")\b")


# Stand users (user decision): on a JoJo page, a value written "with
# <anything>" is the Stand's - "10-A, 8-C with Sticky Fingers" - and a
# Stand user fights through their Stand, the way a caster fights with
# magic. Not "with at least/at most ..." (a hedge, not a Stand).
_STAND_WITH_RE = re.compile(r"\b(?:with|via|using)\s+(?!(?:at least|at most|likely|possibly|probably)\b)(?=[a-z'\"])")
_JOJO_ORIGIN_RE = re.compile(r"jojo|rohan at the louvre", re.IGNORECASE)
_STAND_MENTION_RE = re.compile(r"\bstands?\b", re.IGNORECASE)


def _is_stand_user(stats: CharacterStats) -> bool:
    """A JoJo page that mentions a Stand (classification, abilities or
    equipment). Both are needed: "stand" alone is ordinary English on
    other wikis' pages, and JoJo's Pillar Men, zombies and Hamon/Spin
    users have no Stand."""
    if not _JOJO_ORIGIN_RE.search(stats.origin or ""):
        return False
    blob = " ".join([stats.classification or "", " ".join(stats.powers_and_abilities or []),
                     stats.standard_equipment or ""])
    return bool(_STAND_MENTION_RE.search(blob))


def _condition_after(lowered_text: str, match_end: int, stand_user: bool = False) -> Optional[str]:
    """Tag a token by the condition written right after it: "physical"
    for "... physically", "magic" for "... with magic", and "power" for
    "... with Reality Overwrite" and the other _POWER_ABILITIES - or, for
    a Stand user, "... with <anything>". The physical/magic phrasings are
    the only two found in the data (checked across every stored
    Tier/AP/Speed/Durability string before writing this)."""
    tail = lowered_text[match_end:match_end + 60].lstrip()
    if tail.startswith("physically"):
        return "physical"
    # Not "with Magician's Red" (Avdol's Stand) - that's no caster split.
    if re.match(r"with magic(?!ian)", tail):
        return "magic"
    if _POWER_RE.match(tail) or (stand_user and _STAND_WITH_RE.match(tail)):
        return "power"
    return None


# "3-A, likely Low 2-C with Reality Overwrite": the condition is written
# once, after the last value, but covers the whole hedged range.
# Only a real hedge word counts: a bare comma separates two different
# claims ("At least 9-A, Low 7-C with magic" - Ainz's 9-A is physical).
_HEDGE_GAP_RE = re.compile(r"^\s*,?\s*(?:likely|possibly|probably|potentially)(?:\s+to)?\s*$")


def _find_all_tokens(text: str, ladder: Dict[str, float],
                     stand_user: bool = False) -> List[Tuple[Optional[str], str, float, Optional[str]]]:
    """Scan left to right for every occurrence of a known ladder label in
    `text`, longest match first, respecting word boundaries. Returns a
    list of (qualifier_or_None, matched_label, score, condition_or_None)."""
    lowered = text.lower()
    labels_by_len = sorted(ladder.keys(), key=len, reverse=True)
    results = []
    spans = []
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
            condition = _condition_after(lowered, i + len(matched), stand_user)
            results.append((qualifier, matched, ladder[matched], condition))
            spans.append((i, i + len(matched)))
            i += len(matched)
        else:
            i += 1
    # A value joined to a magic/power one only by a hedge ("X, likely Y
    # with magic") shares its condition - otherwise the "likely" value
    # alone would be scored as the caster's baseline.
    for k in range(len(results) - 2, -1, -1):
        nxt = results[k + 1][3]
        if nxt in ("magic", "power") and results[k][3] is None \
                and _HEDGE_GAP_RE.match(lowered[spans[k][1]:spans[k + 1][0]]):
            results[k] = results[k][:3] + (nxt,)
    # JoJo pages write "own body; Stand": "9-C; at least High 8-C, likely
    # far higher with Gold Experience Requiem". Within a ";"/"|" clause,
    # values before a "with <Stand>" are the Stand's even when the Stand
    # is named after a hedge rather than right after the value. Only when
    # the text is split that way - "At least High 8-C, ..., 4-A with
    # Reality Overwrite" (one clause) must keep its High 8-C physical.
    if stand_user and re.search(r"[;|]", lowered):
        start = 0
        for end in [m.start() for m in re.finditer(r"[;|]", lowered)] + [len(lowered)]:
            for w in _STAND_WITH_RE.finditer(lowered, start, end):
                for k, (s0, e0) in enumerate(spans):
                    if start <= s0 and e0 <= w.start() and results[k][3] is None:
                        results[k] = results[k][:3] + ("power",)
            start = end + 1
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


def _caster_baseline(tokens: list) -> tuple:
    """Pick the token to score as the baseline from score-sorted tokens.
    Normally just the lowest one - but a page that splits a stat into
    "X physically, Y with magic" (Rudeus Greyrat: "9-C physically, 6-C
    with magic"; Ainz Ooal Gown: "At least 9-A, Low 7-C with magic")
    means the physical value isn't the character's real fighting level,
    so scoring it made every caster look several tiers weaker than their
    page says (user decision: score casters by their magic value). The
    baseline is raised to the lowest value NOT tagged "physically" (when
    any value is), and to the lowest value tagged "with magic" or with one
    of the _POWER_ABILITIES (when any is). It only ever moves up - the
    peak is untouched."""
    chosen = tokens[0]
    if any(t[3] == "physical" for t in tokens):
        non_physical = [t for t in tokens if t[3] != "physical"]
        if non_physical and non_physical[0][2] > chosen[2]:
            chosen = non_physical[0]
    magic = [t for t in tokens if t[3] in ("magic", "power")]
    if magic and magic[0][2] > chosen[2]:
        chosen = magic[0]
    return chosen


def parse_range(text: Optional[str], ladder: Dict[str, float], stand_user: bool = False) -> NormalizedRange:
    """Parse a raw wiki stat string (possibly with '|'-separated forms,
    ','-separated progressions, qualifier words, and parenthetical
    justifications) into a baseline/peak NormalizedRange. Falls back to
    an all-None range (with a logged warning) if nothing recognizable is
    found, rather than raising."""
    result = NormalizedRange(raw=text)
    if not text:
        return result

    cleaned = _strip_parentheses(text)
    tokens = _find_all_tokens(cleaned, ladder, stand_user)
    if not tokens:
        logger.warning("normalizer: no recognizable tier/speed token in %r", text)
        return result

    tokens.sort(key=lambda t: t[2])
    lo_qualifier, lo_label, lo_score, _ = _caster_baseline(tokens)
    hi_qualifier, hi_label, hi_score, _ = tokens[-1]

    result.baseline = lo_score
    result.baseline_qualifier = lo_qualifier
    result.baseline_label = lo_label
    result.peak = hi_score
    result.peak_qualifier = hi_qualifier
    result.peak_label = hi_label
    return result


def parse_tier_range(text: Optional[str], stand_user: bool = False) -> NormalizedRange:
    return parse_range(text, TIER_LADDER, stand_user)


def parse_speed_range(text: Optional[str], stand_user: bool = False) -> NormalizedRange:
    return parse_range(text, SPEED_LADDER, stand_user)


_OMNIPRESENT_RE = re.compile(r"\bomnipresent\b", re.IGNORECASE)


def _detect_omnipresent(speed_text: Optional[str]) -> bool:
    """True when the speed text mentions omnipresence at all - as the
    scored value, or only as a higher/possible one ("eventually
    Immeasurable, possibly Omnipresent")."""
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


def _normalize_form(form: CharacterForm, stand_user: bool = False) -> NormalizedForm:
    return NormalizedForm(
        name=form.name,
        tier=parse_tier_range(form.tier, stand_user),
        attack_potency=parse_tier_range(form.stats.attack_potency, stand_user),
        speed=parse_speed_range(form.stats.speed, stand_user),
        durability=parse_tier_range(form.stats.durability, stand_user),
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
    stand_user = _is_stand_user(stats)
    forms = [_normalize_form(f, stand_user) for f in forms_source]
    return NormalizedStats(
        name=stats.name,
        source=stats.source,
        tier=parse_tier_range(stats.tier, stand_user),
        attack_potency=parse_tier_range(stats.attack_potency, stand_user),
        speed=parse_speed_range(stats.speed, stand_user),
        durability=parse_tier_range(stats.durability, stand_user),
        is_omnipresent=_detect_omnipresent(stats.speed),
        forms=forms,
    )
