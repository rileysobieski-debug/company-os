"""
core/primitives/cost.py — Budget thresholds + session state
===========================================================
Plan §9 defines four spend-ratio bands that downstream code treats as
the single source of truth for whether a dispatch should run, warn,
pause, or abort:

  ratio < 0.80          → "ok"
  0.80 ≤ ratio < 1.00   → "warn"
  1.00 ≤ ratio < 1.20   → "paused"        (AWAITING_BUDGET_APPROVAL)
  ratio ≥ 1.20          → "aborted"       (auto-save + stop)

On top of those bands, a monthly ceiling blocks non-founder principals
from initiating any new dispatch once the month is over budget —
founder-initiated dispatches still run (with a "warn" status) because
the founder is the only principal who can authorize spend above the
ceiling. `BudgetBlock` is the structured reason the orchestrator shows
in its denial path.

This module does NOT persist spend. Chunk 1a.9 adds `cost-log.jsonl`
writing on top of `record_spend()`. The in-memory ledger is enough for
the threshold tests here and for 1a.6/1a.7 threading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.config import CostEnvelope, get_cost_envelope


# ---------------------------------------------------------------------------
# Status constants (stable — downstream code pattern-matches on these)
# ---------------------------------------------------------------------------
STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_PAUSED = "paused"
STATUS_ABORTED = "aborted"
STATUS_BLOCKED = "blocked"  # month ceiling gate
STATUS_ACTIVE = "active"    # session state after approved resume

_WARN_RATIO = 0.80
_PAUSE_RATIO = 1.00
_ABORT_RATIO = 1.20


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
@dataclass
class BudgetSession:
    """One session's spend accounting. Not persisted; lives for a run.

    `envelope_max` defaults to the project-wide per-session cap from
    `core.config.get_cost_envelope()`. `month_max` is a coarser ceiling
    that bounds cumulative spend — pre-existing spend is stored on the
    session so `check_budget()` can gate a proposed dispatch without
    needing a separate source of truth.
    """

    session_id: str
    envelope_max: float = 0.0
    month_max: float = 0.0
    spent_this_session: float = 0.0
    spent_this_month: float = 0.0
    state: str = STATUS_OK

    @classmethod
    def from_default_envelope(cls, session_id: str) -> BudgetSession:
        env: CostEnvelope = get_cost_envelope()
        return cls(
            session_id=session_id,
            envelope_max=env.per_session_max,
            # Default month ceiling — 4× per-session envelope is a placeholder
            # until Phase 8 provisions real cost_envelope.yaml files. Document
            # the assumption so downstream readers don't treat it as settled.
            month_max=env.per_session_max * 4,
        )


@dataclass
class BudgetBlock:
    """Structured rejection reason returned when the month ceiling trips."""

    reason: str
    principal: str
    projected_month_spend: float
    month_max: float


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------
def check_budget(
    session: BudgetSession,
    proposed_cost: float = 0.0,
    principal: str = "founder",
) -> dict[str, Any]:
    """Return a status dict for a proposed dispatch of `proposed_cost` USD.

    The returned dict always has `status` and `ratio` keys; when the
    month ceiling blocks the call, `block` holds a `BudgetBlock`.
    Callers should pattern-match on `status` — see STATUS_* constants.
    """
    projected_session = session.spent_this_session + proposed_cost
    projected_month = session.spent_this_month + proposed_cost
    ratio = projected_session / session.envelope_max if session.envelope_max else 0.0

    # Month-ceiling gate — only non-founder principals are blocked.
    if principal != "founder" and projected_month > session.month_max:
        return {
            "status": STATUS_BLOCKED,
            "ratio": ratio,
            "block": BudgetBlock(
                reason="month ceiling exceeded",
                principal=principal,
                projected_month_spend=projected_month,
                month_max=session.month_max,
            ),
        }

    if ratio >= _ABORT_RATIO:
        return {"status": STATUS_ABORTED, "ratio": ratio}
    if ratio >= _PAUSE_RATIO:
        return {"status": STATUS_PAUSED, "ratio": ratio}
    if ratio >= _WARN_RATIO:
        return {"status": STATUS_WARN, "ratio": ratio}
    return {"status": STATUS_OK, "ratio": ratio}


def record_spend(session: BudgetSession, cost: float) -> None:
    """Add `cost` USD to both the session and month counters."""
    if cost < 0:
        raise ValueError("cost must be non-negative")
    session.spent_this_session += cost
    session.spent_this_month += cost


def get_status(session: BudgetSession) -> str:
    """Return the current session state (`ok`/`warn`/`paused`/`active`/...)."""
    return session.state


def pause_session(session: BudgetSession) -> None:
    """Mark the session as paused — dispatches should halt until resume."""
    session.state = STATUS_PAUSED


def resume_session(session: BudgetSession, approval: bool) -> str:
    """Transition `paused` → `active` when `approval` is truthy.

    Returns the resulting state. A non-approval is a no-op that leaves
    the session paused so the orchestrator can loop on user prompt.
    """
    if approval and session.state == STATUS_PAUSED:
        session.state = STATUS_ACTIVE
    return session.state
