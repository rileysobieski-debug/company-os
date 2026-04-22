"""
tests/test_score_parser.py -- R3 score_parser test suite
==========================================================
Covers the required baseline cases plus reviewer-driven additions for
the two-tier extraction strategy (structured <score> tag primary,
last-valid-wins regex fallback).

Policy summary
--------------
- Tier 1 (structured <score> tag): if present, authoritative. Out-of-
  range or non-numeric contents return None (fail-closed).
- Tier 2 (fallback regex): used only when no tag is present. Last
  in-range value wins. Version numbers, identifier digits, and date
  components are excluded by a lookbehind. Percent handled as `%`
  (with optional whitespace) or the word `percent`.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.primitives.evaluators.score_parser import extract_score


# ---------------------------------------------------------------------------
# Required cases from the R3 plan (11 cases)
# ---------------------------------------------------------------------------
# Note: `multi_score_last_wins` used to be `multi_score_first_wins`. The
# policy flipped under reviewer guidance -- LLMs conclude at the end, so
# the LAST in-range score is the primary answer. `0.8` was demoted to the
# sub-score position and `0.9` is now the expected result.
REQUIRED_CASES = [
    pytest.param("0.92", Decimal("0.92"), id="bare_float"),
    pytest.param("Score: 0.87", Decimal("0.87"), id="labeled"),
    pytest.param("90%", Decimal("0.90"), id="percent"),
    pytest.param('{"score": 0.88}', Decimal("0.88"), id="json_embedded"),
    pytest.param(
        "The final rubric score is approximately 0.75.",
        Decimal("0.75"),
        id="prose_wrapped",
    ),
    pytest.param("0.9\n", Decimal("0.9"), id="trailing_newline"),
    pytest.param(
        "Quality: 0.8. Accuracy: 0.9.",
        Decimal("0.9"),
        id="multi_score_last_wins",
    ),
    pytest.param("-0.1", None, id="out_of_range_low"),
    pytest.param("1.3", None, id="out_of_range_high"),
    pytest.param("The result is good.", None, id="no_numeric"),
    pytest.param("", None, id="empty_string"),
]


# ---------------------------------------------------------------------------
# Extra edge cases
# ---------------------------------------------------------------------------
# Note: `multiple_json_keys_last_match` used to be `..._first_match`. Under
# last-valid-wins the parser now returns the confidence value (the last
# in-range number in reading order), not the first score value. In real
# LLM output this is the desired behavior because the final number is
# usually the conclusion.
EXTRA_CASES = [
    pytest.param("   \n\t", None, id="whitespace_only"),
    pytest.param("130%", None, id="percent_out_of_range"),
    pytest.param("1", Decimal("1"), id="integer_upper_boundary"),
    pytest.param("0", Decimal("0"), id="integer_lower_boundary"),
    pytest.param(
        '{"score": 0.88, "confidence": 0.95}',
        Decimal("0.95"),
        id="multiple_json_keys_last_match",
    ),
]


# ---------------------------------------------------------------------------
# Structured <score> tag cases (Tier 1)
# ---------------------------------------------------------------------------
STRUCTURED_TAG_CASES = [
    pytest.param("<score>0.88</score>", Decimal("0.88"), id="structured_tag_basic"),
    pytest.param(
        "Here is my answer: <score>0.75</score>.",
        Decimal("0.75"),
        id="structured_tag_in_prose",
    ),
    pytest.param("<SCORE>0.6</SCORE>", Decimal("0.6"), id="structured_tag_uppercase"),
    pytest.param(
        "<score>  0.9  </score>",
        Decimal("0.9"),
        id="structured_tag_inner_whitespace",
    ),
    pytest.param("<score>90%</score>", Decimal("0.90"), id="structured_tag_percent"),
    pytest.param(
        "<score>1.5</score>",
        None,
        id="structured_tag_out_of_range_fails_closed",
    ),
    pytest.param(
        "<score>not_a_number</score>",
        None,
        id="structured_tag_garbage_fails_closed",
    ),
    pytest.param(
        "<score>0.88</score> (clarity 0.92, completeness 0.65)",
        Decimal("0.88"),
        id="structured_tag_wins_over_fallback",
    ),
]


# ---------------------------------------------------------------------------
# Reviewer-flagged fallback-policy cases (Tier 2)
# ---------------------------------------------------------------------------
FALLBACK_POLICY_CASES = [
    pytest.param(
        "The summary is good (clarity: 0.92, completeness: 0.65). "
        "Overall rubric score: 0.88",
        Decimal("0.88"),
        id="reviewer_last_valid_wins_over_earlier_scores",
    ),
    pytest.param(
        "Model: GPT-4o-v1.0\nScore: 0.88",
        Decimal("0.88"),
        id="version_number_collision_ignored",
    ),
    pytest.param(
        "Note that 1 is the best possible and 0 is worst. "
        "The final score is 0.75.",
        Decimal("0.75"),
        id="reference_boundary_skipped_via_last_valid",
    ),
    pytest.param(
        "The target threshold is 0.9. Achieved 0.85.",
        Decimal("0.85"),
        id="threshold_skipped_via_last_valid",
    ),
    pytest.param("90 %", Decimal("0.90"), id="percent_with_whitespace"),
    pytest.param("90 percent", Decimal("0.90"), id="percent_keyword"),
    pytest.param("90 Percent", Decimal("0.90"), id="percent_keyword_capitalized"),
    pytest.param("ID-123 Score: 0.77", Decimal("0.77"), id="identifier_digits_ignored"),
    pytest.param(
        "2026-04-21 snapshot: score 0.82",
        Decimal("0.82"),
        id="date_digits_ignored",
    ),
]


# ---------------------------------------------------------------------------
# Parametric test bodies
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("llm_text, expected", REQUIRED_CASES)
def test_extract_score_required(llm_text: str, expected: Decimal | None) -> None:
    """The baseline cases from the R3 plan."""
    assert extract_score(llm_text) == expected


@pytest.mark.parametrize("llm_text, expected", EXTRA_CASES)
def test_extract_score_extras(llm_text: str, expected: Decimal | None) -> None:
    """Extra edge cases for boundary and out-of-range percent coverage."""
    assert extract_score(llm_text) == expected


@pytest.mark.parametrize("llm_text, expected", STRUCTURED_TAG_CASES)
def test_extract_score_structured_tags(
    llm_text: str, expected: Decimal | None
) -> None:
    """Tier 1 structured `<score>N</score>` tag cases."""
    assert extract_score(llm_text) == expected


@pytest.mark.parametrize("llm_text, expected", FALLBACK_POLICY_CASES)
def test_extract_score_fallback_policy(
    llm_text: str, expected: Decimal | None
) -> None:
    """Tier 2 reviewer-flagged fallback-policy cases."""
    assert extract_score(llm_text) == expected


# ---------------------------------------------------------------------------
# Behavioral properties (non-parametric)
# ---------------------------------------------------------------------------
class TestReturnType:
    def test_returns_decimal_on_valid_input(self) -> None:
        """When a valid score is parsed, the return type must be Decimal."""
        result = extract_score("0.5")
        assert isinstance(result, Decimal)

    def test_returns_none_on_invalid_input(self) -> None:
        """When no valid score is found, the return type must be None."""
        assert extract_score("no numbers here") is None

    def test_never_raises_on_garbage(self) -> None:
        """The function must never raise, even on clearly malformed input."""
        garbage_inputs = [
            "\\",
            "}}}",
            "0.0.0",
            "nan",
            "inf",
            "1e1000",
            "x" * 10_000,
        ]
        for text in garbage_inputs:
            # Must not raise; return value may be None or a Decimal
            result = extract_score(text)
            assert result is None or isinstance(result, Decimal)


class TestPercentHandling:
    def test_zero_percent(self) -> None:
        """0% is a valid boundary score."""
        assert extract_score("0%") == Decimal("0")

    def test_one_hundred_percent(self) -> None:
        """100% divides to 1.0, the upper boundary, still valid."""
        assert extract_score("100%") == Decimal("1.00")

    def test_fractional_percent(self) -> None:
        """Fractional percents like 87.5% divide cleanly."""
        # 87.5% -> 0.875
        result = extract_score("87.5%")
        assert result == Decimal("0.875")


class TestDecimalPrecision:
    def test_no_float_artifacts(self) -> None:
        """Parsing should use Decimal(str(...)) so 0.1 stays exact."""
        # If the implementation used float conversion, the returned value
        # could be Decimal("0.1000000000000000055...") rather than the
        # literal Decimal("0.1").
        result = extract_score("0.1")
        assert result == Decimal("0.1")
        assert str(result) == "0.1"


class TestTierInteraction:
    def test_tag_present_short_circuits_fallback(self) -> None:
        """When a <score> tag is present, the fallback regex must NOT run,
        even if the surrounding prose has numbers that would outrank it
        under last-valid-wins."""
        # Fallback alone would return 0.99 (last valid); tag wins.
        text = "Noise: 0.55. <score>0.3</score> More noise: 0.99."
        assert extract_score(text) == Decimal("0.3")

    def test_tag_out_of_range_fails_closed_even_with_valid_fallback(self) -> None:
        """Tier 1 fail-closed: if the tag is out of range, we do not
        fall back to the regex even if the prose contains a valid score.
        The LLM emitted a structured-but-invalid answer; trust the tag."""
        text = "<score>1.5</score> but the real answer is 0.8"
        assert extract_score(text) is None
