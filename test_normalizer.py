"""Normalizer tests: the tier/speed/AP/durability strings actually
present in the 3 existing parser fixtures (Saitama, Kirby, Flameskull),
hand-built edge cases the fixtures don't happen to cover:

- "Low 1-C" alone (a Low-graded tier with no pipe/comma at all)
- three pipe-separated values in one string
- a tier string with no qualifier word at all
- a string with no recognizable token (graceful fallback, no crash)

...and regressions for the 5 vocabulary-gap fixes decided after the
Phase 4 sweep of Baki the Grappler / Fairy Tail / Puella Magi Verse
(see vocab_gaps_report.txt): 2 typo aliases, the "Human level" family
aliased to 10-C, the "Speed of Light" anchor, the "Omnipresent" flag
(no numeric score), and the "Infinite" -> "Infinite Speed" alias.

...and multi-form normalization (Phase 4 follow-up): NormalizedStats.forms
is always populated, mirroring CharacterStats.forms - one synthetic
NormalizedForm for ordinary characters (checked for exact agreement
with the top-level fields, since forms[0] is supposed to just be the
same data wrapped uniformly), or one per story arc/key for genuine
multi-form characters (Genos, Vegeta, Goku).

Run with: ./venv/bin/python3 -m pytest test_normalizer.py -v
(or plain: ./venv/bin/python3 test_normalizer.py)
"""

from pathlib import Path

from parser import CharacterStats, parse_character
from normalizer import normalize_character, parse_tier_range, parse_speed_range, TIER_LADDER, SPEED_LADDER

FIXTURES_DIR = Path(__file__).parent / "test_fixtures"


def _load_stats(name: str):
    html = (FIXTURES_DIR / f"{name}.html").read_text(encoding="utf-8")
    return parse_character(html, source=name)


# --- fixture-driven tests --------------------------------------------------

def test_saitama_tier_range_spans_weakest_to_strongest_form():
    stats = _load_stats("Saitama")
    r = parse_tier_range(stats.tier)
    assert r.baseline_label == "9-b"
    assert r.peak_label == "3-c"
    assert r.baseline < r.peak
    assert r.peak_qualifier == "possibly"


def test_saitama_speed_picks_up_pipe_separated_forms():
    stats = _load_stats("Saitama")
    r = parse_speed_range(stats.speed)
    assert r.baseline_label == "superhuman"
    assert r.peak_label == "massively ftl+"
    assert r.baseline < r.peak


def test_kirby_tier_three_pipe_values_and_ap_durability_share_the_tier_ladder():
    stats = _load_stats("Kirby")
    tier = parse_tier_range(stats.tier)          # "5-A | 2-C | 2-C"
    ap = parse_tier_range(stats.attack_potency)   # "Large Planet level ..."
    assert tier.baseline_label == "5-a"
    assert tier.peak_label == "2-c"
    # AP's peak ("Low Multiverse level") must equal Tier's peak ("2-C") -
    # same rank, same shared ladder, different vocabulary.
    assert ap.peak == tier.peak


def test_flameskull_missing_classification_does_not_affect_normalization():
    stats = _load_stats("Flameskull")
    assert stats.classification is None  # Phase 1 behavior, unaffected here
    tier = parse_tier_range(stats.tier)  # "9-A", no pipe, no qualifier
    assert tier.baseline_label == "9-a"
    assert tier.baseline == tier.peak
    assert tier.baseline_qualifier is None


def test_flameskull_speed_extracts_two_tokens_joined_by_prose():
    # "Hypersonic+ with High Hypersonic+ reactions" - no pipe or comma
    # separates these; the tokenizer must still find both.
    stats = _load_stats("Flameskull")
    r = parse_speed_range(stats.speed)
    assert r.baseline_label == "hypersonic+"
    assert r.peak_label == "high hypersonic+"
    assert r.baseline < r.peak


def test_normalize_character_end_to_end():
    stats = _load_stats("Saitama")
    normalized = normalize_character(stats)
    assert normalized.name == stats.name
    assert normalized.tier.baseline is not None
    assert normalized.speed.peak is not None


