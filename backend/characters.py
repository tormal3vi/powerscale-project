"""Character-name display rules and comparisons shared by the API modules
(the core API in main.py and the community/message-board API)."""

import functools
import json
import re
import threading
import unicodedata
from typing import Dict, Optional, Tuple

import calculator
import db
import scraper


def name_key(name: str) -> str:
    """A character's primary name for collision purposes: the first alias
    (names list aliases after ',', ';' or '/'), parentheticals dropped,
    whitespace collapsed - so "Frieza/Freeza/Freezer", "Frieza / Freeza /
    Freezer" and plain "Frieza" all count as the same name. A leading "The"
    doesn't count either: "The Mandarin" (Lego) and "Mandarin" clash."""
    first = re.split(r"[,;/]", name, maxsplit=1)[0]
    first = re.sub(r"\([^)]*\)", "", first)
    return re.sub(r"^the\s+", "", " ".join(first.split()).lower())


def _name_words(s: str) -> set:
    folded = "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))
    return set(re.findall(r"[a-z0-9]{2,}", folded))


def base_name(name: str, source_url: str) -> str:
    """The stored name, unless it shares no word at all with the wiki page
    title (accents ignored) - then the title, minus any "(Series)"
    qualifier. Catches wiki-side mistakes in the "Name:" field itself:
    Land's page literally says "Name: Male", Third Kazekage's says
    "Unknown", Megath's "Varies", Sherry Blendy's "Yuka Suzuki". Only
    ~18 of 1550 characters trip this, and the rest just switch to the
    wiki's own spelling (Nidhogg, Gorgon)."""
    # A '|' in the Name field separates per-FORM names, same convention as
    # every stat field ("Homura Akemi | Same | Homulily | Akuma Homura",
    # "Uub | Majuub") - the first is the character's own name.
    name = re.split(r"\s*\|\s*", name, maxsplit=1)[0]
    if not source_url.startswith("http"):
        return name
    title = scraper.page_title(source_url)
    bare = re.sub(r"\s*\([^)]*\)\s*$", "", title)
    name_words, title_words = _name_words(name), _name_words(bare)
    if name_words and title_words:
        if not (name_words & title_words):
            return bare
        return bare if _title_is_better_known(name, title, bare) else _drop_lead_notes(name)
    return bare if name_key(name) != name_key(bare) else name


def _drop_lead_notes(name: str) -> str:
    """Notes in brackets on the name shown first: "President Max Proffit
    Haltmann (English name)", "Bowser Koopa (Simply called Koopa in
    Japan)", "Mr. Bright (The sun-like character) & Mr. Shine (...)"."""
    m = re.match(r"([^,;/]*)(.*)", name, re.S)
    first, rest = m.group(1), m.group(2)
    if first.count("(") != first.count(")"):
        return name
    cleaned = " ".join(re.sub(r"\([^)]*\)", "", first).split())
    if not cleaned or cleaned == " ".join(first.split()):
        return name  # no notes (or nothing but notes)
    return f"{cleaned} {rest}" if rest.startswith("/") else cleaned + rest


