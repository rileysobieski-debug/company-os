"""
core/onboarding/pre_warm.py — Phase 8.5 — §5.6 manager pre-warm scheduling
==========================================================================
Plan §5.6:

  "Only the #1-priority department's manager gets onboarded synchronously
   in the first 20 minutes — this is the one the founder meets. [...]
   Pre-warming the other two active managers (async). After the first
   deliverable lands (§5.8), managers #2 and #3 onboard asynchronously
   in the background — default specialist rosters from the vertical
   pack, no founder-facing questions, just self-introduction + memory
   seed. The founder is told 'managers for [dept #2] and [dept #3] are
   being pre-warmed and will be ready the first time you invoke them.'"

This primitive is scheduling + ledger only — it does NOT run onboarding
jobs. The orchestrator / async runner consumes the produced jobs and
updates the ledger status. We keep the scheduler pure so it can be
tested without an event loop and so the ledger can be persisted/
inspected between processes.

Status transitions: `pending → running → ready` (or `failed`).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from core.onboarding.dept_selection import VERTICAL_DEPARTMENTS


class PrewarmMode(str, Enum):
    """How a manager is onboarded."""

    SYNCHRONOUS = "synchronous"          # dept #1 — founder-facing
    PREWARM = "pre-warm"                 # depts #2 and #3 — async, after §5.8
    DORMANT = "dormant"                  # rest — on-demand only


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


_TRIGGER_BY_MODE: dict[PrewarmMode, str] = {
    PrewarmMode.SYNCHRONOUS: "interview",
    PrewarmMode.PREWARM: "after-first-deliverable",
    PrewarmMode.DORMANT: "on-demand",
}


@dataclass(frozen=True)
class ManagerOnboardingJob:
    dept: str
    mode: PrewarmMode
    trigger: str
    order: int  # Priority order in the active stack (0 = highest); dormant
                # depts get a stable order drawn from the vertical list.


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
def schedule_manager_onboardings(
    active_departments: Sequence[str],
    *,
    all_departments: Sequence[str] = VERTICAL_DEPARTMENTS,
) -> list[ManagerOnboardingJob]:
    """Build the per-dept onboarding schedule per §5.6.

    * Index 0 of `active_departments` → SYNCHRONOUS.
    * Indexes 1 and 2 → PREWARM.
    * Any active index 3+ → PREWARM (conservative; plan only specifies
      3 active, but keeps the scheduler correct if the caller passes
      more).
    * Every dept in `all_departments` not in `active_departments` →
      DORMANT.

    The returned list is stable: active order first, then dormant order
    (by position in `all_departments`).
    """
    if not active_departments:
        raise ValueError("active_departments must contain at least one dept")
    if len(set(active_departments)) != len(active_departments):
        raise ValueError("active_departments must be distinct")

    active_set = set(active_departments)
    jobs: list[ManagerOnboardingJob] = []
    for i, dept in enumerate(active_departments):
        if i == 0:
            mode = PrewarmMode.SYNCHRONOUS
        else:
            mode = PrewarmMode.PREWARM
        jobs.append(ManagerOnboardingJob(
            dept=dept, mode=mode, trigger=_TRIGGER_BY_MODE[mode], order=i,
        ))
    # Dormant depts follow, preserving vertical-pack order.
    dormant_base = len(active_departments)
    for j, dept in enumerate(all_departments):
        if dept in active_set:
            continue
        jobs.append(ManagerOnboardingJob(
            dept=dept, mode=PrewarmMode.DORMANT,
            trigger=_TRIGGER_BY_MODE[PrewarmMode.DORMANT],
            order=dormant_base + j,
        ))
    return jobs


def prewarm_jobs(jobs: Iterable[ManagerOnboardingJob]) -> list[ManagerOnboardingJob]:
    """Convenience filter: only the jobs that should run async after §5.8."""
    return [j for j in jobs if j.mode is PrewarmMode.PREWARM]


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------
@dataclass
class PrewarmLedger:
    """Mutable status tracker keyed by dept.

    Designed to be persisted to disk so the orchestrator and async
    runner can share state across processes (via `save()` / `load()`).
    """

    jobs: list[ManagerOnboardingJob] = field(default_factory=list)
    status: dict[str, JobStatus] = field(default_factory=dict)

    @classmethod
    def from_schedule(
        cls, jobs: Sequence[ManagerOnboardingJob]
    ) -> "PrewarmLedger":
        ledger = cls(jobs=list(jobs), status={})
        for job in jobs:
            # Synchronous dept is implicitly onboarded by the interview
            # flow; pre-warm and dormant start as pending.
            ledger.status[job.dept] = (
                JobStatus.READY
                if job.mode is PrewarmMode.SYNCHRONOUS
                else JobStatus.PENDING
            )
        return ledger

    def get(self, dept: str) -> JobStatus:
        return self.status.get(dept, JobStatus.PENDING)

    def mark(self, dept: str, new_status: JobStatus) -> None:
        if dept not in {j.dept for j in self.jobs}:
            raise KeyError(f"unknown dept {dept!r} (not in schedule)")
        self.status[dept] = new_status

    def ready_to_dispatch(self, dept: str) -> bool:
        return self.get(dept) is JobStatus.READY

    def jobs_awaiting_prewarm(self) -> list[ManagerOnboardingJob]:
        """Pre-warm jobs still pending — i.e. need to be kicked off."""
        return [
            j for j in self.jobs
            if j.mode is PrewarmMode.PREWARM
            and self.get(j.dept) is JobStatus.PENDING
        ]

    # JSON persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "jobs": [
                {
                    "dept": j.dept,
                    "mode": j.mode.value,
                    "trigger": j.trigger,
                    "order": j.order,
                }
                for j in self.jobs
            ],
            "status": {k: v.value for k, v in self.status.items()},
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, data: dict) -> "PrewarmLedger":
        jobs = [
            ManagerOnboardingJob(
                dept=j["dept"],
                mode=PrewarmMode(j["mode"]),
                trigger=j["trigger"],
                order=int(j["order"]),
            )
            for j in data.get("jobs", [])
        ]
        status = {k: JobStatus(v) for k, v in data.get("status", {}).items()}
        return cls(jobs=jobs, status=status)

    @classmethod
    def load(cls, path: Path) -> "PrewarmLedger":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