def test_single_form_normalization_matches_top_level_exactly():
    # Consistency check across the genuinely single-form fixtures: forms
    # is exactly 1 entry, and every value on it - including
    # is_omnipresent - agrees with the corresponding top-level field.
    # forms[0] is supposed to be the exact same data as the flat fields,
    # just wrapped uniformly; this would catch any drift between the two
    # normalization paths. Saitama and Kirby used to be here too, but
    # both turned out to be genuinely multi-form pages using parser.py's
    # flat-key '|'-segment convention (see test_parser.py) - moved to
    # test_saitama_and_kirby_flat_key_forms_normalize_independently below.
    for name in ["Flameskull", "PromotedRook"]:
        stats = _load_stats(name)
        normalized = normalize_character(stats)
        assert len(normalized.forms) == 1, name
        form = normalized.forms[0]
        assert form.tier == normalized.tier, name
        assert form.attack_potency == normalized.attack_potency, name
        assert form.speed == normalized.speed, name
        assert form.durability == normalized.durability, name
        assert form.is_omnipresent == normalized.is_omnipresent, name


def test_saitama_and_kirby_flat_key_forms_normalize_independently():
    # Saitama and Kirby both use parser.py's flat-key '|'-segment
    # convention (no stats tabber - see test_parser.py), which produces
    # real per-form CharacterForms even though the top-level flat fields
    # also stay populated (unlike the tabber pages, where they're None).
    # This checks normalize_character() picks up each form's own slice
    # rather than reusing the flat top-level range for every form.
    saitama = normalize_character(_load_stats("Saitama"))
    assert len(saitama.forms) == 4
    assert [f.name for f in saitama.forms] == ["Pre-Training", "During Training", "Post-Balding", "Parallel Timeline"]
    # Power rises monotonically across his training arc.
    assert saitama.forms[0].attack_potency.baseline_label == "wall level"
    assert saitama.forms[2].attack_potency.baseline_label == "multi-solar system level"
    assert saitama.forms[0].attack_potency.baseline < saitama.forms[2].attack_potency.baseline
    assert saitama.forms[0].tier.baseline < saitama.forms[2].tier.baseline

    kirby = normalize_character(_load_stats("Kirby"))
    assert len(kirby.forms) == 3
    assert kirby.forms[0].tier.baseline < kirby.forms[-1].tier.baseline


# --- hand-built edge cases --------------------------------------------------

def test_single_low_graded_tier_no_pipe_no_comma():
    r = parse_tier_range("Low 1-C")
    assert r.baseline_label == "low 1-c"
    assert r.baseline == r.peak
    assert r.baseline_qualifier is None
    # Low 1-C must sit strictly between the previous tier (2-A) and
    # plain 1-C - tier ordering runs ... < 2-A < 1-C < 1-B < 1-A.
    assert TIER_LADDER["2-a"] < r.baseline < TIER_LADDER["1-c"]


def test_three_pipe_separated_values():
    r = parse_tier_range("9-B | 8-A | Low 7-B")
    assert r.baseline_label == "9-b"
    assert r.peak_label == "low 7-b"
    assert r.baseline < r.peak


def test_no_qualifier_at_all():
    r = parse_tier_range("Wall level")
    assert r.baseline_label == "wall level"
    assert r.baseline_qualifier is None
    assert r.peak_qualifier is None


def test_unrecognizable_string_falls_back_gracefully_instead_of_crashing():
    r = parse_tier_range("Completely Unknown Nonsense Tier")
    assert r.baseline is None
    assert r.peak is None
    assert r.raw == "Completely Unknown Nonsense Tier"


def test_empty_and_none_input_do_not_crash():
    assert parse_tier_range(None).baseline is None
    assert parse_tier_range("").baseline is None


# --- vocabulary gap sweep fixes (Phase 4) -----------------------------------
# Real strings/characters from vocab_gaps_report.txt (sweep of Baki the
# Grappler, Fairy Tail, Puella Magi Verse), for the 5 fixes the user
# reviewed and decided on.

def test_typo_sub_relativistic_plus():
    # Jackal (Fairy Tail): "Sub-Relatvistic+ with Etherious Form"
    r = parse_speed_range("Sub-Relatvistic+ with Etherious Form")
    assert r.baseline_label == "sub-relatvistic+"
    assert r.baseline == SPEED_LADDER["sub-relativistic+"]


def test_typo_relavistic():
    # Tsukuyo Amane (Puella Magi Verse): "Relavistic attack speed with Cherry Blizzard"
    r = parse_speed_range("Relavistic attack speed with Cherry Blizzard")
    assert r.baseline_label == "relavistic"
    assert r.baseline == SPEED_LADDER["relativistic"]


