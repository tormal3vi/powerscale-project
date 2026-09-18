"""Parses the "Powers and Stats" section out of a VS Battles Wiki
character page's rendered HTML (as returned by scraper.fetch_page).

Layout observed across sampled pages (Saitama, Kirby, Flameskull): the
section is a flat run of sibling elements after
`<h2><span id="Powers_and_Stats">`, up to the next `<h2>`. Each stat field
is a `<p>` starting with `<b>Label:</b>` followed by its value; a field's
value can also spill into further sibling elements before the next
labeled `<p>` (this happens for "Powers and Abilities", which is either
inline comma-separated text or a bulleted `<li>` list, sometimes nested
inside tabs for different forms/timelines).

Known limitation: pages that don't use this flat `<p><b>Label:</b>`
convention (e.g. an old-style wikitable infobox) won't be parsed - the
result will just have unset fields rather than raising, per the
"handle missing fields gracefully" requirement.

Multi-form pages (Genos, Vegeta, Goku - found while diagnosing why
their Attack Potency/Speed/Durability came back None despite Tier
parsing fine): some pages split those 5 fields (plus sometimes Stamina/
Range) into a SEPARATE tabber from the "Powers and Abilities" one -
one tab per story key/arc (e.g. "Saiyan Arc", "Frieza Arc"), each tab
holding its own full stat block as plain <p><b>Label:</b> siblings, no
further nesting. There's no flat fallback for these fields at all on
such pages. `CharacterStats.forms` captures this: it's ALWAYS populated
with at least one `CharacterForm` (never empty), so callers never need
to branch on "does this character have forms or not":
- Ordinary pages get exactly one synthetic form named "Base" (a real
  tab-label the wiki itself uses for "no special form" - see
  `_extract_ability_list`'s docstring), wrapping the same flat values
  already on CharacterStats.
- Multi-form pages get one CharacterForm per tab, named from the tab's
  own header text (ground truth - not re-derived from the separate
  "Key:" field, which is just a convenience summary that could in
  principle drift out of sync).
Each form's `tier` is populated only when the flat Tier field's
'|'-separated segment count matches the tab count exactly (checked
directly against Genos/Vegeta/Goku - it held for all three, but is
verified per-page rather than assumed, since it's an editorial
convention, not a technical guarantee). When the counts don't match,
`tier` stays None on every form rather than forcing a guessed
correspondence; the flat, whole-page `CharacterStats.tier` summary is
unaffected either way.
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from bs4 import BeautifulSoup, NavigableString

# Maps a lowercased wiki field label to a CharacterStats attribute name.
FIELD_MAP = {
    "tier": "tier",
    "name": "name",
    "origin": "origin",
    "gender": "gender",
    "age": "age",
    "classification": "classification",
    "powers and abilities": "powers_and_abilities",
    "attack potency": "attack_potency",
    "speed": "speed",
    "lifting strength": "lifting_strength",
    "striking strength": "striking_strength",
    "durability": "durability",
    "stamina": "stamina",
    "range": "range",
    "standard equipment": "standard_equipment",
    "intelligence": "intelligence",
    "weaknesses": "weaknesses",
}

# The subset of fields that can appear per-form inside a multi-form
# page's stats tabber (see module docstring).
STAT_FIELD_MAP = {
    "attack potency": "attack_potency",
    "speed": "speed",
    "lifting strength": "lifting_strength",
    "striking strength": "striking_strength",
    "durability": "durability",
    "stamina": "stamina",
    "range": "range",
}

# Sentinel form name for ordinary, non-tabbed pages - chosen because
# it's a real tab label the wiki itself already uses for "no special
# form" (see e.g. Kirby's ability-tabber tabs).
DEFAULT_FORM_NAME = "Base"


@dataclass
class StatBlock:
    """The fields that can vary per-form."""
    attack_potency: Optional[str] = None
    speed: Optional[str] = None
    lifting_strength: Optional[str] = None
    striking_strength: Optional[str] = None
    durability: Optional[str] = None
    stamina: Optional[str] = None
    range: Optional[str] = None


@dataclass
class CharacterForm:
    name: str
    tier: Optional[str] = None
    stats: StatBlock = field(default_factory=StatBlock)


@dataclass
class CharacterStats:
    name: Optional[str] = None
    tier: Optional[str] = None
    origin: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[str] = None
    classification: Optional[str] = None
    powers_and_abilities: List[str] = field(default_factory=list)
    attack_potency: Optional[str] = None
    speed: Optional[str] = None
    lifting_strength: Optional[str] = None
    striking_strength: Optional[str] = None
    durability: Optional[str] = None
    stamina: Optional[str] = None
    range: Optional[str] = None
    standard_equipment: Optional[str] = None
    intelligence: Optional[str] = None
    weaknesses: Optional[str] = None
    # Fields the page had that aren't in the standard list above (e.g.
    # "Key", "Standard Tactics", "Note 1") - kept instead of discarded.
    extra_fields: Dict[str, str] = field(default_factory=dict)
    source: Optional[str] = None
    # Always non-empty (see module docstring): one synthetic "Base" form
    # for ordinary pages, or one CharacterForm per tab for multi-form
    # pages. The flat fields above stay the canonical values for
    # ordinary pages (and always match forms[0] there); for multi-form
    # pages they stay None rather than collapsing to one form's values.
    forms: List[CharacterForm] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_top_level(text: str, sep: str = ",") -> List[str]:
    """Split on `sep`, but not inside (...) or [...] - so ability
    justifications like "Fire Manipulation (can melt steel, stone)" stay
    intact as one item."""
    parts, current, depth = [], [], 0
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _strip_citation_markers(soup: BeautifulSoup) -> None:
    for sup in soup.find_all("sup", class_="reference"):
        sup.decompose()


_STATS_HEADING_RE = re.compile(r"^powers?\s+and\s+stats$", re.IGNORECASE)


def _find_stats_heading(soup: BeautifulSoup):
    # Usually "Powers and Stats", but some pages spell it "Power and
    # Stats" (singular) - e.g. Promoted Rook, found while batch-scraping
    # Category:One-Punch_Man. Match both the id and the headline text.
    span = soup.find("span", id="Powers_and_Stats") or soup.find("span", id="Power_and_Stats")
    if span is None:
        for candidate in soup.find_all("span", class_="mw-headline"):
            if _STATS_HEADING_RE.match(candidate.get_text(strip=True)):
                span = candidate
                break
    if span is None:
        return None
    return span.find_parent(["h2", "h3"])


def _label_from_p(p):
    """If `p` opens with a `<b>Label:</b>`, return (b_tag, label); else
    (None, None). Leading whitespace/<br> before the <b> is tolerated."""
    for child in p.children:
        if isinstance(child, NavigableString):
            if child.strip():
                return None, None
            continue
        if child.name == "br":
            continue
        if child.name == "b":
            text = child.get_text(strip=True)
            if text.endswith(":"):
                return child, text.rstrip(":").strip()
        return None, None
    return None, None


def _html_after(p, b) -> str:
    parts, found = [], False
    for child in p.children:
        if child is b:
            found = True
            continue
        if found:
            parts.append(str(child))
    return "".join(parts)


def _collect_field_blocks(heading) -> List[dict]:
    """Walk siblings after `heading` until the next <h2>, grouping content
    into one block per labeled <p> (label + its own inline value + any
    following non-labeled siblings, which belong to that field)."""
    blocks: List[dict] = []
    current = None
    for sib in heading.find_next_siblings():
        name = getattr(sib, "name", None)
        if name == "h2":
            break
        if name == "h3":
            # A subsection boundary (e.g. "Normal Attacks") that isn't
            # part of the standard field list - don't glue its content
            # onto whatever field came before it.
            current = None
            continue
        if name == "p":
            b, label = _label_from_p(sib)
            if label:
                current = {"label": label, "html_parts": [_html_after(sib, b)]}
                blocks.append(current)
                continue
        if name == "table":
            # Tables here (e.g. a "Feats" or "Notable Attacks/Techniques"
            # block) are standalone sections, not a continuation of the
            # preceding field's value - handle them on their own so they
            # don't bleed into it.
            current = None
            table_field = _table_field(sib)
            if table_field:
                blocks.append(table_field)
            continue
        if current is not None:
            current["html_parts"].append(str(sib))
    return blocks


def _table_field(table) -> Optional[dict]:
    """If `table`'s first cell opens with a `<b>Label:</b>`, return a block
    with the table's full text as the value (label prefix stripped)."""
    cell = table.find(["td", "th"])
    if cell is None:
        return None
    b = cell.find("b")
    if b is None:
        return None
    label_text = b.get_text(strip=True)
    if not label_text.endswith(":"):
        return None
    label = label_text.rstrip(":").strip()
    text = _clean_text(table.get_text())
    prefix = label + ":"
    if text.lower().startswith(prefix.lower()):
        text = text[len(prefix):].strip()
    return {"label": label, "html_parts": [], "raw_value": text}


