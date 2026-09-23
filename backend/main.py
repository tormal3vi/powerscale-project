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
import re
import unicodedata
from html import escape as html_escape
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
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
    return _card_info(normalized)[0]


def _card_info(normalized: dict) -> tuple:
    """(tier badge label, tier score, scorable) for the default form."""
    if not normalized.get("forms"):
        return None, None, False
    form = calculator.select_form(normalized)
    tier = form.get("tier") or {}
    label = tier.get("baseline_label")
    scored = sum(
        1 for axis in calculator.AXES
        if (form.get(axis) or {}).get("baseline") is not None or (form.get(axis) or {}).get("peak") is not None
    )
    return (label.upper() if label else None), tier.get("baseline"), scored >= calculator.MIN_AXES_FOR_VERDICT


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


def _name_key(name: str) -> str:
    """A character's primary name for collision purposes: the first alias
    (names list aliases after ',', ';' or '/'), parentheticals dropped,
    whitespace collapsed - so "Frieza/Freeza/Freezer", "Frieza / Freeza /
    Freezer" and plain "Frieza" all count as the same name."""
    first = re.split(r"[,;/]", name, maxsplit=1)[0]
    first = re.sub(r"\([^)]*\)", "", first)
    return " ".join(first.split()).lower()


def _name_words(s: str) -> set:
    folded = "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))
    return set(re.findall(r"[a-z0-9]{2,}", folded))


def _base_name(name: str, source_url: str) -> str:
    """The stored name, unless it shares no word at all with the wiki page
    title (accents ignored) - then the title, minus any "(Series)"
    qualifier. Catches wiki-side mistakes in the "Name:" field itself:
    Land's page literally says "Name: Male", Third Kazekage's says
    "Unknown", Megath's "Varies", Sherry Blendy's "Yuka Suzuki". Only
    ~18 of 1550 characters trip this, and the rest just switch to the
    wiki's own spelling (Nidhogg, Gorgon)."""
    # A '|' in the Name field separates per-FORM names, same convention as
    # every stat field ("Homura Akemi | Same | Homulily | Akuma Homura",
    # "Uub | Majuub") - the first is the character's own name.
    name = re.split(r"\s*\|\s*", name, maxsplit=1)[0]
    if not source_url.startswith("http"):
        return name
    bare = re.sub(r"\s*\([^)]*\)\s*$", "", scraper.page_title(source_url))
    name_words, title_words = _name_words(name), _name_words(bare)
    if name_words and title_words:
        return bare if not (name_words & title_words) else name
    return bare if _name_key(name) != _name_key(bare) else name


def _colliding_names(conn) -> set:
    """Name keys shared by 2+ characters. Distinct wiki pages often carry
    the same "Name:" field - e.g. Ichigo Kurosaki's Pre-Timeskip, Post-
    Timeskip and Live Action pages, or Fairy Tail's X784-X792 vs. X793
    versions - and a couple even carry another character's name by
    mistake on the wiki itself (Sherry Blendy's and Toby Horhorta's pages
    both say "Yuka Suzuki")."""
    counts: Dict[str, int] = {}
    for name, source_url in conn.execute("SELECT name, source_url FROM characters"):
        key = _name_key(_base_name(name, source_url))
        counts[key] = counts.get(key, 0) + 1
    return {k for k, n in counts.items() if n > 1}


