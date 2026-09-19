"""Adds a manually-authored, non-wiki character (e.g. original fiction)
to the DB from a filled-in YAML file - see characters/_template.yaml.

Bypasses scraper.py/parser.py entirely (there's no page to fetch or
parse), but builds the exact same CharacterStats/CharacterForm objects
parser.py would, so the character flows through normalizer.py and
db.py completely unchanged and stays comparable to every scraped
character in the DB - same ladder, same calculator.py, same UI.

There's no real wiki page for `source_url` (which db.py's schema
requires to be non-null and unique), so one is synthesized as
"manual://<category-slug>/<name-slug>" - clearly non-wiki at a glance,
and unique per (category, name) pair the same way a real URL would be.

Usage:
    ./venv/bin/python3 manual_entry.py characters/your_character.yaml
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

import db
import normalizer
from parser import CharacterForm, CharacterStats, DEFAULT_FORM_NAME, StatBlock

STAT_BLOCK_FIELDS = [
    "attack_potency", "speed", "lifting_strength", "striking_strength",
    "durability", "stamina", "range",
]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "character"


def _source_url_for(category: str, name: str) -> str:
    return f"manual://{_slugify(category)}/{_slugify(name)}"


def _build_form(entry: Dict[str, Any]) -> CharacterForm:
    name = entry.get("name")
    if not name:
        raise ValueError("each entry under 'forms:' needs its own 'name'")
    stats = StatBlock(**{field: entry.get(field) for field in STAT_BLOCK_FIELDS})
    return CharacterForm(name=name, tier=entry.get("tier"), stats=stats)


def build_character_stats(data: Dict[str, Any]) -> CharacterStats:
    """Builds a CharacterStats (with `forms` always populated, matching
    parser.parse_character's own guarantee) from a manual-entry dict -
    same shape as a filled-in YAML template, loaded with yaml.safe_load."""
    name = data.get("name")
    if not name:
        raise ValueError("missing required field: name")
    category = data.get("category")
    if not category:
        raise ValueError("missing required field: category")

    stats = CharacterStats(
        name=name,
        tier=data.get("tier"),
        origin=data.get("origin"),
        gender=data.get("gender"),
        age=data.get("age"),
        classification=data.get("classification"),
        powers_and_abilities=list(data.get("powers_and_abilities") or []),
        attack_potency=data.get("attack_potency"),
        speed=data.get("speed"),
        lifting_strength=data.get("lifting_strength"),
        striking_strength=data.get("striking_strength"),
        durability=data.get("durability"),
        stamina=data.get("stamina"),
        range=data.get("range"),
        standard_equipment=data.get("standard_equipment"),
        intelligence=data.get("intelligence"),
        weaknesses=data.get("weaknesses"),
        source=_source_url_for(category, name),
    )

    forms_data = data.get("forms")
    if forms_data:
        stats.forms = [_build_form(entry) for entry in forms_data]
    else:
        # Same synthetic single-form wrapping parser.py's _default_form
        # does for an ordinary (non-tabbed) page - keeps `forms` always
        # non-empty, so normalizer.py/app.py/calculator.py never need to
        # special-case a manually-entered character.
        stats.forms = [CharacterForm(
            name=DEFAULT_FORM_NAME,
            tier=stats.tier,
            stats=StatBlock(**{field: getattr(stats, field) for field in STAT_BLOCK_FIELDS}),
        )]
    return stats


def add_character(yaml_path: Path, db_path: Path = db.DB_PATH) -> CharacterStats:
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not data:
        raise ValueError(f"{yaml_path} is empty or not valid YAML")

    stats = build_character_stats(data)
    normalized = normalizer.normalize_character(stats)

    db.init_db(db_path)
    db.upsert_character(
        name=stats.name,
        source_url=stats.source,
        category=data["category"],
        raw=stats.to_dict(),
        normalized=normalized.to_dict(),
        db_path=db_path,
    )
    return stats


def main() -> int:
    argp = argparse.ArgumentParser(description="Add a manually-authored (non-wiki) character to the DB.")
    argp.add_argument("yaml_path", help="Path to a filled-in character YAML file (see characters/_template.yaml)")
    args = argp.parse_args()

    path = Path(args.yaml_path)
    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        return 1

    try:
        stats = add_character(path)
    except (ValueError, yaml.YAMLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Added {stats.name!r} (source_url={stats.source!r}).")
    print(f"Forms: {len(stats.forms)}")
    for f in stats.forms:
        print(f"  - {f.name}: tier={f.tier!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
