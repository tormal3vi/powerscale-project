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
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import calculator
import db
import normalizer
import parser as parser_module
import scraper
from backend import characters, community, community_api
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
app.include_router(community_api.router)
db.init_db()  # adds columns newer code expects to an older powerscale.db
community.init()


@app.middleware("http")
async def _revalidate_frontend_files(request, call_next):
    # Without this, browsers may reuse a cached page after a deploy while
    # fetching the NEW scripts (or vice versa) - e.g. an old compare.html
    # without nav.js next to a new compare.js that needs it, which breaks
    # the page outright. "no-cache" still caches, but checks the ETag
    # first, so an unchanged file is just a tiny 304.
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


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


def _form_out(raw_form: dict, normalized_form: dict, use_form_images: bool = True) -> FormOut:
    raw_stats = raw_form.get("stats") or {}
    return FormOut(
        name=normalized_form.get("name") or raw_form.get("name") or "Base",
        is_omnipresent=bool(normalized_form.get("is_omnipresent")),
        image_url=raw_form.get("image_url") if use_form_images else None,
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

_list_cache: dict = {}  # {"key": replaced-picture versions, "body": JSON bytes}; see list_characters


def _sort_key(name: str) -> str:
    """A-Z the way people read it: accents folded ("Ōnoki" under O) and
    leading quotes/punctuation ignored - One Piece's '"Don" Sai' and
    '"Sir" Crocodile' otherwise sorted ahead of every A."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"^[^0-9a-z]+", "", folded) or folded


@app.get("/api/characters", response_model=CharacterListOut)
def list_characters(q: Optional[str] = None, category: Optional[str] = None):
    sql = "SELECT id, name, source_url, category, normalized_json, image_url FROM characters"
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

    replaced = community.character_image_versions()
    # The full list (what every page asks for) is built once and kept as
    # ready-to-send JSON until the characters or replaced pictures change -
    # building it took ~1-2s on Render's free-tier CPU.
    unfiltered = not q and not category
    cache_key = tuple(sorted((cid, at.timestamp()) for cid, at in replaced.items()))
    if unfiltered and _list_cache.get("key") == cache_key and "body" in _list_cache:
        return Response(content=_list_cache["body"], media_type="application/json")

    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    colliding = characters.all_collisions()

    out = []
    for row in rows:
        normalized = json.loads(row["normalized_json"])
        forms = normalized.get("forms") or []
        tier_label, tier_score, scorable = _card_info(normalized)
        out.append(CharacterSummaryOut(
            id=row["id"],
            name=characters.display_name(row["name"], row["source_url"], colliding),
            category=row["category"] or "Uncategorized",
            tier_label=tier_label,
            tier_score=tier_score,
            aliases=row["name"],
            scorable=scorable,
            form_count=len(forms) or 1,
            is_multi_form=len(forms) > 1,
            image_url=community_api.character_image_url(row["id"], replaced.get(row["id"])) or row["image_url"],
        ))
    out.sort(key=lambda c: _sort_key(c.name))
    result = CharacterListOut(total=len(out), characters=out)
    if unfiltered:
        body = result.model_dump_json().encode()
        _list_cache.update(key=cache_key, body=body)
        return Response(content=body, media_type="application/json")
    return result


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

    # An admin's replacement picture stands for every form - it usually
    # exists because the wiki's pictures were wrong or missing.
    replaced_url = community_api.character_image_url(row["id"], community.character_image_versions().get(row["id"]))
    forms = [
        _form_out(rf, nf, use_form_images=replaced_url is None)
        for rf, nf in zip(raw_forms, normalized_forms)
    ]

    colliding = characters.all_collisions()

    return CharacterDetailOut(
        id=row["id"],
        name=characters.display_name(row["name"], row["source_url"], colliding),
        category=row["category"] or "Uncategorized",
        source_url=row["source_url"],
        origin=raw.get("origin"),
        classification=raw.get("classification"),
        powers_and_abilities=raw.get("powers_and_abilities") or [],
        weaknesses=raw.get("weaknesses"),
        image_url=replaced_url or raw.get("image_url"),
        image_replaced=replaced_url is not None,
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

    characters.invalidate()  # names, verdicts and the cached list include the new character
    _list_cache.clear()
    row = db.get_character(source_url)
    normalized = json.loads(row["normalized_json"])
    forms = normalized.get("forms") or []
    colliding = characters.all_collisions()
    return CharacterSummaryOut(
        id=row["id"],
        name=characters.display_name(row["name"], row["source_url"], colliding),
        category=row["category"] or "Uncategorized",
        tier_label=_tier_badge(normalized),
        form_count=len(forms) or 1,
        is_multi_form=len(forms) > 1,
        image_url=stats.image_url,
    )


# --- /api/compare -----------------------------------------------------------

@app.post("/api/compare", response_model=VerdictOut)
def compare(payload: CompareIn):
    try:
        verdict = characters.run_compare(payload.char_a, payload.char_b, payload.form_a, payload.form_b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = _verdict_out(verdict)
    if isinstance(payload.char_a, int) and isinstance(payload.char_b, int):
        out.override = community_api.override_out(payload.char_a, payload.char_b, verdict.form_a, verdict.form_b)
    return out


# --- link previews ------------------------------------------------------------
# Discord/WhatsApp/iMessage build a link's preview card from the page's
# <meta> tags without running any JavaScript, so a matchup/character link
# only previews properly if the server writes its summary into the HTML.

def _preview_image(char_id: int) -> Optional[str]:
    """A square crop of the character's wiki picture for a link preview
    (same CDN crop the pages use - see characterPictureUrl in api.js)."""
    row = db.get_character_by_id(char_id)
    url = row.get("image_url") if row else None
    if not url:
        return None
    path, _, query = url.partition("?")
    return f"{path}/top-crop/width/400/height/400" + (f"?{query}" if query else "")


def _page_with_preview(filename: str, title: Optional[str], description: Optional[str],
                       image: Optional[str] = None) -> HTMLResponse:
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
        if image:
            tags += f'<meta property="og:image" content="{html_escape(image)}">\n'
        html = html.replace("</head>", tags + "</head>", 1)
    return HTMLResponse(html)


def _int_param(value: Optional[str]) -> Optional[int]:
    return int(value) if value and value.isdigit() else None


def _compare_preview(a: Optional[str], b: Optional[str], fa: Optional[str], fb: Optional[str]):
    char_a, char_b = _int_param(a), _int_param(b)
    if char_a is None or char_b is None:
        return None, None, None
    try:
        v = characters.run_compare(char_a, char_b, fa or None, fb or None)
    except ValueError:
        return None, None, None
    title = f"{characters.short_name(v.character_a)} vs {characters.short_name(v.character_b)} — Powerscale"
    ov = community.get_override(char_a, char_b, v.form_a, v.form_b)
    # The preview shows whoever the card says wins; A when it's a toss-up.
    pictured = char_a
    if ov is not None:
        pictured = ov["winner_id"]
    elif v.favored == v.character_b:
        pictured = char_b
    if ov is not None:
        winner = v.character_a if ov["winner_id"] == char_a else v.character_b
        verdict = f"{characters.short_name(winner)} wins — overruled by admins"
    elif v.composite is None:
        verdict = "Not enough data for a verdict"
    elif v.favored:
        verdict = f"{characters.short_name(v.favored)} favored — {v.label}"
        if v.confidence_hint != "n/a":
            verdict += f" ({v.confidence_hint})"
    else:
        verdict = v.label
    return title, f"{verdict}. {v.form_a} vs {v.form_b}.", _preview_image(pictured)


def _character_preview(char_id: Optional[str]):
    cid = _int_param(char_id)
    row = db.get_character_by_id(cid) if cid is not None else None
    if row is None:
        return None, None, None
    with db.connect() as conn:
        name = characters.display_name(row["name"], row["source_url"], characters.all_collisions())
    normalized = json.loads(row["normalized_json"])
    tier = _tier_badge(normalized)
    forms = len(normalized.get("forms") or [])
    desc = f"{row['category']}" + (f" · Tier {tier}" if tier else "") + (f" · {forms} forms" if forms > 1 else "")
    return f"{characters.short_name(name)} — Powerscale", desc, _preview_image(cid)


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
