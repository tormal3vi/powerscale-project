"""Parser robustness tests against real character pages with
deliberately different infobox layouts:

- Saitama: Powers and Abilities as a bulleted <li> list nested inside
  tabs for different training stages, plus non-field table sections
  ("Feats", "Notable Attacks/Techniques") sitting right after
  Weaknesses that must NOT bleed into it. Also genuinely multi-form -
  see the flat-key bullet below.
- Kirby: the largest/most complex page tested (~900KB of HTML), Powers
  and Abilities as a huge nested-tab bulleted list across many forms.
  Also genuinely multi-form via the same flat-key pattern as Saitama -
  found as a side effect of that fix rather than looked for
  deliberately, once it was clear the bug wasn't Saitama-specific.
- Flameskull: a shorter page with a genuinely missing field
  (Classification) and Powers and Abilities as flat inline
  comma-separated text with no <li> bullets at all.
- Promoted Rook: found by batch-scraping Category:One-Punch_Man in
  Phase 3 - its section heading is literally "Power and Stats"
  (singular "Power"), not the usual "Powers and Stats", which the
  parser originally missed entirely (it was skipped as "no stats
  found"). Kept as a regression test for that heading variant.

Flameskull and Promoted Rook are single-form pages (Attack Potency/
Speed/Durability etc. are flat top-level fields) - each also gets a
`forms` assertion (exactly one synthetic "Base" form matching the flat
fields) to confirm the multi-form work below didn't disturb the common
case.

- Genos, Vegeta (Dragon Ball Z), Goku (Dragon Ball Z): genuine
  multi-form pages, found while diagnosing why their Attack Potency/
  Speed/Durability came back None despite Tier parsing fine (Phase 4
  follow-up). Each splits those fields into a separate tabber, one tab
  per story arc/key, with no flat fallback at all - Tier stays a flat
  '|'-separated summary across all arcs. All three were checked
  directly against the raw HTML before writing any parsing logic: tab
  count, Key-field count, and Tier's pipe-segment count all matched
  exactly for all three (6, 7, and 8 respectively), in the same
  chronological order - confirmed here as a regression check per form,
  not assumed.
- Denji (Chainsaw Man): found while diagnosing why an entire batch of
  Chainsaw Man characters got "Insufficient data" verdicts from
  calculator.py - Tier parsed fine (it's a flat field outside the
  tabber) but Attack Potency/Speed/Durability came back None for every
  form. Root cause: this page's stat tabber wraps each tab's stat <p>'s
  in an extra `<div class="scrollable">` layer that `_find_stats_tabber`/
  `_extract_forms_from_stats_tabber` only ever looked past when they
  were direct children - confirmed against the raw HTML (Genos' same
  fields are direct children; Denji's are one level deeper inside that
  wrapper) before fixing it.
- Frieren (Frieren: Beyond Journey's End): a third, different root
  cause behind the same "no verdict" symptom - her "Powers and
  Abilities" section is itself tabbed, and the whole rest of the flat
  stat block sits inside that same tabber div as plain <p> siblings
  after its tabs, previously invisible to the ordinary heading-sibling
  walk in `_collect_field_blocks`. Same DOM family as Chainsaw Man's
  `Reze`/`Samurai Sword` (found and reported, but not fixed, while
  diagnosing the Denji bug above - deferred as a narrow 2-character
  case at the time, then fixed generally here once it recurred on a
  second, unrelated category). Single-form, not multi-form - confirms
  the fix doesn't misfire on an unrelated ability-category tabber.
- Saitama again, this time for `_extract_forms_from_flat_key`: found
  from a user question ("why does Saitama show as Wall level?") - his
  page has no stats tabber at all; every stat field packs all 4 of his
  training-arc forms into one flat '|'-separated string, named by a
  parallel "Key:" field. Without this, the whole multi-segment string
  collapsed into a single synthetic "Base" form, and normalizer just
  grabbed the lowest tier token anywhere in it - his early, 12-year-old
  "Wall level" stage - as if it were his only/current power level.
  Unlike the tabber pages above, the flat top-level fields (tier,
  attack_potency, ...) stay populated here rather than None, since
  they're genuinely flat <p> fields on this page, not tabber-only ones.
  Kirby turned out to have the exact same bug (see above) - checked
  once this fix was confirmed general, not assumed from Saitama alone.

Run with: ./venv/bin/python3 -m pytest test_parser.py -v
(or plain: ./venv/bin/python3 test_parser.py)
"""

