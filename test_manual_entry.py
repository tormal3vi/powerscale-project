"""manual_entry.py tests: building a CharacterStats from a manual-entry
dict (the same shape yaml.safe_load produces from a filled-in template)
without touching the DB, plus confirming the result flows through
normalizer.py exactly like a scraped character.

Run with: ./venv/bin/python3 -m pytest test_manual_entry.py -v
(or plain: ./venv/bin/python3 test_manual_entry.py)
"""

from manual_entry import _slugify, _source_url_for, build_character_stats
from normalizer import normalize_character
from parser import DEFAULT_FORM_NAME


def test_minimal_entry_only_needs_name_and_category():
    stats = build_character_stats({"name": "Test Hero", "category": "Original"})
    assert stats.name == "Test Hero"
    assert stats.tier is None
    assert stats.powers_and_abilities == []
    # Always non-empty, same guarantee parser.parse_character makes.
    assert len(stats.forms) == 1
    assert stats.forms[0].name == DEFAULT_FORM_NAME


def test_single_form_entry_wraps_flat_fields_into_base_form():
    data = {
        "name": "Aurelia Voss",
        "category": "Crimson Cross",
        "tier": "7-B",
        "attack_potency": "City level",
        "speed": "Massively Hypersonic+",
        "durability": "City level",
        "weaknesses": "Needs her blade.",
        "powers_and_abilities": ["Superhuman Physical Characteristics", "Regeneration (Mid)"],
    }
    stats = build_character_stats(data)
    assert len(stats.forms) == 1
    form = stats.forms[0]
    assert form.name == DEFAULT_FORM_NAME
    assert form.tier == "7-B"
    assert form.stats.attack_potency == "City level"
    assert form.stats.speed == "Massively Hypersonic+"
    assert form.stats.durability == "City level"
    assert stats.weaknesses == "Needs her blade."
    assert len(stats.powers_and_abilities) == 2


def test_multi_form_entry_builds_independent_forms():
    data = {
        "name": "Aurelia Voss",
        "category": "Crimson Cross",
        "forms": [
            {"name": "Unbound", "tier": "7-B", "attack_potency": "City level", "speed": "Massively Hypersonic+"},
            {"name": "Crimson Awakening", "tier": "6-A", "attack_potency": "Continent level", "speed": "Massively Hypersonic+"},
        ],
    }
    stats = build_character_stats(data)
    assert len(stats.forms) == 2
    assert stats.forms[0].name == "Unbound"
    assert stats.forms[0].tier == "7-B"
    assert stats.forms[1].name == "Crimson Awakening"
    assert stats.forms[1].tier == "6-A"
    # forms is the source of truth for a multi-form character - the flat
    # top-level fields are never silently populated from forms[0].
    assert stats.tier is None
    assert stats.attack_potency is None


def test_form_without_a_name_raises():
    data = {
        "name": "Test",
        "category": "Original",
        "forms": [{"tier": "7-B"}],
    }
    try:
        build_character_stats(data)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "name" in str(exc)


def test_missing_name_raises():
    try:
        build_character_stats({"category": "Original"})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "name" in str(exc)


def test_missing_category_raises():
    try:
        build_character_stats({"name": "Test"})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "category" in str(exc)


def test_source_url_is_synthetic_and_unique_per_name():
    assert _source_url_for("Crimson Cross", "Aurelia Voss") == "manual://crimson-cross/aurelia-voss"
    assert _source_url_for("Crimson Cross", "Kael Draven") == "manual://crimson-cross/kael-draven"
    # Punctuation/apostrophes don't break the slug or collide two different names.
    assert _slugify("D'Arby, Jr.") != _slugify("Darby")
    assert _slugify("") == "character"


def test_built_stats_normalize_through_the_same_ladder_as_scraped_data():
    # The actual point of requirement 2: no hand-picked numeric scores -
    # this must produce the exact same score a scraped "7-B"/"City
    # level" character would, via the real TIER_LADDER lookup.
    stats = build_character_stats({
        "name": "Aurelia Voss",
        "category": "Crimson Cross",
        "tier": "7-B",
        "attack_potency": "City level",
        "speed": "Massively Hypersonic+",
    })
    normalized = normalize_character(stats)
    assert len(normalized.forms) == 1
    form = normalized.forms[0]
    assert form.tier.baseline == form.attack_potency.baseline == 15.6
    assert form.speed.baseline == 6.35


def test_multi_form_stats_normalize_independently():
    stats = build_character_stats({
        "name": "Aurelia Voss",
        "category": "Crimson Cross",
        "forms": [
            {"name": "Unbound", "tier": "7-B"},
            {"name": "Crimson Awakening", "tier": "6-A"},
        ],
    })
    normalized = normalize_character(stats)
    assert len(normalized.forms) == 2
    assert normalized.forms[0].tier.baseline == 15.6
    assert normalized.forms[1].tier.baseline == 23.0
    assert normalized.forms[0].tier.baseline < normalized.forms[1].tier.baseline


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