def _find_stats_tabber(heading):
    """Find the tabber (among heading's siblings, at any depth - it may
    sit inside a "scrollable" wrapper alongside the unrelated Powers-
    and-Abilities tabber) whose tabs directly hold stat-field <p>'s, as
    opposed to an abilities tabber's <li> lists. Returns the first
    match, or None if this page uses the ordinary flat layout."""
    for sib in heading.find_next_siblings():
        if getattr(sib, "name", None) == "h2":
            break
        if not hasattr(sib, "find_all"):
            continue
        # The stats tabber can be `sib` itself (a direct sibling of the
        # heading) or nested inside it (e.g. alongside the unrelated
        # Powers-and-Abilities tabber inside a "scrollable" wrapper) -
        # find_all() alone only searches descendants, so `sib` itself
        # must be checked too.
        candidates = sib.find_all("div", class_="tabber")
        if "tabber" in (sib.get("class") or []):
            candidates = [sib] + candidates
        for tabber in candidates:
            top_contents = tabber.find_all("div", class_="wds-tab__content", recursive=False)
            if not top_contents:
                continue
            for p in top_contents[0].find_all("p", recursive=False):
                b = p.find("b")
                if b:
                    label = b.get_text(strip=True).rstrip(":").strip().lower()
                    if label in STAT_FIELD_MAP:
                        return tabber
    return None