from pathlib import Path

from parser import parse_character

FIXTURES_DIR = Path(__file__).parent / "test_fixtures"


def _load(name: str) -> str:
    return (FIXTURES_DIR / f"{name}.html").read_text(encoding="utf-8")


def test_saitama_core_fields():
    stats = parse_character(_load("Saitama"))
    assert stats.name.startswith("Saitama")
    assert stats.origin == "One-Punch Man"
    assert stats.gender == "Male"
    assert "9-B" in stats.tier
    assert len(stats.powers_and_abilities) > 10
    # tab-header chrome must not leak into the ability list
    assert not any(a in ("Base", "Post-Balding ▾", "Pre-/During Training ▾") for a in stats.powers_and_abilities)


def test_saitama_weaknesses_does_not_absorb_unrelated_tables():
    stats = parse_character(_load("Saitama"))
    assert "Notable Attacks/Techniques" not in stats.weaknesses
    assert "STRENGTH:" not in stats.weaknesses
    # that content should show up as its own extra field instead
    assert "Notable Attacks/Techniques" in stats.extra_fields
    assert "Feats" in stats.extra_fields


def test_kirby_handles_a_large_multi_form_page():
    stats = parse_character(_load("Kirby"))
    assert stats.name.startswith("Kirby")
    assert stats.origin == "Kirby"
    assert len(stats.powers_and_abilities) > 50
    assert all(isinstance(a, str) and a for a in stats.powers_and_abilities)
    # "Standard Tactics" and "Note N" aren't in the standard field list
    assert "Standard Tactics" in stats.extra_fields
    # Also a flat-key multi-form page (Key: "Pre-Canvas Curse |
    # Pre-Star Allies | Post-Star Allies", Tier: "5-A | 2-C | 2-C") -
    # found as a side effect of the Saitama fix rather than looked for
    # deliberately: same bug class, caught here for free.
    assert len(stats.forms) == 3
    assert [f.name for f in stats.forms] == ["Pre-Canvas Curse", "Pre-Star Allies", "Post-Star Allies"]
    assert all(f.stats.attack_potency for f in stats.forms)
    assert "Large Planet level" in stats.forms[0].stats.attack_potency
    assert "Low Multiverse level" in stats.forms[-1].stats.attack_potency


def test_flameskull_missing_field_and_inline_abilities():
    stats = parse_character(_load("Flameskull"))
    assert stats.name == "Flameskull"
    assert stats.tier == "9-A"
    # Classification genuinely isn't on this page - must be None, not crash
    assert stats.classification is None
    # no <li> bullets here, so the parser must split flat inline text
    assert len(stats.powers_and_abilities) > 5
    assert "Superhuman Physical Characteristics" in stats.powers_and_abilities
    _assert_single_default_form(stats)


def test_promoted_rook_singular_power_and_stats_heading():
    # Regression test: this page's heading is "Power and Stats", not
    # "Powers and Stats" - originally caused the whole section to be
    # missed (surfaced by batch-scraping Category:One-Punch_Man).
    stats = parse_character(_load("PromotedRook"))
    assert stats.name == "Promoted Rook"
    assert stats.tier == "6-B"
    assert stats.origin == "One-Punch Man"
    _assert_single_default_form(stats)