def test_human_level_aliases_all_map_to_10c():
    # Romeo Conbolt (Fairy Tail): "Below Average Human level"
    r1 = parse_tier_range("Below Average Human level")
    assert r1.baseline == TIER_LADDER["below average level"]

    # King, One-Punch Man / Hitomi Shizuki (Puella Magi Verse): "Human level"
    r2 = parse_tier_range("Human level")
    assert r2.baseline == TIER_LADDER["below average level"]

    # Kosane Kiriha (Puella Magi Verse, durability field): "Average Human"
    r3 = parse_tier_range("Average Human")
    assert r3.baseline == TIER_LADDER["below average level"]

    # The SPEED ladder's own separate "Average Human" entry must be untouched.
    assert SPEED_LADDER["average human"] != TIER_LADDER["average human"]


def test_speed_of_light_anchor_and_variant():
    # Laxus Dreyar (Fairy Tail): "Speed of Light with Fairy Law"
    r = parse_speed_range("Speed of Light with Fairy Law")
    assert r.baseline_label == "speed of light"
    assert r.baseline == SPEED_LADDER["speed of light"]
    # Exact boundary: strictly above Massively Relativistic (~c, approaching
    # but not touching light speed), strictly below FTL.
    assert SPEED_LADDER["massively relativistic"] < r.baseline < SPEED_LADDER["ftl"]


def test_omnipresent_gets_flag_not_a_score():
    # Homura Akemi / Madoka Kaname (Puella Magi Verse): "Omnipresent"
    r = parse_speed_range("Omnipresent")
    assert r.baseline is None  # no ladder score - by design, not a bug
    assert r.peak is None
    assert "omnipresent" not in SPEED_LADDER

    stats = CharacterStats(name="Test Character", speed="Omnipresent")
    normalized = normalize_character(stats)
    assert normalized.is_omnipresent is True
    assert normalized.speed.baseline is None


def test_non_omnipresent_character_flag_is_false():
    stats = CharacterStats(name="Test Character", speed="Massively FTL+")
    normalized = normalize_character(stats)
    assert normalized.is_omnipresent is False
    assert normalized.speed.baseline is not None


def test_infinite_alias_for_infinite_speed():
    # Jeanne d'Arc (Puella Magi Verse): "Infinite"
    r = parse_speed_range("Infinite")
    assert r.baseline_label == "infinite"
    assert r.baseline == SPEED_LADDER["infinite speed"]


# --- vocabulary gap sweep fixes (batch2: Dragon Ball / JoJo's Bizarre Adventure) --
# Real strings from vocab_gaps_report_batch2.txt (sweep triggered by batch-scraping
# Dragon Ball and JoJo, run against already-cached DB data). Two decisions:
# 1. 10-A's canonical name renamed "Peak Human level" -> "Athlete level" to match
#    the wiki's current Tiering System/Attack Potency page naming; old name kept
#    as an alias, same score (2.8).
# 2. "Brown Dwarf level" (High 5-A) added as its own new anchor at 37.84 -
#    log10 of the wiki's published High 5-A lower bound (~6.906e37 J) - strictly
#    between 5-A "Large Planet level" (36.0) and 4-C "Star level" (41.8), same
#    anchoring method as the existing Moon/Sun/Earth entries. "Small Star level"
#    (Low 4-C, the next rung up) deliberately NOT added - not yet observed in
#    scraped data, same policy that gated adding Brown Dwarf itself.

def test_athlete_level_is_the_renamed_10a_canonical_name():
    r = parse_tier_range("Athlete level")
    assert r.baseline_label == "athlete level"
    assert r.baseline == TIER_LADDER["10-a"] == 2.8


def test_peak_human_level_still_works_as_an_alias():
    # Old canonical name for 10-A, kept as an alias after the rename - same score.
    r = parse_tier_range("Peak Human level")
    assert r.baseline == TIER_LADDER["athlete level"] == 2.8


def test_outerversal_aliases_to_outerverse_level():
    # Varga Kolos (Crimson Cross, manual entry): Attack Potency said
    # "Planet level (...); Outerversal (true, unexercised ceiling)" -
    # "Outerversal" is the adjective form of the existing 1-A anchor,
    # not a new concept. Without the alias, the peak silently collapsed
    # to the baseline ("Planet level") instead of reaching 1-A.
    r = parse_tier_range("Planet level (demonstrated baseline); Outerversal (true, unexercised ceiling)")
    assert r.baseline_label == "planet level"
    assert r.peak_label == "outerversal"
    assert r.peak == TIER_LADDER["outerverse level"] == 160.0


