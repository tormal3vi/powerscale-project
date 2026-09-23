"""Calculator tests: pure compare_forms()/ability_flags() logic against
hand-built form dicts (deterministic, no DB dependency), plus a real
multi-form character (Genos, id 62 in the local DB - see db.py) to prove
form selection actually changes the outcome rather than just changing
which numbers get printed.

Matchup coverage required for Phase 5 sign-off:
- a close/fair fight
- a wildly lopsided one
- a multi-form character tested at two different form choices
- a matchup with sparse/missing data

Run with: ./venv/bin/python3 -m pytest test_calculator.py -v
(or plain: ./venv/bin/python3 test_calculator.py)
"""

import math

import calculator as calc
import db


def _form(name="Base", tier=None, ap=None, dur=None, speed=None, omnipresent=False,
          tier_peak=None, ap_peak=None, dur_peak=None, speed_peak=None):
    def rng(baseline, peak):
        return {"baseline": baseline, "peak": peak if peak is not None else baseline}
    return {
        "name": name,
        "tier": rng(tier, tier_peak),
        "attack_potency": rng(ap, ap_peak),
        "durability": rng(dur, dur_peak),
        "speed": rng(speed, speed_peak),
        "is_omnipresent": omnipresent,
    }


# --- a close/fair fight -------------------------------------------------

def test_close_fight_is_a_slight_edge_or_toss_up_not_a_confident_verdict():
    a = _form("A", tier=20.0, ap=20.0, dur=20.0, speed=5.0)
    b = _form("B", tier=20.5, ap=19.8, dur=20.3, speed=4.9)
    v = calc.compare_forms("Alpha", a, "Beta", b)
    assert v.axes_used == 4
    assert v.label in ("Too close to call", "Slight edge")
    assert abs(v.composite) < 0.30


# --- a wildly lopsided fight ---------------------------------------------

def test_wildly_lopsided_fight_is_overwhelming_favorite():
    weak = _form("Weak", tier=3.6, ap=3.6, dur=3.6, speed=1.0)
    strong = _form("Strong", tier=70.1, ap=70.1, dur=70.1, speed=9.0)
    v = calc.compare_forms("Weakling", weak, "Titan", strong)
    assert v.axes_used == 4
    assert v.label == "Overwhelming favorite"
    assert v.favored == "Titan"
    assert v.composite < -0.80


# --- multi-form character: form choice must change the outcome -------------

def test_genos_real_forms_flip_the_verdict_against_a_fixed_rival():
    # Real DB data (id 62, "Genos, Demon Cyborg"). This used to also check
    # that his weakest form had no scored Durability - that turned out to
    # be a parser bug (bare-label markup, see test_parser.py's Genos
    # test), and every form now has its own Durability.
    raw, normalized = calc.load_character(62)
    assert "Genos" in raw["name"]

    weakest = calc.select_form(normalized, "Beginning of Series")
    strongest = calc.select_form(normalized, "Post-Elder Centipede")
    assert weakest["durability"]["baseline"] < strongest["durability"]["baseline"]
    assert strongest["tier"]["baseline"] > weakest["tier"]["baseline"]

    rival = _form("Rival", tier=15.0, ap=15.0, dur=15.0, speed=4.5)

    v_weak = calc.compare_forms("Genos", weakest, "Rival", rival)
    v_strong = calc.compare_forms("Genos", strongest, "Rival", rival)

    # Same two characters, same rival, only the form choice differs - the
    # favored side must flip, proving form selection isn't cosmetic.
    assert v_weak.favored == "Rival"
    assert v_strong.favored == "Genos"
    assert v_weak.composite < 0 < v_strong.composite

    # Default selection (no override) must land on *a* form tied for
    # Genos' highest Tier score (17.5 - three of his forms share it) -
    # which specific tied form isn't promised, only the tier value.
    assert v_weak.axes_used == 4
    assert v_strong.axes_used == 4
    default_pick = calc.select_form(normalized)
    assert default_pick["tier"]["baseline"] == strongest["tier"]["baseline"] == 17.5


# --- sparse / missing data --------------------------------------------------

def test_single_shared_axis_is_insufficient_data_not_a_guess():
    sparse = _form("Sparse", tier=None, ap=None, dur=None, speed=3.0)
    normal = _form("Normal", tier=20.0, ap=20.0, dur=20.0, speed=5.0)
    v = calc.compare_forms("Ghost", sparse, "Regular", normal)
    assert v.axes_used == 1
    assert v.composite is None
    assert v.label == "Insufficient data"
    assert v.favored is None


def test_fully_unscored_character_is_insufficient_data():
    blank = _form("Blank")
    normal = _form("Normal", tier=20.0, ap=20.0, dur=20.0, speed=5.0)
    v = calc.compare_forms("Nobody", blank, "Somebody", normal)
    assert v.axes_used == 0
    assert v.composite is None
    assert v.label == "Insufficient data"


# --- missing-stat handling details ------------------------------------------

def test_peak_fallback_is_used_and_flagged_when_baseline_is_missing():
    a = _form("A", tier=20.0, ap=None, ap_peak=25.0, dur=20.0, speed=5.0)
    b = _form("B", tier=20.0, ap=20.0, dur=20.0, speed=5.0)
    v = calc.compare_forms("A", a, "B", b)
    ap_comp = next(c for c in v.axis_comparisons if c.axis == "attack_potency")
    assert ap_comp.a_source == "peak"
    assert ap_comp.a_value == 25.0
    assert ap_comp.advantage is not None  # still usable, not excluded
    assert any("no baseline score" in n and "attack_potency" in n for n in v.notes)