def test_son_goku_classic_toei_colon_outside_bold_tag():
    # Found by a user question ("why is 23rd Tournament's Attack Potency
    # unscored?") - this page's stat labels are usually "<b>Label:</b>
    # value", but 5 of its 7 forms write Attack Potency specifically as
    # "<b>Attack potency</b>: value" instead - the colon sits just
    # outside the bold tag. Not a one-off typo: confirmed systemic
    # across this page (and, checked against the whole cache/, 4 other
    # already-scraped characters - Garou, Minato Namikaze, Power) before
    # generalizing the fix (_label_text() in parser.py) rather than
    # patching this one page.
    stats = parse_character(_load("SonGokuClassicToei"))
    assert len(stats.forms) == 7
    names = [f.name for f in stats.forms]
    assert "23rd Tournament" in names
    assert "Stamina" not in names  # regression guard for a variable-shadowing bug hit while fixing this
    assert all(f.stats.attack_potency for f in stats.forms), [f.name for f in stats.forms if not f.stats.attack_potency]


def test_hulk_powers_and_statistics_heading_variant():
    # Found adding Hulk individually: his heading is spelled out in full
    # as "Powers and Statistics", not "Powers and Stats"/"Power and
    # Stats" - a third heading variant _STATS_HEADING_RE didn't cover
    # (matching "...stats$" doesn't match "...statistics$" at all, a
    # different suffix, not a superset). Without the fix, the whole
    # section - and even stats.name - came back None, since
    # parse_character returns an empty CharacterStats when no heading is
    # found at all. Also genuinely multi-form (5 forms, his personas),
    # confirming the heading fix doesn't just help the flat-field path.
    stats = parse_character(_load("Hulk"))
    assert stats.name and "Bruce Banner" in stats.name
    assert len(stats.forms) == 5
    assert stats.forms[0].name == "Bruce Banner"
    assert all(f.stats.attack_potency for f in stats.forms)
    assert all(f.stats.speed for f in stats.forms)


def test_missing_powers_and_stats_section_degrades_gracefully():
    stats = parse_character("<html><body><h2>Unrelated Page</h2><p>No stats here.</p></body></html>")
    assert stats.name is None
    assert stats.tier is None
    assert stats.powers_and_abilities == []
    assert stats.extra_fields == {}
    # forms is still non-empty even in the total-failure case - one
    # synthetic "Base" form of all-None values, same as any other
    # single-form page.
    _assert_single_default_form(stats)


# --- forms: single-form consistency helper ----------------------------

def _assert_single_default_form(stats):
    """For an ordinary (non-tabbed) page: forms has exactly one entry,
    named "Base", whose values simply match the flat top-level fields -
    the flat fields are the canonical source for these pages, forms[0]
    is just the same data wrapped uniformly (see parser.py docstring)."""
    from parser import DEFAULT_FORM_NAME

    assert len(stats.forms) == 1
    form = stats.forms[0]
    assert form.name == DEFAULT_FORM_NAME
    assert form.tier == stats.tier
    assert form.stats.attack_potency == stats.attack_potency
    assert form.stats.speed == stats.speed
    assert form.stats.lifting_strength == stats.lifting_strength
    assert form.stats.striking_strength == stats.striking_strength
    assert form.stats.durability == stats.durability
    assert form.stats.stamina == stats.stamina
    assert form.stats.range == stats.range


# --- forms: multi-form pages -------------------------------------------

def test_genos_multi_form_extraction():
    stats = parse_character(_load("Genos"))
    # 6 keys, 6 tabs, 6 Tier pipe-segments - confirmed by direct HTML
    # inspection before writing the extraction logic.
    assert len(stats.forms) == 6
    names = [f.name for f in stats.forms]
    assert names == [
        "Beginning of Series",
        "House of Evolution to Alien Conquerors Arc",
        "Post-VGS",
        "Post-G4",
        "Post-Superfight",
        "Post-Elder Centipede",
    ]
    # Tier segment count matched the tab count, so every form gets its
    # own aligned Tier slice (not the identical flat string repeated).
    assert all(f.tier for f in stats.forms)
    assert len({f.tier for f in stats.forms}) == 6

    # Each form has its own Attack Potency; power rises across the story
    # (monotonic non-decrease from "Town level" up to "Mountain level").
    assert stats.forms[0].stats.attack_potency.startswith("Town level")
    assert "Mountain level" in stats.forms[-1].stats.attack_potency

    # Genos' Durability is only documented in his LAST form - the other
    # 5 tabs genuinely don't have it (wiki-side sparsity, not a bug) -
    # each form must handle that independently, not fall back to
    # another form's value.
    assert stats.forms[0].stats.durability is None
    assert stats.forms[-1].stats.durability is not None

    # The flat top-level fields stay None for a genuine multi-form page
    # - never collapsed to any one form's values.
    assert stats.attack_potency is None
    assert stats.durability is None
    assert stats.speed is None
    # Tier is the one exception: its flat '|'-separated summary is
    # still populated (that's where the per-form tiers were sliced from).
    assert stats.tier is not None