def test_bobby_jean_athlete_level_real_string():
    # Bobby Jean, Agent Bobby Jean (JoJo's Bizarre Adventure):
    # AP "Athlete level, Street level with USP-45 (...)"
    r = parse_tier_range("Athlete level, Street level with USP-45 (Firepower of bullets have an average of 705.45 Joules)")
    assert r.baseline_label == "athlete level"
    assert r.peak_label == "street level"
    assert r.baseline < r.peak

    # Durability: "Athlete level" alone
    r_dur = parse_tier_range("Athlete level")
    assert r_dur.baseline == r_dur.peak == TIER_LADDER["athlete level"]


def test_brown_dwarf_level_is_a_new_anchor_between_5a_and_4c():
    r = parse_tier_range("Brown Dwarf level")
    assert r.baseline_label == "brown dwarf level"
    assert r.baseline == TIER_LADDER["high 5-a"]
    assert TIER_LADDER["5-a"] < r.baseline < TIER_LADDER["4-c"]


def test_brown_dwarf_level_plus_grades_above_plain_and_stays_below_4c():
    r_plain = parse_tier_range("Brown Dwarf level")
    r_plus = parse_tier_range("Brown Dwarf level+")
    assert r_plain.baseline < r_plus.baseline < TIER_LADDER["4-c"]


def test_brocco_real_strings_resolve_via_code_and_name_forms():
    # Brocco, "Shorty" (Dragon Ball) - Tier given as the bare code "High 5-A",
    # AP/Durability given as the descriptive name "Brown Dwarf level+".
    tier = parse_tier_range("High 5-A")
    assert tier.baseline == tier.peak == TIER_LADDER["high 5-a"]

    ap = parse_tier_range('Brown Dwarf level+ (Superior to Garlic Jr.)')
    assert ap.baseline == ap.peak == TIER_LADDER["brown dwarf level+"]

    dur = parse_tier_range("Brown Dwarf level+")
    assert dur.baseline == dur.peak == TIER_LADDER["brown dwarf level+"]


def test_son_goku_toei_beginning_of_z_form_finds_brown_dwarf_amid_prose():
    # Son Goku (Toei)'s "Beginning of Z" form - real multi-form data with lots
    # of unrecognized prose ("Varies", "up to far higher with Kamehameha")
    # mixed in; the new anchor must still be found correctly amid the noise.
    tier = parse_tier_range("High 5-A, Varies, up to far higher with Kamehameha")
    assert tier.baseline == tier.peak == TIER_LADDER["high 5-a"]

    ap = parse_tier_range(
        "Brown Dwarf level+ (Overwhelmed Super Garlic. Jr after removing his weighted clothing alongside Piccolo), "
        "Varies (The Kamehameha works by condensing one's Ki into a single point), "
        "up to far higher with Kamehameha (Scared Raditz enough to force his to dodge)"
    )
    assert ap.baseline == ap.peak == TIER_LADDER["brown dwarf level+"]


def test_small_star_level_added_once_real_data_surfaced_it():
    # Low 4-C's real name per the wiki. Originally deliberately NOT added
    # (per explicit instruction) until it showed up in scraped data -
    # it had, 100 times across 17 characters, but never got flagged:
    # the vocab sweep only reports unrecognized text, and "Small Star
    # level" was silently matched as plain "Star level" (4-C) instead.
    # Now an alias for the ladder's automatic Low 4-C grade, not a new
    # anchor - same score "low 4-c" already had.
    assert TIER_LADDER["small star level"] == TIER_LADDER["low 4-c"]
    assert TIER_LADDER["high 5-a"] < TIER_LADDER["low 4-c"] < TIER_LADDER["4-c"]