def test_partial_data_caps_confidence_below_overwhelming():
    # 3 axes, each saturated to near-max advantage - the raw math alone
    # would land in "Overwhelming favorite", but partial data (Tier
    # missing on one side) must cap it at "Clear favorite" instead.
    a = _form("A", tier=None, ap=100.0, dur=100.0, speed=100.0)
    b = _form("B", tier=20.0, ap=1.0, dur=1.0, speed=1.0)
    v = calc.compare_forms("A", a, "B", b)
    assert v.axes_used == 3
    assert v.partial_data is True
    assert v.label == "Clear favorite"
    assert v.label != "Overwhelming favorite"


def test_omnipresent_speed_is_scored_and_needs_no_note():
    # Omnipresent scores as the top of the Speed ladder (normalizer), so it
    # simply wins the Speed axis - no caveat needed.
    a = _form("A", tier=20.0, ap=20.0, dur=20.0, speed=35.0, omnipresent=True)
    a["speed"]["baseline_label"] = "omnipresent"
    b = _form("B", tier=20.0, ap=20.0, dur=20.0, speed=5.0)
    v = calc.compare_forms("Ghost", a, "Regular", b)
    speed_comp = next(c for c in v.axis_comparisons if c.axis == "speed")
    assert speed_comp.advantage > 0.9
    assert not any("Omnipresen" in n for n in v.notes)


def test_omnipresence_only_as_a_peak_gets_a_note():
    # "Sub-Relativistic+, ..., possibly Omnipresent": scored at the base
    # value, and the note says the omnipresence isn't counted.
    a = _form("A", tier=20.0, ap=20.0, dur=20.0, speed=7.0, omnipresent=True, speed_peak=35.0)
    a["speed"]["baseline_label"] = "sub-relativistic+"
    b = _form("B", tier=20.0, ap=20.0, dur=20.0, speed=5.0)
    v = calc.compare_forms("Kriemhild", a, "Regular", b)
    assert any("Omnipresence" in n and "base" in n for n in v.notes)


# --- ability flags never touch the score ------------------------------------

def test_ability_flags_detected_but_do_not_move_the_composite():
    same_a = _form("A", tier=20.0, ap=20.0, dur=20.0, speed=5.0)
    same_b = _form("B", tier=20.0, ap=20.0, dur=20.0, speed=5.0)
    v = calc.compare_forms("Regen Guy", same_a, "Mortal Guy", same_b)
    assert v.composite == 0.0  # identical stats -> perfectly neutral

    raw_a = {"name": "Regen Guy", "powers_and_abilities": ["Regeneration (High-Godly)", "Immortality (Type 8)"], "weaknesses": None}
    raw_b = {"name": "Mortal Guy", "powers_and_abilities": ["Master Swordsman"], "weaknesses": "None notable"}
    flags = calc.ability_flags(raw_a, raw_b)
    tags = {f.tag for f in flags}
    assert "Regeneration" in tags
    assert "Immortality" in tags
    regen_flag = next(f for f in flags if f.tag == "Regeneration")
    assert regen_flag.characters == ["Regen Guy"]

    # Flags are computed independently of compare_forms - confirming here
    # that nothing in ability_flags touches AxisComparison/composite math.
    assert v.composite == 0.0


def test_resistance_keyword_and_durability_negation_both_flag():
    raw_a = {"powers_and_abilities": ["Attack Potency ignores durability"], "weaknesses": None, "name": "A"}
    raw_b = {"powers_and_abilities": ["Resistance to mind manipulation"], "weaknesses": None, "name": "B"}
    flags = calc.ability_flags(raw_a, raw_b)
    tags = {f.tag: f.characters for f in flags}
    assert tags["Durability Negation"] == ["A"]
    assert tags["Resistance"] == ["B"]


def test_no_ability_flags_when_nothing_matches():
    raw_a = {"powers_and_abilities": ["Superhuman Strength", "Flight"], "weaknesses": None, "name": "A"}
    raw_b = {"powers_and_abilities": ["Master Martial Artist"], "weaknesses": "Cannot swim", "name": "B"}
    assert calc.ability_flags(raw_a, raw_b) == []


# --- form selection edge cases ------------------------------------------

def test_select_form_defaults_to_highest_tier():
    normalized = {"forms": [
        _form("Low", tier=5.0),
        _form("High", tier=50.0),
        _form("Mid", tier=25.0),
    ]}
    assert calc.select_form(normalized)["name"] == "High"


def test_select_form_explicit_override():
    normalized = {"forms": [_form("Low", tier=5.0), _form("High", tier=50.0)]}
    assert calc.select_form(normalized, "Low")["name"] == "Low"


def test_select_form_unknown_name_raises_with_available_list():
    normalized = {"name": "Test", "forms": [_form("Low", tier=5.0), _form("High", tier=50.0)]}
    try:
        calc.select_form(normalized, "Nonexistent")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Low" in str(exc) and "High" in str(exc)


def test_select_form_unscored_forms_dont_crash():
    # every form lacking a Tier score - max() must still pick something,
    # not raise, since -inf sorts consistently.
    normalized = {"forms": [_form("A"), _form("B")]}
    picked = calc.select_form(normalized)
    assert picked["name"] in ("A", "B")


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