def test_vegeta_multi_form_extraction():
    stats = parse_character(_load("Vegeta"))
    assert len(stats.forms) == 7
    assert stats.forms[0].name == "Saiyan Arc"
    assert stats.forms[-1].name == "Majin Buu Arc"
    assert all(f.tier for f in stats.forms)
    assert len({f.tier for f in stats.forms}) == 7
    # every form has its own Attack Potency, Speed, Durability
    assert all(f.stats.attack_potency for f in stats.forms)
    assert all(f.stats.durability for f in stats.forms)
    assert stats.attack_potency is None


def test_goku_multi_form_extraction():
    stats = parse_character(_load("Goku"))
    assert len(stats.forms) == 8
    assert stats.forms[0].name == "Beginning of Z"
    assert stats.forms[-1].name == "Kid Buu Fight"
    assert all(f.tier for f in stats.forms)
    assert len({f.tier for f in stats.forms}) == 8
    assert stats.attack_potency is None


def test_saitama_flat_key_multi_form_extraction():
    # No stats tabber on this page at all (see module docstring) - Tier/
    # Attack Potency/Speed/Durability are each one flat <p> whose value
    # packs all 4 forms into a single '|'-separated string, named by a
    # parallel "Key:" field. Before _extract_forms_from_flat_key, this
    # collapsed into one synthetic "Base" form wrapping the whole
    # unsplit string, and normalizer picked up "Wall level" - his
    # earliest, weakest stage - as if it were his only Attack Potency.
    stats = parse_character(_load("Saitama"))
    assert len(stats.forms) == 4
    names = [f.name for f in stats.forms]
    assert names == ["Pre-Training", "During Training", "Post-Balding", "Parallel Timeline"]
    assert all(f.tier for f in stats.forms)
    assert len({f.tier for f in stats.forms}) == 4
    assert all(f.stats.attack_potency for f in stats.forms)
    assert all(f.stats.durability for f in stats.forms)
    assert "Wall level" in stats.forms[0].stats.attack_potency
    assert "Galaxy level" in stats.forms[2].stats.attack_potency
    # Unlike the tabber pages above, this page's flat top-level fields
    # are genuinely flat <p>'s (no tabber to hide them inside), so they
    # stay populated - just as the unsplit multi-segment string, since
    # nothing downstream reads them once forms exist (see module
    # docstring's "flat, whole-page ... summary is unaffected either way").
    assert stats.attack_potency is not None
    assert "|" in stats.attack_potency


def test_bambietta_per_field_tabber_and_shared_unsplit_field():
    # Found diagnosing a Bambietta-vs-Ainz verdict: Key "Alive | Zombie",
    # but two different things went wrong on this one page -
    # 1. Her Attack Potency value lives in its OWN small Alive/Zombie
    #    tabber under an empty "Attack Potency:" label, which plain
    #    get_text() mashed into "AliveZombieMulti-Continent level..."
    #    (no '|', so every form's AP came back None).
    # 2. Her Speed is a single unsplit value ("Massively Hypersonic"),
    #    the wiki's shorthand for "same for every form" - but the first
    #    version of _extract_forms_from_flat_key only trusted exact
    #    count matches and dropped it to None on every form instead.
    #    Checked across the whole DB: 349 multi-form characters had at
    #    least one field dropped this way.
    stats = parse_character(_load("BambiettaBasterbine"))
    assert [f.name for f in stats.forms] == ["Alive", "Zombie"]
    alive, zombie = stats.forms
    assert alive.stats.attack_potency.startswith("Multi-Continent level")
    assert zombie.stats.attack_potency.startswith("Country level")
    assert "AliveZombie" not in stats.attack_potency
    assert alive.stats.speed == zombie.stats.speed
    assert alive.stats.speed.startswith("Massively Hypersonic")


