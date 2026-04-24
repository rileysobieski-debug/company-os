"""Budget holds with TTL tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.primitives.cost import (
    DEFAULT_HOLD_TTL,
    BudgetHold,
    BudgetHoldLedger,
    BudgetWallet,
    HoldExpired,
    HoldNotFound,
    InsufficientBudgetForHold,
    confirm_hold,
    place_hold,
    release_hold,
    sweep_expired_holds,
)


NOW = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)


def _session(*, budget_cents: int = 10_000, spent_cents: int = 0) -> BudgetWallet:
    return BudgetWallet(
        wallet_id="w1",
        budget_usd_cents=budget_cents,
        actual_spend_usd_cents=spent_cents,
    )


# ---------------------------------------------------------------------------
# place_hold
# ---------------------------------------------------------------------------
def test_place_hold_returns_hold_with_id_and_expiry() -> None:
    wallet = _session()
    ledger = BudgetHoldLedger()
    hold = place_hold(wallet, ledger, 500, now=NOW)
    assert hold.hold_id.startswith("hold_")
    assert hold.amount_usd_cents == 500
    assert hold.created_at == NOW.isoformat()


def test_place_hold_respects_remaining_budget() -> None:
    wallet = _session(budget_cents=1000, spent_cents=400)
    ledger = BudgetHoldLedger()
    place_hold(wallet, ledger, 500, now=NOW)
    # 1000 - 400 - 500 = 100 remaining. A 200c hold must fail.
    with pytest.raises(InsufficientBudgetForHold):
        place_hold(wallet, ledger, 200, now=NOW)


def test_place_hold_stacks_cumulative() -> None:
    wallet = _session(budget_cents=1000)
    ledger = BudgetHoldLedger()
    place_hold(wallet, ledger, 300, now=NOW)
    place_hold(wallet, ledger, 300, now=NOW)
    assert ledger.total_held_cents() == 600


def test_place_hold_negative_amount_raises() -> None:
    wallet = _session()
    ledger = BudgetHoldLedger()
    with pytest.raises(ValueError):
        place_hold(wallet, ledger, -1, now=NOW)


def test_place_hold_zero_is_allowed() -> None:
    wallet = _session()
    ledger = BudgetHoldLedger()
    place_hold(wallet, ledger, 0, now=NOW)
    assert ledger.total_held_cents() == 0


# ---------------------------------------------------------------------------
# confirm_hold
# ---------------------------------------------------------------------------
def test_confirm_hold_moves_amount_to_spend() -> None:
    wallet = _session(budget_cents=1000)
    ledger = BudgetHoldLedger()
    hold = place_hold(wallet, ledger, 500, now=NOW)
    billed = confirm_hold(wallet, ledger, hold.hold_id, now=NOW)
    assert billed == 500
    assert wallet.actual_spend_usd_cents == 500
    assert ledger.total_held_cents() == 0


def test_confirm_hold_overbill_charges_actual() -> None:
    wallet = _session(budget_cents=1000)
    ledger = BudgetHoldLedger()
    hold = place_hold(wallet, ledger, 100, now=NOW)
    billed = confirm_hold(wallet, ledger, hold.hold_id, actual_amount_usd_cents=150, now=NOW)
    assert billed == 150
    assert wallet.actual_spend_usd_cents == 150


def test_confirm_hold_underbill_releases_difference() -> None:
    wallet = _session(budget_cents=1000)
    ledger = BudgetHoldLedger()
    hold = place_hold(wallet, ledger, 500, now=NOW)
    billed = confirm_hold(wallet, ledger, hold.hold_id, actual_amount_usd_cents=100, now=NOW)
    assert billed == 100
    assert wallet.actual_spend_usd_cents == 100


def test_confirm_hold_unknown_raises() -> None:
    wallet = _session()
    ledger = BudgetHoldLedger()
    with pytest.raises(HoldNotFound):
        confirm_hold(wallet, ledger, "hold_nonexistent", now=NOW)


def test_confirm_hold_expired_raises() -> None:
    wallet = _session(budget_cents=1000)
    ledger = BudgetHoldLedger()
    hold = place_hold(wallet, ledger, 500, ttl=timedelta(minutes=5), now=NOW)
    much_later = NOW + timedelta(hours=1)
    with pytest.raises(HoldExpired):
        confirm_hold(wallet, ledger, hold.hold_id, now=much_later)


def test_confirm_hold_negative_actual_raises() -> None:
    wallet = _session(budget_cents=1000)
    ledger = BudgetHoldLedger()
    hold = place_hold(wallet, ledger, 100, now=NOW)
    with pytest.raises(ValueError):
        confirm_hold(wallet, ledger, hold.hold_id, actual_amount_usd_cents=-1, now=NOW)


# ---------------------------------------------------------------------------
# release_hold
# ---------------------------------------------------------------------------
def test_release_hold_returns_amount_to_budget() -> None:
    wallet = _session(budget_cents=1000)
    ledger = BudgetHoldLedger()
    hold = place_hold(wallet, ledger, 500, now=NOW)
    released = release_hold(ledger, hold.hold_id)
    assert released.hold_id == hold.hold_id
    assert ledger.total_held_cents() == 0
    # Session actual spend unchanged (hold never converted).
    assert wallet.actual_spend_usd_cents == 0


def test_release_hold_unknown_raises() -> None:
    ledger = BudgetHoldLedger()
    with pytest.raises(HoldNotFound):
        release_hold(ledger, "hold_ghost")


# ---------------------------------------------------------------------------
# sweep_expired_holds
# ---------------------------------------------------------------------------
def test_sweep_releases_expired_only() -> None:
    wallet = _session(budget_cents=10_000)
    ledger = BudgetHoldLedger()
    fresh = place_hold(wallet, ledger, 100, ttl=timedelta(hours=4), now=NOW)
    stale = place_hold(wallet, ledger, 200, ttl=timedelta(minutes=1), now=NOW)
    # Sweep 30 minutes later: the 1-minute hold is expired, the 4-hour one is not.
    released = sweep_expired_holds(ledger, now=NOW + timedelta(minutes=30))
    assert [h.hold_id for h in released] == [stale.hold_id]
    assert ledger.total_held_cents() == 100


def test_sweep_empty_ledger_is_noop() -> None:
    ledger = BudgetHoldLedger()
    assert sweep_expired_holds(ledger, now=NOW) == []


def test_sweep_all_expired_clears_ledger() -> None:
    wallet = _session(budget_cents=10_000)
    ledger = BudgetHoldLedger()
    for _ in range(5):
        place_hold(wallet, ledger, 100, ttl=timedelta(seconds=1), now=NOW)
    released = sweep_expired_holds(ledger, now=NOW + timedelta(minutes=5))
    assert len(released) == 5
    assert ledger.total_held_cents() == 0


# ---------------------------------------------------------------------------
# Dataclass + defaults
# ---------------------------------------------------------------------------
def test_default_hold_ttl_is_four_hours() -> None:
    assert DEFAULT_HOLD_TTL == timedelta(hours=4)


def test_hold_dataclass_is_frozen() -> None:
    hold = BudgetHold(
        hold_id="h_x", amount_usd_cents=100,
        created_at="2026-04-24T00:00:00+00:00",
        expires_at="2026-04-24T04:00:00+00:00",
        reason="test",
    )
    with pytest.raises(Exception):
        hold.amount_usd_cents = 200  # type: ignore[misc]


def test_ledger_iter_all_returns_snapshot(_session=_session) -> None:
    wallet = _session()
    ledger = BudgetHoldLedger()
    place_hold(wallet, ledger, 100, now=NOW)
    snapshot = ledger.iter_all()
    # Mutating ledger after iter_all snapshot must not affect the snapshot.
    place_hold(wallet, ledger, 200, now=NOW)
    assert len(snapshot) == 1
