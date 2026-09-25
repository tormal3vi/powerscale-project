"""Batch-scrapes an entire VS Battles Wiki category: fetches the member
list, then scrapes + parses + normalizes each character page in
sequence, storing results in the local SQLite DB.

Usage:
    python batch_scrape.py "Category:Kages"
    python batch_scrape.py "One-Punch Man"        # "Category:" prefix optional
    python batch_scrape.py "Kages" --dry-run
    python batch_scrape.py "Kages" --force
    python batch_scrape.py "Kages" --freshness-days 7
    python batch_scrape.py "Bat Family" --series DC   # stored as DC, by subseries
"""

import argparse
import sys
from typing import List, Optional

import db
import normalizer
import parser as parser_module
import scraper
from category_fetcher import fetch_category_members

DEFAULT_FRESHNESS_DAYS = 30

# Franchises too big for one Browse filter. A batch run with --series NAME
# stores every character under that one series, plus a subseries: the
# first part below whose wiki categories list the page, else the default.
# Only each category's own pages count, never its subcategories - the
# wiki nests the general "Justice League Members" inside DC Extended
# Universe, which would file every comics Leaguer under the films.
SUBSERIES = {
    "DC": ("Comics", [
        ("Arkham", ["Batman: Arkham"]),
        ("Injustice", ["Injustice: Gods Among Us"]),
        ("DCEU", ["DC Extended Universe", "Man of Steel"]),
        ("DCU", ["DC Universe", "Justice Gang (DC Universe)"]),
        ("DC Animated Universe", ["DC Animated Universe"]),
        ("Animated Movies", ["DC Animated Movies", "Tomorrowverse"]),
        ("Arrowverse", ["Arrowverse (CW)"]),
        ("Young Justice", ["Young Justice"]),
        ("Teen Titans (2003)", ["Teen Titans"]),
        ("Smallville", ["Smallville"]),
        ("Dark Knight Trilogy", ["The Dark Knight Trilogy"]),
        ("Other adaptations", [
            "Batman (1943 Serial)", "Batman (1966)", "Batman (Burtonverse)", "Batman Unlimited",
            "Batman: Caped Crusader", "Batman: The Brave and the Bold", "Batman: The Telltale Series",
            "Batman: Under the Red Hood", "Catwoman (2004)", "Constantine (2005 Movie)", "DC League of Superpets",
            "DC Serial", "DC Showcase", "DC Super Hero Girls", "Deathstroke: Knights & Dragons",
            "Doom Patrol (TV Series)", "Gotham (TV Series)", "Harley Quinn (TV Series)", "Justice League Action",
            "Justice League: Crisis on Two Earths", "Krypto the Superdog (2005 Cartoon)", "Lego DC",
            "Lucifer (TV Series)", "My Adventures with Superman", "Super Friends", "Superman (Fleischer Cartoons)",
            "Superman (Titus Interactive)", "Superman and Lois", "Swamp Thing (1982 Movie)", "Teen Titans GO!",
            "The Batman (2004)", "The Batman Epic Crime Saga", "Wonder Woman (1975 TV Series)",
        ]),
        ("Vertigo", ["Vertigo"]),
        ("Wildstorm", ["Wildstorm Comics"]),
        ("Watchmen", ["Watchmen"]),
    ]),
}


# Adaptation pages no adaptation category lists (the wiki files them under
# the character or a one-page category named like the page itself, which
# category_fetcher drops as the series' own page).
SUBSERIES_BY_TITLE = {
    "DC": {
        "Batman (The Lego Movie)": "Other adaptations",
        "Green Lantern (2011 Film Version)": "Other adaptations",
        "Superman (Titus Interactive)": "Other adaptations",
        "Superman (Superman vs The Elite)": "Animated Movies",
        "Orion (Young Justice)": "Young Justice",
    },
}


def subseries_lookup(series: str):
    """title -> subseries for `series`, from the member lists of its parts'
    categories (a few cheap API calls, no character pages)."""
    default, parts = SUBSERIES[series]
    owner = dict(SUBSERIES_BY_TITLE.get(series, {}))
    for label, categories in parts:
        for category in categories:
            for title in fetch_category_members(category):
                owner.setdefault(title, label)
    return lambda title: owner.get(title, default)


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
    series: Optional[str] = None,
    subseries_of=None,
) -> dict:
    """With `series`, every character is stored under that series (not the
    wiki category scraped) with its subseries; `subseries_of` can pass a
    lookup already built, when running several categories in a row."""
    print(f"Fetching member list for {category!r}...")
    titles = fetch_category_members(category)
    print(f"Found {len(titles)} character page(s) after filtering.")
    if series and subseries_of is None:
        subseries_of = subseries_lookup(series)

    if dry_run:
        _print_dry_run(titles)
        if series:
            counts = {}
            for t in titles:
                counts[subseries_of(t)] = counts.get(subseries_of(t), 0) + 1
            print("Subseries:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
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
            if series:  # already here (e.g. under another category): just file it
                db.set_series(url, series, subseries_of(title))
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
                category=series or category,
                raw=stats.to_dict(),
                normalized=normalized.to_dict(),
                subseries=subseries_of(title) if series else None,
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
    argp.add_argument("category", nargs="+", help="Category name(s), e.g. 'Kages' or 'Category:Naruto'")
    argp.add_argument("--force", action="store_true", help="Re-scrape even if already fresh in the DB")
    argp.add_argument(
        "--freshness-days", type=int, default=DEFAULT_FRESHNESS_DAYS,
        help=f"Skip characters scraped within this many days (default: {DEFAULT_FRESHNESS_DAYS})",
    )
    argp.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be scraped (count + sample titles) without fetching any character page",
    )
    argp.add_argument(
        "--series", choices=sorted(SUBSERIES),
        help="Store everything under this one series, split into subseries (see SUBSERIES)",
    )
    args = argp.parse_args()

    try:
        lookup = subseries_lookup(args.series) if args.series else None
        for category in args.category:
            run_batch(category, force=args.force, freshness_days=args.freshness_days, dry_run=args.dry_run,
                      series=args.series, subseries_of=lookup)
    except scraper.FetchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
