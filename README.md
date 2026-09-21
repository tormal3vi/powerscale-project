# Powerscale Project

Fetches a character page from [VS Battles Wiki](https://vsbattles.fandom.com),
parses its "Powers and Stats" section into structured JSON (Phase 1),
normalizes the tier/speed/AP/durability strings into comparable numeric
scores (Phase 2), batch-scrapes an entire category into a local SQLite
database (Phase 3), sweeps categories for tier/speed vocabulary the
normalizer doesn't yet recognize (Phase 4 prep), compares characters
side by side (Phase 4), and estimates who would win a fight between
two characters with a weighted stat comparison plus score-free ability
flags (Phase 5). Characters with no wiki page at all (original
fiction) can also be entered manually and flow through the exact same
normalization/comparison pipeline as scraped ones. The comparison UI
is now a real FastAPI backend + a plain HTML/CSS/JS frontend (Phase
6), replacing the original Streamlit app, which is kept around
untouched as a fallback - see "Web UI (Phase 6)" below.

## How it fetches pages (important context)

`vsbattles.fandom.com` puts a Cloudflare bot-challenge in front of its
rendered `/wiki/<Character>` pages, which blocks plain HTTP clients like
`requests` no matter what `User-Agent` is sent (it's TLS/JS fingerprinting,
not a header check). Its MediaWiki API endpoint, `/api.php`, is not behind
that challenge and is explicitly allowed by `robots.txt` for all bots, so
`scraper.py` fetches rendered page HTML through `action=parse&prop=text`
on that endpoint instead of scraping `/wiki/` pages directly.

One consequence: `robots.txt` itself is *also* behind the same
Cloudflare challenge for non-browser clients, so it can't be fetched live
at runtime either. It was checked manually in a real browser instead
(`/api.php` is allowed for `User-agent: *`), and that's hardcoded as a
guard in `scraper._check_robots_allowed` with a comment explaining why -
see that function if this ever needs re-verifying.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### Deploying (e.g. Streamlit Community Cloud)

This subsection is specifically about deploying the legacy Streamlit
app (`app.py`) to Streamlit Community Cloud - it predates the FastAPI +
static-frontend UI and doesn't apply to it. The current web UI is a
plain ASGI app (`backend.main:app`); deploying it would mean an ASGI
host (Render, Fly.io, a VPS behind `uvicorn`/`gunicorn`, etc.), not
Streamlit Cloud specifically - not set up yet, since local use has
been the only requirement so far. The `powerscale.db`-is-tracked-in-git
reasoning below still applies equally to either UI, for the same
reason (no way to run `batch_scrape.py` on most hosts).

`app.py` calls `db.init_db()` on startup (a no-op if the schema already
exists), so a fresh clone with no `powerscale.db` at all still runs -
it just starts with an empty character list rather than crashing with
"no such table: characters" (the failure mode before this was added,
found by actually deploying and hitting it).

`powerscale.db` is intentionally **tracked in git**, not ignored like
`cache/`/`data/` - a hosted deploy has no way to run `batch_scrape.py`
itself, so the committed DB is the only way it ends up with real data.
This is a point-in-time **snapshot, not a live sync**: after any local
batch scrape or one-off addition you want reflected in a deployment,
re-add and commit `powerscale.db` same as any other changed file. Also
worth knowing: a public deployment lets any visitor trigger a live
fetch against VS Battles Wiki via "Add a character," not just you.

## Usage

```bash
./venv/bin/python3 main.py "Saitama"
./venv/bin/python3 main.py "https://vsbattles.fandom.com/wiki/Kirby"
./venv/bin/python3 main.py "Saitama" --refresh   # bypass the page cache
```

Prints the parsed dict as JSON and saves it to `data/<Character>.json`.

### Web UI (Compare + Browse)

```bash
./venv/bin/uvicorn backend.main:app --reload
```

One process, one port - serves both the JSON API (`/api/*`) and the
static frontend. Open `http://localhost:8000/browse.html` to search/
filter the roster and stage a comparison, or `http://localhost:8000/`
to go straight to Compare. `/docs` gets you FastAPI's interactive
Swagger UI for exercising every endpoint by hand. See "Web UI (Phase
6)" below for the architecture.

### Comparison UI (Streamlit, legacy)

```bash
./venv/bin/streamlit run app.py
```

Opens at `http://localhost:8501`. Superseded by the web UI above, but
kept around untouched as a fallback - see "Comparison UI (Phase 4)"
below for how it works.

### Batch-scraping a category

```bash
./venv/bin/python3 batch_scrape.py "Kages" --dry-run   # sanity-check first, no pages fetched
./venv/bin/python3 batch_scrape.py "Kages"              # scrape + parse + normalize + store in the DB
./venv/bin/python3 batch_scrape.py "Kages" --force      # re-scrape even if already fresh
./venv/bin/python3 batch_scrape.py "Kages" --freshness-days 7
```

The `Category:` prefix is optional (`"Kages"` and `"Category:Kages"` are
equivalent). See "Batch scraping (Phase 3)" below for details.

## Project layout

- `scraper.py` - fetches a page via the MediaWiki API, rate-limited
  (1.5s minimum between live requests) and cached to `cache/<Title>.html`
  so repeated runs during development don't re-hit the network.
- `parser.py` - parses the rendered HTML into a `CharacterStats`
  dataclass: `tier`, `name`, `origin`, `gender`, `age`, `classification`,
  `powers_and_abilities` (list of strings), `attack_potency`, `speed`,
  `lifting_strength`, `striking_strength`, `durability`, `stamina`,
  `range`, `standard_equipment`, `intelligence`, `weaknesses`. Values are
  kept as raw wiki text (e.g. `"9-B"`, `"Massively Hypersonic+"`) -
  no normalization yet, that's Phase 2. Any field the page has that isn't
  in that standard list (e.g. `Key`, `Feats`, `Notable Attacks/Techniques`)
  is kept in `extra_fields` instead of being discarded. Fields missing
  from a page are simply `None` / an empty list, never an error.
- `normalizer.py` - converts a `CharacterStats` into a `NormalizedStats`
  with numeric `tier`, `attack_potency`, `speed`, and `durability` fields
  (each a `NormalizedRange` with `baseline`/`peak` scores, and the
  qualifier word - "At least", "Possibly", etc. - attached to each, so
  speculative and confirmed values stay distinguishable). See "Numeric
  normalization (Phase 2)" below for how the scale itself works.
- `main.py` - CLI: `python main.py "Character Name"`.
- `test_parser.py` - parser tests against 3 real, differently-laid-out
  character pages (see below).
- `test_normalizer.py` - normalizer tests against the same 3 fixtures'
  actual tier/speed/AP/durability strings, plus hand-built edge cases
  (see "Numeric normalization (Phase 2)" below).
- `cache/` - raw HTML fetched via the API during development (gitignored,
  purely a local dev speedup - not the same as the test fixtures below).
- `data/` - JSON output from `main.py` runs (gitignored).
- `test_fixtures/` - the 3 real pages `test_parser.py` runs against,
  saved as HTML so tests don't need network access and are tracked in
  version control (unlike `cache/`).
- `category_fetcher.py` - fetches a category's full member list via the
  MediaWiki API (`list=categorymembers`, paginated), filtered down to
  character pages. See "Batch scraping (Phase 3)" below.
- `db.py` - SQLite storage (`powerscale.db`, gitignored) for scraped
  characters. See "Batch scraping (Phase 3)" below for the schema.
- `batch_scrape.py` - CLI: `python batch_scrape.py "Category Name"`.
  Orchestrates fetch → parse → normalize → store for every character in
  a category, in sequence (no parallel requests).
- `vocab_sweep.py` - CLI: `python vocab_sweep.py`. Standalone,
  exploratory scrape (no DB writes) that collects every tier/speed/AP/
  durability string the normalizer's ladders don't recognize, grouped
  and deduplicated for review. See "Vocabulary gap sweep (Phase 4
  prep)" below.