def _extract_forms_from_stats_tabber(tabber, flat_tier: Optional[str]) -> List[CharacterForm]:
    top_ul = tabber.find("ul", class_="wds-tabs")
    top_contents = tabber.find_all("div", class_="wds-tab__content", recursive=False)
    if top_ul is None or not top_contents:
        return []
    tab_labels = [li.get_text(strip=True) for li in top_ul.find_all("li", recursive=False)]

    # Only trust the flat Tier summary's '|'-segments as per-form tiers
    # when the count lines up exactly with the tab count - verified per
    # page rather than assumed (see module docstring).
    tier_segments = _split_top_level(flat_tier, "|") if flat_tier else []
    aligned_tiers = tier_segments if len(tier_segments) == len(tab_labels) else None

    forms = []
    for i, (label, content) in enumerate(zip(tab_labels, top_contents)):
        stat_values: Dict[str, str] = {}
        for p in content.find_all("p", recursive=False):
            b = p.find("b")
            if b is None:
                continue
            label_text = b.get_text(strip=True)
            if not label_text.endswith(":"):
                continue
            field_key = STAT_FIELD_MAP.get(label_text.rstrip(":").strip().lower())
            if field_key is None:
                continue
            fragment = BeautifulSoup(_html_after(p, b), "lxml")
            value = _clean_text(fragment.get_text())
            if value:
                stat_values[field_key] = value
        forms.append(CharacterForm(
            name=label,
            tier=aligned_tiers[i].strip() if aligned_tiers else None,
            stats=StatBlock(**stat_values),
        ))
    return forms


def _extract_ability_list(fragment: BeautifulSoup) -> List[str]:
    # Exclude tabber widget tab-headers (e.g. "Base", "Ghost Kirby ▾") -
    # they're navigation chrome for alternate forms/timelines, not abilities.
    lis = [
        li for li in fragment.find_all("li")
        if "wds-tabs__tab" not in (li.get("class") or [])
    ]
    if lis:
        items = []
        for li in lis:
            # Re-parse each <li> alone and drop nested <ul>/<ol> so a
            # parent bullet's text isn't duplicated with its sub-bullets.
            clone = BeautifulSoup(str(li), "lxml").find("li")
            for nested in clone.find_all(["ul", "ol"]):
                nested.decompose()
            text = _clean_text(clone.get_text())
            if text:
                items.append(text)
        return items
    text = _clean_text(fragment.get_text())
    return _split_top_level(text, ",") if text else []


def _default_form(stats: CharacterStats) -> CharacterForm:
    """The synthetic single form for an ordinary (non-tabbed) page -
    wraps the same values already on `stats`, so forms[0] and the flat
    fields simply match for these pages."""
    return CharacterForm(
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
    )


def parse_character(html: str, source: Optional[str] = None) -> CharacterStats:
    """Parse a character page's rendered HTML into a CharacterStats.
    `stats.forms` is always populated with at least one CharacterForm -
    see the module docstring."""
    soup = BeautifulSoup(html, "lxml")
    _strip_citation_markers(soup)
    stats = CharacterStats(source=source)

    heading = _find_stats_heading(soup)
    if heading is None:
        stats.forms = [_default_form(stats)]
        return stats

    for block in _collect_field_blocks(heading):
        label = block["label"]
        key = FIELD_MAP.get(label.lower())

        if "raw_value" in block:
            value = block["raw_value"]
            if not value:
                continue
            if key:
                setattr(stats, key, value)
            else:
                stats.extra_fields[label] = value
            continue

        fragment = BeautifulSoup("".join(block["html_parts"]), "lxml")

        if key == "powers_and_abilities":
            stats.powers_and_abilities = _extract_ability_list(fragment)
            continue

        value = _clean_text(fragment.get_text())
        if not value:
            continue
        if key:
            setattr(stats, key, value)
        else:
            stats.extra_fields[label] = value

    stats_tabber = _find_stats_tabber(heading)
    if stats_tabber is not None:
        forms = _extract_forms_from_stats_tabber(stats_tabber, stats.tier)
    else:
        forms = []
    stats.forms = forms if forms else [_default_form(stats)]

    return stats
