"""
core/autoresearch.py — Phase 11 — Agent-prompted autoresearch lifecycle
========================================================================
Plan references:
  * §13 line 688: "Phase 11 — Agent-prompted autoresearch. Evaluator-
    alone trigger (§8.1 revised), 7-day TTL on proposals, budget-
    deferred→founder escalation (§8.2 revised)."
  * §8.1 (revised): Evaluator owns the autoresearch trigger decision
    — the manager is informed, not consulted.
  * §8.2 (revised): Budget-deferred proposals escalate to the founder
    rather than silently dying.

The evaluator's `consider_autoresearch_trigger()` (Phase 7) produces a
`TriggerDecision`. This module turns that decision into a persistent
`AutoresearchProposal` with a 7-day TTL and tracks lifecycle:

  pending   → proposal approved by evaluator; ready for background runner
  running   → picked up by the autoresearch runner (not yet landed)
  completed → run finished; artifact persisted at `artifact_path`
  expired   → 7-day TTL elapsed without execution; discarded
  escalated → budget defer → founder must decide; no auto-run

On-disk layout:
  <company>/autoresearch-runs/proposals/<proposal_id>.json

Proposal ID: `{specialist_id}--{skill_id}--{ts}` (filename-safe). The
timestamp is the proposal's created_at, so concurrent proposals for the
same (specialist, skill) never collide.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from core.dispatch.evaluator import TriggerAction, TriggerDecision

DEFAULT_TTL_DAYS = 7
PROPOSALS_SUBDIR = "autoresearch-runs/proposals"
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class ProposalStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    EXPIRED = "expired"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class AutoresearchProposal:
    proposal_id: str
    specialist_id: str
    skill_id: str
    trigger_reason: str
    failures_in_window: int
    skill_pattern_count: int
    budget_estimate: float
    created_at: str
    ttl_days: int = DEFAULT_TTL_DAYS
    status: ProposalStatus = ProposalStatus.PENDING
    started_at: str | None = None
    completed_at: str | None = None
    artifact_path: str | None = None
    notes: str = ""

    # -----------------------------------------------------------------
    # TTL arithmetic
    # -----------------------------------------------------------------
    def expires_at(self) -> datetime:
        return _parse_iso(self.created_at) + timedelta(days=self.ttl_days)

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.status in (
            ProposalStatus.COMPLETED,
            ProposalStatus.EXPIRED,
        ):
            return False
        ref = now if now is not None else datetime.now(tz=timezone.utc)
        return ref >= self.expires_at()

    # -----------------------------------------------------------------
    # Serialisation
    # -----------------------------------------------------------------
    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _safe(component: str) -> str:
    return _FILENAME_SAFE_RE.sub("-", component).strip("-") or "anon"


# ---------------------------------------------------------------------------
# Creation (Chunk 11.1 entry point)
# ---------------------------------------------------------------------------
def build_proposal(
    *,
    specialist_id: str,
    skill_id: str,
    decision: TriggerDecision,
    budget_estimate: float,
    now_iso: str | None = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> AutoresearchProposal:
    """Turn a `TriggerDecision` into a persistable `AutoresearchProposal`.

    * APPROVE → status=pending (the runner picks it up)
    * DEFER   → status=escalated (founder decides)
    * DECLINE → ValueError (no proposal to build)
    """
    if decision.action is TriggerAction.DECLINE:
        raise ValueError(
            f"cannot build proposal from DECLINE decision: {decision.reason}"
        )
    ts = now_iso or _now_iso()
    status = (
        ProposalStatus.PENDING
        if decision.action is TriggerAction.APPROVE
        else ProposalStatus.ESCALATED
    )
    ts_tag = _safe(ts)
    proposal_id = f"{_safe(specialist_id)}--{_safe(skill_id)}--{ts_tag}"
    return AutoresearchProposal(
        proposal_id=proposal_id,
        specialist_id=specialist_id,
        skill_id=skill_id,
        trigger_reason=decision.reason,
        failures_in_window=decision.failures_in_window,
        skill_pattern_count=decision.skill_pattern_count,
        budget_estimate=budget_estimate,
        created_at=ts,
        ttl_days=ttl_days,
        status=status,
    )


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------
def proposal_path(company_dir: Path, proposal: AutoresearchProposal) -> Path:
    return company_dir / PROPOSALS_SUBDIR / f"{proposal.proposal_id}.json"


def write_proposal(company_dir: Path, proposal: AutoresearchProposal) -> Path:
    path = proposal_path(company_dir, proposal)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(proposal.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def load_proposal(path: Path) -> AutoresearchProposal:
    data = json.loads(path.read_text(encoding="utf-8"))
    return _from_dict(data)


def _from_dict(data: dict) -> AutoresearchProposal:
    return AutoresearchProposal(
        proposal_id=str(data["proposal_id"]),
        specialist_id=str(data["specialist_id"]),
        skill_id=str(data["skill_id"]),
        trigger_reason=str(data.get("trigger_reason", "")),
        failures_in_window=int(data.get("failures_in_window", 0)),
        skill_pattern_count=int(data.get("skill_pattern_count", 0)),
        budget_estimate=float(data.get("budget_estimate", 0.0)),
        created_at=str(data["created_at"]),
        ttl_days=int(data.get("ttl_days", DEFAULT_TTL_DAYS)),
        status=ProposalStatus(str(data.get("status", "pending"))),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
        artifact_path=data.get("artifact_path"),
        notes=str(data.get("notes", "")),
    )


def iter_proposals(company_dir: Path) -> list[AutoresearchProposal]:
    """Load every proposal JSON in the company's proposals dir."""
    root = company_dir / PROPOSALS_SUBDIR
    if not root.exists():
        return []
    out: list[AutoresearchProposal] = []
    for p in sorted(root.glob("*.json")):
        try:
            out.append(load_proposal(p))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return out


