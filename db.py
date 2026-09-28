"""SQLite storage for scraped/parsed/normalized character data.

One row per character (keyed by its canonical wiki URL). Raw
(CharacterStats) and normalized (NormalizedStats) results are stored as
JSON blobs rather than individual columns - Phase 1/2's schemas are
still evolving, and a blob means adding a new field there never
requires a migration here.

The two blobs are stored zlib-compressed (declared type ZJSON): it took
the file from 44 MB to a third of that, and it's committed to git, which
refuses files over 100 MB. Reads don't notice - connect() turns a ZJSON
column back into the JSON text it was, so `json.loads(row["raw_json"])`
works as before. upsert_character() is the only writer.

Known simplification: `category` holds whichever category name a
character was most recently scraped under, not a full many-to-many
membership list. A character scraped via two different category batch
runs will just have its `category` column overwritten by the second
run. Good enough for "pull all characters from series X"; a join table
would be needed if a character's full category membership ever matters.
"""

import json
import sqlite3
import zlib
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
    raw_json ZJSON NOT NULL,
    normalized_json ZJSON NOT NULL,
    image_url TEXT,
    subseries TEXT
);

CREATE INDEX IF NOT EXISTS idx_characters_name ON characters(name);
CREATE INDEX IF NOT EXISTS idx_characters_category ON characters(category);
"""


def _pack(obj: Any) -> bytes:
    return zlib.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9)


def _unpack(value: bytes) -> str:
    # zlib output starts with 0x78 ("x"); JSON text with "{" - so a copy
    # stored before compression still reads fine.
    return zlib.decompress(value).decode("utf-8") if value[:1] == b"x" else value.decode("utf-8")


sqlite3.register_converter("ZJSON", _unpack)


@contextmanager
def connect(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
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
        # A big franchise's part (DC: "Comics", "Arkham", "DCEU"...), shown as
        # a second row of filters under the series. NULL for everyone else.
        if "subseries" not in columns:
            conn.execute("ALTER TABLE characters ADD COLUMN subseries TEXT")
        _compress_storage(conn)


def _compress_storage(conn: sqlite3.Connection) -> bool:
    """A copy made before compression: rebuild the table with ZJSON
    columns (ids kept). True if it did; run compact() after to shrink
    the file itself."""
    types = {r["name"]: (r["type"] or "").upper() for r in conn.execute("PRAGMA table_info(characters)")}
    if types.get("raw_json") == "ZJSON":
        return False
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(characters)")]
    conn.execute(_SCHEMA.split(";")[0].replace("IF NOT EXISTS characters", "characters_packed"))
    rows = conn.execute(f"SELECT {', '.join(columns)} FROM characters").fetchall()
    packed = [tuple(zlib.compress(row[c].encode("utf-8"), 9) if c in ("raw_json", "normalized_json") else row[c]
                    for c in columns) for row in rows]
    conn.executemany(f"INSERT INTO characters_packed ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                     packed)
    # Ids of deleted characters are never handed out again (community data
    # may still name them): keep the counter where it was.
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'characters'").fetchone()
    conn.execute("DROP TABLE characters")
    conn.execute("ALTER TABLE characters_packed RENAME TO characters")
    if seq:
        conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'characters'", (seq[0],))
    conn.executescript(_SCHEMA.split(";", 1)[1])  # the indexes
    return True


def compact(db_path: Path = DB_PATH) -> None:
    """Gives the space freed (e.g. by _compress_storage) back to the disk."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


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
    subseries: Optional[str] = None,
) -> None:
    """Insert or refresh a character. A None subseries keeps the stored one,
    so re-parsing a page doesn't forget which part of its series it's in."""
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO characters (name, source_url, category, last_scraped_at, raw_json, normalized_json, image_url, subseries)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_url) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                last_scraped_at = excluded.last_scraped_at,
                raw_json = excluded.raw_json,
                normalized_json = excluded.normalized_json,
                image_url = excluded.image_url,
                subseries = COALESCE(excluded.subseries, characters.subseries)
            """,
            (
                name,
                source_url,
                category,
                now,
                _pack(raw),
                _pack(normalized),
                raw.get("image_url"),
                subseries,
            ),
        )


def set_series(source_url: str, category: str, subseries: Optional[str], db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE characters SET category = ?, subseries = ? WHERE source_url = ?",
                     (category, subseries, source_url))


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