- `vocab_gaps_report.txt` - the saved output of the last `vocab_sweep.py`
  run (gitignored - regenerate it by re-running the sweep).
- `app.py` - Streamlit UI (legacy, kept but unused): pick 2-4 characters
  from the DB and compare their normalized stats (radar/bar chart, raw
  stat text, abilities/weaknesses). See "Comparison UI (Phase 4)" below.
- `backend/main.py` / `backend/schemas.py` - the current UI's API: thin
  FastAPI wrapper around `db.py`/`normalizer.py`/`calculator.py` (zero
  business logic of its own), plus the Pydantic response models. Also
  mounts `frontend/` as static files. See "Web UI (Phase 6)" below.
- `frontend/` - the current UI itself: `browse.html`/`browse.js`
  (search/filter/stage-a-comparison), `compare.html`/`compare.js`
  (hero cards, radar chart, stat table, verdict panel), `styles.css`
  (shared design tokens), `api.js` (shared fetch helpers). Plain HTML/
  CSS/JS, no framework, no build step.
- `.claude/launch.json` - dev-server config so either UI can be
  previewed in an editor/agent's browser pane (`streamlit-app` and
  `backend-api` entries); not needed to just run either yourself.

## Tests

```bash
./venv/bin/python3 test_parser.py
# or, if pytest is installed:
./venv/bin/python3 -m pytest test_parser.py -v
```

Tests run against 3 cached real pages chosen to stress different parts of
the parser:

- **Saitama** - multi-form tiered stats (`"9-B | At least 9-B... | 4-A..."`),
  `Powers and Abilities` as a bulleted list nested inside tabs for
  different training stages, and non-standard table-based sections
  (`Feats`, `Notable Attacks/Techniques`) that sit right after
  `Weaknesses` in the HTML and must not bleed into it.
- **Kirby** - the largest page tested (~900KB HTML), `Powers and
  Abilities` as a huge bulleted list nested across many form tabs.
- **Flameskull** - a shorter page with a genuinely missing field
  (`Classification` isn't on the page - must come back `None`, not
  crash), and `Powers and Abilities` as flat inline comma-separated
  text with no bullet points at all, which needed different handling
  than the bulleted-list pages above.

## Known limitations (Phase 1 scope)

- Only the flat `<p><b>Label:</b> value</p>` layout (the convention on
  every page sampled) is parsed. A page using an old-style wikitable
  infobox instead would come back with unset fields rather than an error.

## Numeric normalization (Phase 2)

`normalizer.py` converts `Tier`, `Attack Potency`, `Speed`, and
`Durability` strings into numeric scores so two characters can be
compared. The scale's shape was reviewed and approved before the full
ladder was written (see module docstring in `normalizer.py` for the
full reasoning); the short version:

