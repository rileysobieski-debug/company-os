"""Manager pre-warm scheduling (Phase 8.5 — §5.6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.onboarding.dept_selection import VERTICAL_DEPARTMENTS
from core.onboarding.pre_warm import (
    JobStatus,
    ManagerOnboardingJob,
    PrewarmLedger,
    PrewarmMode,
    prewarm_jobs,
    schedule_manager_onboardings,
)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
def test_first_dept_is_synchronous() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    first = next(j for j in jobs if j.dept == "finance")
    assert first.mode is PrewarmMode.SYNCHRONOUS
    assert first.trigger == "interview"
    assert first.order == 0


def test_second_and_third_dept_are_prewarm() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    warm = {j.dept: j for j in jobs if j.mode is PrewarmMode.PREWARM}
    assert set(warm) == {"marketing", "operations"}
    for j in warm.values():
        assert j.trigger == "after-first-deliverable"


def test_other_depts_are_dormant() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    dormant = {j.dept for j in jobs if j.mode is PrewarmMode.DORMANT}
    expected = set(VERTICAL_DEPARTMENTS) - {"finance", "marketing", "operations"}
    assert dormant == expected
    for j in jobs:
        if j.mode is PrewarmMode.DORMANT:
            assert j.trigger == "on-demand"


def test_all_depts_covered_exactly_once() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    depts = [j.dept for j in jobs]
    assert len(depts) == len(VERTICAL_DEPARTMENTS)
    assert set(depts) == set(VERTICAL_DEPARTMENTS)


def test_scheduler_stable_ordering() -> None:
    """Active first, dormant follows in vertical-pack order."""
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    # First three = active in given order.
    assert [j.dept for j in jobs[:3]] == ["finance", "marketing", "operations"]
    # Dormant rest = vertical-pack order minus actives.
    dormant_order = [j.dept for j in jobs[3:]]
    expected = [
        d for d in VERTICAL_DEPARTMENTS
        if d not in {"finance", "marketing", "operations"}
    ]
    assert dormant_order == expected


def test_scheduler_rejects_empty_active() -> None:
    with pytest.raises(ValueError, match="at least one dept"):
        schedule_manager_onboardings([])


def test_scheduler_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="distinct"):
        schedule_manager_onboardings(["finance", "finance", "marketing"])


def test_scheduler_handles_more_than_three_active() -> None:
    """If caller passes 4 actives, index 3 is still PREWARM."""
    jobs = schedule_manager_onboardings(
        ["finance", "marketing", "operations", "community"]
    )
    community = next(j for j in jobs if j.dept == "community")
    assert community.mode is PrewarmMode.PREWARM
    assert community.order == 3


def test_prewarm_jobs_filters_only_prewarm() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    warm = prewarm_jobs(jobs)
    assert len(warm) == 2
    assert {j.dept for j in warm} == {"marketing", "operations"}


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------
def test_ledger_initial_status_ready_for_synchronous() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    ledger = PrewarmLedger.from_schedule(jobs)
    assert ledger.get("finance") is JobStatus.READY
    assert ledger.ready_to_dispatch("finance")


def test_ledger_initial_status_pending_for_prewarm_and_dormant() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    ledger = PrewarmLedger.from_schedule(jobs)
    assert ledger.get("marketing") is JobStatus.PENDING
    assert ledger.get("operations") is JobStatus.PENDING
    # A dormant one:
    assert ledger.get("community") is JobStatus.PENDING


def test_ledger_mark_transitions_status() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    ledger = PrewarmLedger.from_schedule(jobs)
    ledger.mark("marketing", JobStatus.RUNNING)
    assert ledger.get("marketing") is JobStatus.RUNNING
    assert not ledger.ready_to_dispatch("marketing")
    ledger.mark("marketing", JobStatus.READY)
    assert ledger.ready_to_dispatch("marketing")


def test_ledger_mark_unknown_dept_raises() -> None:
    jobs = schedule_manager_onboardings(["finance"])
    ledger = PrewarmLedger.from_schedule(jobs)
    with pytest.raises(KeyError, match="not-a-dept"):
        ledger.mark("not-a-dept", JobStatus.READY)


def test_ledger_jobs_awaiting_prewarm() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    ledger = PrewarmLedger.from_schedule(jobs)
    awaiting = ledger.jobs_awaiting_prewarm()
    assert {j.dept for j in awaiting} == {"marketing", "operations"}
    ledger.mark("marketing", JobStatus.READY)
    awaiting = ledger.jobs_awaiting_prewarm()
    assert {j.dept for j in awaiting} == {"operations"}


def test_ledger_jobs_awaiting_excludes_dormant() -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    ledger = PrewarmLedger.from_schedule(jobs)
    awaiting = ledger.jobs_awaiting_prewarm()
    for j in awaiting:
        assert j.mode is PrewarmMode.PREWARM


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def test_ledger_roundtrips_through_disk(tmp_path: Path) -> None:
    jobs = schedule_manager_onboardings(["finance", "marketing", "operations"])
    ledger = PrewarmLedger.from_schedule(jobs)
    ledger.mark("marketing", JobStatus.RUNNING)
    path = tmp_path / "state" / "prewarm.json"
    ledger.save(path)
    assert path.exists()
    restored = PrewarmLedger.load(path)
    assert restored.get("finance") is JobStatus.READY
    assert restored.get("marketing") is JobStatus.RUNNING
    assert {j.dept for j in restored.jobs} == {j.dept for j in jobs}


def test_ledger_to_dict_stable_shape() -> None:
    jobs = schedule_manager_onboardings(["finance"])
    ledger = PrewarmLedger.from_schedule(jobs)
    data = ledger.to_dict()
    assert set(data.keys()) == {"jobs", "status"}
    assert all(
        set(j.keys()) == {"dept", "mode", "trigger", "order"}
        for j in data["jobs"]
    )
