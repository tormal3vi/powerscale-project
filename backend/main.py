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
import os
import re
from urllib.parse import urlencode
import unicodedata
import urllib.request
from collections import OrderedDict
from html import escape as html_escape
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import calculator
import db
import normalizer
import parser as parser_module
import scraper
from backend import (
    characters, community, community_api, daily, discord_bot, discord_webhooks, duels, duels_api, form_renames,
    linked_roles, profiles_api, tickets_api,
)
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
    SubseriesOut,
    VerdictOut,
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="Powerscale API")
app.include_router(community_api.router)
app.include_router(duels_api.router)
app.include_router(tickets_api.router)
app.include_router(profiles_api.router)
app.include_router(discord_bot.router)
app.include_router(linked_roles.router)
db.init_db()  # adds columns newer code expects to an older powerscale.db
community.init()
form_renames.apply()  # moves overrules etc. onto renamed forms, once per batch
discord_bot.start()  # registers the Discord commands, if Discord is set up
linked_roles.start()  # Discord Linked Roles, if DISCORD_CLIENT_SECRET is set
discord_webhooks.start_schedule()  # weekly leaderboard and matchup of the day, if their channels are set up
discord_webhooks.start_updates()  # this version's update notes, if not announced yet


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
    """(tier badge label, tier score, scorable, tie-breakers) for the default
    form. Tie-breakers, in order: its AP, Durability and Speed; then the top
    of its ranges ("up to ..."); then the character's strongest form."""
    if not normalized.get("forms"):
        return None, None, False, []
    form = calculator.select_form(normalized)
    tier = form.get("tier") or {}
    label = tier.get("baseline_label")
    scored = sum(
        1 for axis in calculator.AXES
        if (form.get(axis) or {}).get("baseline") is not None or (form.get(axis) or {}).get("peak") is not None
    )
    axes = ("tier", "attack_potency", "durability", "speed")
    tiebreak = [(form.get(axis) or {}).get("baseline") for axis in axes[1:]]
    tiebreak += [(form.get(axis) or {}).get("peak") for axis in axes]
    for axis in axes:
        peaks = [(f.get(axis) or {}).get("peak") for f in normalized["forms"]]
        tiebreak.append(max((p for p in peaks if p is not None), default=None))
    return (label.upper() if label else None), tier.get("baseline"), scored >= calculator.MIN_AXES_FOR_VERDICT, tiebreak


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

# Parts that read as a sequence rather than by size.
SUBSERIES_ORDER = {"Final Fantasy": ["I–VI", "VII", "VIII–X", "XI–XV", "Tactics", "Spin-offs & more"]}


@app.get("/api/categories", response_model=List[CategoryOut])
def list_categories():
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT category, subseries, COUNT(*) AS n FROM characters GROUP BY category, subseries"
        ).fetchall()
    out: Dict[str, CategoryOut] = {}
    for r in rows:
        name = r["category"] or "Uncategorized"
        cat = out.setdefault(name, CategoryOut(name=name, count=0))
        cat.count += r["n"]
        if r["subseries"]:
            cat.subseries.append(SubseriesOut(name=r["subseries"], count=r["n"]))
    for cat in out.values():
        order = SUBSERIES_ORDER.get(cat.name, [])
        # in order where one is set, else biggest first: DC's Comics, then Arrowverse...
        cat.subseries.sort(key=lambda sub: (order.index(sub.name) if sub.name in order else len(order), -sub.count))
    return sorted(out.values(), key=lambda c: c.name)


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
    sql = "SELECT id, name, source_url, category, subseries, normalized_json, image_url FROM characters"
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
        tier_label, tier_score, scorable, tiebreak = _card_info(normalized)
        out.append(CharacterSummaryOut(
            id=row["id"],
            name=characters.display_name(row["name"], row["source_url"], colliding),
            category=row["category"] or "Uncategorized",
            subseries=row["subseries"],
            tier_label=tier_label,
            tier_score=tier_score,
            tiebreak=tiebreak,
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
        subseries=row["subseries"],
        source_url=row["source_url"],
        origin=raw.get("origin"),
        classification=raw.get("classification"),
        powers_and_abilities=raw.get("powers_and_abilities") or [],
        weaknesses=raw.get("weaknesses"),
        image_url=replaced_url or raw.get("image_url"),
        image_replaced=replaced_url is not None,
        forms=forms,
    )


# --- /api/characters/{id}/picture (for drawing on a canvas) -------------------
# The wiki's image server doesn't let other sites read its pictures from a
# script, so a canvas that draws one can't be saved (the share image on
# Compare). This serves the same picture from here. It takes a character
# and form - never a URL - so it can't be pointed anywhere else.

_picture_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
_PICTURE_CACHE_SIZE = 200
_PICTURE_MAX_BYTES = 6 * 1024 * 1024


def _square_frame(data: bytes, px: int) -> bytes:
    from io import BytesIO
    from PIL import Image, ImageOps
    try:
        with Image.open(BytesIO(data)) as img:
            if img.width * img.height > 40_000_000:
                raise ValueError("too large")
            img.seek(0)
            square = ImageOps.fit(img.convert("RGBA"), (px, px), Image.Resampling.LANCZOS, centering=(0.5, 0))
            out = BytesIO()
            square.save(out, "WEBP", quality=88)
            return out.getvalue()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="The wiki's picture couldn't be read") from exc


