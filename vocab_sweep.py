"""Standalone vocabulary-gap sweep (no DB writes): scrapes a handful of
categories, parses + normalizes each character, and collects every
tier/speed/attack-potency/durability chunk that TIER_LADDER/SPEED_LADDER
don't recognize - so real gaps can be reviewed and decided on before
Phase 4 builds charts on top of potentially-incomplete data.

This intentionally does NOT use batch_scrape.py / db.py - it's
exploratory only, per Phase 4 prep: "surface gaps without committing to
a full batch scrape." Categories were picked (and confirmed to exist,
with real member counts) to stress different vocabulary than what
Category:Kages and Category:One-Punch_Man already covered - see
DEFAULT_CATEGORIES below for the reasoning behind each.

Miss detection works at the chunk level, not the whole-field level: each
raw field value is split the same way the wiki actually formats it
(top-level '|' for alternate forms, then top-level ',' for progressions
within a form, both bracket-aware so parenthetical text with its own
commas doesn't get mis-split). Each chunk is checked independently
against the relevant ladder. This catches PARTIAL misses a whole-string
check would hide - e.g. "9-B | UnrecognizedTerm" has a recognized first
chunk, so normalizer.parse_range succeeds and logs no warning at all,
but the second chunk is still a real gap.

Usage:
    python vocab_sweep.py
    python vocab_sweep.py --categories "Baki the Grappler" "Fairy Tail"
"""

import argparse
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import normalizer
import parser as parser_module
import scraper
from category_fetcher import fetch_category_members
from parser import _split_top_level  # bracket-aware splitter, reused for both '|' and ','

DEFAULT_CATEGORIES = [
    "Baki the Grappler",   # mundane/low-tier, no superpowers - stresses the human-scale low end
    "Fairy Tail",           # long-running shonen, reputation for messy/inconsistent stat blocks
    "Puella Magi Verse",    # small cast ascending to abstract/conceptual power - unique top-end terms
]

_STAT_FIELDS = [
    ("tier", normalizer.TIER_LADDER),
    ("attack_potency", normalizer.TIER_LADDER),
    ("durability", normalizer.TIER_LADDER),
    ("speed", normalizer.SPEED_LADDER),
]

REPORT_PATH = Path(__file__).parent / "vocab_gaps_report.txt"


@dataclass
class Miss:
    field: str
    chunk: str
    character: str
    category: str


def _chunks(raw_value: str) -> List[str]:
    cleaned = normalizer._strip_parentheses(raw_value)
    chunks = []
    for pipe_part in _split_top_level(cleaned, "|"):
        for comma_part in _split_top_level(pipe_part, ","):
            piece = comma_part.strip()
            if piece:
                chunks.append(piece)
    return chunks


def _collect_misses_for_character(stats: "parser_module.CharacterStats", category: str, misses: List[Miss]) -> int:
    found = 0
    for field_name, ladder in _STAT_FIELDS:
        raw_value = getattr(stats, field_name)
        if not raw_value:
            continue
        for chunk in _chunks(raw_value):
            if not normalizer._find_all_tokens(chunk, ladder):
                misses.append(Miss(field=field_name, chunk=chunk, character=stats.name or "?", category=category))
                found += 1
    return found


def sweep(categories: List[str]) -> List[Miss]:
    all_misses: List[Miss] = []
    for category in categories:
        print(f"\n=== {category} ===")
        titles = fetch_category_members(category)
        print(f"{len(titles)} character page(s)")
        for i, title in enumerate(titles, start=1):
            print(f"{i}/{len(titles)}: {title}...", end=" ", flush=True)
            try:
                html = scraper.fetch_page(title)
                stats = parser_module.parse_character(html, source=scraper.page_url(title))
            except Exception as exc:  # noqa: BLE001 - one bad page must not kill the sweep
                print(f"FAILED ({exc})")
                continue
            found = _collect_misses_for_character(stats, category, all_misses)
            print(f"done ({found} miss{'es' if found != 1 else ''})" if found else "done")
    return all_misses


# --- dedup + grouping --------------------------------------------------

def _normalize_for_dedup(text: str) -> str:
    return " ".join(text.lower().split())


def _dedupe(misses: List[Miss]) -> Dict[str, dict]:
    groups: Dict[str, dict] = {}
    for m in misses:
        key = _normalize_for_dedup(m.chunk)
        if key not in groups:
            groups[key] = {"chunk": m.chunk, "count": 0, "fields": set(), "examples": []}
        g = groups[key]
        g["count"] += 1
        g["fields"].add(m.field)
        example = f"{m.character} ({m.category}, {m.field})"
        if example not in g["examples"] and len(g["examples"]) < 4:
            g["examples"].append(example)
    return groups


def _cluster(unique_keys: List[str], threshold: float = 0.82) -> List[List[str]]:
    """Group textually-similar miss strings together (likely typo
    variants of the same gap), leaving genuinely distinct ones as
    singleton clusters."""
    clusters: List[List[str]] = []
    for key in unique_keys:
        placed = False
        for cluster in clusters:
            if any(difflib.SequenceMatcher(None, key, other).ratio() >= threshold for other in cluster):
                cluster.append(key)
                placed = True
                break
        if not placed:
            clusters.append([key])
    return clusters


def format_report(misses: List[Miss]) -> str:
    lines: List[str] = []
    if not misses:
        lines.append("No vocabulary gaps found - every tier/speed/AP/durability chunk was recognized.")
        return "\n".join(lines)

    groups = _dedupe(misses)
    lines.append("=" * 60)
    lines.append(f"Vocabulary gap sweep: {len(misses)} total miss(es), {len(groups)} unique string(s)")
    lines.append("=" * 60)

    clusters = _cluster(list(groups.keys()))
    clusters.sort(key=lambda c: sum(groups[k]["count"] for k in c), reverse=True)

    for i, cluster in enumerate(clusters, start=1):
        cluster.sort(key=lambda k: -groups[k]["count"])
        kind = "cluster (likely related/typo variants)" if len(cluster) > 1 else "singleton"
        total = sum(groups[k]["count"] for k in cluster)
        lines.append(f"\n[{i}] {kind} - {total} occurrence(s):")
        for key in cluster:
            g = groups[key]
            fields = ",".join(sorted(g["fields"]))
            lines.append(f"    \"{g['chunk']}\"  x{g['count']}  [{fields}]")
            for ex in g["examples"]:
                lines.append(f"        - {ex}")

    return "\n".join(lines)


def main() -> int:
    argp = argparse.ArgumentParser(description="Sweep categories for tier/speed vocabulary gaps (no DB writes).")
    argp.add_argument("--categories", nargs="+", default=DEFAULT_CATEGORIES)
    args = argp.parse_args()

    misses = sweep(args.categories)
    report = format_report(misses)
    print("\n" + report)

    REPORT_PATH.write_text(report + "\n", encoding="utf-8")
    print(f"\nReport saved to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
