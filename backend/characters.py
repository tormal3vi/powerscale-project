"""Character-name display rules and comparisons shared by the API modules
(the core API in main.py and the community/message-board API)."""

import functools
import re
import threading
import unicodedata
from typing import Dict, Optional

import calculator
import db
import scraper


def name_key(name: str) -> str:
    """A character's primary name for collision purposes: the first alias
    (names list aliases after ',', ';' or '/'), parentheticals dropped,
    whitespace collapsed - so "Frieza/Freeza/Freezer", "Frieza / Freeza /
    Freezer" and plain "Frieza" all count as the same name."""
    first = re.split(r"[,;/]", name, maxsplit=1)[0]
    first = re.sub(r"\([^)]*\)", "", first)
    return " ".join(first.split()).lower()


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
        return bare if _title_is_better_known(name, title, bare) else name
    return bare if name_key(name) != name_key(bare) else name


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
    if first.count("(") > first.count(")"):
        # The first alias split mid-note: "Mistral (Her codename, true name
        # is unknown)", "Raiden (雷電?) (Birth name unknown, but ...".
        return _name_words(re.sub(r"\(.*", "", first)) == title_words
    if re.match(r"\s*unknown\b", name, re.I):
        return True  # "Unknown, impersonated Captain Tennille", "Unknown. Aliases include the Phantom Stranger"
    if not title_words <= _name_words(name) or not lead or title_words <= lead:
        return False  # "Son Goku, Kakarot" is fine next to a page titled "Goku"
    qualifier = re.search(r"\(([^)]*)\)\s*$", title)
    return not (qualifier and _name_words(qualifier.group(1)) <= lead)


def _title_key(source_url: str) -> Optional[str]:
    """"Omni-Man (Comics)" -> "title:omni-man". None for manual entries."""
    if not source_url.startswith("http"):
        return None
    bare = re.sub(r"\s*\([^)]*\)\s*$", "", scraper.page_title(source_url)).strip()
    return "title:" + bare.lower()


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


def short_name(name: str) -> str:
    return re.split(r";|,\s", name, maxsplit=1)[0].strip()  # not "170,000 ..." -> "170"


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


def invalidate() -> None:
    """Call after changing the characters table."""
    global _colliding
    with _colliding_lock:
        _colliding = None
    _compare_cached.cache_clear()


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
