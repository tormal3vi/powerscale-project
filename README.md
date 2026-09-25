# Powerscale

**Who would win?** A site for settling fictional fights: pick any two of
2,300+ characters from anime, comics and games and get a verdict built from
the stats on [VS Battles Wiki](https://vsbattles.fandom.com), with the
reasoning shown.

**Live:** https://powerscale.online

## What you can do

- **Browse** 2,348 characters from 34 series, with pictures, search (names,
  nicknames, accents ignored), series filters and sorting by strength.
- **Compare** two characters: their Tier, Attack Potency, Speed and
  Durability side by side, a radar chart, and a verdict ("Kratos favored —
  Overwhelming favorite") with the reasons behind it. Characters with
  several forms (Base, Bankai, Gear 5th, Comics vs TV versions…) can be
  compared form by form. Every matchup has its own shareable link, with a
  link preview.
- **Character pages** with every form's stats, powers and weaknesses.
- **Tournaments:** seed 8 or 16 characters and watch the bracket play out.
- **The Board:** a small message board. Post takes, attach a matchup (with
  a live verdict preview), like, and reply.
- **Accounts:** profile picture, bio and favorite character, which show next
  to your posts.
- **Admin overrules:** when the calculator gets a fight wrong, an admin can
  overrule it for that exact pair of forms. The ruling shows on the matchup
  and is posted to the Board automatically.

## How a verdict works

1. **Stats come straight from the wiki.** Each character page's "Powers and
   Stats" section is fetched and parsed: Tier, Attack Potency, Speed and
   Durability for every form.
2. **Words become numbers.** Phrases like "Large Building level", "6-B" or
   "Massively FTL+" are mapped onto one scale per stat (roughly logarithmic,
   so each tier step means a lot more power). A stat written as a range
   ("9-B, up to 7-C with X") is scored at its lower end, and the higher end
   is shown as "up to …".
3. **The four stats are compared** with weights, and the combined edge
   becomes a verdict from "Too close to call" to "Overwhelming favorite".
   A verdict needs at least two stats the wiki actually rates. Many support
   characters are listed as "Unknown".
4. **Abilities are flagged, not scored.** Hax like time stop or soul
   manipulation is listed alongside the verdict, because no fair number
   exists for it.

A few scoring rules were chosen on purpose:

- Casters are scored by their magic ("Street level physically, Island level
  with magic" counts as Island level).
- The same goes for reality-warping abilities (Reality Overwrite, Plot
  Manipulation, Wish Granting…).
- JoJo Stand users are scored by their Stand.
- Omnipresence counts as the fastest possible speed.
- Transformations and one-off finishing moves don't raise the baseline.

## Series included

Dragon Ball (297) · One Piece (209) · Fairy Tail (196) · Naruto (181) ·
JoJo's Bizarre Adventure (152) · Bleach (139) · My Hero Academia (126) ·
One-Punch Man (122) · Puella Magi (85) · Baki the Grappler (81) ·
Invincible (80) · God of War (79) · Re:Zero (77) · Hunter x Hunter (75) ·
Black Clover (61) · Devil May Cry (52) · Jujutsu Kaisen (51) · Demon Slayer
(45) · Yu Yu Hakusho (42) · Overlord (38) · Metal Gear (34) · Chainsaw Man (33) · Attack on
Titan (20) · Mob Psycho 100 (17) · Hellsing (14) · Tengen Toppa Gurren
Lagann (14) · Frieren (11) · Despicable Me (4) · Lord of the Mysteries (4) ·
Mushoku Tensei (3) · Marvel (2) · Zoolander (2) · DC (1) · Solo Leveling (1)

## How it's built

- **Backend:** Python, [FastAPI](https://fastapi.tiangolo.com). The character data
  is a SQLite file shipped with the code (`powerscale.db`). Accounts, posts
  and overrules live in Postgres ([Neon](https://neon.tech)) via SQLAlchemy,
  or a local SQLite file when no database is configured.
- **Frontend:** plain HTML, CSS and JavaScript (no framework), in `frontend/`.
- **Scraping:** pages are fetched through the wiki's MediaWiki API (`api.php`),
  which its `robots.txt` allows, at no more than one request per 1.5 s, and
  cached locally. Parsing uses BeautifulSoup.
- **Pictures:** character pictures are loaded from the wiki's image server,
  with no referrer (it blocks hotlinked thumbnails otherwise). Profile
  pictures are re-encoded server-side (Pillow) into small WebP squares.
- **Hosting:** [Render](https://render.com) free tier (`render.yaml`).

| Piece | Where |
|---|---|
| Fetching wiki pages | `scraper.py`, `category_fetcher.py` |
| Parsing "Powers and Stats" | `parser.py` |
| Turning stat text into scores | `normalizer.py` |
| The verdict | `calculator.py` |
| Character data | `db.py`, `powerscale.db` |
| API, accounts, board, overrules | `backend/` |
| Pages | `frontend/` |

## Running it locally

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
ADMIN_USERNAMES=yourname ./venv/bin/uvicorn backend.main:app --port 8000
```

Then open http://localhost:8000 and create an account called `yourname`
to be an admin. Without `DATABASE_URL`, accounts and posts go into a local
`community.db` file.

### Adding a series

```bash
./venv/bin/python batch_scrape.py "One Piece" --dry-run   # what would be added
./venv/bin/python batch_scrape.py "One Piece"             # add it
```

The argument is a VS Battles Wiki category name. Characters without a wiki
page can be added by hand from a YAML file, as described in
`characters/_template.yaml` and `manual_entry.py`.

### Tests

```bash
for t in test_parser test_normalizer test_calculator test_manual_entry test_community; do ./venv/bin/python $t.py; done
./venv/bin/python test_scraper.py   # needs internet: talks to the live wiki
```

## Deploying

`render.yaml` describes the service. Two settings are entered in Render's
dashboard and never committed:

- `DATABASE_URL`: the Postgres connection string.
- `ADMIN_USERNAMES`: comma-separated usernames with admin rights.

New database columns are added automatically on startup.

## Credits

All character stats and pictures come from
[VS Battles Wiki](https://vsbattles.fandom.com) and its editors. Wiki text is
under [CC BY-SA](https://www.fandom.com/licensing), and the pictures belong to
their respective owners. This is an unofficial fan project, not affiliated
with Fandom or any of the franchises above.

For how everything was designed and the bugs found along the way, see the
[development log](docs/DEVELOPMENT.md).