- **Score = `log10(energy in joules)`** for Tier/Attack Potency/Durability,
  anchored to real physics where the wiki's tier definitions actually
  have one (10-C through 3-A "Universe level" - e.g. 5-B "Planet level"
  is anchored to Earth's real gravitational binding energy). This is a
  raw, physically-interpretable number, not rescaled to 0-100, and the
  gaps between tiers are deliberately uneven because the real energy
  gaps are uneven.
- **Attack Potency and Durability reuse the exact same scale as Tier**
  rather than getting their own ladder - VS Battles Wiki defines a
  character's Tier *as* a function of AP and Durability, so all three
  share one vocabulary, just expressed as a code (`"9-B"`) in the Tier
  field vs. a name (`"Wall level"`) in AP/Durability. Both forms map to
  the same score.
- **Above 3-A**, tiers describe multiversal/dimensional concepts with no
  real joule value (the wiki never assigns one). Scores continue with a
  fixed, deliberately large synthetic step per tier - ordinal only, not
  a real energy claim, and documented as such in the code.
- **Speed** gets its own ladder (unrelated vocabulary - "Subsonic",
  "Massively FTL+"), anchored to `log10(m/s)` the same way, with
  synthetic sentinel scores for the immeasurable/infinite top end.
  Includes one physically-precise anchor: **"Speed of Light"** at
  `log10(299,792,458)` ≈ 8.4768 - exactly *c*, positioned as the
  boundary between the sub-light tiers and FTL, distinct from the
  nearby "Massively Relativistic" entry (which means *approaching*,
  not touching, light speed).
- **Aliases**: both ladders support mapping an alternate real-world
  phrasing to an *existing* entry's score, rather than that phrasing
  getting its own value. Three kinds so far, all added after a
  [vocabulary gap sweep](#vocabulary-gap-sweep-phase-4-prep) rather
  than guessed ahead of time: wiki-side typos of a term already on the
  ladder (e.g. `"Relavistic"` → `"Relativistic"`, `"Sub-Relatvistic+"`
  → `"Sub-Relativistic+"`, `"LargeTown level"` → `"Town level"`'s High
  grade), informal synonyms (e.g. `"Human level"`, `"Average Human"`,
  and `"Below Average Human level"` all alias to 10-C, whose canonical
  name - `"Below Average level"` - doesn't itself say "Human"), and a
  **superseded canonical name**: 10-A was originally modeled as
  `"Peak Human level"`, but a later sweep found the wiki's own Tiering
  System/Attack Potency pages now call it `"Athlete level"` - the
  canonical label was renamed to match, with `"Peak Human level"` kept
  as an alias pointing at it (same score, 2.8 - see [batch2
  sweep](#vocabulary-gap-sweep-batch2-dragon-ball--jojos-bizarre-adventure)
  below). An alias never overrides a ladder key that already has its
  own independently-computed score.
- **New anchors**: sometimes a gap isn't a phrasing variant of an
  existing entry at all, but a real tier the ladder's coarser 24-rung
  grid never modeled. `"Brown Dwarf level"` (wiki code `High 5-A`) is
  the first case of this - added as its own anchor at **37.84**
  (`log10` of the wiki's own published `High 5-A` lower energy bound,
  ~6.906×10³⁷ J), strictly between `5-A` "Large Planet level" (36.0)
  and `4-C` "Star level" (41.8), using the same real-physics anchoring
  method as the Moon/Earth/Sun entries. Once added, the ladder's
  existing Low/High/`+` auto-grading (see `_build_tier_ladder`)
  produces `"Brown Dwarf level+"` automatically - no separate alias
  needed for the `+` grade, since every other tier's `+` suffix is a
  *computed* bump above baseline, not an identical-score alias.
- **"Omnipresent" gets no numeric score at all**, by design - existing
  everywhere at once isn't a point on a "how fast" scale, it's a
  categorically different kind of claim, and forcing a number onto it
  would misrepresent what the wiki text actually says. Instead
  `NormalizedStats.is_omnipresent` is a separate boolean, detected
  independently of the speed ladder lookup; a character with only
  `"Omnipresent"` in their Speed field gets `is_omnipresent=True` and
  `speed.baseline`/`speed.peak` left `None`, rather than an invented
  "very high" number.

**Parsing a raw string** (e.g. `"9-B | At least 9-B, up to at least
6-A"`) strips parenthetical justification text, then scans for every
occurrence of a known tier/speed label in the remainder (handling
pipe-separated forms, comma-separated progressions, and labels embedded
in plain prose like `"Hypersonic+ with High Hypersonic+ reactions"`
alike). The **lowest-scoring match becomes `baseline`**, the
**highest-scoring becomes `peak`**, and each carries whatever qualifier
word ("At least", "Possibly", "Up to", ...) immediately preceded it in
the source text - so a speculative peak stays visibly distinct from a
confirmed one. A string with no recognizable label returns an all-`None`
range and logs a warning, rather than raising.

Not handled yet (unchanged from the original raw text): `Powers and
Abilities` and `Weaknesses` need categorical, not numeric, normalization
- planned for a later phase.

## Vocabulary gap sweep (Phase 4 prep)

Before building anything on top of the normalizer, `vocab_sweep.py`
scraped 3 categories deliberately chosen to be unlike the two already
covered (`Kages`, `One-Punch_Man`) - `Baki the Grappler` (mundane/low-tier,
no superpowers), `Fairy Tail` (long-running shonen with a reputation for
messy/inconsistent stat blocks), and `Puella Magi Verse` (small cast
ascending to abstract/conceptual power) - and collected every
tier/speed/AP/durability string neither ladder recognized, without
writing anything to the DB. See `vocab_gaps_report.txt` for the full
output.

It chunks each raw field the same way the wiki formats it (top-level
`|` for alternate forms, then top-level `,` for progressions, both
bracket-aware) and checks each chunk independently - catching partial
misses a whole-string check would hide, e.g. `"9-B | UnknownTerm"`
where the first chunk parses fine but the second doesn't.

Raw result: 801 misses, 212 unique strings. The overwhelming majority
of those turned out not to be vocabulary gaps at all - they're the
wiki's convention of writing `"<value>, higher with <character-specific
technique>"` without ever stating the boosted value (e.g. `"higher with
Dragon Force"`, `"Varies with Enchantments"`). There's genuinely
nothing to extract there; "no token found" is correct behavior. After
manually triaging that noise out, 5 real, recurring findings remained,
reviewed and decided by the user (not this tool) and now implemented in
`normalizer.py`:

1. Two wiki-side typos aliased to their correct term: `"Sub-Relatvistic+"`
   and `"Relavistic"`.
2. The `"Human level"` family (`"Human level"`, `"Below Average Human
   level"`, `"Average Human"`) aliased to the existing 10-C anchor. The
   rest of `TIER_LADDER` was audited for the same "missing Human
   qualifier" issue - 10-B (`"Athletic Human level"`) and 10-A
   (`"Peak Human level"`) already say "Human" in their canonical
   names, so only 10-C needed the fix.
3. `"Speed of Light"` added as its own physically-precise anchor (see
   above), with its ~10 phrasing variants (`"Speed of Light with Fairy
   Law"`, etc.) needing no individual aliases - they all contain
   `"Speed of Light"` as a substring, which the tokenizer already
   matches anywhere in the text.
4. `"Omnipresent"` handled as the `is_omnipresent` flag described
   above, not a score.
5. `"Infinite"` (standalone) aliased to the existing `"Infinite Speed"`
   entry.

Re-running the sweep afterward confirmed the fix cleanly: 801 → 754
misses (−47, exactly matching the sum of all fixed occurrences: 2
typos + 10 Human-level variants + 34 Speed-of-Light variants + 1
Infinite), 212 → 192 unique strings, with `"Omnipresent"` still
correctly appearing (3 occurrences) since the sweep only checks ladder
scoring, not the separate flag - not a bug, expected by design. No new
gaps were introduced.

```bash
./venv/bin/python3 vocab_sweep.py                                    # the 3 categories above
./venv/bin/python3 vocab_sweep.py --categories "Kages" "Some Category"  # or your own pick
```

## Vocabulary gap sweep: batch2 (Dragon Ball / JoJo's Bizarre Adventure)

A second round, triggered by batch-scraping 3 new categories (Dragon
Ball, `Re:Zero kara Hajimeru Isekai Seikatsu`, JoJo's Bizarre
Adventure - see [Batch scraping](#batch-scraping-phase-3)). Unlike the
Phase 4 sweep, this one reused the just-scraped DB data instead of
re-fetching pages over the network, and was extended to check
per-*form* stat strings too, not just the flat top-level fields - the
multi-form model didn't exist yet when `vocab_sweep.py` was originally
written, so a form-only gap (like one buried in a Goku/Vegeta
transformation) would previously have gone uncounted.

Two real, recurring gaps came out of it, both reviewed and decided by
the user, not guessed ahead of time:

1. **`"Athlete level"`** (86 occurrences, JoJo Stand users' baseline
   physical stats). Checking the live wiki directly (not just
   assuming) showed this is the *current* official name for tier
   10-A on both the Tiering System and Attack Potency pages - the
   ladder's existing 10-A entry was labeled `"Peak Human level"`,
   which no longer appears as live wiki terminology. **Decision:**
   rename the canonical 10-A label to `"Athlete level"`, keep
   `"Peak Human level"` as an alias to the same score (2.8) - see
   [Numeric normalization](#numeric-normalization-phase-2).
2. **`"Brown Dwarf level+"`** (22+ occurrences, Dragon Ball - Brocco,
   Paragus, and several Goku/Vegeta transformation forms). Not an
   alias case: the wiki's own Attack Potency chart defines this as a
   real tier, code `High 5-A`, with a published energy range - one of
   ~15 "Low X"/"High X" fractional sub-tiers the Phase 2 ladder never
   modeled (it only covers the 24 whole-letter tiers, 10-C through
   3-A). **Decision:** add it as its own new anchor at **37.84**
   (see [Numeric normalization](#numeric-normalization-phase-2) for
   the exact anchoring method), rather than rounding it down to the
   nearest existing whole tier.

**`"Small Star level"` (`Low 4-C`, the very next sub-tier up from
Brown Dwarf) was deliberately *not* added.** It's a real, documented
wiki tier and will likely surface as its own gap eventually, but
hasn't actually shown up in any scraped character's data yet -
adding it preemptively would mean guessing at a gap instead of
reacting to one, the same policy that gated adding Brown Dwarf level
itself (which only got added once real characters surfaced it). It
stays a noted possibility, not a to-do.

Regression tests for both fixes use real strings pulled straight from
this sweep - Bobby Jean's `"Athlete level, Street level with
USP-45..."`, Brocco's bare-code `"High 5-A"` Tier field alongside its
`"Brown Dwarf level+"` AP/Durability, and Son Goku (Toei)'s
"Beginning of Z" form finding the anchor correctly amid a lot of
unrelated narrative prose (`"Varies"`, `"up to far higher with
Kamehameha"`) - see `test_normalizer.py`'s "vocabulary gap sweep
fixes (batch2...)" section. Re-running the sweep afterward confirmed
both `"athlete level"` and `"brown dwarf level"`/`"brown dwarf
level+"` no longer appear as misses anywhere in Dragon Ball or JoJo.

One unrelated, single-character oddity turned up along the way and
was *not* acted on: `Magent Magent` (JoJo) has `"Athlete level"`
written directly in her **Speed** field (`"Athlete level, Massively
FTL reaction time..."`), where it doesn't belong - Athlete level is a
Tier/AP concept, and Speed uses a completely different vocabulary
("Subsonic", "Superhuman", etc.). This is almost certainly a wiki
authoring slip on that one page, not a systemic gap - aliasing
`"Athlete level"` into `SPEED_LADDER` to "fix" it would be
conceptually wrong and risk false matches elsewhere, so it's left as
a correctly-unscored `"Unknown"`-equivalent miss.

## Batch scraping (Phase 3)

`batch_scrape.py` fetches every character page in a VS Battles Wiki
category, running each one through the existing scraper → parser →
normalizer pipeline, and stores the result in a local SQLite database
(`powerscale.db`) so later phases can pull "all characters from series
X" without looking them up one at a time.

### Category member filtering

`category_fetcher.py` fetches the category's member list via
`action=query&list=categorymembers` (paginating through `cmcontinue` -
categories can have hundreds of members and the API caps each request).
The filtering rules came from actually inspecting two real categories
first (`Category:One-Punch_Man`, 249 raw members; `Category:Naruto`,
351 raw members) rather than guessing:

1. **Keep only namespace 0** (the main article namespace). This alone
   removes almost all the noise: community power-calculation blog posts
   live in namespace 500 (161 of Naruto's 351 members: `User
   blog:Therefir/One-Punch Man: Serious Sneeze` and similar), and nested
   subcategory links (e.g. `Category:Tank Topper Army`) show up as
   namespace 14.
2. **Drop the series/franchise overview page**, which is always a
   member of its own category. Its title is usually `"<Series> (Verse)"`
   (disambiguated when the bare name would collide with a character,
   e.g. `"Naruto (Verse)"`), but falls back to the bare series name when
   there's no collision (e.g. `"One-Punch Man"` itself, confirmed by
   fetching that exact title). Both forms are filtered.
3. **What's deliberately *not* filtered here**: a handful of
   ability/mechanic pages slip past both rules above (`Sharingan`,
   `Chakra Cannon`, `Ōtsutsuki Physiology` all showed up in
   `Category:Naruto`'s ns=0 members). They don't follow a reliable title
   pattern, and guessing one risks excluding real character names by
   accident. Instead, `batch_scrape.py` catches these at runtime: any
   page where parsing finds *none* of Tier/Attack Potency/Speed/
   Durability is logged as "skipped (no stats found)" rather than
   stored or treated as a failure.

### Database schema

```sql
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_url TEXT NOT NULL UNIQUE,   -- canonical wiki URL; the natural key
    category TEXT,                     -- most recent category this was scraped under
    last_scraped_at TEXT NOT NULL,     -- ISO 8601 UTC timestamp
    raw_json TEXT NOT NULL,            -- CharacterStats.to_dict(), JSON
    normalized_json TEXT NOT NULL      -- NormalizedStats.to_dict(), JSON
);

CREATE INDEX idx_characters_name ON characters(name);
CREATE INDEX idx_characters_category ON characters(category);
```

Raw and normalized stats are stored as JSON blobs rather than individual
columns - both schemas are still evolving (Phase 4+ will add categorical
normalization for abilities/weaknesses), and a blob means a new field
there never needs a DB migration. `source_url` is the unique key per
character; re-scraping the same character upserts the existing row
rather than duplicating it.

**Known simplification**: `category` holds only the *most recent*
category a character was scraped under, not a full membership list. A
character that belongs to two categories you batch-scrape separately
will just have its `category` column overwritten by whichever run went
second. Fine for "pull all characters from series X" per-category
queries; a join table would be needed if full multi-category membership
ever matters.

### Batch orchestration behavior

- **Sequential only** - every page fetch goes through the same rate
  limiter as `main.py` (`scraper.prepare_api_request`, 1.5s minimum
  between live requests). Nothing in this batch path runs in parallel.
- **Freshness skip** - before fetching, each title's canonical URL is
  checked against the DB; if it was scraped within `--freshness-days`
  (default 30), it's skipped. `--force` bypasses this and re-scrapes
  everything.
- **Per-character failures don't stop the batch** - a `try/except`
  around each character's fetch+parse+normalize logs the title and
  error, then moves on. The end-of-run summary reports counts for
  scraped / skipped (fresh) / skipped (no stats found) / failed, plus
  the title and error message for every failure.
- **Progress output** - each character prints as `i/total: Name...
  done` (or `skipped (...)` / `FAILED (...)`) while it runs.

### Stress test: Category:Kages (17 members)

First real test beyond the 3 hand-picked Phase 1/2 fixtures - a small,
already-clean category:

```
Batch complete: 17 candidate page(s)
  Scraped:                17
  Skipped (already fresh): 0
  Skipped (no stats found): 0
  Failed:                 0
```

17/17 parsed successfully, including two different characters both
literally named "A" (`A (Fourth Raikage)`, `A (Third Raikage)`) and
title-style names like `Third Kazekage`. One character (`Muu`) has a
`Tier` field that's literally the text `"Unknown"` - the normalizer
correctly logged a warning and returned an all-`None` range for it
instead of crashing or guessing.

### Stress test: Category:One-Punch_Man (122 members)

A larger, noisier category run, to actually stress the parser across a
whole series' worth of real-world page variation rather than 3
hand-picked fixtures. First pass:

```
Batch complete: 122 candidate page(s)
  Scraped:                121
  Skipped (already fresh): 0
  Skipped (no stats found): 1
  Failed:                 0
```

**The 1 skip was a real parser bug**, not a false positive from the
category filter: `Promoted Rook`'s section heading is literally `"Power
and Stats"` (singular "Power"), not the usual `"Powers and Stats"`, so
`parser._find_stats_heading` never found it and the whole page came
back empty. Fixed by matching both spellings
(`^powers?\s+and\s+stats$`), added as a 4th tracked fixture
(`test_fixtures/PromotedRook.html`) with a regression test. Re-running
with `--force` after the fix:

```
Batch complete: 122 candidate page(s)
  Scraped:                122
  Skipped (already fresh): 0
  Skipped (no stats found): 0
  Failed:                 0
```

122/122, 0 failures, 0 skips. Along the way, the normalizer logged (but
didn't crash on) warnings for text it can't map to a numeric score -
worth noting since these are real coverage gaps, not bugs:

- **Genuinely unrated stats** (~20 characters) - fields whose actual
  wiki text is literally `"Unknown"` (e.g. `Crablante`, `Vaccine Man`).
  This is correct behavior: there's nothing to parse, and returning an
  all-`None` range with a logged warning is exactly what should happen.
- **Vocabulary the ladder doesn't cover** - two real gaps: `King
  (One-Punch Man)`'s Attack Potency is `"Human level"`, an informal
  tier descriptor `TIER_LADDER` doesn't include; `Rafflesidon`'s is
  `"LargeTown level"` (a wiki-side missing-space typo for "Large Town
  level"). Both are legitimate scope gaps in the current ladder rather
  than parser bugs - noted here rather than silently patched, since
  expanding the ladder's vocabulary to match everything real pages
  actually say is an ongoing, open-ended tail rather than a one-line
  fix, and better scoped as deliberate follow-up work.

### Stress test: Category:Chainsaw Man (33 members)

Added after noticing Chainsaw Man characters weren't getting a "who
would win" verdict at all. Diagnosed the same way as the Genos/Vegeta/
Goku multi-form bug: inspected the real HTML before touching any code,
not guessed at.

**Root cause**: Tier parsed fine for characters like `Denji` and
`Power` (it's a flat field outside the tabber), but Attack
Potency/Speed/Durability all came back `None` for every one of them -
enough that most pairings had fewer than 2 comparable axes and
`calculator.py` correctly (by design) refused to produce a verdict at
all, which is what looked like "an issue." The actual bug: most of the
Chainsaw Man cast's stat tabber wraps each tab's stat `<p>`'s in an
extra `<div class="scrollable">` layer - `_find_stats_tabber`/
`_extract_forms_from_stats_tabber` only ever looked at a tab's *direct*
`<p>` children (true for Genos/Vegeta/Goku, where the fields sit
directly in the tab), so on this page layout they silently found
nothing. Fixed with a small `_stat_paragraphs()` helper in `parser.py`
that also checks one level into a direct-child `div.scrollable`, used
in both places. Regression test: `test_denji_multi_form_extraction_
through_a_scrollable_wrapper` in `test_parser.py`, using Denji's real
page as a new fixture.

**Impact after re-parsing the category from cache (no re-fetch
needed)**: characters with fewer than 2 scored axes dropped from ~12 to
2, and 9 characters that were incorrectly flattened to a single "Base"
form now correctly show their real per-arc forms (Denji: 4, Power: 3,
etc.) - the same `Key:` progression pattern documented under
"Multi-form characters" below.

**`Reze`/`Samurai Sword` were a third, different bug - deferred here,
then fixed once it recurred.** Their stat block wasn't inside a
per-form tab at all: it sat as flat `<p>` siblings *after* an unrelated
outer tabber (a Reze/"Bomb Girl" persona-selector, in Reze's case).
Originally reported rather than patched - it looked narrowly specific
to these two pages, and a quick fix risked being fragile or overfit.
It recurred on an unrelated category (`Frieren`, in
`Frieren: Beyond Journey's End` - see that stress test below), which
confirmed it as a real, identifiable DOM family rather than a one-off,
and got fixed generally at that point (`_labeled_paragraphs_in` in
`parser.py`). Both characters now parse correctly.

### Stress test: Category:Frieren: Beyond Journey's End (12 members)

Added alongside Mushoku Tensei and Hulk. Clean run (11 scraped, 1
non-character page correctly skipped, 0 failed) - except `Frieren`
herself, the title character, came back with the exact same
"Insufficient data" symptom as the Chainsaw Man bug above, but a third
distinct cause.

**Root cause**: her "Powers and Abilities" section is itself tabbed
(split into "Powers and Abilities" / "Resistances" tabs), and the page
wraps the *entire rest* of the flat stat block - Attack Potency,
Speed, Durability, Weaknesses, Standard Equipment, all of it - inside
that same tabber `<div>` too, as plain `<p>` siblings positioned after
its tabs. They're not nested inside any specific tab, and they're one
level deeper than `_collect_field_blocks`'s ordinary heading-sibling
walk looks, so they were previously silently absorbed as unparsed raw
HTML into whichever field was open when the tabber was reached (in
practice, "Powers and Abilities," since that's what preceded it) -
without corrupting the extracted ability list, since that extraction
only ever looks for `<li>` elements and simply ignored the extra `<p>`
noise mixed into the same blob.

This is the same DOM family as `Reze`/`Samurai Sword` (Chainsaw Man,
above) - found there first but deferred as looking like a narrow
2-character oddity. Recurring here, on a completely unrelated
category, confirmed it as a real pattern worth fixing generally rather
than a one-off: added `_labeled_paragraphs_in()` in `parser.py`,
which scans any sibling `<div class="tabber">`'s *direct* children for
labeled `<p>`'s and adds them as their own fields - purely additive,
and a confirmed no-op on every ordinary multi-form stats tabber
(Genos, Vegeta, Goku, Denji, ...), whose `<p>`'s all live nested one
level deeper, inside a `wds-tab__content` tab, never as direct
children of the tabber `<div>` itself. Fixing this also retroactively
fixed `Reze`/`Samurai Sword` and `Frieren`'s own `Serie` once the
Chainsaw Man and Frieren categories were re-parsed from cache.
Regression test: `test_frieren_flat_fields_wrapped_in_an_unrelated_
ability_tabber` in `test_parser.py`, using Frieren's real page as a
new fixture.

### Adding Hulk (Marvel Comics) individually

A fourth, unrelated root cause, found the same day: Hulk's page spells
the section heading out in full as **"Powers and Statistics"**, not
"Powers and Stats" or the already-handled singular "Power and Stats"
(Promoted Rook, Phase 3). `_STATS_HEADING_RE` matched `...stats$`
specifically, which "statistics" doesn't satisfy at all (different
suffix, not a superset) - so the whole section, and even `stats.name`,
came back empty, since `parse_character` returns a blank
`CharacterStats` when no heading is found. Fixed by widening the
regex to `stat(s|istics)$` and adding the two `id="..._Statistics"`
variants to the same id fast-path fix. Regression test:
`test_hulk_powers_and_statistics_heading_variant`, using Hulk's real
page (also genuinely multi-form - 5 personas - confirming the fix
helps the multi-form path too, not just flat fields).

**One further, narrower issue on the same page, reported not fixed**:
all 5 of Hulk's forms come back with Durability unscored. Root cause
is a wiki-authoring typo specific to this page, not a DOM pattern:
right after Striking Strength's closing `</p>`, the editor never
opened a new `<p>` before `<b>Durability</b>` - so it sits as loose
inline text directly inside the tab's container `<div>`, invisible to
every field-extraction path in `parser.py`, all of which only ever
look for `<b>` labels inside `<p>` elements. Narrow enough (one field,
one page, an apparent one-off editing mistake rather than a template
pattern) that it's reported here rather than patched, same call as
`Petra Leyte`'s bold-run label issue above.

**Category:Mushoku Tensei ～Isekai Ittara Honki Dasu～ (3 members)**:
scraped clean, 3/3, 0 failures, no new gaps.

### Found via the web UI: colon outside the bold tag

Not from a batch scrape - a user question about why Son Goku (Classic
Toei)'s "23rd Tournament" form showed unscored Attack Potency in the
Compare screen. Checked the real HTML rather than guessing: the usual
markup is `<b>Attack Potency:</b> value`, but this page writes
`<b>Attack potency</b>: value` instead - the colon sits just outside
the bold tag. Confirmed systemic on this specific page (5 of its 7
forms, all missing Attack Potency the same way) before generalizing
the fix, not patched as a one-off: `_label_text()` in `parser.py` now
also recognizes a label when the colon is the first character
immediately *after* `</b>`, consuming it in place so downstream
"everything after the label" extraction doesn't see a stray leading
`:`. Both call sites (`_label_from_p` for flat fields,
`_extract_forms_from_stats_tabber` for per-form fields) share the one
helper.

Checked whether this recurred elsewhere before calling it done: scanned
every already-cached page (1,145 files) for the same pattern - 5 hits
total, all already-scraped characters (`Garou`, `Minato Namikaze`,
`Son Goku (Classic Toei)`, `Son Goku (Toei)`, `Power` from Chainsaw
Man), re-parsed from cache and re-stored with the fix applied.

**A real bug introduced and caught while fixing this**: the first pass
used the same variable name (`label`) for both the outer per-form loop
and the inner per-field loop in `_extract_forms_from_stats_tabber` -
since Python doesn't scope `for` loops separately, the inner loop
silently overwrote the outer one, so every form's `name` came out as
whatever the last stat field happened to be (`"Stamina"` for all 7
Goku forms) instead of its real tab name. Caught immediately by
checking the actual output rather than assuming the fix worked;
`test_son_goku_classic_toei_colon_outside_bold_tag` in
`test_parser.py` asserts against it directly (`"Stamina" not in
names`) so it can't silently return.

## Comparison UI (Phase 4)

```bash
./venv/bin/streamlit run app.py
```

Pick 2-4 characters from the sidebar and compare their normalized stats
visually. Purely a visualization/comparison layer on top of the existing
DB - no "who would win" calculator yet (that's a later phase).

### Layout

- **Sidebar**: an "Add a character" form (name or URL + Fetch button -
  runs the full scraper → parser → normalizer pipeline and upserts into
  the DB, with a spinner and inline success/error message, never
  crashing the page on a bad title); a searchable multiselect of
  every character in the DB, labeled `"Name (Category)"` so same-named
  characters from different series stay distinguishable, plus `"· N
  forms"` for any character with more than one form; a **Forms** block
  (only rendered when at least one selected character has more than one
  form) with one dropdown per such character, defaulting to its first
  form; a chart-type toggle (Radar/Bar); a plot-value toggle
  (Peak/Baseline).
- **Main area**: an empty-state prompt when fewer than 2 characters are
  selected; otherwise the chart, a raw-stats table, and per-character
  detail panels, in that order.

### Multi-form characters

Following up on the multi-form data model (parser.py/normalizer.py now
always populate `forms`, one entry per story key/arc for characters
like Genos/Vegeta/Goku/Garou/Erza Scarlet/Minato Namikaze - 8 in the
current DB), every part of this page reads through
`forms[selected_index]` rather than the old flat fields, so switching a
character's form in the sidebar updates the chart, the raw-stats table,
and the "Comparing: ..." header live, exactly like any other control.

An ordinary single-`"Base"`-form character (the other ~493 characters)
is completely unaffected - no Forms row, no name suffix, nothing new to
look at. A multi-form character's display name only grows a suffix once
a *non-default* form is actually selected (e.g. `"Genos, Demon Cyborg"`
stays bare at its default form 0, `"Beginning of Series"`, but becomes
`"Genos, Demon Cyborg (Post-Elder Centipede)"` the moment you pick that
form) - this appears in the chart legend, the raw-stats table's
Character column, and the header, all from one `display_name` computed
once per row.

Each multi-form character's detail panel also gets an **"All forms
(N)"** expander (collapsed by default, placed above Powers and
Abilities) listing every form's name and Tier value, with a `→` marker
on whichever one is currently active in the comparison - so you can
eyeball the character's full range without leaving the page. Abilities
and Weaknesses stay character-level text either way (per-form ability
extraction wasn't part of this data model change).

Form selection is deliberately **not** remembered once a character
leaves the comparison: each form dropdown is keyed per character id
(`form_select_<id>`), and every rerun prunes any such key whose
character isn't currently selected. Re-adding a character later always
starts back at form 0, rather than silently resurrecting whatever form
was picked the last time it was in the comparison.

### The chart

Three axes/groups: **Tier/AP** (Attack Potency's score, falling back to
Tier's if AP itself didn't parse - they're nearly always close since
Tier is derived from AP+Durability), **Durability**, **Speed**. Radar
uses `plotly`'s `Scatterpolar` (`connectgaps=False`); Bar uses grouped
`Bar` traces. Both plot the **Peak** score by default (a character's
best stated capability) rather than Baseline, to avoid cluttering the
chart with two traces per character - switch the sidebar toggle to see
Baseline instead.

**Missing values are never plotted as zero.** A `None` stat (an
unrated/`"Unknown"` field, or nothing recognizable in the ladder) leaves
a genuine gap in that character's shape/bars, and a caption under the
chart spells out why per character and per stat (e.g. `"Genos, Demon
Cyborg — Durability: no recognizable Durability value"`). The
`is_omnipresent` flag from Phase 4 prep is surfaced independently of
whether Speed also has a real numeric value - a character can have both
a stated combat speed *and* a separate "exists everywhere" claim (e.g.
Madoka Kaname has `"Massively Hypersonic+"` *and* is tagged Omnipresent
from a different clause in the same field), so the note always appears
when the flag is set rather than only when Speed is otherwise empty.

### Raw stats table

One row per character (reading its currently-selected form), one column
per stat, each cell showing baseline → peak with qualifiers inline (e.g.
`"9-B (confirmed) → 3-C (possibly)"`), reusing the labels/qualifiers
Phase 2 already computed. The Speed column appends `"+ Omnipresent"`
rather than overwriting the cell when both a real value and the flag
are present, for the same reason as the chart caption above -
`is_omnipresent` is itself a per-form value now, checked independently
per form rather than once per character.

### Detail panels

One column per selected character: name/category header, an "All forms"
expander for multi-form characters only (see above), Powers and
Abilities as a list (collapsed by default past 5 items), Weaknesses as
plain text - `"None listed."` rather than a blank space when a field is
empty.

### Who would win?

A "Who would win?" section sits below the Details panels, wired
directly to Phase 5's `calculator.py`. Deliberately **no second form
picker** - it reuses whichever form is already active per character in
the sidebar's own Forms picker (the exact `form` each `rows[i]` dict
already carries for the chart/table), so the verdict is always
computed from the same form the chart is currently plotting, never a
silently different one. This means the *default* form used here is
index 0 (this app's own convention), not `calculator.py`'s standalone
default of "highest Tier" - a deliberate divergence for this app
specifically (see `app.py`'s module docstring).

Two dropdowns (`Character A`/`Character B`) let you pick any 2 of the
currently-selected 2-4 characters for the verdict - reusing the
existing selection rather than a separate character picker. Live-
updates on every rerun (no button - `compare_forms()` is cheap, pure
math over already-loaded data, so there's no reason to gate it), which
is also what makes switching a character's form in the sidebar
immediately flip the verdict, confirmed by testing this live with
Genos across two forms: `"Clear favorite"` for Garou at Genos'
weakest form, `"Too close to call"` once Genos' strongest (scored
Durability) form is selected instead - matching `test_calculator.py`'s
form-selection regression test with real data instead of a synthetic
rival.

The stat-breakdown table uses short `"A"`/`"B"` column headers rather
than the full (often long, alias-heavy) display names - found by
testing this live that two verbose names as side-by-side dataframe
columns pushed the second one past the visible table width with no
scroll indication. A caption above the table (`"A = ... | B = ..."`)
and the verdict line itself already spell out which is which, so
nothing is lost by keeping the columns short.

Ability flags render exactly as `calculator.py` intends: a separate,
clearly-labeled section below the stat table, never touching the
verdict's numbers - see "Who would win calculator" above for why.

### Notes from building/testing this

- **`db.py` gained two read helpers** for this phase:
  `get_all_characters()` (lightweight listing for the selector, no JSON
  blobs) and `get_character_by_id()` (full row once selected).
- **A character added via the sidebar form has no batch category**, so
  it's stored under its parsed `Origin` field as a stand-in (e.g.
  fetching "Vegeta" cold stores it under whatever `Origin` its page
  parses to, or `"Uncategorized"` if that field is empty too) - more
  meaningful than an actual `"Uncategorized"` bucket for everything.
- **The multiselect needs an explicit `key`** (`"character_selector"`),
  *and* its selected value needs to be explicitly re-asserted into
  `st.session_state` after a successful add. Found by testing the add-
  character flow end-to-end: clearing the character-list cache changes
  the widget's `options` on that rerun, and Streamlit drops the current
  selection when `options` changes under a multiselect even with a
  stable key - so the fix captures the selection before the cache
  clear and writes it straight back into session state afterward.
- **`watchdog` is a real dependency, not just a suggestion** - without
  it, Streamlit's file-change detection during development was
  unreliable enough to cost real debugging time (a code fix would
  sometimes silently not be running yet despite a page reload). Added
  to `requirements.txt`.
- **Two data-quality findings surfaced by using the UI** (`Genos`'s
  Attack Potency/Durability coming back `None` despite Tier parsing
  fine; Dragon Ball pages with no numeric stats at all) turned out to
  be the same root cause: these pages split AP/Speed/Durability/Lifting
  Strength/Striking Strength into a separate tabber, one tab per story
  key/arc, with no flat fallback - confirmed directly against the raw
  HTML for Genos, Vegeta, and Goku before writing any extraction logic.
  Not Dragon-Ball-specific either - the same pattern turned up on 8
  characters across the real DB once the fix landed (see "Multi-form
  characters" above and `parser.py`'s module docstring for the full
  design). `Piccolo` genuinely was a separate, simpler bug: a redirect
  the scraper wasn't following, fixed in `scraper.py` (`redirects=1`).
- **`db.get_form_counts()` and two small app.py helpers** support the
  Forms UI: `option_label()` now takes a `form_counts` dict (one query,
  cached, rather than loading every character's full JSON blob just to
  badge a handful of them), and `_prune_stale_form_selections()` runs
  every rerun to drop session-state keys for characters no longer
  selected (see "Multi-form characters" above).
- **"Refresh character list" sidebar button.** `load_characters()`/
  `load_form_counts()` are cached per-process (`st.cache_data`), which
  is invisible as long as every DB write goes through the sidebar's own
  "Add a character" flow - it already clears both caches after a
  successful add. It breaks the moment a character gets added any other
  way (a `batch_scrape.py` run, a one-off script like adding Silver
  Surfer/Superboy-Prime individually - see below) while a Streamlit
  session is already running: the new row is confirmed in the DB but
  invisible in the character selector, because the long-running process
  never knew the DB changed. Found exactly this way in practice - a
  session that had been up since the day before didn't show two
  characters added that morning. The fix is a button that runs the
  *exact same* two `.clear()` calls the add-flow already does, exposed
  on demand rather than automatically, with the same selection-
  preservation logic (re-asserting `character_selector` into
  session_state afterward, since clearing the cache changes the
  multiselect's `options` identity and would otherwise silently drop
  the current selection - same Streamlit quirk noted above).

## "Who would win" calculator (Phase 5)

`calculator.py` estimates a winner between two characters (by DB id or
exact name - name lookups that collide across source pages, e.g.
multiple `"Son Goku"` variants, raise and list the ids to disambiguate
rather than guessing). Built as a CLI plus a set of pure, testable
functions (`compare_forms`, `select_form`, `ability_flags`) with no
DB/Streamlit dependency of their own, then wired into `app.py` as a
follow-up (see "Who would win?" below) - same pattern as the
multi-form UI being a follow-up to the multi-form data model.

**Form selection**: an explicit form name can be passed per character;
if omitted, `select_form()` defaults to the form with the highest
`tier.baseline` (forms with no Tier score at all sort last).
Deliberately not "the fairest form for a matchup" - that has no
objectively correct answer, so it isn't guessed at.

**Weighting** (reviewed and signed off before implementation, same as
the original tier ladder scale): Attack Potency 35%, Durability 25%,
Speed 25%, Tier 15%. Tier is intentionally the lowest weight - VS
Battles Wiki defines Tier *as a function of* AP and Durability, so
weighting all three equally would partly double-count the same signal.
Each axis's raw delta (in that axis's own log10 units) is squashed
through `tanh(delta / 5.0)` into a bounded `[-1, +1]` "advantage"
before weighting, so an absurdly lopsided axis (a 40-order-of-magnitude
Tier gap) saturates toward the edge instead of mathematically drowning
out the other axes just by having more zeros.

**Missing stats**: baseline first, falling back to peak if baseline is
`None` (flagged in the breakdown as peak-based, not silently swapped
in). If neither side has *either* value for an axis, that axis is
excluded and its weight is redistributed proportionally across the
axes that do have data - explicitly not treated as a tie, since a
missing stat is absence of evidence, not evidence of parity. Fewer
than 2 comparable axes between the two characters returns an
`"Insufficient data"` verdict outright rather than a confident-looking
guess from one stat. With 2-3 axes, a verdict still computes but is
capped below the top confidence band ("Overwhelming favorite" is
unreachable off partial data - see `test_partial_data_caps_confidence_
below_overwhelming` in `test_calculator.py`).

**Confidence phrasing** is a fixed lookup from the weighted composite
score to a label (`"Too close to call"` → `"Overwhelming favorite"`),
each printed with a percentage-style hint (`"~65%"`) *and* an explicit
disclaimer baked into the CLI output itself - `"Heuristic estimate
from normalized stats - not a calibrated win probability"` - rather
than only living in this README where a caller of the bare function
might never see it.

**Ability/weakness flags are score-free by design.** A curated list of
~12 keywords (Regeneration, Immortality, Reality Warping, Acausality,
Non-Corporeal, BFR, Existence Erasure, Petrification, Durability
Negation, One-Hit-Kill/Instant Death, Probability Manipulation,
Resistance) is scanned case-insensitively against each character's
`powers_and_abilities` + `weaknesses` text and surfaced as flags -
which character, which tag - in their own clearly-separated output
section, alongside the full raw text. **They never touch the numeric
composite.** A keyword match can't tell "Low-Godly regeneration" from
a one-off scratch-heal, doesn't know if the opponent's kit already
counters it, and isn't scoped to the specific form chosen for the
numeric comparison (abilities/weaknesses live only on the flat
top-level character record - see `parser.py`'s `CharacterForm`, which
carries no textual fields at all) - so treating a match as a score
bump would be pretending to a precision this system doesn't have.
Considered and rejected: a keyword-triggered numeric modifier (would
silently move a printed confidence number off a substring match) and a
plain text-dump with no structure at all (honest, but throws away
information a reader would otherwise have to re-derive every time).

**`is_omnipresent`** gets the same treatment as a missing stat when it
appears on the selected form - the Speed axis is excluded exactly as
if Speed had no value at all (Omnipresent is categorically not a point
on a numeric speed scale, so it earns no hidden bonus), and a note is
attached explaining that the exclusion doesn't capture what Omnipresent
actually means in a fight.

```bash
./venv/bin/python3 calculator.py "Saitama, \"Caped Baldy,\" \"The Abominable Fist That Turned Against God\"" "Vegeta III"
./venv/bin/python3 calculator.py 116 642 --form-a "Base" --show-abilities
./venv/bin/python3 -m pytest test_calculator.py -v   # or: ./venv/bin/python3 test_calculator.py
```

Tested against the required matchup range in `test_calculator.py`: a
close/fair fight (small deltas on all four axes → `"Slight edge"` or
below), a wildly lopsided one (`"Overwhelming favorite"`), sparse data
(a single shared axis, and a fully-unscored character, both correctly
returning `"Insufficient data"` rather than a guess), and a real
multi-form character - Genos (DB id 62) compared against a fixed
synthetic rival at his weakest form (`"Beginning of Series"`, no
Durability score) versus his strongest (`"Post-Elder Centipede"`) -
confirming the favored side actually flips between the two form
choices, not just the printed numbers.

## Manual character entry

`manual_entry.py` adds a character with no wiki page at all (e.g.
original fiction) - copy `characters/_template.yaml`, fill it in, run:

```bash
./venv/bin/python3 manual_entry.py characters/your_character.yaml
```

**Deliberately YAML, not interactive prompts or a Python dict to
edit.** This was reviewed before building, same as the calculator's
weighting scheme: prompts don't scale to ~13 flat fields plus a
variable-length abilities list plus optional nested forms without
turning into a long back-and-forth with no way to see the whole
picture, or edit a mistake without starting over. A Python dict avoids
a new dependency but means quoting and comma-matching for every string
and every list item - real ability text is full of parentheses and
commas (`"Regeneration (Mid; can recover from dismemberment)"`),
which is exactly what YAML's indented list syntax avoids needing to
escape. The one cost is a new dependency (`pyyaml`, added to
`requirements.txt`) - small and justified by how much friction it
removes for what's meant to be repeated, occasional use as a writing
project's cast grows.

**Bypasses `scraper.py`/`parser.py` entirely** (there's nothing to
fetch or parse) but builds the *exact same* `CharacterStats`/
`CharacterForm` objects `parser.parse_character()` would - stat fields
are the same raw wiki-format strings (`"7-B"`, `"Massively
Hypersonic+"`), fed through the same `normalizer.py` ladder, so a
manually-entered character's score means the same thing as a scraped
one's rather than a hand-picked number that might not line up with the
ladder's actual scale. `forms:` is optional in the YAML and maps
directly onto the same `CharacterForm` list multi-form wiki characters
use (see "Multi-form characters" above) - omit it for an ordinary
single-form character and `manual_entry.py` synthesizes the same single
`"Base"` form `parser.py` would, so nothing downstream (`app.py`,
`calculator.py`) needs to know or care that this character didn't come
from a scrape.

**`source_url`** (required non-null and unique by `db.py`'s schema,
but there's no real URL) is synthesized as `manual://<category-slug>/
<name-slug>` - obviously non-wiki at a glance, and unique the same way
a real URL would be as long as (category, name) pairs don't collide.

Verified end-to-end, not just unit-tested: ran a filled-in two-form
example through `manual_entry.py`, confirmed the DB row's normalized
scores matched the ladder exactly (`"City level"` → 15.6, `"Continent
level"` → 23.0 - not approximations), clicked "Refresh character list"
(see above) and confirmed it appeared in the picker with its category
badge and multi-form "· 2 forms" tag with zero special-casing needed,
then ran a real "Who would win?" matchup against a scraped character
(Garou) and confirmed a live verdict, stat breakdown, and ability
flags (its "Regeneration" ability text was correctly flagged) came
back exactly like any wiki-sourced pairing.

## Web UI (Phase 6)

Replaces the Streamlit app (`app.py`, kept but no longer used) with a
real frontend matching a finalized visual design - a FastAPI backend
in front of the exact same `db.py`/`normalizer.py`/`calculator.py`
every earlier phase already built, and a plain HTML/CSS/JS frontend
with no framework and no build step. The architecture itself was
proposed and signed off before any code was written, same process as
the tier ladder scale and the calculator's weighting scheme.

### Why FastAPI, why no frontend framework

**FastAPI**, because it makes "verify before any frontend exists"
free: its auto-generated `/docs` Swagger UI lets every endpoint be
exercised by hand in a browser (used exactly this way for Stage (a),
before `frontend/` had a single file), and Pydantic response models
mean a malformed response is an error at request time, not a silent
bug the frontend has to guess at. The backend's job is deliberately
narrow - `backend/main.py` packs existing dataclasses/dicts into typed
responses; it does not compute anything `calculator.py` doesn't
already compute. The one exception, and it's presentational rather
than a numeric decision: the radar chart's per-axis scaling and the
ability-flag "dot" markers are frontend-only math, described below.

**Plain HTML/CSS/JS**, because the actual surface area doesn't need
more: two screens (Browse, Compare), a handful of interactive elements
(search, category pills, form pills, add-to-comparison), and state
simple enough for plain JS variables + DOM updates - nothing here
benefits from a framework's data-binding. `frontend/browse.html`/
`compare.html` are real, separate pages linking to each other with
plain `<a href>`/`window.location`, same navigation model the design
mockup itself used. Passing which two characters to compare from
Browse to Compare is a URL param (`compare.html?a=<id>&b=<id>`) rather
than any shared client state.

### Running it

```bash
./venv/bin/uvicorn backend.main:app --reload
```

One process, one port, same-origin (FastAPI mounts `frontend/` as
static files under `/`, so there's no CORS to configure). `/docs` for
the raw API, `/browse.html` and `/compare.html` for the UI.

### API surface (`backend/main.py`)

All five endpoints are thin wrappers - see `backend/schemas.py` for
the full typed shapes, which mirror `NormalizedForm`/`CharacterForm`/
`calculator.Verdict` field-for-field rather than inventing a second
data model:

- `GET /api/categories` - names + counts, for Browse's filter pills
- `GET /api/characters?q=&category=` - search/filter list (wraps
  `get_all_characters()`; the Tier badge per card reuses
  `calculator.select_form()` directly rather than re-deriving "which
  form is the highest tier" a second time)
- `GET /api/characters/{id}` - full detail, `forms[]` merging each
  `CharacterForm`'s raw text with its matching `NormalizedForm`'s
  scores (raw for display, normalized for the radar/bars)
- `POST /api/characters/fetch` - the live scrape-and-add flow
  (`scraper.fetch_page` → `parse_character` → `normalize_character` →
  `db.upsert_character`), identical to the Streamlit sidebar's
  version, including the `category = stats.origin or "Uncategorized"`
  fallback
- `POST /api/compare` - wraps `calculator.compare_characters()`
  directly; a `ValueError` (ambiguous name, unknown form) becomes a
  400 with `calculator.py`'s own message verbatim, not a re-written one

### Design fidelity

The mockup (`Browse.dc.html`/`Main.dc.html`, a design-tool export, not
production code) was rendered live and compared pixel-for-pixel
against the real implementation via computed styles, not eyeballed -
colors, border radii, and fonts all matched exactly on the first pass
(`rgb(29,26,21)` = `#1D1A15`, etc.), expressed as CSS custom properties
in `styles.css` rather than copied as inline styles. Fraunces for
names/headlines, Public Sans for body text, IBM Plex Mono for anything
numeric. Six accent colors, assigned deterministically per character
id (`api.js`'s `accentFor()`) so the same character always gets the
same color across screens and reloads.

One real gap found *after* the CSS already matched exactly: the
perceived difference turned out to be content, not style - Browse's
default (no search, no filter) view was dumping the full ~1,100-
character roster in raw alphabetical order, an overwhelming wall of
obscure names instead of the mockup's tidy, complete 12-card grid.
Fixed by capping only that specific default state at 12 (matching the
mockup exactly, zero scrolling) with an explicit "Showing 12 of
N - search or pick a category to see the rest, or **show the full
roster**" note, rather than silently hiding data. Search and category
filters were never capped - confirmed live (searching "Son Goku"
correctly returns all 9 real variants, uncapped).

### Browse screen

Fetches the full character list **once** (a ~146KB response for
~1,100 characters - trivial to hold in memory) and does all search/
category filtering **client-side**, so typing is instant rather than a
network round-trip per keystroke. Comparison staging caps at **2**
characters (a toast explains the cap rather than silently refusing a
3rd), superseding the Streamlit version's 4-character *chart* cap -
see "Why 2, not 4" below. "Add a character" is a toggleable panel in
the top bar wired to `POST /api/characters/fetch`; on success it
re-fetches both the character and category lists (not just splices the
one row in), so a brand-new category shows up as a new filter pill
immediately, same as a fresh page load would show.

### Why 2, not 4

The Streamlit app compared up to 4 characters on the chart, but the
*verdict* was always pairwise underneath (`calculator.compare_forms`
has no multi-way "who wins a 4-person fight" logic, and never did).
The finalized design is built around a pairwise layout end to end - a
literal "vs" divider, left/right card symmetry, a 2-column stat table,
a verdict phrased as one binary outcome - with no natural extension to
3-4 without becoming a different design. Decided explicitly (not
assumed) before building: match the design's actual 2-character scope,
formally retiring the old 4-character cap rather than bolting a second,
unmocked UI mode onto it.

### Compare screen

Reads `?a=<id>&b=<id>`, defaults each character to its highest-tier
form (`defaultFormIndex()` in `compare.js`, mirroring
`calculator.select_form()`'s own rule exactly so the initial verdict
matches what's displayed), and re-runs `POST /api/compare` with the
explicit form names on every pill click - confirmed live that
switching Genos from his strongest form to his weakest flips the
displayed verdict from "Clear favorite" to a materially different
composite, not just re-labeled numbers.

**Radar chart and stat bars** use a **per-axis, per-matchup relative
scale** - for each axis, the stronger of the two characters reaches
~100% and the weaker sits proportionally between that and a 15% floor
(never fully collapsed to the center, so a real value stays visible
even when heavily outmatched). This is deliberately *not* an absolute
scale against the ladder's full range (a Street-level vs. Street-level
matchup would otherwise render as two invisible dots near the origin)
- captioned honestly on the chart itself ("Scaled per-axis for this
matchup, not an absolute scale") rather than left implicit. A `null`
value (stat excluded, same rule as `calculator.py`'s own axis
exclusion) plots at the center for that side, never fabricated as
zero-but-different-from-missing.

**Ability pills get a small colored dot** when their own text contains
one of `calculator.ABILITY_TAGS`' keywords - this is a decorative,
frontend-only heuristic (`ABILITY_KEYWORDS` in `compare.js`), *not* a
re-derivation of which tags `calculator.ability_flags()` actually
matched. The verdict panel's amber caveat box (built straight from the
real `ability_flags` API response) is the authoritative source; the
dot is just a visual pointer toward *why*, and can't be more precise
than that without the backend telling it which pill triggered which
tag, which it doesn't and isn't asked to.

**Verdict panel** preserves every required behavior: the partial-data
callout ("3/4 stats were comparable, so this can't reach the top
confidence band") when `partial_data` is true, a distinct "Insufficient
data" layout (no meter, no reasoning bullets, just the honest
axes-used count) when `composite` is `null`, the Omnipresent flag
handled as an excluded axis rather than a hidden bonus, and the
"abilities aren't tracked per-form" note whenever ability flags are
shown - all read straight from `VerdictOut.notes`/`.partial_data`
rather than re-decided in the frontend. The reasoning bullets
themselves (e.g. "Attack Potency strongly favors X") *are* new,
frontend-only prose, generated from each axis's real `advantage`
magnitude/sign (`reasonBullets()` in `compare.js`) - legitimate
presentation logic, not a second scoring system.

**One real bug found and fixed while testing live**: Garou's full name
(with all his aliases, `"Garou, Hero Hunter, Human Monster, Awakened
Garou, ..."`) wrapped across multiple lines in the radar chart's
legend, since that element - unlike the character-card names - had no
`overflow`/`white-space` constraint, inflating the whole radar card's
height well past the stat table beside it. Fixed with the same
ellipsis/truncation treatment already used elsewhere.

Tested live at every stage (Browse search/filter/staging/add-character,
Compare across a multi-form pairing, a single-form near-tie, and a
genuine insufficient-data pairing), not just read over - see the
per-stage descriptions above for exactly what was checked.
