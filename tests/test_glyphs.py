"""Unit tests for the deterministic glyph repair layer.

Every case here is a real artifact class from glyph_artifacts.md
(Biochemistry ED8) — the repair table is frozen by that census.
"""

from collections import Counter

import pytest

from qbank import glyphs
from qbank.glyphs import SENT_DEGREE, SENT_SQUARE, repair


def _fix(text):
    counts = Counter()
    out = repair(text, counts)
    return out, counts


def test_real_degree_sign_is_never_touched():
    # ArialMT degrees are not sentinelised upstream, so no rule can
    # see them — the repair layer must pass them through verbatim.
    text = "Normal value 98.6° F, measured at 37° C"
    out, counts = _fix(text)
    assert out == text
    assert not counts


def test_ago_range_dash():
    out, counts = _fix(f"members of (Ago 1{SENT_DEGREE}4) family")
    assert out == "members of (Ago1-4) family"
    assert counts["ago_range_dash"] == 1


def test_cap_link_dash():
    out, counts = _fix(f"the 5'{SENT_DEGREE}5' triphosphate linkage")
    assert out == "the 5'-5' triphosphate linkage"
    assert counts["cap_link_dash"] == 1


def test_direction_arrow():
    out, counts = _fix(f"3'{SENT_DEGREE}5' exonuclease activity")
    assert out == "3'\u21925' exonuclease activity"
    assert counts["direction_arrow"] == 1


def test_bond_paren_arrow():
    out, counts = _fix(f"\u03b1 (1{SENT_DEGREE}6) linkage")
    assert out == "\u03b1(1\u21926) linkage"
    assert counts["bond_paren_arrow"] == 1


def test_bond_alpha1_arrow():
    out, counts = _fix(f"\u03b11 {SENT_DEGREE} 4 glucosidic bond")
    assert out == "\u03b11\u21924 glucosidic bond"
    assert counts["bond_alpha1_arrow"] == 1


def test_bond_bare1_arrow():
    out, counts = _fix(f"cleavage of the 1 {SENT_DEGREE} 6 bond")
    assert out == "cleavage of the 1\u21926 bond"
    assert counts["bond_bare1_arrow"] == 1


def test_reaction_arrow_generic():
    out, counts = _fix(f"Pyruvate + CoA {SENT_DEGREE} Acetyl-CoA")
    assert out == "Pyruvate + CoA \u2192 Acetyl-CoA"
    assert counts["reaction_arrow"] == 1


def test_delta_before_digit():
    out, counts = _fix(f"{SENT_SQUARE}9 desaturase")
    assert out == "\u03949 desaturase"
    assert counts["delta_greek"] == 1


def test_quote_pair():
    out, counts = _fix(f"{SENT_SQUARE}turn off{SENT_SQUARE} the gene")
    assert out == '"turn off" the gene'
    assert counts["quote_pair"] == 1


def test_stray_drop():
    out, counts = _fix(f"reaction to{SENT_SQUARE} Benedict's reagent")
    assert out == "reaction to Benedict's reagent"
    assert counts["stray_drop"] == 1


def test_unknown_sentinel_is_restored_and_flagged_not_guessed():
    # a SENT_DEGREE with no matchable context cannot survive the
    # generic reaction rule, but SENT_SQUARE with nothing after it
    # exercises the unknown path when paired content is absent:
    text = f"value is 5{SENT_SQUARE}"          # stray at end -> dropped
    out, counts = _fix(text)
    assert out == "value is 5"
    assert counts["stray_drop"] == 1

    # force a truly unknown case by monkeypatching the rule table
    import re
    saved = list(glyphs._RULES)
    try:
        glyphs._RULES[:] = [r for r in glyphs._RULES
                            if r[0] not in (glyphs.R_DELTA,
                                            glyphs.R_QUOTE,
                                            glyphs.R_STRAY)]
        out, counts = _fix(f"{SENT_SQUARE}9 desaturase")
        assert out == "\u25a09 desaturase"     # restored verbatim
        assert counts["unknown_glyph"] == 1
    finally:
        glyphs._RULES[:] = saved


def test_repair_is_idempotent_on_clean_text():
    text = "Glucose \u2192 Pyruvate \u2192 Acetyl-CoA, \u03949, \u03b1(1\u21924)"
    assert repair(text) == text