# ---------------------------------------------------------------------------
# Status transitions (Chunk 11.2)
# ---------------------------------------------------------------------------
class IllegalTransitionError(ValueError):
    """Raised when a status transition is not permitted by the lifecycle."""


_ALLOWED_TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.PENDING: frozenset({
        ProposalStatus.RUNNING, ProposalStatus.EXPIRED, ProposalStatus.ESCALATED,
    }),
    ProposalStatus.RUNNING: frozenset({
        ProposalStatus.COMPLETED, ProposalStatus.EXPIRED,
    }),
    ProposalStatus.COMPLETED: frozenset(),
    ProposalStatus.EXPIRED: frozenset(),
    # An ESCALATED proposal can resume as pending once the founder
    # approves the budget (§8.2 revised).
    ProposalStatus.ESCALATED: frozenset({
        ProposalStatus.PENDING, ProposalStatus.EXPIRED,
    }),
}


def _with_status(
    proposal: AutoresearchProposal,
    new_status: ProposalStatus,
    **updates,
) -> AutoresearchProposal:
    """Produce a copy of `proposal` with the new status + any supplied
    field overrides. Enforces the lifecycle transition table."""
    allowed = _ALLOWED_TRANSITIONS.get(proposal.status, frozenset())
    if new_status not in allowed:
        raise IllegalTransitionError(
            f"cannot transition proposal {proposal.proposal_id!r} from "
            f"{proposal.status.value!r} → {new_status.value!r}"
        )
    return replace(proposal, status=new_status, **updates)


def mark_running(
    proposal: AutoresearchProposal,
    *,
    now_iso: str | None = None,
) -> AutoresearchProposal:
    """Pending → running. Records `started_at`."""
    ts = now_iso or _now_iso()
    return _with_status(proposal, ProposalStatus.RUNNING, started_at=ts)


def mark_completed(
    proposal: AutoresearchProposal,
    *,
    artifact_path: str,
    now_iso: str | None = None,
    notes: str = "",
) -> AutoresearchProposal:
    """Running → completed. Records `completed_at` + `artifact_path`."""
    ts = now_iso or _now_iso()
    return _with_status(
        proposal, ProposalStatus.COMPLETED,
        completed_at=ts,
        artifact_path=artifact_path,
        notes=notes or proposal.notes,
    )


def mark_expired(
    proposal: AutoresearchProposal,
    *,
    reason: str = "TTL elapsed",
) -> AutoresearchProposal:
    """Any pre-terminal state → expired."""
    existing = f"{proposal.notes}\n{reason}".strip() if proposal.notes else reason
    return _with_status(proposal, ProposalStatus.EXPIRED, notes=existing)


def resume_from_escalation(
    proposal: AutoresearchProposal,
    *,
    now_iso: str | None = None,
    ttl_days: int | None = None,
) -> AutoresearchProposal:
    """Escalated → pending. Founder approved the budget; proposal resumes.

    The TTL clock restarts from `now_iso` (default: now) because the
    original created_at is by now potentially stale. `ttl_days` can be
    overridden for policy exceptions.
    """
    ts = now_iso or _now_iso()
    refreshed = replace(
        proposal,
        created_at=ts,
        ttl_days=ttl_days if ttl_days is not None else proposal.ttl_days,
    )
    # Run through _with_status so the transition-table check is honored.
    return _with_status(refreshed, ProposalStatus.PENDING)


# ---------------------------------------------------------------------------
# TTL sweep + escalation queue (Chunk 11.3)
# ---------------------------------------------------------------------------
def sweep_expired(
    proposals: Iterable[AutoresearchProposal],
    *,
    now: datetime | None = None,
) -> list[AutoresearchProposal]:
    """Return the subset of `proposals` that should be transitioned to
    EXPIRED because their TTL has elapsed.

    Skips proposals already in terminal states (completed, expired).
    The caller is responsible for persisting the returned (expired)
    copies with `persist_transition()`.
    """
    ref = now if now is not None else datetime.now(tz=timezone.utc)
    expired: list[AutoresearchProposal] = []
    for p in proposals:
        if p.is_expired(now=ref):
            expired.append(mark_expired(p, reason="TTL elapsed"))
    return expired


def pending_queue(
    proposals: Iterable[AutoresearchProposal],
) -> list[AutoresearchProposal]:
    """Proposals the runner should pick up next (status=pending, not expired)."""
    now = datetime.now(tz=timezone.utc)
    return [
        p for p in proposals
        if p.status is ProposalStatus.PENDING and not p.is_expired(now=now)
    ]


def escalated_queue(
    proposals: Iterable[AutoresearchProposal],
) -> list[AutoresearchProposal]:
    """Proposals awaiting founder review. Sorted by created_at ascending so
    the oldest escalations surface first."""
    escalated = [p for p in proposals if p.status is ProposalStatus.ESCALATED]
    return sorted(escalated, key=lambda p: p.created_at)


def persist_transition(
    company_dir: Path,
    proposal: AutoresearchProposal,
) -> Path:
    """Overwrite the proposal JSON with its new status. The file on disk
    is keyed by proposal_id (which is immutable across transitions), so
    subsequent calls replace the prior version cleanly.
    """
    return write_proposal(company_dir, proposal)