def _display_name(name: str, source_url: str, colliding: set) -> str:
    """The base name (see _base_name), unless another character shares it -
    then the full wiki page title, which the wiki guarantees is unique
    (e.g. "Ichigo Kurosaki (Pre-Timeskip)"). Manually entered characters
    have no real page title, so they always keep their name."""
    base = _base_name(name, source_url)
    if _name_key(base) not in colliding or not source_url.startswith("http"):
        return base
    return scraper.page_title(source_url)


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
    sql = "SELECT id, name, source_url, category, normalized_json FROM characters"
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
        colliding = _colliding_names(conn)

    out = []
    for row in rows:
        normalized = json.loads(row["normalized_json"])
        forms = normalized.get("forms") or []
        tier_label, tier_score, scorable = _card_info(normalized)
        out.append(CharacterSummaryOut(
            id=row["id"],
            name=_display_name(row["name"], row["source_url"], colliding),
            category=row["category"] or "Uncategorized",
            tier_label=tier_label,
            tier_score=tier_score,
            aliases=row["name"],
            scorable=scorable,
            form_count=len(forms) or 1,
            is_multi_form=len(forms) > 1,
        ))
    out.sort(key=lambda c: c.name.lower())
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

    with db.connect() as conn:
        colliding = _colliding_names(conn)

    return CharacterDetailOut(
        id=row["id"],
        name=_display_name(row["name"], row["source_url"], colliding),
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
    with db.connect() as conn:
        colliding = _colliding_names(conn)
    return CharacterSummaryOut(
        id=row["id"],
        name=_display_name(row["name"], row["source_url"], colliding),
        category=row["category"] or "Uncategorized",
        tier_label=_tier_badge(normalized),
        form_count=len(forms) or 1,
        is_multi_form=len(forms) > 1,
    )


# --- /api/compare -----------------------------------------------------------

def _run_compare(char_a, char_b, form_a=None, form_b=None) -> "calculator.Verdict":
    with db.connect() as conn:
        colliding = _colliding_names(conn)

    def name_for(ref):
        if not isinstance(ref, int):
            return None
        row = db.get_character_by_id(ref)
        return _display_name(row["name"], row["source_url"], colliding) if row else None

    return calculator.compare_characters(
        char_a, char_b, form_a, form_b,
        name_a=name_for(char_a), name_b=name_for(char_b),
    )


@app.post("/api/compare", response_model=VerdictOut)
def compare(payload: CompareIn):
    try:
        verdict = _run_compare(payload.char_a, payload.char_b, payload.form_a, payload.form_b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _verdict_out(verdict)


# --- link previews ------------------------------------------------------------
# Discord/WhatsApp/iMessage build a link's preview card from the page's
# <meta> tags without running any JavaScript, so a matchup/character link
# only previews properly if the server writes its summary into the HTML.

def _short(name: str) -> str:
    return re.split(r"[;,]", name, maxsplit=1)[0].strip()


def _page_with_preview(filename: str, title: Optional[str], description: Optional[str]) -> HTMLResponse:
    html = (FRONTEND_DIR / filename).read_text(encoding="utf-8")
    if title:
        t, d = html_escape(title), html_escape(description or "")
        tags = (
            f'<meta property="og:title" content="{t}">\n'
            f'<meta property="og:description" content="{d}">\n'
            f'<meta property="og:type" content="website">\n'
            f'<meta property="og:site_name" content="Powerscale">\n'
            f'<meta name="twitter:card" content="summary">\n'
            f'<meta name="description" content="{d}">\n'
        )
        html = html.replace("</head>", tags + "</head>", 1)
    return HTMLResponse(html)


def _int_param(value: Optional[str]) -> Optional[int]:
    return int(value) if value and value.isdigit() else None


def _compare_preview(a: Optional[str], b: Optional[str], fa: Optional[str], fb: Optional[str]):
    char_a, char_b = _int_param(a), _int_param(b)
    if char_a is None or char_b is None:
        return None, None
    try:
        v = _run_compare(char_a, char_b, fa or None, fb or None)
    except ValueError:
        return None, None
    title = f"{_short(v.character_a)} vs {_short(v.character_b)} — Powerscale"
    if v.composite is None:
        verdict = "Not enough data for a verdict"
    elif v.favored:
        verdict = f"{_short(v.favored)} favored — {v.label}"
        if v.confidence_hint != "n/a":
            verdict += f" ({v.confidence_hint})"
    else:
        verdict = v.label
    return title, f"{verdict}. {v.form_a} vs {v.form_b}."


def _character_preview(char_id: Optional[str]):
    cid = _int_param(char_id)
    row = db.get_character_by_id(cid) if cid is not None else None
    if row is None:
        return None, None
    with db.connect() as conn:
        name = _display_name(row["name"], row["source_url"], _colliding_names(conn))
    normalized = json.loads(row["normalized_json"])
    tier = _tier_badge(normalized)
    forms = len(normalized.get("forms") or [])
    desc = f"{row['category']}" + (f" · Tier {tier}" if tier else "") + (f" · {forms} forms" if forms > 1 else "")
    return f"{_short(name)} — Powerscale", desc


# --- static frontend (mounted once frontend/ exists) -------------------
# StaticFiles(html=True) serves index.html for a directory request, but
# frontend/ has no index.html (browse.html is the real landing page) - so
# "/" itself 404s unless something redirects it first. Registered before
# the mount so this exact-path route wins over the mount's catch-all.

if FRONTEND_DIR.exists():
    @app.get("/", include_in_schema=False)
    def _root():
        return RedirectResponse(url="/browse.html")

    @app.get("/compare.html", include_in_schema=False)
    def _compare_page(a: Optional[str] = None, b: Optional[str] = None,
                      fa: Optional[str] = None, fb: Optional[str] = None):
        return _page_with_preview("compare.html", *_compare_preview(a, b, fa, fb))

    @app.get("/character.html", include_in_schema=False)
    def _character_page(id: Optional[str] = None):
        return _page_with_preview("character.html", *_character_preview(id))

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
