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


# ---------------------------------------------------------------------------
# Budget holds with TTL (v6 Weeks 4-5 deliverable 7)
# ---------------------------------------------------------------------------
# A "hold" is a provisional commitment against the remaining budget. Used
# by the deterministic evaluator to pre-reserve spend at approval time so
# a second concurrent request cannot also see the full remaining budget
# and double-spend it. Holds carry a TTL; a background sweep releases
# orphaned holds (agent crashed, never confirmed, etc.) so the budget
# envelope eventually heals.

import threading as _threading
import uuid as _uuid
from datetime import datetime as _datetime, timedelta as _timedelta, timezone as _timezone
from dataclasses import dataclass as _dataclass, field as _field


DEFAULT_HOLD_TTL = _timedelta(hours=4)


@_dataclass
class BudgetWallet:
    """Int-cents wallet used by the v6 holds primitive.

    Distinct from the legacy float-dollars `BudgetSession` above
    because the evaluator (Memory layer) works in int cents and the
    hold math must be exact. Legacy and new coexist; a future pass
    unifies them in Weeks 9-12 when the rewrite of cost.py lands."""
    wallet_id: str
    budget_usd_cents: int
    actual_spend_usd_cents: int = 0


@_dataclass(frozen=True)
class BudgetHold:
    """A provisional reservation against a BudgetWallet.

    The hold is converted to a real spend via `confirm_hold(wallet, hold)`
    (moves the reserved amount into actual_spend) or released via
    `release_hold(wallet, hold)` (returns to remaining budget)."""
    hold_id: str
    amount_usd_cents: int
    created_at: str  # ISO-8601 UTC
    expires_at: str  # ISO-8601 UTC
    reason: str = ""


@_dataclass
class BudgetHoldLedger:
    """Intra-process registry of live holds against a BudgetSession.

    The ledger is NOT persisted by default; callers that need durable
    holds across process restarts wrap this with `write_holds_to_disk`
    (v6 Weeks 9-12 operational surface). For now the holds live only
    while the process runs, which is acceptable because the TTL sweep
    releases stale holds regardless."""
    holds: dict[str, BudgetHold] = _field(default_factory=dict)
    _lock: _threading.Lock = _field(default_factory=_threading.Lock)

    def total_held_cents(self) -> int:
        with self._lock:
            return sum(h.amount_usd_cents for h in self.holds.values())

    def register(self, hold: BudgetHold) -> None:
        with self._lock:
            self.holds[hold.hold_id] = hold

    def pop(self, hold_id: str) -> BudgetHold | None:
        with self._lock:
            return self.holds.pop(hold_id, None)

    def iter_all(self):
        with self._lock:
            return list(self.holds.values())


class BudgetHoldError(RuntimeError):
    """Base class for hold-specific failures."""


class InsufficientBudgetForHold(BudgetHoldError):
    """Raised when an attempted hold exceeds remaining budget minus
    already-held amount. Evaluator treats this as an AUTO_DENY."""


class HoldNotFound(BudgetHoldError):
    """Raised when confirm / release targets an unknown hold id."""


class HoldExpired(BudgetHoldError):
    """Raised when confirm_hold targets a hold whose TTL has passed.
    The hold is released; caller must re-request a fresh one."""


def _utc_now() -> _datetime:
    return _datetime.now(_timezone.utc)


def place_hold(
    wallet: "BudgetWallet",
    ledger: BudgetHoldLedger,
    amount_usd_cents: int,
    *,
    ttl: _timedelta = DEFAULT_HOLD_TTL,
    reason: str = "",
    now: _datetime | None = None,
) -> BudgetHold:
    """Reserve `amount_usd_cents` against the session. Raises
    `InsufficientBudgetForHold` when the session + existing holds do
    not leave enough headroom."""
    resolved_now = now or _utc_now()
    if amount_usd_cents < 0:
        raise ValueError("hold amount must be non-negative")
    total = wallet.budget_usd_cents
    used = wallet.actual_spend_usd_cents
    held = ledger.total_held_cents()
    headroom = total - used - held
    if amount_usd_cents > headroom:
        raise InsufficientBudgetForHold(
            f"hold for {amount_usd_cents}c exceeds headroom {headroom}c "
            f"(budget={total}c, spent={used}c, held={held}c)",
        )
    hold = BudgetHold(
        hold_id=f"hold_{_uuid.uuid4().hex}",
        amount_usd_cents=amount_usd_cents,
        created_at=resolved_now.isoformat(),
        expires_at=(resolved_now + ttl).isoformat(),
        reason=reason,
    )
    ledger.register(hold)
    return hold


def confirm_hold(
    wallet: "BudgetWallet",
    ledger: BudgetHoldLedger,
    hold_id: str,
    *,
    actual_amount_usd_cents: int | None = None,
    now: _datetime | None = None,
) -> int:
    """Convert a hold to actual spend. Returns the amount actually
    billed (the hold amount, or `actual_amount_usd_cents` when the
    live charge differed from the pre-reserve).

    A None `actual_amount_usd_cents` means bill exactly the held
    amount; otherwise the caller declares the real charge and any
    difference is released back to remaining budget.
    """
    resolved_now = now or _utc_now()
    hold = ledger.pop(hold_id)
    if hold is None:
        raise HoldNotFound(hold_id)
    expires = _parse_iso(hold.expires_at)
    if resolved_now > expires:
        raise HoldExpired(
            f"hold {hold_id} expired at {hold.expires_at}; caller must "
            "request a fresh hold",
        )
    billed = hold.amount_usd_cents if actual_amount_usd_cents is None else actual_amount_usd_cents
    if billed < 0:
        raise ValueError("actual_amount must be non-negative")
    if billed > hold.amount_usd_cents:
        # Caller over-spent the reservation; charge the full actual.
        wallet.actual_spend_usd_cents += billed
    else:
        wallet.actual_spend_usd_cents += billed
    return billed


def release_hold(
    ledger: BudgetHoldLedger,
    hold_id: str,
) -> BudgetHold:
    """Release a hold back to remaining budget without spending it.
    Typical on dispatcher-side failure: the handler errored before
    the charge went through, so the reservation should not stand."""
    hold = ledger.pop(hold_id)
    if hold is None:
        raise HoldNotFound(hold_id)
    return hold


def sweep_expired_holds(
    ledger: BudgetHoldLedger,
    *,
    now: _datetime | None = None,
) -> list[BudgetHold]:
    """Release every expired hold. Intended to run on a timer; the
    default TTL is 4 hours so hourly sweeps are more than sufficient.
    Returns the list of released holds so the caller can log them."""
    resolved_now = now or _utc_now()
    released: list[BudgetHold] = []
    for hold in list(ledger.iter_all()):
        expires = _parse_iso(hold.expires_at)
        if resolved_now > expires:
            popped = ledger.pop(hold.hold_id)
            if popped is not None:
                released.append(popped)
    return released


def _parse_iso(timestamp: str) -> _datetime:
    s = timestamp.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = _datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_timezone.utc)
    return dt