def test_casters_are_scored_by_their_magic_value_not_physical():
    # User decision: a stat split into "X physically, Y with magic" is
    # scored by Y - the physical value isn't how a caster actually
    # fights. Real strings: Rudeus Greyrat, Ainz Ooal Gown (no
    # "physically" at all - only "with magic" marks the split), Gaara.
    rudeus = parse_tier_range("9-C physically, 6-C with magic")
    assert rudeus.baseline_label == "6-c"
    ainz = parse_tier_range("At least 9-A, Low 7-C with magic, higher with the Staff of Ainz Ooal Gown")
    assert ainz.baseline_label == "low 7-c"
    gaara = parse_tier_range("8-B physically, 8-A with Sand, higher with Partial Transformation")
    assert gaara.baseline_label == "8-a"
    ap = parse_tier_range("Street level physically, Island level with magic (Used a Saint-ranked spell)")
    assert ap.baseline_label == "island level"
    # Only ever raises the baseline, and leaves unsplit stats alone.
    assert parse_tier_range("High 6-A, higher with Blut Arterie").baseline_label == "high 6-a"
    assert parse_tier_range("9-B | At least 9-B, up to at least 6-A | 4-A, possibly 3-C").baseline_label == "9-b"


def test_top_tier_names_match_the_codes_pages_pair_them_with():
    # Found by a whole-DB audit: pages pair "1-C" with "Complex Multiverse
    # level" (72 times) and "Low 1-C" with "Low Complex Multiverse level"
    # (30), and "Low 2-C" with "Universe level+" (18). The ladder had the
    # 1-X names one step off, so e.g. God of War's Zeus (Tier 1-C, AP
    # "Complex Multiverse level") had his AP scored a tier above his Tier.
    assert TIER_LADDER["complex multiverse level"] == TIER_LADDER["1-c"]
    assert TIER_LADDER["low complex multiverse level"] == TIER_LADDER["low 1-c"]
    assert TIER_LADDER["hyperverse level"] == TIER_LADDER["1-b"]
    assert TIER_LADDER["universe level+"] == TIER_LADDER["low 2-c"]
    assert TIER_LADDER["3-a"] < TIER_LADDER["universe level+"] < TIER_LADDER["2-c"]


def test_sub_grade_names_no_longer_collapse_to_their_plain_tier():
    # Regression for the whole class, using real strings: "Multi-Continent
    # level" (Bambietta Basterbine's Attack Potency) used to score as
    # plain "Continent level", and "Small Town level" (Ainz Ooal Gown's
    # Durability) as plain "Town level" - no warning either time, since
    # a shorter known label was always found inside the longer one.
    assert parse_tier_range("Multi-Continent level").baseline == TIER_LADDER["high 6-a"]
    assert parse_tier_range("Small Town level").baseline == TIER_LADDER["low 7-c"]
    assert parse_tier_range("Multi-Continent level").baseline > parse_tier_range("Continent level").baseline
    assert parse_tier_range("Small Town level").baseline < parse_tier_range("Town level").baseline


# --- multi-form normalization (Phase 4 follow-up) ---------------------------

def test_genos_forms_normalize_independently_and_increase_in_power():
    stats = _load_stats("Genos")
    normalized = normalize_character(stats)
    assert len(normalized.forms) == 6
    # each form's own ladder lookup, not a shared/reused value
    ap_scores = [f.attack_potency.baseline for f in normalized.forms]
    assert all(v is not None for v in ap_scores)
    # power is non-decreasing across the story's chronological forms
    assert ap_scores == sorted(ap_scores)
    assert ap_scores[0] < ap_scores[-1]

    # Genos' Durability is on all 6 forms (bare-label markup in 5 of them
    # used to hide it - see test_parser.py), each scored independently.
    durability_scores = [f.durability.baseline for f in normalized.forms]
    assert all(v is not None for v in durability_scores)
    assert durability_scores[0] < durability_scores[-1]

    # top-level stays unscored for a genuine multi-form character
    assert normalized.attack_potency.baseline is None
    assert normalized.durability.baseline is None


def test_vegeta_and_goku_forms_count_and_tier_alignment():
    for name, expected_count in [("Vegeta", 7), ("Goku", 8)]:
        stats = _load_stats(name)
        normalized = normalize_character(stats)
        assert len(normalized.forms) == expected_count, name
        # every form's Tier was aligned (segment count matched tab count)
        assert all(f.tier.baseline is not None for f in normalized.forms), name
        # every form has its own Attack Potency and Durability
        assert all(f.attack_potency.baseline is not None for f in normalized.forms), name
        assert all(f.durability.baseline is not None for f in normalized.forms), name
        # scores rise monotonically across the (chronological) forms
        tiers = [f.tier.baseline for f in normalized.forms]
        assert tiers == sorted(tiers), name
        # top-level flat fields stay unscored - never collapsed to one form
        assert normalized.attack_potency.baseline is None, name
        assert normalized.durability.baseline is None, name


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
