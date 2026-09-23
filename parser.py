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

Flat-page multi-form pages (Saitama - found while investigating why he
showed as flatly "Wall level" despite the page describing Galaxy level
feats): no tabber at all, but every stat `<p>` packs all forms into one
string, '|'-separated in the same order as a parallel "Key:" field
(e.g. Tier: "9-B | At least 9-B... | 4-A..." with Key: "Pre-Training |
During Training | Post-Balding | Parallel Timeline"). Without special
handling this collapses to a single synthetic "Base" form whose fields
are the raw, unsplit multi-segment string - normalizer.parse_tier_range
then just grabs the lowest/highest tier token found anywhere in it,
which for a growth-arc character means "lowest" is an early, weak stage
rather than a real floor. `_extract_forms_from_flat_key` splits this the
same way the tabber path does: Key's segments name the forms, and each
field's own '|' segments are trusted only when their count matches the
Key's count exactly, checked per field independently (a page can list
one field's variation without meaning to split every field into forms).
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

_FUSED_LABEL_RE = re.compile(r"^([A-Za-z][A-Za-z /&-]{0,40}):\s*(\S.*)$", re.S)
_FUSABLE_LABELS = set(FIELD_MAP) | {"key"}

# Fields whose value can differ per form - the only ones worth splitting
# out of a per-field tabber (see _per_tab_field_value).
_PER_FORM_KEYS = set(STAT_FIELD_MAP.values()) | {"tier"}

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


_STATS_HEADING_RE = re.compile(r"^powers?\s+and\s+stat(s|istics)$", re.IGNORECASE)


def _find_stats_heading(soup: BeautifulSoup):
    # Usually "Powers and Stats", but some pages spell it "Power and
    # Stats" (singular) - e.g. Promoted Rook, found while batch-scraping
    # Category:One-Punch_Man - or spell the word out in full, "Powers
    # and Statistics" - e.g. Hulk (Marvel Comics), found adding it
    # individually. Match both the id and the headline text.
    span = (
        soup.find("span", id="Powers_and_Stats")
        or soup.find("span", id="Power_and_Stats")
        or soup.find("span", id="Powers_and_Statistics")
        or soup.find("span", id="Power_and_Statistics")
    )
    if span is None:
        for candidate in soup.find_all("span", class_="mw-headline"):
            if _STATS_HEADING_RE.match(candidate.get_text(strip=True)):
                span = candidate
                break
    if span is None:
        return _abilities_heading_with_tier(soup)
    return span.find_parent(["h2", "h3"])


def _abilities_heading_with_tier(soup: BeautifulSoup):
    """Last resort: a "Powers and Abilities" section heading, but only when
    a Tier field actually follows it. Hajime Kashimo (Jujutsu Kaisen) puts
    his whole stat block under that heading instead of "Powers and
    Stats" - the only character page in ~1,550 doing so. The Tier check
    keeps mechanics pages that also use this heading (e.g. Slayer Magic)
    from being read as characters."""
    for candidate in soup.find_all("span", class_="mw-headline"):
        if candidate.get_text(strip=True).lower() != "powers and abilities":
            continue
        heading = candidate.find_parent(["h2", "h3"])
        if heading is None:
            continue
        for sib in heading.find_next_siblings():
            if getattr(sib, "name", None) == "h2":
                break
            if getattr(sib, "name", None) == "p":
                _, label = _label_from_p(sib)
                if label and label.lower() == "tier":
                    return heading
    return None


def _label_text(b) -> Optional[str]:
    """If `b` is a field label, return its text (colon stripped); else
    None. Normally the colon is the last character inside `<b>Label:
    </b>`, but some pages put it just outside instead - `<b>Label</b>:
    value` - found systemically across most of Son Goku (Classic
    Toei)'s forms (5 of 7 tabs, all missing Attack Potency the same
    way) rather than as a one-off typo, so it's worth recognizing
    generally rather than per-page. When the colon sits outside, it's
    consumed from that sibling node in place so downstream "everything
    after b" extraction doesn't see a stray leading ":"."""
    text = b.get_text(strip=True)
    if text.endswith(":"):
        return text[:-1].strip()
    nxt = b.next_sibling
    if isinstance(nxt, NavigableString) and nxt.lstrip().startswith(":"):
        nxt.replace_with(nxt.lstrip()[1:])
        return text
    # Third variant: label AND the start of the value fused inside one
    # bold run - `<b>Key: Alive</b> | <b>Edo Tensei</b> | ...` (Madara
    # Uchiha). Unrecognized, that whole <p> was glued onto the previous
    # field (his Tier), which broke the per-form split and left every
    # form's Tier unscored. 44 cached pages had it (Tatsumaki, Jiren,
    # Broly, Power...). Only known field names count, so bold text that
    # merely contains a colon ("Note: ...") isn't misread as a label.
    # The fused value is moved out of the <b>, in place, so it reads as
    # the start of the field's value.
    m = _FUSED_LABEL_RE.match(text)
    if m and m.group(1).strip().lower() in _FUSABLE_LABELS:
        label = m.group(1).strip()
        b.string = label + ":"
        b.insert_after(NavigableString(" " + m.group(2)))
        return label
    return None


def _label_from_p(p):
    """If `p` opens with a labeled `<b>`, return (b_tag, label); else
    (None, None). Leading whitespace/<br> before the <b> is tolerated."""
    for child in p.children:
        if isinstance(child, NavigableString):
            if child.strip():
                return None, None
            continue
        if child.name == "br":
            continue
        if child.name == "b":
            label = _label_text(child)
            if label is not None:
                return child, label
        return None, None
    return None, None


def _peek_label(b) -> Optional[str]:
    """What _label_text would return for `b`, without changing the tree."""
    text = b.get_text(strip=True)
    if text.endswith(":"):
        return text[:-1].strip()
    nxt = b.next_sibling
    if isinstance(nxt, NavigableString) and nxt.lstrip().startswith(":"):
        return text
    m = _FUSED_LABEL_RE.match(text)
    return m.group(1).strip() if m else None


def _p_fields(p, b, label: str) -> List[tuple]:
    """(label, value_html) for every field in paragraph `p`, starting at its
    opening label `b`. Normally just one - but a few pages pack several
    fields into one <p>, separated only by <br> (Son Goku (DBS Manga):
    Speed, Lifting Strength, Striking Strength, Durability and Stamina in
    a single paragraph on 6 of his forms), which used to swallow the rest
    into the first field's value. A new field starts at any later <b>
    that is a KNOWN field label; other bold text ending in ":" stays part
    of the value."""
    fields, cur_label, parts, found = [], label, [], False
    for child in list(p.children):
        if child is b:
            found = True
            continue
        if not found:
            continue
        if getattr(child, "name", None) == "b":
            peek = _peek_label(child)
            if peek and peek.lower() in FIELD_MAP:
                fields.append((cur_label, "".join(parts)))
                cur_label, parts = _label_text(child), []
                continue
        parts.append(str(child))
    fields.append((cur_label, "".join(parts)))
    return fields


def _html_after(p, b) -> str:
    parts, found = [], False
    for child in p.children:
        if child is b:
            found = True
            continue
        if found:
            parts.append(str(child))
    return "".join(parts)


def _labeled_paragraphs_in(tabber) -> List[dict]:
    """Direct-child <p>'s of a tabber div itself (not nested inside any
    of its own tabs) that open with a recognized <b>Label:</b>. Some
    pages tab the "Powers and Abilities" section itself (e.g. a
    "Powers and Abilities"/"Resistances" split - Frieren; a two-persona
    selector - Reze) and, on those pages, wrap the *entire rest* of the
    flat stat block (Attack Potency, Speed, Durability, Weaknesses,
    ...) inside that same tabber div too, as plain <p> siblings after
    its wds-tab__content tabs - one level deeper than an ordinary
    sibling walk looks, and not inside any specific tab either, so
    they'd otherwise be silently absorbed as unparsed raw HTML into
    whatever field was open when the tabber was reached (see
    _collect_field_blocks). Confirmed against real HTML (Frieren, Reze)
    before writing this, not assumed."""
    blocks: List[dict] = []
    for child in tabber.find_all("p", recursive=False):
        b, label = _label_from_p(child)
        if label:
            blocks.append({"label": label, "html_parts": [_html_after(child, b)]})
    return blocks


def _stat_siblings(heading):
    """Siblings after the stats heading, with a top-level `div.scrollable`
    wrapper flattened into its children when it holds labeled fields.
    Bickslow and Evergreen (Fairy Tail) wrap Attack Potency through
    Stamina in one such scroll box, which the sibling walk used to treat
    as a single opaque block - every form came back with only a Tier.
    Same wrapper _stat_paragraphs already looks through inside tabs."""
    for sib in heading.find_next_siblings():
        if getattr(sib, "name", None) == "div" and "scrollable" in (sib.get("class") or []):
            if any((_peek_label(p.find("b")) or "").lower() in FIELD_MAP
                   for p in sib.find_all("p", recursive=False) if p.find("b")):
                yield from list(sib.children)
                continue
        yield sib


def _collect_field_blocks(heading) -> List[dict]:
    """Walk siblings after `heading` until the next <h2>, grouping content
    into one block per labeled <p> (label + its own inline value + any
    following non-labeled siblings, which belong to that field)."""
    blocks: List[dict] = []
    current = None
    for sib in _stat_siblings(heading):
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
                for field_label, value_html in _p_fields(sib, b, label):
                    current = {"label": field_label, "html_parts": [value_html]}
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
        if name == "div" and "tabber" in (sib.get("class") or []):
            # See _labeled_paragraphs_in - additive only, and a no-op on
            # every ordinary multi-form stats tabber (Genos, Vegeta,
            # Goku, Denji, ...), whose <p>'s all live nested inside a
            # wds-tab__content tab, never as direct children of the
            # tabber div itself.
            blocks.extend(_labeled_paragraphs_in(sib))
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


def _stat_paragraphs(content_div) -> List:
    """<p> elements to scan for stat-field labels within one tab's
    wds-tab__content div: direct children, plus (merged in) any inside
    a direct-child `div.scrollable`. Most pages (Genos, Vegeta, Goku)
    put the stat <p>'s directly in the tab content; some (found on
    Chainsaw Man's cast - e.g. Denji, Power) wrap the entire stat block
    in a horizontally-scrollable div one level deeper instead, leaving
    only empty spacer <p>'s as direct children. Checking both means
    either layout is found without needing a page-specific branch."""
    paragraphs = list(content_div.find_all("p", recursive=False))
    for scrollable in content_div.find_all("div", class_="scrollable", recursive=False):
        paragraphs.extend(scrollable.find_all("p", recursive=False))
    return paragraphs


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
            for p in _stat_paragraphs(top_contents[0]):
                b = p.find("b")
                if b:
                    label = b.get_text(strip=True).rstrip(":").strip().lower()
                    if label in STAT_FIELD_MAP:
                        return tabber
    return None


_BLOCK_TAGS = {"p", "div", "table", "ul", "ol", "h2", "h3", "h4", "center", "figure"}


def _bare_label_fields(container) -> List[tuple]:
    """(label, value) for fields written as a bare <b>Label:</b> plus loose
    inline nodes directly inside `container`, with no <p> around them -
    malformed wiki markup found on 34 cached pages (Madara Uchiha, Kratos,
    Dante, Hashirama, several Gokus): every other field in the same tab
    is a proper <p>, but e.g. Durability isn't, so a <p>-only scan never
    saw it. A value runs until the next block element or next label."""
    fields = []
    label, parts = None, []
    for node in list(container.children):
        name = getattr(node, "name", None)
        new_label = _label_text(node) if name == "b" else None
        if name in _BLOCK_TAGS or new_label:
            if label:
                value = _clean_text(BeautifulSoup("".join(parts), "lxml").get_text())
                if value:
                    fields.append((label, value))
            label, parts = new_label, []
            continue
        if label is not None:
            parts.append(str(node))
    if label:
        value = _clean_text(BeautifulSoup("".join(parts), "lxml").get_text())
        if value:
            fields.append((label, value))
    return fields


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
    for i, (tab_label, content) in enumerate(zip(tab_labels, top_contents)):
        stat_values: Dict[str, str] = {}
        for p in _stat_paragraphs(content):
            b = p.find("b")
            if b is None:
                continue
            first_label = _label_text(b)
            if first_label is None:
                continue
            for field_label, value_html in _p_fields(p, b, first_label):
                field_key = STAT_FIELD_MAP.get(field_label.lower())
                if field_key is None:
                    continue
                value = _clean_text(BeautifulSoup(value_html, "lxml").get_text())
                if value:
                    stat_values[field_key] = value
        for container in [content] + content.find_all("div", class_="scrollable", recursive=False):
            for field_label, value in _bare_label_fields(container):
                field_key = STAT_FIELD_MAP.get(field_label.lower())
                if field_key and field_key not in stat_values:
                    stat_values[field_key] = value
        forms.append(CharacterForm(
            name=tab_label,
            tier=aligned_tiers[i].strip() if aligned_tiers else None,
            stats=StatBlock(**stat_values),
        ))
    return forms


def _segments_for_forms(flat_value: Optional[str], n: int) -> List[Optional[str]]:
    """One value per form from a flat, possibly '|'-split field. Handled
    per field, independently - a page can vary one field across forms
    (e.g. just Speed) without every other field following the same
    split. n segments map to forms in order. Exactly one (unsplit) value
    is the wiki's shorthand for "same for every form" - e.g. Bambietta
    Basterbine's Speed, "Massively Hypersonic" with no '|' at all - so
    it's shared rather than dropped. Any other count (2+ but not n)
    stays None: there's no safe way to know which segment is whose."""
    segments = _split_top_level(flat_value, "|") if flat_value else []
    if len(segments) == n:
        return segments
    if len(segments) == 1:
        return segments * n
    return [None] * n


def _per_tab_field_value(fragment: BeautifulSoup) -> Optional[str]:
    """Some pages put ONE field's value in its own small tabber, one tab
    per form, under an otherwise-empty label - e.g. Bambietta
    Basterbine's "Attack Potency:" followed by an "Alive"/"Zombie"
    tabber. Plain get_text() mashes the tab labels and every tab's
    content into one run ("AliveZombieMulti-Continent level..."). Rejoin
    the tabs with the same '|' convention flat multi-form fields already
    use, in tab order, so _extract_forms_from_flat_key splits it like any
    other field. Returns None (caller falls back to plain text) unless
    the value lives entirely inside that tabber, and the tabber isn't a
    full stats tabber (tabs holding their own labeled stat fields)."""
    tabber = fragment.find("div", class_="tabber")
    if tabber is None:
        return None
    contents = tabber.find_all("div", class_="wds-tab__content", recursive=False)
    if len(contents) < 2:
        return None
    for content in contents:
        for b in content.find_all("b"):
            if b.get_text(strip=True).endswith(":"):
                return None
    outside = BeautifulSoup(str(fragment), "lxml")
    outside.find("div", class_="tabber").decompose()
    if _clean_text(outside.get_text()):
        return None
    parts = [_clean_text(c.get_text()) for c in contents]
    if not all(parts):
        return None
    return " | ".join(parts)


def _extract_forms_from_flat_key(stats: "CharacterStats") -> List[CharacterForm]:
    """Fallback for flat (non-tabber) pages whose stat fields pack every
    form into one '|'-separated string, named by a parallel "Key:" field
    (see module docstring). Only fires when Key has 2+ segments and Tier's
    own '|' segments line up with it exactly - Tier is the field always
    present, so it decides whether this page is multi-form at all."""
    key_text = stats.extra_fields.get("Key")
    if not key_text:
        return []
    key_segments = _split_top_level(key_text, "|")
    tier_segments = _split_top_level(stats.tier, "|") if stats.tier else []
    if len(key_segments) < 2 or len(tier_segments) != len(key_segments):
        return []

    n = len(key_segments)
    field_segments = {
        field_key: _segments_for_forms(getattr(stats, field_key), n)
        for field_key in STAT_FIELD_MAP.values()
    }

    forms = []
    for i, name in enumerate(key_segments):
        stat_values = {field_key: field_segments[field_key][i] for field_key in STAT_FIELD_MAP.values()}
        forms.append(CharacterForm(
            name=name,
            tier=tier_segments[i].strip(),
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

        value = _per_tab_field_value(fragment) if key in _PER_FORM_KEYS else None
        if value is None:
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
        # Hybrid pages (Ichigo Kurosaki (Pre-Timeskip): Attack Potency
        # inside the tabs, but Durability/Speed/Stamina as flat '|'-split
        # fields outside them) - the tabs alone lost those fields for
        # every form. Fill only what a tab doesn't itself list, by the
        # same per-field rule as flat multi-form pages; a tab's own value
        # always wins, and pages with no flat value (Genos) are untouched.
        for field_key in STAT_FIELD_MAP.values():
            segments = _segments_for_forms(getattr(stats, field_key), len(forms))
            for form, segment in zip(forms, segments):
                if segment and not getattr(form.stats, field_key):
                    setattr(form.stats, field_key, segment)
    else:
        forms = _extract_forms_from_flat_key(stats)
    stats.forms = forms if forms else [_default_form(stats)]

    return stats
