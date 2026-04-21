"""Autoresearch proposal data model + persistence (Phase 11.1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.autoresearch import (
    DEFAULT_TTL_DAYS,
    PROPOSALS_SUBDIR,
    AutoresearchProposal,
    ProposalStatus,
    build_proposal,
    iter_proposals,
    load_proposal,
    proposal_path,
    write_proposal,
)
from core.dispatch.evaluator import TriggerAction, TriggerDecision


def _approve() -> TriggerDecision:
    return TriggerDecision(
        action=TriggerAction.APPROVE,
        reason="3 failures in last 10",
        failures_in_window=3,
        skill_pattern_count=2,
    )


def _defer() -> TriggerDecision:
    return TriggerDecision(
        action=TriggerAction.DEFER,
        reason="budget shy",
        failures_in_window=3,
        skill_pattern_count=3,
    )


def _decline() -> TriggerDecision:
    return TriggerDecision(
        action=TriggerAction.DECLINE,
        reason="only 1 failure",
    )


# ---------------------------------------------------------------------------
# build_proposal status routing
# ---------------------------------------------------------------------------
def test_approve_decision_yields_pending_proposal() -> None:
    p = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
    )
    assert p.status is ProposalStatus.PENDING
    assert p.specialist_id == "copywriter"
    assert p.skill_id == "web-researcher"
    assert p.budget_estimate == 0.50


def test_defer_decision_yields_escalated_proposal() -> None:
    p = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_defer(),
        budget_estimate=0.50,
    )
    assert p.status is ProposalStatus.ESCALATED


def test_decline_decision_raises() -> None:
    with pytest.raises(ValueError, match="DECLINE"):
        build_proposal(
            specialist_id="copywriter",
            skill_id="web-researcher",
            decision=_decline(),
            budget_estimate=0.50,
        )


# ---------------------------------------------------------------------------
# Proposal identity + TTL
# ---------------------------------------------------------------------------
def test_proposal_id_encodes_specialist_skill_timestamp() -> None:
    p = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
        now_iso="2026-04-18T12:00:00+00:00",
    )
    assert "copywriter" in p.proposal_id
    assert "web-researcher" in p.proposal_id
    assert "2026-04-18T12" in p.proposal_id


def test_proposal_id_is_filename_safe() -> None:
    p = build_proposal(
        specialist_id="copy writer / v2",  # nasty input
        skill_id="web research",
        decision=_approve(),
        budget_estimate=0.50,
    )
    # No path separators, no colons in an identifier component.
    assert "/" not in p.proposal_id
    assert " " not in p.proposal_id


def test_ttl_default_is_seven_days() -> None:
    assert DEFAULT_TTL_DAYS == 7
    p = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
    )
    assert p.ttl_days == 7


def test_expires_at_is_created_plus_ttl() -> None:
    p = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
        now_iso="2026-04-18T12:00:00+00:00",
    )
    exp = p.expires_at()
    assert exp == datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)


def test_is_expired_false_before_ttl() -> None:
    p = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
        now_iso="2026-04-18T12:00:00+00:00",
    )
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    assert not p.is_expired(now=now)


def test_is_expired_true_past_ttl() -> None:
    p = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
        now_iso="2026-04-18T12:00:00+00:00",
    )
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    assert p.is_expired(now=now)


def test_completed_proposal_never_expires() -> None:
    p = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
        now_iso="2026-04-18T12:00:00+00:00",
    )
    completed = AutoresearchProposal(**{
        **p.to_dict(),
        "status": ProposalStatus.COMPLETED,
    })
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert not completed.is_expired(now=far_future)


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------
def test_write_creates_proposal_file(tmp_path: Path) -> None:
    p = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
    )
    path = write_proposal(tmp_path, p)
    assert path.exists()
    assert path.parent.name == "proposals"
    # Path is under the canonical subdir (normalise separators for Windows).
    rel_posix = path.relative_to(tmp_path).as_posix()
    assert rel_posix.startswith(PROPOSALS_SUBDIR)


def test_roundtrip_through_disk(tmp_path: Path) -> None:
    original = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
    )
    write_proposal(tmp_path, original)
    loaded = load_proposal(proposal_path(tmp_path, original))
    assert loaded == original


def test_iter_proposals_reads_all(tmp_path: Path) -> None:
    p1 = build_proposal(
        specialist_id="copywriter",
        skill_id="web-researcher",
        decision=_approve(),
        budget_estimate=0.50,
        now_iso="2026-04-18T12:00:00+00:00",
    )
    p2 = build_proposal(
        specialist_id="market-researcher",
        skill_id="kb-retriever",
        decision=_defer(),
        budget_estimate=0.75,
        now_iso="2026-04-18T13:00:00+00:00",
    )
    write_proposal(tmp_path, p1)
    write_proposal(tmp_path, p2)
    loaded = iter_proposals(tmp_path)
    assert len(loaded) == 2
    ids = {p.proposal_id for p in loaded}
    assert ids == {p1.proposal_id, p2.proposal_id}


def test_iter_empty_dir_returns_empty_list(tmp_path: Path) -> None:
    assert iter_proposals(tmp_path) == []


def test_iter_skips_malformed_json(tmp_path: Path) -> None:
    (tmp_path / PROPOSALS_SUBDIR).mkdir(parents=True)
    (tmp_path / PROPOSALS_SUBDIR / "broken.json").write_text("not json")
    (tmp_path / PROPOSALS_SUBDIR / "incomplete.json").write_text('{"foo": "bar"}')
    assert iter_proposals(tmp_path) == []
