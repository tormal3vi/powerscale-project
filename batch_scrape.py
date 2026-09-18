"""Batch-scrapes an entire VS Battles Wiki category: fetches the member
list, then scrapes + parses + normalizes each character page in
sequence, storing results in the local SQLite DB.

Usage:
    python batch_scrape.py "Category:Kages"
    python batch_scrape.py "One-Punch Man"        # "Category:" prefix optional
    python batch_scrape.py "Kages" --dry-run
    python batch_scrape.py "Kages" --force
    python batch_scrape.py "Kages" --freshness-days 7
"""

import argparse
import sys
from typing import List

import db
import normalizer
import parser as parser_module
import scraper
from category_fetcher import fetch_category_members

DEFAULT_FRESHNESS_DAYS = 30


def _looks_like_a_character(stats: parser_module.CharacterStats) -> bool:
    """A page that has none of the 4 core numeric stats almost certainly
    isn't a character page (e.g. an ability/mechanic page that slipped
    past category_fetcher's title-based filter) - see category_fetcher's
    module docstring for why that filter is deliberately conservative."""
    return any([stats.tier, stats.attack_potency, stats.speed, stats.durability])


def _print_dry_run(titles: List[str]) -> None:
    n = len(titles)
    print(f"\nDry run: {n} character page(s) would be scraped (no pages fetched).")
    if n == 0:
        return
    head = titles[:5]
    print("First:", ", ".join(head))
    if n > 10:
        print("Last: ", ", ".join(titles[-5:]))
    elif n > 5:
        print("Rest: ", ", ".join(titles[5:]))


def _print_summary(summary: dict, total: int) -> None:
    print("\n" + "=" * 50)
    print(f"Batch complete: {total} candidate page(s)")
    print(f"  Scraped:                {summary['scraped']}")
    print(f"  Skipped (already fresh): {summary['skipped_fresh']}")
    print(f"  Skipped (no stats found): {summary['skipped_no_stats']}")
    print(f"  Failed:                 {summary['failed']}")
    if summary["failures"]:
        print("\nFailures:")
        for title, err in summary["failures"]:
            print(f"  - {title}: {err}")


def run_batch(
    category: str,
    force: bool = False,
    freshness_days: int = DEFAULT_FRESHNESS_DAYS,
    dry_run: bool = False,
) -> dict:
    print(f"Fetching member list for {category!r}...")
    titles = fetch_category_members(category)
    print(f"Found {len(titles)} character page(s) after filtering.")

    if dry_run:
        _print_dry_run(titles)
        return {"titles": titles}

    db.init_db()

    summary = {
        "scraped": 0,
        "skipped_fresh": 0,
        "skipped_no_stats": 0,
        "failed": 0,
        "failures": [],
    }
    total = len(titles)

    for i, title in enumerate(titles, start=1):
        url = scraper.page_url(title)
        prefix = f"{i}/{total}: {title}..."

        if not force and db.is_fresh(url, max_age_days=freshness_days):
            print(f"{prefix} skipped (already fresh)")
            summary["skipped_fresh"] += 1
            continue

        print(prefix, end=" ", flush=True)
        try:
            html = scraper.fetch_page(title)
            stats = parser_module.parse_character(html, source=url)

            if not _looks_like_a_character(stats):
                print("skipped (no Powers and Stats found)")
                summary["skipped_no_stats"] += 1
                continue

            normalized = normalizer.normalize_character(stats)
            db.upsert_character(
                name=stats.name or title,
                source_url=url,
                category=category,
                raw=stats.to_dict(),
                normalized=normalized.to_dict(),
            )
            summary["scraped"] += 1
            print("done")
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the batch
            summary["failed"] += 1
            summary["failures"].append((title, str(exc)))
            print(f"FAILED ({exc})")

    _print_summary(summary, total)
    return summary


def main() -> int:
    argp = argparse.ArgumentParser(description="Batch-scrape a VS Battles Wiki category into the local DB.")
    argp.add_argument("category", help="Category name, e.g. 'Kages' or 'Category:Naruto'")
    argp.add_argument("--force", action="store_true", help="Re-scrape even if already fresh in the DB")
    argp.add_argument(
        "--freshness-days", type=int, default=DEFAULT_FRESHNESS_DAYS,
        help=f"Skip characters scraped within this many days (default: {DEFAULT_FRESHNESS_DAYS})",
    )
    argp.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be scraped (count + sample titles) without fetching any character page",
    )
    args = argp.parse_args()

    try:
        run_batch(args.category, force=args.force, freshness_days=args.freshness_days, dry_run=args.dry_run)
    except scraper.FetchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
