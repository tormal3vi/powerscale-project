"""SQLite storage for scraped/parsed/normalized character data.

One row per character (keyed by its canonical wiki URL). Raw
(CharacterStats) and normalized (NormalizedStats) results are stored as
JSON blobs rather than individual columns - Phase 1/2's schemas are
still evolving, and a blob means adding a new field there never
requires a migration here.

Known simplification: `category` holds whichever category name a
character was most recently scraped under, not a full many-to-many
membership list. A character scraped via two different category batch
runs will just have its `category` column overwritten by the second
run. Good enough for "pull all characters from series X"; a join table
would be needed if a character's full category membership ever matters.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).parent / "powerscale.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_url TEXT NOT NULL UNIQUE,
    category TEXT,
    last_scraped_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    image_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_characters_name ON characters(name);
CREATE INDEX IF NOT EXISTS idx_characters_category ON characters(category);
"""


@contextmanager
def connect(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # image_url came later: a copy made before it gets the column here
        # (CREATE TABLE IF NOT EXISTS never adds columns). It duplicates
        # raw_json's image_url so the character list needn't parse raw_json.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(characters)")}
        if "image_url" not in columns:
            conn.execute("ALTER TABLE characters ADD COLUMN image_url TEXT")


def get_character(source_url: str, db_path: Path = DB_PATH) -> Optional[sqlite3.Row]:
    with connect(db_path) as conn:
        cur = conn.execute("SELECT * FROM characters WHERE source_url = ?", (source_url,))
        return cur.fetchone()


def is_fresh(source_url: str, max_age_days: int, db_path: Path = DB_PATH) -> bool:
    """True if source_url is in the DB and was scraped within the last
    max_age_days. False (never crashes) if it's missing or the stored
    timestamp can't be parsed."""
    row = get_character(source_url, db_path)
    if row is None:
        return False
    try:
        last_scraped = datetime.fromisoformat(row["last_scraped_at"])
    except ValueError:
        return False
    return datetime.now(timezone.utc) - last_scraped < timedelta(days=max_age_days)


def upsert_character(
    name: str,
    source_url: str,
    category: str,
    raw: Dict[str, Any],
    normalized: Dict[str, Any],
    db_path: Path = DB_PATH,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO characters (name, source_url, category, last_scraped_at, raw_json, normalized_json, image_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_url) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                last_scraped_at = excluded.last_scraped_at,
                raw_json = excluded.raw_json,
                normalized_json = excluded.normalized_json,
                image_url = excluded.image_url
            """,
            (
                name,
                source_url,
                category,
                now,
                json.dumps(raw, ensure_ascii=False),
                json.dumps(normalized, ensure_ascii=False),
                raw.get("image_url"),
            ),
        )


def get_characters_by_category(category: str, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    with connect(db_path) as conn:
        cur = conn.execute("SELECT * FROM characters WHERE category = ? ORDER BY name", (category,))
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def count_characters(db_path: Path = DB_PATH) -> int:
    with connect(db_path) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM characters")
        return cur.fetchone()[0]


def get_all_characters(db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    """Lightweight listing (no JSON blobs) for populating a selector."""
    with connect(db_path) as conn:
        cur = conn.execute(
            "SELECT id, name, category, source_url, last_scraped_at FROM characters ORDER BY name"
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def get_form_counts(db_path: Path = DB_PATH) -> Dict[int, int]:
    """id -> number of forms (from normalized_json's "forms" list, always
    >= 1). Used to badge multi-form characters in a selector without
    every caller needing to load and parse the full JSON blob per row."""
    with connect(db_path) as conn:
        cur = conn.execute("SELECT id, normalized_json FROM characters")
        rows = cur.fetchall()
    counts: Dict[int, int] = {}
    for row in rows:
        try:
            normalized = json.loads(row["normalized_json"])
            counts[row["id"]] = len(normalized.get("forms") or []) or 1
        except (ValueError, TypeError):
            counts[row["id"]] = 1
    return counts


def find_characters_by_name(name: str, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    """Case-insensitive exact-name lookup (lightweight, no JSON blobs) -
    may return more than one row, since character names collide across
    distinct source pages (e.g. multiple "Son Goku" variants, "Paragus
    (Toei)" vs "Paragus (Dragon Ball Super)" both display as "Paragus").
    Callers that need exactly one character should disambiguate by id."""
    with connect(db_path) as conn:
        cur = conn.execute(
            "SELECT id, name, category, source_url, last_scraped_at FROM characters "
            "WHERE name = ? COLLATE NOCASE ORDER BY id",
            (name,),
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def get_character_by_id(char_id: int, db_path: Path = DB_PATH) -> Optional[Dict[str, Any]]:
    with connect(db_path) as conn:
        cur = conn.execute("SELECT * FROM characters WHERE id = ?", (char_id,))
        row = cur.fetchone()
    return dict(row) if row else None
