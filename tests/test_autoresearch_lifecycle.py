"""Autoresearch proposal status transitions (Phase 11.2)."""
from __future__ import annotations

import pytest

from core.autoresearch import (
    AutoresearchProposal,
    IllegalTransitionError,
    ProposalStatus,
    build_proposal,
    mark_completed,
    mark_expired,
    mark_running,
    resume_from_escalation,
)
from core.dispatch.evaluator import TriggerAction, TriggerDecision


def _pending() -> AutoresearchProposal:
    return build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=TriggerDecision(
            action=TriggerAction.APPROVE,
            reason="3 failures in last 10",
            failures_in_window=3,
        ),
        budget_estimate=0.50,
        now_iso="2026-04-18T12:00:00+00:00",
    )


def _escalated() -> AutoresearchProposal:
    return build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=TriggerDecision(
            action=TriggerAction.DEFER,
            reason="budget shy",
            failures_in_window=3,
        ),
        budget_estimate=0.75,
        now_iso="2026-04-18T12:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# pending → running
# ---------------------------------------------------------------------------
def test_pending_transitions_to_running() -> None:
    p = _pending()
    running = mark_running(p, now_iso="2026-04-18T13:00:00+00:00")
    assert running.status is ProposalStatus.RUNNING
    assert running.started_at == "2026-04-18T13:00:00+00:00"
    assert running.proposal_id == p.proposal_id  # identity preserved


def test_mark_running_preserves_other_fields() -> None:
    p = _pending()
    running = mark_running(p, now_iso="2026-04-18T13:00:00+00:00")
    assert running.specialist_id == p.specialist_id
    assert running.budget_estimate == p.budget_estimate
    assert running.ttl_days == p.ttl_days


# ---------------------------------------------------------------------------
# running → completed
# ---------------------------------------------------------------------------
def test_running_transitions_to_completed_with_artifact() -> None:
    p = _pending()
    running = mark_running(p, now_iso="2026-04-18T13:00:00+00:00")
    done = mark_completed(
        running,
        artifact_path="autoresearch-runs/artifacts/2026-04-18-copywriter-web.md",
        now_iso="2026-04-18T14:00:00+00:00",
    )
    assert done.status is ProposalStatus.COMPLETED
    assert done.completed_at == "2026-04-18T14:00:00+00:00"
    assert done.artifact_path.endswith("copywriter-web.md")


def test_mark_completed_from_pending_raises() -> None:
    p = _pending()
    with pytest.raises(IllegalTransitionError, match="pending"):
        mark_completed(p, artifact_path="x")


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------
def test_pending_can_expire() -> None:
    p = _pending()
    expired = mark_expired(p)
    assert expired.status is ProposalStatus.EXPIRED
    assert "TTL elapsed" in expired.notes


def test_running_can_expire() -> None:
    p = mark_running(_pending(), now_iso="2026-04-18T13:00:00+00:00")
    expired = mark_expired(p, reason="runner timed out")
    assert expired.status is ProposalStatus.EXPIRED
    assert "runner timed out" in expired.notes


def test_completed_cannot_expire() -> None:
    p = mark_running(_pending(), now_iso="2026-04-18T13:00:00+00:00")
    done = mark_completed(
        p,
        artifact_path="a.md",
        now_iso="2026-04-18T14:00:00+00:00",
    )
    with pytest.raises(IllegalTransitionError):
        mark_expired(done)


def test_expired_cannot_become_anything_else() -> None:
    p = mark_expired(_pending())
    with pytest.raises(IllegalTransitionError):
        mark_running(p)


# ---------------------------------------------------------------------------
# Escalation → resume
# ---------------------------------------------------------------------------
def test_escalated_resumes_to_pending() -> None:
    p = _escalated()
    assert p.status is ProposalStatus.ESCALATED
    resumed = resume_from_escalation(
        p, now_iso="2026-04-25T12:00:00+00:00",
    )
    assert resumed.status is ProposalStatus.PENDING
    # TTL clock resets from the resume time.
    assert resumed.created_at == "2026-04-25T12:00:00+00:00"


def test_resume_can_override_ttl_days() -> None:
    p = _escalated()
    resumed = resume_from_escalation(
        p, now_iso="2026-04-25T12:00:00+00:00", ttl_days=3,
    )
    assert resumed.ttl_days == 3


def test_escalated_can_expire_without_resuming() -> None:
    p = _escalated()
    expired = mark_expired(p, reason="founder never reviewed")
    assert expired.status is ProposalStatus.EXPIRED


def test_cannot_mark_escalated_running_directly() -> None:
    """Escalated must resume (to pending) before it can be picked up."""
    p = _escalated()
    with pytest.raises(IllegalTransitionError):
        mark_running(p)


# ---------------------------------------------------------------------------
# Sanity: full happy-path sequence
# ---------------------------------------------------------------------------
def test_full_lifecycle_sequence() -> None:
    pending = _pending()
    running = mark_running(pending, now_iso="2026-04-18T13:00:00+00:00")
    completed = mark_completed(
        running, artifact_path="x.md",
        now_iso="2026-04-18T14:00:00+00:00",
    )
    assert completed.status is ProposalStatus.COMPLETED
    assert completed.started_at
    assert completed.completed_at
    assert completed.artifact_path == "x.md"
