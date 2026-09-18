"""CLI entry point: fetch a VS Battles Wiki character page, parse its
Powers and Stats, print it, and save it as JSON under /data.

Usage:
    python main.py "Character Name"
    python main.py "https://vsbattles.fandom.com/wiki/Character_Name"
    python main.py "Character Name" --refresh   # bypass the page cache
"""

import argparse
import json
import re
import sys
from pathlib import Path

from scraper import FetchError, fetch_page, page_title, page_url
from parser import parse_character

DATA_DIR = Path(__file__).parent / "data"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", name.strip().replace(" ", "_"))
    return slug or "character"


def main() -> int:
    argp = argparse.ArgumentParser(description="Scrape and parse a VS Battles Wiki character page.")
    argp.add_argument("character", help="Character name or full page URL")
    argp.add_argument("--refresh", action="store_true", help="Bypass the disk cache and re-fetch")
    args = argp.parse_args()

    try:
        html = fetch_page(args.character, force_refresh=args.refresh)
    except FetchError as exc:
        print(f"Error fetching page: {exc}", file=sys.stderr)
        return 1

    stats = parse_character(html, source=page_url(args.character))
    result = stats.to_dict()

    print(json.dumps(result, indent=2, ensure_ascii=False))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{_slugify(page_title(args.character))}.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