def test_ichigo_hybrid_tabber_page_fills_flat_fields_outside_the_tabs():
    # Stats tabber holds Attack Potency per tab, but Durability/Speed
    # live OUTSIDE the tabs as flat '|'-split fields - the tabber path
    # alone dropped them for all 8 forms (found noticing a Pre- vs.
    # Post-Timeskip Ichigo verdict only compared 2 of 4 stats).
    stats = parse_character(_load("IchigoPreTimeskip"))
    assert len(stats.forms) == 8
    assert all(f.stats.attack_potency for f in stats.forms)
    assert all(f.stats.durability for f in stats.forms)
    assert stats.forms[-1].stats.durability.startswith("Multi-Galaxy level")
    # Speed has 7 '|' segments for 8 forms - no safe way to line them
    # up, so it stays unscored rather than being guessed.
    assert not any(f.stats.speed for f in stats.forms)


def test_denji_multi_form_extraction_through_a_scrollable_wrapper():
    # Found while diagnosing why Chainsaw Man characters got "Insufficient
    # data" verdicts from calculator.py: Denji's page (and most of the
    # Chainsaw Man cast) wraps each tab's stat <p>'s in an extra
    # `<div class="scrollable">` layer the tabber-detection/extraction
    # code didn't look inside - Tier still parsed (it's a flat field
    # outside the tabber entirely) while Attack Potency/Speed/Durability
    # silently came back None for every form. Confirmed directly against
    # the raw HTML before fixing `_stat_paragraphs()` in parser.py.
    stats = parse_character(_load("Denji"))
    assert len(stats.forms) == 4
    assert stats.forms[0].name == "Pre-Training"
    assert stats.forms[-1].name == "Post-Fear Boost"
    assert all(f.tier for f in stats.forms)
    # The actual regression: every form must have real Attack
    # Potency/Speed/Durability text, not None from a missed <p>.
    assert all(f.stats.attack_potency for f in stats.forms)
    assert all(f.stats.speed for f in stats.forms)
    assert all(f.stats.durability for f in stats.forms)


def test_frieren_flat_fields_wrapped_in_an_unrelated_ability_tabber():
    # Found while batch-scraping Category:Frieren: Beyond Journey's End -
    # Frieren herself (the title character) got the same "no verdict"
    # symptom as the Chainsaw Man bug, but a different root cause: her
    # "Powers and Abilities" section is itself tabbed ("Powers and
    # Abilities" / "Resistances"), and the page wraps the *entire rest*
    # of the flat stat block (Attack Potency, Speed, Durability,
    # Weaknesses, ...) inside that same tabber div too, as plain <p>
    # siblings after its tabs - not nested in any tab, and one level
    # deeper than the ordinary heading-sibling walk looks. Previously
    # silently absorbed as unparsed raw HTML into whatever field was
    # open when the tabber was reached. Same underlying DOM shape as
    # Reze (see README's Chainsaw Man stress-test section) - confirmed
    # there first, fixed generally via `_labeled_paragraphs_in`.
    stats = parse_character(_load("Frieren"))
    assert len(stats.forms) == 1  # not multi-form - an ability-category tabber, not a per-form one
    assert stats.tier and "10-B" in stats.tier
    assert stats.attack_potency and "Human level" in stats.attack_potency
    assert stats.speed and "Average Human" in stats.speed
    assert stats.durability and "Human level" in stats.durability
    assert stats.weaknesses and "mana detection" in stats.weaknesses
    # The ability list itself must stay clean - no stray stat-field text
    # leaking in from the same tabber the fix now reaches into.
    assert len(stats.powers_and_abilities) > 10
    assert not any(a.startswith("Attack Potency") for a in stats.powers_and_abilities)


if __name__ == "__main__":
    import sys

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