def _title_is_better_known(name: str, title: str, bare: str) -> bool:
    """True when the Name field leads with something other than the name
    the page is titled by, though every word of that title is in the field:
    the lead is a real name the character is rarely called ("John ("Jack"),
    Naked Snake, Big Boss", "Charlotte Linlin, "Big Mom"", "Robert Bruce
    Banner; The Incredible Hulk"), a sentence ("Real name unknown, known as
    The Sorrow"), or just part of the title ("Ocelot/Revolver Ocelot").
    Pages titled "Real name (Hero name)" keep the field: "Bad (Metal Bat)"
    and "Isamu (Child Emperor)" are called by the part in brackets."""
    first = re.split(r"[,;/]", name, maxsplit=1)[0]
    lead = _name_words(first)
    title_words = _name_words(bare)
    if re.match(r"\s*(?:(?:real|true|birth) name\s+(?:is\s+)?)?unknown\b|\s*(?:has\s+)?no\s+(?:actual\s+|real\s+|true\s+)?name\b",
                name, re.I):
        # "Unknown, impersonated Captain Tennille", "Unknown (Only known as
        # "Flam·Rouge" ...)", "Real name unknown. Referred as Prometheus",
        # "Has no actual name but is referred to as Demise"
        return True
    if first.count("(") > first.count(")"):
        # The first alias split mid-note: "Mistral (Her codename, true name
        # is unknown)", "Raiden (雷電?) (Birth name unknown, but ...",
        # "The Weatherheads (Hail-O-Pods, ..." on a page titled "Weatherheads".
        return _name_words(re.sub(r"\(.*", "", first)) - {"the"} == title_words - {"the"}
    note = re.search(r"\(([^)]*)\)", first)
    if note and re.search(r"\b(unknown|real name|true name)\b", note.group(1), re.I):
        # "Joker (Real name is unknown)"; "Grey Cloud (real name), Nightwolf"
        # - a real name they're rarely called: the title, if it's in there.
        return title_words <= _name_words(name)
    quoted = _name_words(" ".join(re.findall(r'["\u201c]([^"\u201d]+)["\u201d]', first)))
    if quoted & title_words and title_words <= _name_words(first):
        # The title is the nickname in quotes: 'Jackson "Jax" Briggs' is Jax,
        # 'Jacqueline Sonya "Jacqui" Briggs' Jacqui Briggs, '"Alien"' Alien.
        return True
    if not title_words <= _name_words(name) or not lead or title_words <= lead:
        return False  # "Son Goku, Kakarot" is fine next to a page titled "Goku"
    qualifier = re.search(r"\(([^)]*)\)\s*$", title)
    return not (qualifier and _name_words(qualifier.group(1)) <= lead)


def _title_key(source_url: str) -> Optional[str]:
    """"Omni-Man (Comics)" -> "title:omni-man". None for manual entries."""
    if not source_url.startswith("http"):
        return None
    bare = re.sub(r"\s*\([^)]*\)\s*$", "", scraper.page_title(source_url)).strip()
    return "title:" + re.sub(r"^the\s+", "", bare.lower())


def colliding_names(conn) -> set:
    """Name keys shared by 2+ characters. Distinct wiki pages often carry
    the same "Name:" field - e.g. Ichigo Kurosaki's Pre-Timeskip, Post-
    Timeskip and Live Action pages, or Fairy Tail's X784-X792 vs. X793
    versions - and a couple even carry another character's name by
    mistake on the wiki itself (Sherry Blendy's and Toby Horhorta's pages
    both say "Yuka Suzuki").

    Also page titles that differ only in their "(...)" qualifier, whatever
    the Name fields say: Invincible's Comics and TV Series pages spell
    names differently ("Nowl-Ahn, Nolan Grayson, Omni-Man" vs "Nolan
    Grayson, "Omni-Man"..."), so the name rule alone left two unlabelled
    Omni-Men; same for Kurama (Kyūbi) vs Kurama (Yu Yu Hakusho)."""
    counts: Dict[str, int] = {}
    for name, source_url in conn.execute("SELECT name, source_url FROM characters"):
        for key in (name_key(base_name(name, source_url)), _title_key(source_url)):
            if key:
                counts[key] = counts.get(key, 0) + 1
    return {k for k, n in counts.items() if n > 1}


def display_name(name: str, source_url: str, colliding: set) -> str:
    """The base name (see base_name), unless another character shares it -
    then the full wiki page title, which the wiki guarantees is unique
    (e.g. "Ichigo Kurosaki (Pre-Timeskip)"). Manually entered characters
    have no real page title, so they always keep their name."""
    base = base_name(name, source_url)
    if not source_url.startswith("http"):
        return base
    if name_key(base) in colliding or _title_key(source_url) in colliding:
        return scraper.page_title(source_url)
    return base


def display_name_for_id(char_id: int, colliding: Optional[set] = None) -> Optional[str]:
    row = db.get_character_by_id(char_id)
    if row is None:
        return None
    if colliding is None:
        colliding = all_collisions()
    return display_name(row["name"], row["source_url"], colliding)


def is_gif(url: Optional[str]) -> bool:
    """A wiki picture that's a GIF (often animated): Fandom's resizer gives
    up on the big ones after 12 seconds (503), so they're used whole."""
    return bool(url) and url.split("?")[0].split("/revision/")[0].lower().endswith(".gif")


