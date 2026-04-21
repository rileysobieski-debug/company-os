"""TTL sweep + escalation queue + persistence (Phase 11.3)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.autoresearch import (
    AutoresearchProposal,
    ProposalStatus,
    build_proposal,
    escalated_queue,
    iter_proposals,
    mark_completed,
    mark_running,
    pending_queue,
    persist_transition,
    resume_from_escalation,
    sweep_expired,
    write_proposal,
)
from core.dispatch.evaluator import TriggerAction, TriggerDecision


def _approve(reason: str = "trigger") -> TriggerDecision:
    return TriggerDecision(
        action=TriggerAction.APPROVE,
        reason=reason,
        failures_in_window=3,
    )


def _defer() -> TriggerDecision:
    return TriggerDecision(
        action=TriggerAction.DEFER,
        reason="budget shy",
    )


def _pending(specialist: str, skill: str, ts: str) -> AutoresearchProposal:
    return build_proposal(
        specialist_id=specialist,
        skill_id=skill,
        decision=_approve(),
        budget_estimate=0.50,
        now_iso=ts,
    )


def _escalated(specialist: str, ts: str) -> AutoresearchProposal:
    return build_proposal(
        specialist_id=specialist,
        skill_id="web-researcher",
        decision=_defer(),
        budget_estimate=0.50,
        now_iso=ts,
    )


# ---------------------------------------------------------------------------
# sweep_expired
# ---------------------------------------------------------------------------
def test_sweep_returns_only_expired() -> None:
    fresh = _pending("a", "s", "2026-04-18T12:00:00+00:00")
    stale = _pending("b", "s", "2026-04-01T12:00:00+00:00")  # > 7 days old
    now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
    out = sweep_expired([fresh, stale], now=now)
    assert len(out) == 1
    assert out[0].proposal_id == stale.proposal_id
    assert out[0].status is ProposalStatus.EXPIRED


def test_sweep_empty_when_none_stale() -> None:
    fresh = _pending("a", "s", "2026-04-18T12:00:00+00:00")
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    assert sweep_expired([fresh], now=now) == []


def test_sweep_skips_already_completed() -> None:
    p = _pending("a", "s", "2026-04-01T12:00:00+00:00")
    running = mark_running(p, now_iso="2026-04-02T12:00:00+00:00")
    done = mark_completed(
        running, artifact_path="x.md",
        now_iso="2026-04-02T12:30:00+00:00",
    )
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Completed is terminal — no expiry action.
    assert sweep_expired([done], now=now) == []


def test_sweep_can_expire_escalated_proposals() -> None:
    """Escalated proposals should also expire if founder never acts."""
    e = _escalated("a", "2026-04-01T12:00:00+00:00")
    now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)  # 17 days later
    out = sweep_expired([e], now=now)
    assert len(out) == 1
    assert out[0].status is ProposalStatus.EXPIRED


# ---------------------------------------------------------------------------
# pending_queue / escalated_queue
# ---------------------------------------------------------------------------
def test_pending_queue_excludes_non_pending() -> None:
    p = _pending("a", "s", "2026-04-18T12:00:00+00:00")
    e = _escalated("b", "2026-04-18T12:00:00+00:00")
    queue = pending_queue([p, e])
    assert len(queue) == 1
    assert queue[0].proposal_id == p.proposal_id


def test_pending_queue_excludes_expired_proposals() -> None:
    """A proposal past TTL should NOT show up in the runner's queue,
    even if it hasn't been formally marked expired yet."""
    stale = _pending("a", "s", "2026-04-01T12:00:00+00:00")
    # Don't actually mark it expired — just let the queue filter based on
    # the is_expired() predicate.
    # (sweep_expired is a separate, formal transition.)
    # Because pending_queue uses 'now' at call time, manipulating it is
    # impractical — instead verify with a stale-enough timestamp that
    # the TTL clearly exceeded.
    queue = pending_queue([stale])
    # Should be empty; 17 days stale vs 7-day TTL.
    assert queue == []


def test_escalated_queue_sorted_by_creation() -> None:
    e1 = _escalated("a", "2026-04-18T14:00:00+00:00")
    e2 = _escalated("b", "2026-04-18T12:00:00+00:00")
    e3 = _escalated("c", "2026-04-18T13:00:00+00:00")
    queue = escalated_queue([e1, e2, e3])
    assert [p.specialist_id for p in queue] == ["b", "c", "a"]


def test_escalated_queue_excludes_pending() -> None:
    p = _pending("a", "s", "2026-04-18T12:00:00+00:00")
    e = _escalated("b", "2026-04-18T12:00:00+00:00")
    queue = escalated_queue([p, e])
    assert len(queue) == 1
    assert queue[0].proposal_id == e.proposal_id


# ---------------------------------------------------------------------------
# persist_transition on disk
# ---------------------------------------------------------------------------
def test_persist_transition_overwrites_proposal_file(tmp_path: Path) -> None:
    p = _pending("a", "s", "2026-04-18T12:00:00+00:00")
    write_proposal(tmp_path, p)
    running = mark_running(p, now_iso="2026-04-18T13:00:00+00:00")
    persist_transition(tmp_path, running)
    loaded = iter_proposals(tmp_path)
    assert len(loaded) == 1  # same proposal_id → one file
    assert loaded[0].status is ProposalStatus.RUNNING
    assert loaded[0].started_at == "2026-04-18T13:00:00+00:00"


def test_full_cycle_disk_roundtrip(tmp_path: Path) -> None:
    p = _pending("a", "s", "2026-04-18T12:00:00+00:00")
    write_proposal(tmp_path, p)

    running = mark_running(p, now_iso="2026-04-18T13:00:00+00:00")
    persist_transition(tmp_path, running)

    done = mark_completed(
        running, artifact_path="r.md",
        now_iso="2026-04-18T14:00:00+00:00",
    )
    persist_transition(tmp_path, done)

    loaded = iter_proposals(tmp_path)
    assert len(loaded) == 1
    final = loaded[0]
    assert final.status is ProposalStatus.COMPLETED
    assert final.artifact_path == "r.md"


# ---------------------------------------------------------------------------
# End-to-end: escalation → resume → run → complete
# ---------------------------------------------------------------------------
def test_escalation_resume_run_complete_sequence() -> None:
    e = _escalated("a", "2026-04-18T12:00:00+00:00")
    # Founder approves budget a few days later:
    resumed = resume_from_escalation(e, now_iso="2026-04-21T12:00:00+00:00")
    assert resumed.status is ProposalStatus.PENDING
    running = mark_running(resumed, now_iso="2026-04-21T12:30:00+00:00")
    done = mark_completed(
        running, artifact_path="r.md",
        now_iso="2026-04-21T13:00:00+00:00",
    )
    assert done.status is ProposalStatus.COMPLETED
    assert done.artifact_path == "r.md"
    # Identity preserved throughout.
    assert done.proposal_id == e.proposal_id