@app.get("/api/characters/{char_id}/picture")
def character_picture(char_id: int, form: Optional[str] = None, px: int = 400):
    px = px if px in (200, 400, 800) else 400
    replaced = community.character_image_versions().get(char_id)
    if replaced:  # an admin's replacement: already served from here
        return RedirectResponse(community_api.character_image_url(char_id, replaced))
    url = characters.form_picture(char_id, form)
    if not url or not url.startswith("https://static.wikia.nocookie.net/vsbattles/"):
        raise HTTPException(status_code=404, detail="No picture")
    key = (url, px)
    if key not in _picture_cache:
        crop = characters.wiki_square(url, px)
        req = urllib.request.Request(crop, headers={"User-Agent": scraper.USER_AGENT,
                                                    "Accept": "image/webp,image/png,image/*"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                kind = resp.headers.get("Content-Type", "")
                body = resp.read(_PICTURE_MAX_BYTES + 1)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail="The wiki's picture didn't load") from exc
        if not kind.startswith("image/") or len(body) > _PICTURE_MAX_BYTES:
            raise HTTPException(status_code=502, detail="The wiki sent something other than a picture")
        if crop == url:  # a GIF, whole: square its first frame here, as the CDN would
            body, kind = _square_frame(body, px), "image/webp"
        _picture_cache[key] = (body, kind)
        while len(_picture_cache) > _PICTURE_CACHE_SIZE:
            _picture_cache.popitem(last=False)
    else:
        _picture_cache.move_to_end(key)
    body, kind = _picture_cache[key]
    return Response(body, media_type=kind, headers={"Cache-Control": "public, max-age=86400"})


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


@app.get("/api/daily-matchup")
def daily_matchup(response: Response):
    """Today's matchup of the day (see backend/daily.py)."""
    day = daily.today()
    a, b = daily.pick(day)
    response.headers["Cache-Control"] = "public, max-age=300"
    return {"date": day.isoformat(), "a": a, "b": b}


# --- link previews ------------------------------------------------------------
# Discord/WhatsApp/iMessage build a link's preview card from the page's
# <meta> tags without running any JavaScript, so a matchup/character link
# only previews properly if the server writes its summary into the HTML.

def _preview_image(char_id: int, form: Optional[str] = None) -> Optional[str]:
    """A square crop of the character's wiki picture for a link preview
    (same CDN crop the pages use - see characterPictureUrl in api.js):
    that form's picture when one is named, else the character's."""
    if form:
        url = characters.form_picture(char_id, form)
    else:
        row = db.get_character_by_id(char_id)
        url = row.get("image_url") if row else None
    return characters.wiki_square(url, 400) if url else None


# Every page gets a card: its own title and a line about the site, unless
# a page below writes a more specific one (a matchup, a character, a duel,
# a profile, a tournament).
SITE_DESCRIPTION = ("Who would win? Compare VS Battles Wiki characters' tiers and stats, "
                    "run tournaments, and duel other fans on the verdicts.")
PAGE_TITLES = {
    "browse.html": "Powerscale — who would win?",
    "compare.html": "Compare — Powerscale",
    "character.html": "Character — Powerscale",
    "tournament.html": "Tournament — Powerscale",
    "duels.html": "Duels — Powerscale",
    "board.html": "Board — Powerscale",
    "user.html": "Profile — Powerscale",
    "login.html": "Log in — Powerscale",
    "profile.html": "Your profile — Powerscale",
    "tickets.html": "Tickets — Powerscale",
    "faq.html": "FAQ — Powerscale",
    "gauntlet.html": "Gauntlet — Powerscale",
    "terms.html": "Terms of Service — Powerscale",
    "privacy.html": "Privacy Policy — Powerscale",
}
SITE_IMAGE = "/apple-touch-icon.png?v=2"  # bump v when the logo changes: apps cache by URL


def _base_url(request: Request) -> str:
    """This site's origin as the visitor sees it (Render's proxy says
    https in X-Forwarded-Proto) - link previews need absolute image URLs."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


# The query parameters that make each page what it is: kept in canonical
# links, so ?d=123 or a tracker's tag doesn't look like another page.
_CANONICAL_PARAMS = {"compare.html": ("a", "b", "fa", "fb"), "character.html": ("id",), "user.html": ("u",),
                     "duels.html": ("game",), "tournament.html": ("ids",),
                     "gauntlet.html": ("char", "source", "series", "opponents", "seed")}


def _structured_data(base: str, about: Optional[list], page_name: str, url: str) -> str:
    """JSON-LD: what the site is (a fan site about fictional characters -
    not Dell's PowerScale storage or ABB's power supplies), and what a
    page is about when it's one or two characters."""
    graph = [{"@type": "WebSite", "@id": f"{base}/#site", "name": "Powerscale",
              "alternateName": ["Powerscale.online", "Powerscale: who would win"], "url": f"{base}/",
              "description": SITE_DESCRIPTION,
              "about": {"@type": "Thing", "name": "Fictional character power scaling (VS Battles)"}}]
    if about:
        graph.append({"@type": "WebPage", "name": page_name, "url": url, "isPartOf": {"@id": f"{base}/#site"},
                      "about": [{"@type": "Thing", "name": name, "description": desc} for name, desc in about]})
    # "</" can't appear inside a <script>: a name like "</script>" would end it.
    data = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{data}</script>\n'


def _page_with_preview(request: Request, filename: str, title: Optional[str], description: Optional[str],
                       image: Optional[str] = None, about: Optional[list] = None) -> HTMLResponse:
    """`about`: [(character name, description)] for the structured data."""
    html = (FRONTEND_DIR / filename).read_text(encoding="utf-8")
    base = _base_url(request)
    t = html_escape(title or PAGE_TITLES.get(filename, "Powerscale"))
    d = html_escape(description or SITE_DESCRIPTION)
    image = image or SITE_IMAGE
    if image.startswith("/"):
        image = base + image
    url = base + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    keep = [(k, v) for k, v in request.query_params.multi_items() if k in _CANONICAL_PARAMS.get(filename, ())]
    canonical = base + request.url.path + (f"?{urlencode(keep)}" if keep else "")
    if title:  # the page's own <title> too: search results show that, not og:title
        html = re.sub(r"<title>.*?</title>", f"<title>{t}</title>", html, count=1, flags=re.S)
    tags = (
        f'<meta property="og:title" content="{t}">\n'
        f'<meta property="og:description" content="{d}">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:site_name" content="Powerscale">\n'
        f'<meta property="og:url" content="{html_escape(url)}">\n'
        f'<meta property="og:image" content="{html_escape(image)}">\n'
        f'<meta name="twitter:card" content="summary">\n'
        f'<meta name="theme-color" content="#D9A441">\n'
        f'<meta name="description" content="{d}">\n'
        f'<link rel="canonical" href="{html_escape(canonical)}">\n'
    ) + _structured_data(base, about, title or PAGE_TITLES.get(filename, "Powerscale"), canonical)
    invite = _discord_invite()
    if invite:  # nav.js turns it into the "Join our Discord" button
        tags += f'<meta name="discord-invite" content="{html_escape(invite)}">\n'
    return HTMLResponse(html.replace("</head>", tags + "</head>", 1))


def _discord_invite() -> Optional[str]:
    """The server's invite link (DISCORD_INVITE_URL), if it's one."""
    url = os.environ.get("DISCORD_INVITE_URL", "").strip()
    return url if re.fullmatch(r"https://(discord\.gg|discord\.com/invite)/[A-Za-z0-9-]+", url) else None


def _int_param(value: Optional[str]) -> Optional[int]:
    return int(value) if value and value.isdigit() else None


def _compare_preview(a: Optional[str], b: Optional[str], fa: Optional[str], fb: Optional[str]):
    char_a, char_b = _int_param(a), _int_param(b)
    if char_a is None or char_b is None:
        return None, None, None, None, None
    try:
        v = characters.run_compare(char_a, char_b, fa or None, fb or None)
    except ValueError:
        return None, None, None, None
    name_a, name_b = characters.short_name(v.character_a), characters.short_name(v.character_b)
    title = f"{name_a} vs {name_b}: who would win? — Powerscale"
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
    pictured_form = v.form_a if pictured == char_a else v.form_b
    series = {cid: (db.get_character_by_id(cid) or {}).get("category") or "" for cid in (char_a, char_b)}
    about = [(name_a, f"Fictional character from {series[char_a]}"), (name_b, f"Fictional character from {series[char_b]}")]
    desc = (f"{verdict}. {name_a} ({v.form_a}) vs {name_b} ({v.form_b}), compared on tier, attack potency, "
            f"speed and durability from the VS Battles Wiki.")
    return title, desc, _preview_image(pictured, pictured_form), about


def _character_preview(char_id: Optional[str]):
    cid = _int_param(char_id)
    row = db.get_character_by_id(cid) if cid is not None else None
    if row is None:
        return None, None, None, None
    with db.connect() as conn:
        name = characters.display_name(row["name"], row["source_url"], characters.all_collisions())
    normalized = json.loads(row["normalized_json"])
    tier = _tier_badge(normalized)
    forms = len(normalized.get("forms") or [])
    short = characters.short_name(name)
    desc = (f"{short} ({row['category']})" + (f": Tier {tier}" if tier else "") + (f", {forms} forms" if forms > 1 else "")
            + ". Attack potency, speed and durability from the VS Battles Wiki - see who they beat.")
    shown = short if row["category"].lower() in short.lower() else f"{short} ({row['category']})"  # not "Kirby (Kirby)"
    return (f"{shown} — Powerscale", desc, _preview_image(cid),
            [(short, f"Fictional character from {row['category']}")])


def _names(people: List[str]) -> str:
    return people[0] if len(people) == 1 else ", ".join(people[:-1]) + " & " + people[-1] if people else ""


def _duel_preview(game: Optional[str]):
    gid = _int_param(game)
    if gid is None:
        return None, None, None
    try:
        g = duels_api._one(gid, None)
    except HTTPException:
        return None, None, None
    kind = f"{ {'draft': 'Draft', 'gauntlet': 'Gauntlet'}.get(g.mode, 'Prediction')} duel · {g.format}"
    sides = [_names([p.username for p in g.players if p.team == t]) for t in range(1, g.teams + 1)]
    sides = [x for x in sides if x]
    title = (" vs ".join(sides) if len(sides) > 1 else f"{g.creator}'s duel") + " — Powerscale"
    pictured = next((p for p in g.players if p.username == g.creator), None)
    if g.status == "open":
        desc = (f"{kind} · {g.seats_left} seat{'s' if g.seats_left != 1 else ''} left"
                + (" · invite only" if g.private else " · anyone can join")
                + ". Five rounds, 20 seconds each.")
    elif g.status == "active":
        desc = f"{kind} · in progress."
    elif g.status == "done":
        winners = [p for p in g.players if p.outcome == "win"]
        scores = "–".join(str(s) for s in sorted(g.team_scores, reverse=True))  # the winner's first
        desc = f"{kind} · {_names([p.username for p in winners])} won, {scores}." if winners else f"{kind} · a draw, {scores}."
        pictured = winners[0] if winners else pictured
    else:
        desc = f"{kind} · {g.status}."
    return title, desc, pictured.avatar_url if pictured else None


def _user_preview(username: Optional[str]):
    profile = community.get_profile(username=username) if username else None
    if profile is None:
        return None, None, None
    out = community_api._profile_out(profile, own=False)
    rec = out.record
    parts = [f"Member since {out.member_since:%b %Y}", f"{out.post_count} post{'s' if out.post_count != 1 else ''}"]
    if rec.wins + rec.draws + rec.losses:
        parts.append(f"duels {rec.wins}–{rec.draws}–{rec.losses}")
    if out.favorite:
        parts.append(f"favorite: {characters.short_name(out.favorite.name)}")
    desc = " · ".join(parts) + "."
    if out.bio:
        desc = f"{out.bio[:160]}{'…' if len(out.bio) > 160 else ''} — {desc}"
    image = out.avatar_url or (_preview_image(out.favorite.id) if out.favorite else None)
    return f"{out.username}{' (admin)' if out.is_admin else ''} — Powerscale", desc, image


def _tournament_preview(ids: Optional[str]):
    wanted = [int(x) for x in (ids or "").split(",") if x.isdigit()]
    names = [characters.short_name(n) for n in (characters.display_name_for_id(i) for i in wanted) if n]
    if len(names) not in (8, 16) or len(names) != len(wanted):
        return None, None, None
    shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
    return f"{len(names)}-character tournament — Powerscale", f"Who takes it? {shown}.", _preview_image(wanted[0])


# --- for search engines ----------------------------------------------------------
# robots.txt points crawlers at the sitemap: the main pages and every
# character page (matchups are endless, so only characters are listed -
# a crawler still finds matchups through their links).

def _sitemap(request: Request) -> Response:
    base = _base_url(request)
    pages = ("browse.html", "duels.html", "board.html", "tournament.html", "gauntlet.html", "faq.html", "terms.html",
             "privacy.html")
    urls = [(f"{base}/{page}", None) for page in pages]
    with db.connect() as conn:
        for row in conn.execute("SELECT id, last_scraped_at FROM characters ORDER BY id"):
            urls.append((f"{base}/character.html?id={row[0]}", (row[1] or "")[:10] or None))
    # The matchups people actually talk about - a few hundred real pages,
    # not every possible pair (thin pages search engines ignore anyway).
    try:
        urls += [(f"{base}/compare.html?a={a}&b={b}", None) for a, b in community.discussed_matchups()]
    except Exception:  # noqa: BLE001 - the community database is down: characters only
        pass
    body = "".join(f"<url><loc>{html_escape(loc)}</loc>{f'<lastmod>{day}</lastmod>' if day else ''}</url>"
                   for loc, day in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


def _robots(request: Request) -> Response:
    text = ("User-agent: *\n"
            "Allow: /api/avatars/\n"
            "Disallow: /api/\n"
            "Disallow: /login.html\n"
            "Disallow: /profile.html\n"
            "Disallow: /tickets.html\n"
            f"Sitemap: {_base_url(request)}/sitemap.xml\n")
    return Response(text, media_type="text/plain")


app.add_api_route("/sitemap.xml", _sitemap, methods=["GET", "HEAD"], include_in_schema=False)
app.add_api_route("/robots.txt", _robots, methods=["GET", "HEAD"], include_in_schema=False)


# --- static frontend (mounted once frontend/ exists) -------------------
# StaticFiles(html=True) serves index.html for a directory request, but
# frontend/ has no index.html (browse.html is the real landing page) - so
# "/" itself 404s unless something redirects it first. Registered before
# the mount so this exact-path route wins over the mount's catch-all.

if FRONTEND_DIR.exists():
    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def _root():
        return RedirectResponse(url="/browse.html")

    @app.api_route("/compare.html", methods=["GET", "HEAD"], include_in_schema=False)
    def _compare_page(request: Request, a: Optional[str] = None, b: Optional[str] = None,
                      fa: Optional[str] = None, fb: Optional[str] = None):
        return _page_with_preview(request, "compare.html", *_compare_preview(a, b, fa, fb))

    @app.api_route("/character.html", methods=["GET", "HEAD"], include_in_schema=False)
    def _character_page(request: Request, id: Optional[str] = None):
        return _page_with_preview(request, "character.html", *_character_preview(id))

    @app.api_route("/duels.html", methods=["GET", "HEAD"], include_in_schema=False)
    def _duels_page(request: Request, game: Optional[str] = None):
        return _page_with_preview(request, "duels.html", *_duel_preview(game))

    @app.api_route("/user.html", methods=["GET", "HEAD"], include_in_schema=False)
    def _user_page(request: Request, u: Optional[str] = None):
        return _page_with_preview(request, "user.html", *_user_preview(u))

    @app.api_route("/tournament.html", methods=["GET", "HEAD"], include_in_schema=False)
    def _tournament_page(request: Request, ids: Optional[str] = None):
        return _page_with_preview(request, "tournament.html", *_tournament_preview(ids))

    # The rest: the site-wide card.
    def _plain_page_route(filename: str):
        def page(request: Request):
            return _page_with_preview(request, filename, None, None)
        return page

    for _name in ("browse.html", "board.html", "login.html", "profile.html", "tickets.html", "faq.html",
                  "terms.html", "privacy.html", "gauntlet.html"):
        app.add_api_route(f"/{_name}", _plain_page_route(_name), methods=["GET", "HEAD"], include_in_schema=False)

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