def wiki_square(url: str, px: int) -> str:
    """A px-by-px top crop of a wiki picture from Fandom's CDN (see
    characterPictureUrl in frontend/api.js) - GIFs whole, other sites' as is."""
    if not url.startswith("https://static.wikia.nocookie.net/") or is_gif(url):
        return url
    path, _, query = url.partition("?")
    return f"{path}/top-crop/width/{px}/height/{px}" + (f"?{query}" if query else "")


def short_name(name: str) -> str:
    # Not "170,000 ..." -> "170"; "Team Kirby / ...", but "Ocelot/Revolver Ocelot" stays whole.
    return re.split(r";|,\s|\s/|/\s", name, maxsplit=1)[0].strip()


# Character data only changes on a deploy (a new powerscale.db) or when a
# character is added through the API, which calls invalidate(). Until then
# these results can't change, and Render's free tier (a tenth of a CPU)
# made recomputing them per request the slow part of the Board and the
# character list: ~0.3s per matchup verdict, ~0.3s for the name clashes.
_colliding: Optional[set] = None
_colliding_lock = threading.Lock()


def all_collisions() -> set:
    """colliding_names() for the whole database, computed once."""
    global _colliding
    with _colliding_lock:
        if _colliding is None:
            with db.connect() as conn:
                _colliding = colliding_names(conn)
        return _colliding


@functools.lru_cache(maxsize=4096)
def form_picture(char_id: int, form_name: Optional[str] = None) -> Optional[str]:
    """The wiki picture for one of a character's forms - the default form
    (its strongest, what duels and verdicts use) when none is named: the
    form's own picture if its page has one (God Dimple), else the
    character's. An admin's replacement picture is the caller's to prefer."""
    row = db.get_character_by_id(char_id)
    if row is None:
        return None
    normalized = json.loads(row["normalized_json"])
    raw_forms = json.loads(row["raw_json"]).get("forms") or []
    forms = normalized.get("forms") or []
    if forms:
        name = form_name or calculator.select_form(normalized).get("name")
        # raw and normalized forms are parallel lists
        index = next((i for i, f in enumerate(forms) if f.get("name") == name), None)
        if index is not None and index < len(raw_forms) and raw_forms[index].get("image_url"):
            return raw_forms[index]["image_url"]
    return row.get("image_url")


def invalidate() -> None:
    """Call after changing the characters table."""
    global _colliding
    with _colliding_lock:
        _colliding = None
    _compare_cached.cache_clear()
    scorable_pool.cache_clear()
    form_picture.cache_clear()


@functools.lru_cache(maxsize=1)
def scorable_pool() -> Tuple[Tuple[int, float, str], ...]:
    """(id, Tier score, series) of every character whose default form can
    get a verdict - what prediction duels draw random matchups from."""
    pool = []
    with db.connect() as conn:
        for row in conn.execute("SELECT id, category, normalized_json FROM characters"):
            normalized = json.loads(row["normalized_json"])
            if not normalized.get("forms"):
                continue
            form = calculator.select_form(normalized)
            tier = (form.get("tier") or {}).get("baseline")
            scored = sum(1 for axis in calculator.AXES if (form.get(axis) or {}).get("baseline") is not None)
            if tier is not None and scored >= calculator.MIN_AXES_FOR_VERDICT:
                pool.append((row["id"], tier, row["category"] or "Uncategorized"))
    return tuple(pool)


@functools.lru_cache(maxsize=4096)
def _compare_cached(char_a, char_b, form_a, form_b) -> "calculator.Verdict":
    names = all_collisions()

    def name_for(ref):
        return display_name_for_id(ref, names) if isinstance(ref, int) else None

    return calculator.compare_characters(
        char_a, char_b, form_a, form_b,
        name_a=name_for(char_a), name_b=name_for(char_b),
    )


def run_compare(char_a, char_b, form_a=None, form_b=None) -> "calculator.Verdict":
    """calculator.compare_characters with display names in the verdict.
    Callers only read the result (it's shared between requests)."""
    return _compare_cached(char_a, char_b, form_a, form_b)
