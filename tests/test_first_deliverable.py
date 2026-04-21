"""First-deliverable proposal (Phase 8.4 — §5.8)."""
from __future__ import annotations

import pytest

from core.onboarding.first_deliverable import (
    CONVICTIONS_SUMMARY,
    POSITIONING_STATEMENT,
    PRIORITY_RISK_MATRIX,
    DeliverableProposal,
    propose_first_deliverable,
)


def _base_answers() -> dict:
    return {
        "company_name": "Old Press Wine Company LLC",
        "twelve_month_vision": "First vintage from coastal Maine released.",
        "five_year_vision": "Established brand with loyal buyer list.",
        "priority_stack": [
            "Secure W-2 income",
            "Finish TTB paperwork",
            "Draft positioning",
        ],
        "hard_rules": ["No selling through distributors in Year 1"],
        "regulatory": "TTB alternating-proprietor path",
        "budget": "$500/mo operating",
        "settled_convictions": [
            "Coastal Maine is the operational base",
            "Quiet-abundance brand stance; no public founder",
        ],
    }


# ---------------------------------------------------------------------------
# Kind selection
# ---------------------------------------------------------------------------
def test_multiple_convictions_beat_vision_and_constraints() -> None:
    """2 convictions scores 4; constraints score 3+1+1+1=6 — constraints wins on
    rich input, but let's make convictions dominant first."""
    answers = _base_answers()
    # Remove vision and most constraints so convictions dominates.
    answers["twelve_month_vision"] = ""
    answers["five_year_vision"] = ""
    answers["hard_rules"] = []
    answers["regulatory"] = ""
    answers["budget"] = ""
    answers["priority_stack"] = ["only one"]  # score 1
    proposal = propose_first_deliverable(
        answers, active_departments=["editorial", "marketing", "operations"],
    )
    assert proposal.kind == CONVICTIONS_SUMMARY
    assert proposal.assigned_dept == "editorial"


def test_rich_constraints_wins_when_most_signal_there() -> None:
    answers = _base_answers()
    answers["settled_convictions"] = []
    answers["twelve_month_vision"] = ""
    answers["five_year_vision"] = ""
    proposal = propose_first_deliverable(
        answers, active_departments=["operations", "finance", "marketing"],
    )
    assert proposal.kind == PRIORITY_RISK_MATRIX
    assert proposal.assigned_dept == "operations"


def test_vision_picks_positioning_when_only_signal() -> None:
    answers = {
        "twelve_month_vision": "Ship first vintage to 50 local buyers.",
        "five_year_vision": "Regional brand in New England coastal wine.",
        "priority_stack": ["p1"],  # score 1 — vision score 2 wins
        "hard_rules": [],
        "settled_convictions": [],
    }
    proposal = propose_first_deliverable(
        answers, active_departments=["marketing", "operations"],
    )
    assert proposal.kind == POSITIONING_STATEMENT
    assert proposal.assigned_dept == "marketing"


def test_empty_signals_defaults_to_priority_risk() -> None:
    answers = {
        "twelve_month_vision": "",
        "five_year_vision": "",
        "priority_stack": [],
        "hard_rules": [],
        "settled_convictions": [],
    }
    proposal = propose_first_deliverable(
        answers, active_departments=["marketing", "finance"],
    )
    assert proposal.kind == PRIORITY_RISK_MATRIX


# ---------------------------------------------------------------------------
# Dept fallback
# ---------------------------------------------------------------------------
def test_preferred_dept_not_active_falls_back_to_first() -> None:
    answers = _base_answers()
    answers["settled_convictions"] = []
    answers["twelve_month_vision"] = ""
    answers["five_year_vision"] = ""
    # Score → priority_risk_matrix → prefers 'operations'. Exclude it.
    proposal = propose_first_deliverable(
        answers, active_departments=["finance", "marketing"],
    )
    assert proposal.kind == PRIORITY_RISK_MATRIX
    assert proposal.assigned_dept == "finance"
    assert "not active" in proposal.rationale
    assert "finance" in proposal.rationale


def test_preferred_dept_active_noted_in_rationale() -> None:
    answers = _base_answers()
    proposal = propose_first_deliverable(
        answers, active_departments=["editorial", "marketing", "operations"],
    )
    assert "preferred dept" in proposal.rationale.lower()


def test_no_active_departments_raises() -> None:
    with pytest.raises(ValueError, match="at least one active department"):
        propose_first_deliverable(_base_answers(), active_departments=[])


# ---------------------------------------------------------------------------
# Brief content
# ---------------------------------------------------------------------------
def test_positioning_brief_quotes_founder_vision() -> None:
    answers = _base_answers()
    answers["settled_convictions"] = []
    answers["priority_stack"] = ["one"]
    answers["hard_rules"] = []
    answers["regulatory"] = ""
    answers["budget"] = ""
    proposal = propose_first_deliverable(
        answers, active_departments=["marketing"],
    )
    assert proposal.kind == POSITIONING_STATEMENT
    assert "First vintage from coastal Maine released" in proposal.brief
    assert "Established brand with loyal buyer list" in proposal.brief


def test_priority_risk_brief_lists_priorities_and_rules() -> None:
    answers = _base_answers()
    answers["settled_convictions"] = []
    answers["twelve_month_vision"] = ""
    answers["five_year_vision"] = ""
    proposal = propose_first_deliverable(
        answers, active_departments=["operations"],
    )
    assert "Secure W-2 income" in proposal.brief
    assert "No selling through distributors" in proposal.brief
    assert "TTB alternating-proprietor" in proposal.brief
    assert "$500/mo operating" in proposal.brief


def test_convictions_brief_includes_do_not_reexamine_annotation() -> None:
    answers = _base_answers()
    answers["twelve_month_vision"] = ""
    answers["five_year_vision"] = ""
    answers["hard_rules"] = []
    answers["regulatory"] = ""
    answers["budget"] = ""
    answers["priority_stack"] = ["one"]
    proposal = propose_first_deliverable(
        answers, active_departments=["editorial"],
    )
    assert proposal.kind == CONVICTIONS_SUMMARY
    assert "DO NOT RE-EXAMINE" in proposal.brief
    assert "Coastal Maine is the operational base" in proposal.brief


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------
def test_proposal_is_frozen_dataclass() -> None:
    answers = _base_answers()
    proposal = propose_first_deliverable(
        answers, active_departments=["marketing"],
    )
    assert isinstance(proposal, DeliverableProposal)
    assert proposal.title  # non-empty
    assert proposal.brief  # non-empty
    assert proposal.score >= 0


def test_proposal_kind_is_canonical_string() -> None:
    answers = _base_answers()
    proposal = propose_first_deliverable(
        answers, active_departments=["marketing"],
    )
    assert proposal.kind in {
        CONVICTIONS_SUMMARY, POSITIONING_STATEMENT, PRIORITY_RISK_MATRIX,
    }
