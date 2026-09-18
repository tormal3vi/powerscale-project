"""Fetches the member list of a VS Battles Wiki category via the
MediaWiki API (action=query&list=categorymembers), paginating through
cmcontinue tokens, and filters out non-character noise.

Filtering rationale (from directly inspecting Category:One-Punch_Man,
249 members, and Category:Naruto, 351 members, before writing any of
this):

- The overwhelming majority of non-character noise is `User blog:`
  pages - community power-scaling calculation posts, e.g. "User
  blog:Therefir/One-Punch Man: Serious Sneeze" - which live in namespace
  500. Subcategory links (e.g. "Category:Tank Topper Army" nested inside
  Category:One-Punch_Man) show up as namespace 14. Restricting to the
  main article namespace (ns=0) removes essentially all of this in one
  shot - out of 249 One-Punch Man members, only 98 were ns=0.
- Within ns=0, VS Battles Wiki follows one systematic non-character
  convention per series: a page for the series/franchise itself, which
  is a member of its own category. Its title is usually "<Series>
  (Verse)" (e.g. "Naruto (Verse)" - disambiguated because "Naruto" alone
  would collide with the character), but when there's no such collision
  it's just the bare series name (e.g. "One-Punch Man" in
  Category:One-Punch_Man, confirmed by fetching that exact title - no
  "(Verse)" suffix exists for it). Both forms are filtered: by the
  "(Verse)" suffix, and by an exact match against the category's own
  name.
- A handful of ability/mechanic pages slip past both filters - e.g.
  "Sharingan", "Chakra Cannon", "Ōtsutsuki Physiology" in
  Category:Naruto. These don't follow a reliable title pattern, and
  guessing at one (e.g. "exclude single-word titles") risks false
  positives against real character names. They're intentionally left
  unfiltered here - batch_scrape.py catches them instead, by skipping
  any page where parsing finds no Powers and Stats data at all, rather
  than treating it as a hard failure.
"""

from dataclasses import dataclass
from typing import List

import requests

import scraper

CATEGORY_MEMBERS_LIMIT = 500  # MediaWiki's per-request cap for categorymembers

_VERSE_SUFFIX = " (Verse)"
_EXCLUDED_TITLE_PREFIXES = (
    "Category:", "Template:", "User:", "User blog:", "User talk:", "File:", "Help:",
)


@dataclass
class CategoryMember:
    title: str
    ns: int
    is_character: bool


def _is_character_page(ns: int, title: str, category_bare_name: str) -> bool:
    if ns != 0:
        return False
    if title.startswith(_EXCLUDED_TITLE_PREFIXES):
        return False
    if title.endswith(_VERSE_SUFFIX):
        return False
    if title.strip().lower() == category_bare_name.strip().lower():
        return False
    return True


def fetch_category_members_raw(category: str) -> List[CategoryMember]:
    """Fetch every member of `category` (paginating as needed), unfiltered
    - each tagged with whether it passed the character-page filter. Useful
    for inspecting a category's actual contents before trusting the filter."""
    if not category.startswith("Category:"):
        category = f"Category:{category}"
    category_bare_name = category[len("Category:"):].replace("_", " ")

    members: List[CategoryMember] = []
    cmcontinue = None
    while True:
        scraper.prepare_api_request()
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category,
            "cmlimit": CATEGORY_MEMBERS_LIMIT,
            "format": "json",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        try:
            response = requests.get(
                scraper.API_ENDPOINT,
                params=params,
                headers={"User-Agent": scraper.USER_AGENT},
                timeout=scraper.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise scraper.FetchError(f"failed to fetch category members for {category!r}: {exc}") from exc

        if "error" in payload:
            info = payload["error"].get("info", payload["error"])
            raise scraper.FetchError(f"MediaWiki API error for category {category!r}: {info}")

        for member in payload.get("query", {}).get("categorymembers", []):
            members.append(CategoryMember(
                title=member["title"],
                ns=member["ns"],
                is_character=_is_character_page(member["ns"], member["title"], category_bare_name),
            ))

        cmcontinue = payload.get("continue", {}).get("cmcontinue")
        if not cmcontinue:
            break

    return members


def fetch_category_members(category: str) -> List[str]:
    """Fetch all character-page titles in `category` (filtered - see
    module docstring)."""
    return [m.title for m in fetch_category_members_raw(category) if m.is_character]
