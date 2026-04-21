"""Top-3 dept selection (Phase 8.2 — §5.5)."""
from __future__ import annotations

import pytest

from core.onboarding.dept_selection import (
    VERTICAL_DEPARTMENTS,
    DepartmentChoice,
    apply_founder_override,
    dormant_departments,
    suggest_top_n_departments,
)


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------
def test_priority_with_clear_finance_hit_picks_finance() -> None:
    picks = suggest_top_n_departments(["Secure W-2 income and budget"])
    assert len(picks) == 1
    assert picks[0].dept == "finance"
    assert picks[0].hit_count >= 1


def test_priority_with_clear_ops_hit_picks_operations() -> None:
    picks = suggest_top_n_departments(
        ["Finish TTB alternating-proprietor paperwork"]
    )
    assert picks[0].dept == "operations"
    assert picks[0].hit_count >= 1


def test_priority_with_clear_marketing_hit_picks_marketing() -> None:
    picks = suggest_top_n_departments(
        ["Draft positioning and brand messaging doc"]
    )
    assert picks[0].dept == "marketing"


def test_returns_three_distinct_departments() -> None:
    priorities = [
        "Secure W-2 income in Maine",
        "Finish TTB alternating-proprietor paperwork",
        "Draft brand and positioning doc",
    ]
    picks = suggest_top_n_departments(priorities)
    depts = [p.dept for p in picks]
    assert len(depts) == 3
    assert len(set(depts)) == 3  # distinct


def test_zero_hits_falls_through_to_vertical_order() -> None:
    """Unmappable priorities still get a dept, with a 'default assignment'
    rationale the orchestrator can surface."""
    picks = suggest_top_n_departments(["totally generic priority"])
    assert len(picks) == 1
    assert picks[0].hit_count == 0
    assert "default assignment" in picks[0].rationale


def test_respects_priority_order() -> None:
    """First priority claims first; conflicting second priority gets its
    next-best pick."""
    # Both priorities likely map to "operations" (TTB/compliance/paperwork).
    picks = suggest_top_n_departments([
        "Complete TTB paperwork",
        "Handle supplier logistics",
    ])
    assert picks[0].priority_index == 0
    assert picks[1].priority_index == 1
    assert picks[0].dept != picks[1].dept  # distinct even when same category


def test_limits_to_n_picks() -> None:
    priorities = [f"priority-{i}" for i in range(10)]
    picks = suggest_top_n_departments(priorities, n=3)
    assert len(picks) == 3


def test_n_zero_returns_empty() -> None:
    assert suggest_top_n_departments(["x"], n=0) == []


def test_suggest_restricted_to_available_subset() -> None:
    """Callers can narrow the dept universe (e.g. to exclude AI for a
    non-AI-leaning vertical)."""
    picks = suggest_top_n_departments(
        ["draft positioning"],
        available=("marketing", "finance"),
    )
    assert picks[0].dept == "marketing"


# ---------------------------------------------------------------------------
# Founder override
# ---------------------------------------------------------------------------
def test_override_replaces_suggested_dept() -> None:
    suggestions = suggest_top_n_departments([
        "Secure W-2 income",
        "Finish paperwork",
        "Draft positioning",
    ])
    # Replace priority 0 finance → community
    merged = apply_founder_override(suggestions, {0: "community"})
    assert merged[0].dept == "community"
    assert "founder override" in merged[0].rationale
    # Others unchanged.
    assert merged[1].dept == suggestions[1].dept
    assert merged[2].dept == suggestions[2].dept


def test_override_with_duplicate_rejected() -> None:
    """Override creating a duplicate dept across priorities → ValueError."""
    suggestions = suggest_top_n_departments([
        "Secure W-2 income",
        "Draft positioning",
    ])
    # Suggestions[0]=finance, [1]=marketing. Override [1] to finance.
    with pytest.raises(ValueError, match="duplicate department"):
        apply_founder_override(suggestions, {1: suggestions[0].dept})


def test_override_noop_when_not_supplied() -> None:
    suggestions = suggest_top_n_departments(["Draft positioning"])
    merged = apply_founder_override(suggestions, {})
    assert merged == suggestions


# ---------------------------------------------------------------------------
# Dormant departments
# ---------------------------------------------------------------------------
def test_dormant_excludes_active() -> None:
    picks = suggest_top_n_departments([
        "Secure W-2 income",
        "Finish TTB paperwork",
        "Draft positioning",
    ])
    dormant = dormant_departments(picks)
    active_depts = {p.dept for p in picks}
    for d in dormant:
        assert d not in active_depts
    assert len(dormant) == len(VERTICAL_DEPARTMENTS) - len(active_depts)


def test_dormant_accepts_plain_strings() -> None:
    dormant = dormant_departments(["finance", "marketing"])
    assert "finance" not in dormant
    assert "marketing" not in dormant


def test_dormant_preserves_vertical_order() -> None:
    dormant = dormant_departments(["finance"])
    # The remaining order should match VERTICAL_DEPARTMENTS minus "finance".
    expected = [d for d in VERTICAL_DEPARTMENTS if d != "finance"]
    assert dormant == expected
