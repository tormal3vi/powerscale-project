"""Warms the wiki's image server for pictures a page is about to need.

Character pictures are cropped and resized by Fandom's image server on
first request, then cached. For a size nobody has asked for yet, that
first request took 0.7-18 seconds (measured); once cached, ~0.3s. A duel
round draws random characters, so nearly every picture was a first
request - while the round's 20-second clock was running. When a game is
created the server already knows its ten characters, so it requests
their pictures here, in the background, before anyone plays. Nothing
is sent to the players, so the matchups stay hidden.
"""

import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List, Optional, Tuple

import db
import scraper

DUEL_PX = 320  # the size duels.js asks for: characterPictureUrl(url, 320)
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="prewarm")


def picture_url(url: Optional[str], px: int) -> Optional[str]:
    """The same URL frontend/api.js's characterPictureUrl builds - it has
    to match exactly for the cached copy to be the one the page gets."""
    if not url or not url.startswith("https://static.wikia.nocookie.net/"):
        return None
    path, _, query = url.partition("?")
    return f"{path}/top-crop/width/{px}/height/{px}" + (f"?{query}" if query else "")


def _fetch(url: str) -> None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": scraper.USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except Exception:  # noqa: BLE001 - a picture that didn't warm just loads slower
        pass


def round_pictures(pairs: Iterable[Tuple[int, int]]) -> None:
    """Queues both pictures of each (character, character) round, in round
    order, so the first rounds are ready first."""
    if os.environ.get("PREWARM_PICTURES") == "0":  # tests: no network
        return
    urls: List[str] = []
    for pair in pairs:
        for char_id in pair:
            row = db.get_character_by_id(char_id) or {}
            url = picture_url(row.get("image_url"), DUEL_PX)
            if url and url not in urls:
                urls.append(url)
    for url in urls:
        _pool.submit(_fetch, url)
