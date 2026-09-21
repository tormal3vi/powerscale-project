"""Thin HTTP layer in front of db.py/normalizer.py/calculator.py - no
business logic lives here that isn't already in those modules. Backs
the frontend/ static site (mounted below, once it exists) and is
independently browsable via FastAPI's auto-generated /docs.

Run with:
    ./venv/bin/uvicorn backend.main:app --reload
Then open http://localhost:8000/docs to exercise every endpoint by
hand, or http://localhost:8000/ once frontend/ exists.
"""

import json
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

import calculator
import db
import normalizer
import parser as parser_module
import scraper
from backend.schemas import (
    AbilityFlagOut,
    AxisComparisonOut,
    CategoryOut,
    CharacterDetailOut,
    CharacterListOut,
    CharacterSummaryOut,
    CompareIn,
    FetchCharacterIn,
    FormOut,
    NormalizedRangeOut,
    VerdictOut,
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="Powerscale API")


# --- helpers: dict/dataclass -> response model -----------------------------

def _range_out(range_dict: Optional[dict]) -> NormalizedRangeOut:
    return NormalizedRangeOut(**(range_dict or {}))


def _tier_badge(normalized: dict) -> Optional[str]:
    """The badge shown on a character card - the Tier of whichever form
    calculator.select_form() would pick by default (highest tier.baseline),
    same "no cleverness beyond that" default used everywhere else."""
    forms = normalized.get("forms") or []
    if not forms:
        return None
    form = calculator.select_form(normalized)
    label = (form.get("tier") or {}).get("baseline_label")
    return label.upper() if label else None


def _form_out(raw_form: dict, normalized_form: dict) -> FormOut:
    raw_stats = raw_form.get("stats") or {}
    return FormOut(
        name=normalized_form.get("name") or raw_form.get("name") or "Base",
        is_omnipresent=bool(normalized_form.get("is_omnipresent")),
        tier_raw=raw_form.get("tier"),
        tier=_range_out(normalized_form.get("tier")),
        attack_potency_raw=raw_stats.get("attack_potency"),
        attack_potency=_range_out(normalized_form.get("attack_potency")),
        speed_raw=raw_stats.get("speed"),
        speed=_range_out(normalized_form.get("speed")),
        durability_raw=raw_stats.get("durability"),
        durability=_range_out(normalized_form.get("durability")),
        lifting_strength_raw=raw_stats.get("lifting_strength"),
        striking_strength_raw=raw_stats.get("striking_strength"),
        stamina_raw=raw_stats.get("stamina"),
        range_raw=raw_stats.get("range"),
    )


def _verdict_out(v: "calculator.Verdict") -> VerdictOut:
    return VerdictOut(
        character_a=v.character_a,
        character_b=v.character_b,
        form_a=v.form_a,
        form_b=v.form_b,
        axis_comparisons=[
            AxisComparisonOut(
                axis=c.axis, a_value=c.a_value, a_source=c.a_source,
                b_value=c.b_value, b_source=c.b_source, delta=c.delta,
                advantage=c.advantage, weight_used=c.weight_used,
            )
            for c in v.axis_comparisons
        ],
        axes_used=v.axes_used,
        composite=v.composite,
        label=v.label,
        confidence_hint=v.confidence_hint,
        favored=v.favored,
        partial_data=v.partial_data,
        ability_flags=[AbilityFlagOut(tag=f.tag, characters=f.characters) for f in v.ability_flags],
        notes=v.notes,
    )


# --- /api/categories ---------------------------------------------------

@app.get("/api/categories", response_model=List[CategoryOut])
def list_categories():
    with db.connect() as conn:
        cur = conn.execute(
            "SELECT category, COUNT(*) AS n FROM characters GROUP BY category ORDER BY category"
        )
        rows = cur.fetchall()
    return [CategoryOut(name=r["category"] or "Uncategorized", count=r["n"]) for r in rows]


# --- /api/characters (list/search) ------------------------------------------

@app.get("/api/characters", response_model=CharacterListOut)
def list_characters(q: Optional[str] = None, category: Optional[str] = None):
    sql = "SELECT id, name, category, normalized_json FROM characters"
    clauses, params = [], []
    if q:
        clauses.append("name LIKE ?")
        params.append(f"%{q}%")
    if category:
        clauses.append("category = ?")
        params.append(category)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY name"

    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    out = []
    for row in rows:
        normalized = json.loads(row["normalized_json"])
        forms = normalized.get("forms") or []
        out.append(CharacterSummaryOut(
            id=row["id"],
            name=row["name"],
            category=row["category"] or "Uncategorized",
            tier_label=_tier_badge(normalized),
            form_count=len(forms) or 1,
            is_multi_form=len(forms) > 1,
        ))
    return CharacterListOut(total=len(out), characters=out)


# --- /api/characters/{id} (detail) -----------------------------------------

@app.get("/api/characters/{char_id}", response_model=CharacterDetailOut)
def get_character(char_id: int):
    row = db.get_character_by_id(char_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No character with id {char_id}")

    raw = json.loads(row["raw_json"])
    normalized = json.loads(row["normalized_json"])
    raw_forms = raw.get("forms") or []
    normalized_forms = normalized.get("forms") or []

    forms = [
        _form_out(rf, nf)
        for rf, nf in zip(raw_forms, normalized_forms)
    ]

    return CharacterDetailOut(
        id=row["id"],
        name=row["name"],
        category=row["category"] or "Uncategorized",
        source_url=row["source_url"],
        origin=raw.get("origin"),
        classification=raw.get("classification"),
        powers_and_abilities=raw.get("powers_and_abilities") or [],
        weaknesses=raw.get("weaknesses"),
        forms=forms,
    )


# --- /api/characters/fetch (add a character, live) --------------------------

@app.post("/api/characters/fetch", response_model=CharacterSummaryOut)
def fetch_character(payload: FetchCharacterIn):
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query must not be empty")

    try:
        html = scraper.fetch_page(query)
    except scraper.FetchError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't fetch {query!r}: {exc}") from exc

    source_url = scraper.page_url(query)
    stats = parser_module.parse_character(html, source=source_url)
    normalized_stats = normalizer.normalize_character(stats)
    category = stats.origin or "Uncategorized"

    db.upsert_character(
        name=stats.name or query,
        source_url=source_url,
        category=category,
        raw=stats.to_dict(),
        normalized=normalized_stats.to_dict(),
    )

    row = db.get_character(source_url)
    normalized = json.loads(row["normalized_json"])
    forms = normalized.get("forms") or []
    return CharacterSummaryOut(
        id=row["id"],
        name=row["name"],
        category=row["category"] or "Uncategorized",
        tier_label=_tier_badge(normalized),
        form_count=len(forms) or 1,
        is_multi_form=len(forms) > 1,
    )


# --- /api/compare -----------------------------------------------------------

@app.post("/api/compare", response_model=VerdictOut)
def compare(payload: CompareIn):
    try:
        verdict = calculator.compare_characters(
            payload.char_a, payload.char_b, payload.form_a, payload.form_b,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _verdict_out(verdict)


# --- static frontend (mounted once frontend/ exists) -------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
