"""Tests for core/primitives/cost.py — threshold bands + session state.

No LLM calls. Verifies the spend-ratio bands from plan §9 and the
month-ceiling gate. Chunk 1a.3 acceptance.
"""
from __future__ import annotations

import pytest


def _session(envelope_max: float = 10.0, month_max: float = 40.0) -> "BudgetSession":
    from core.primitives.cost import BudgetSession
    return BudgetSession(
        session_id="test",
        envelope_max=envelope_max,
        month_max=month_max,
    )


def test_spend_at_79_percent_returns_ok() -> None:
    from core.primitives.cost import STATUS_OK, check_budget
    s = _session(envelope_max=10.0)
    result = check_budget(s, proposed_cost=7.9)
    assert result["status"] == STATUS_OK
    assert 0.78 < result["ratio"] < 0.80


def test_spend_at_80_percent_returns_warn() -> None:
    from core.primitives.cost import STATUS_WARN, check_budget
    s = _session(envelope_max=10.0)
    result = check_budget(s, proposed_cost=8.0)
    assert result["status"] == STATUS_WARN


def test_spend_at_100_percent_returns_paused() -> None:
    from core.primitives.cost import STATUS_PAUSED, check_budget
    s = _session(envelope_max=10.0)
    result = check_budget(s, proposed_cost=10.0)
    assert result["status"] == STATUS_PAUSED


def test_spend_at_120_percent_returns_aborted() -> None:
    from core.primitives.cost import STATUS_ABORTED, check_budget
    s = _session(envelope_max=10.0)
    result = check_budget(s, proposed_cost=12.0)
    assert result["status"] == STATUS_ABORTED


def test_month_ceiling_blocks_non_founder_dispatch() -> None:
    from core.primitives.cost import STATUS_BLOCKED, BudgetBlock, check_budget
    s = _session(envelope_max=10.0, month_max=40.0)
    s.spent_this_month = 39.0

    result = check_budget(s, proposed_cost=2.0, principal="specialist")
    assert result["status"] == STATUS_BLOCKED
    assert isinstance(result["block"], BudgetBlock)
    assert result["block"].reason == "month ceiling exceeded"
    assert result["block"].principal == "specialist"
    assert result["block"].projected_month_spend == pytest.approx(41.0)

    # Founder can still dispatch under the same conditions.
    founder_result = check_budget(s, proposed_cost=2.0, principal="founder")
    assert founder_result["status"] != STATUS_BLOCKED


def test_resume_session_with_approval_transitions_to_active() -> None:
    from core.primitives.cost import (
        STATUS_ACTIVE,
        STATUS_PAUSED,
        pause_session,
        resume_session,
    )
    s = _session()
    pause_session(s)
    assert s.state == STATUS_PAUSED

    result = resume_session(s, approval=True)
    assert result == STATUS_ACTIVE
    assert s.state == STATUS_ACTIVE

    # Non-approval on a paused session is a no-op.
    pause_session(s)
    result = resume_session(s, approval=False)
    assert result == STATUS_PAUSED
