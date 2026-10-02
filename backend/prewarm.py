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
from typing import Iterable, List, Optional

import scraper

DUEL_PX = 320  # the size duels.js asks for: characterPictureUrl(url, 320)
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="prewarm")


def picture_url(url: Optional[str], px: int) -> Optional[str]:
    """The same URL frontend/api.js's characterPictureUrl builds - it has
    to match exactly for the cached copy to be the one the page gets."""
    if not url or not url.startswith("https://static.wikia.nocookie.net/"):
        return None
    from backend import characters
    return characters.wiki_square(url, px)


def _fetch(url: str) -> None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": scraper.USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except Exception:  # noqa: BLE001 - a picture that didn't warm just loads slower
        pass


def round_pictures(rounds: Iterable[tuple]) -> None:
    """Queues every picture of each round, in round order, so the first
    rounds are ready first. A round lists its characters - ids (their
    default form: a draft hand) or (id, form) pairs - and each gets the
    picture of that form, as the round will show it."""
    if os.environ.get("PREWARM_PICTURES") == "0":  # tests: no network
        return
    from backend import characters
    urls: List[str] = []
    for shown in rounds:
        for entry in shown:
            char_id, form = entry if isinstance(entry, tuple) else (entry, None)
            url = picture_url(characters.form_picture(char_id, form), DUEL_PX)
            if url and url not in urls:
                urls.append(url)
    for url in urls:
        _pool.submit(_fetch, url)
