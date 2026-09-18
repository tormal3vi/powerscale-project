"""Fetches VS Battles Wiki character pages, with rate limiting and a disk
cache so repeated runs during development don't re-hit the network.

Note on fetch strategy: vsbattles.fandom.com puts a Cloudflare bot-challenge
in front of its rendered `/wiki/<Title>` pages, which blocks plain HTTP
clients like `requests` regardless of User-Agent (confirmed by testing -
it's TLS/JS fingerprinting, not a header check). The MediaWiki API endpoint
`/api.php` is not behind that challenge and returns the same rendered HTML,
so this module fetches through the API instead of scraping pages directly.
See `_check_robots_allowed` below for why robots.txt is checked the way it
is here.
"""

import hashlib
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

BASE_URL = "https://vsbattles.fandom.com"
API_ENDPOINT = f"{BASE_URL}/api.php"
USER_AGENT = (
    "PowerscaleResearchTool/0.1 "
    "(personal, non-commercial research project; contact: tormal3vi@gmail.com)"
)
CACHE_DIR = Path(__file__).parent / "cache"
MIN_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT = 15


class FetchError(RuntimeError):
    """Raised when a page can't be fetched (network error, API error, or
    a path that hasn't been verified against robots.txt)."""


class RateLimiter:
    """Enforces a minimum delay between consecutive network requests."""

    def __init__(self, min_delay: float = MIN_DELAY_SECONDS):
        self.min_delay = min_delay
        self._last_request_at = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_delay - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()


_rate_limiter = RateLimiter()


def _check_robots_allowed(path: str) -> None:
    """robots.txt itself sits behind the same Cloudflare bot-challenge as
    the rest of the site for non-browser HTTP clients, so it can't be
    fetched live from here (a `requests`/`urllib` GET to /robots.txt also
    gets the challenge page, not the file). It was checked manually in a
    real browser instead: for `User-agent: *`, `/api.php` is explicitly
    allowed, and only the Special:/User:/Template:/Help: namespaces under
    /wiki/ are disallowed. This module only ever calls /api.php, so that's
    the only path this guard needs to allow.
    """
    if not path.startswith("/api.php"):
        raise FetchError(
            f"refusing to fetch {path}: not verified against robots.txt "
            "(this tool only fetches via /api.php)"
        )


def page_url(name_or_url: str) -> str:
    """Canonical /wiki/ URL for a name or URL, for display/record-keeping
    (not used for fetching - fetching goes through /api.php)."""
    title = _page_title(name_or_url)
    return f"{BASE_URL}/wiki/{title.replace(' ', '_')}"


def page_title(name_or_url: str) -> str:
    """Public wrapper around the name-or-URL -> bare page title logic."""
    return _page_title(name_or_url)


def _page_title(name_or_url: str) -> str:
    """Turn a character name or a /wiki/<Title> URL into a bare page title."""
    if name_or_url.startswith("http://") or name_or_url.startswith("https://"):
        path = urlparse(name_or_url).path
        title = path.rsplit("/wiki/", 1)[-1]
        return unquote(title).replace("_", " ")
    return name_or_url.strip()


def _cache_path(title: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", title.strip().replace(" ", "_"))
    if not slug:
        slug = hashlib.sha256(title.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{slug}.html"


def prepare_api_request() -> None:
    """Robots.txt check + rate-limit wait for a call to API_ENDPOINT.
    Shared by every module that hits the MediaWiki API directly (this
    module's fetch_page, and category_fetcher's category-listing calls)
    so they all go through the same single rate limiter - callers must
    never issue requests to the API without calling this first."""
    _check_robots_allowed(urlparse(API_ENDPOINT).path)
    _rate_limiter.wait()


def fetch_page(name_or_url: str, force_refresh: bool = False) -> str:
    """Return the rendered HTML for a character page's content.

    Uses the on-disk cache when present unless force_refresh is True.
    Otherwise fetches via the MediaWiki API (action=parse), applying the
    rate limiter first.
    """
    title = _page_title(name_or_url)
    cache_file = _cache_path(title)

    if not force_refresh and cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    prepare_api_request()

    try:
        response = requests.get(
            API_ENDPOINT,
            # redirects=1: without it, a redirect page (e.g. "Piccolo" ->
            # "Piccolo (Dragon Ball Z)") returns only a "Redirect to: ..."
            # stub with no Powers and Stats section at all, instead of
            # the real target page - found while diagnosing why Piccolo
            # came back with zero parsed stats (it wasn't a parser bug;
            # the scraper just never followed the redirect).
            params={"action": "parse", "page": title, "format": "json", "prop": "text", "redirects": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise FetchError(f"failed to fetch page {title!r}: {exc}") from exc

    if "error" in payload:
        info = payload["error"].get("info", payload["error"])
        raise FetchError(f"MediaWiki API error for {title!r}: {info}")

    html = payload["parse"]["text"]["*"]
    cache_file.write_text(html, encoding="utf-8")
    return html
