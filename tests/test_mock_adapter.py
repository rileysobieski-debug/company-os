"""
tests/test_mock_adapter.py — Ticket 3 coverage
==============================================
Tests for `core.primitives.settlement_adapters.mock_adapter.MockSettlementAdapter`.

Covered:
- supports() advertises capability correctly
- lock freezes balance; release transfers, emits valid SettlementReceipt
- get_status transitions locked -> released / slashed
- slash with burn (beneficiary=None): burns slash_amount, remainder to locker
- slash with beneficiary: transferred to beneficiary, remainder to locker
- double-release raises EscrowStateError
- get_status on unknown handle raises EscrowStateError
- unsupported asset raises UnsupportedAssetError in lock / fund
- balance() on unseen principal returns zero-Money
- nonce replay rejected across re-used nonces
- distinct nonces succeed independently
- multi-asset support: one adapter handles USD + EUR with separate balances
- SettlementReceipt.ts matches canonical UTC-Z form
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from core.primitives.asset import AssetRef
from core.primitives.exceptions import (
    EscrowStateError,
    UnsupportedAssetError,
)
from core.primitives.money import Money
from core.primitives.settlement_adapters.mock_adapter import MockSettlementAdapter


_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------
# Capability + unsupported assets
# ---------------------------------------------------------------------------
def test_supports_reports_configured_assets(asset_registry):
    usd = asset_registry.get("mock-usd")
    eur = asset_registry.get("mock-eur")
    adapter = MockSettlementAdapter((usd,))
    assert adapter.supports(usd) is True
    assert adapter.supports(eur) is False


def test_lock_unsupported_asset_raises(asset_registry):
    usd = asset_registry.get("mock-usd")
    eur = asset_registry.get("mock-eur")
    adapter = MockSettlementAdapter((usd,))
    with pytest.raises(UnsupportedAssetError):
        adapter.lock(
            Money(Decimal("1"), eur),
            ref="x",
            nonce="n0",
            principal="alice",
        )


def test_fund_unsupported_asset_raises(asset_registry):
    usd = asset_registry.get("mock-usd")
    eur = asset_registry.get("mock-eur")
    adapter = MockSettlementAdapter((usd,))
    with pytest.raises(UnsupportedAssetError):
        adapter.fund("alice", Money(Decimal("1"), eur))


def test_constructor_requires_at_least_one_asset():
    with pytest.raises(ValueError):
        MockSettlementAdapter(())


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------
def test_balance_unseen_principal_returns_zero(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    bal = adapter.balance("nobody", usd)
    assert bal == Money.zero(usd)


def test_fund_then_balance(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))
    assert adapter.balance("alice", usd) == Money(Decimal("100"), usd)


# ---------------------------------------------------------------------------
# Lock / release happy path
# ---------------------------------------------------------------------------
def test_lock_freezes_balance(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))

    handle = adapter.lock(
        Money(Decimal("30"), usd),
        ref="sla-1",
        nonce="n1",
        principal="alice",
    )

    # Balance debited; escrow status is "locked".
    assert adapter.balance("alice", usd) == Money(Decimal("70"), usd)
    assert adapter.get_status(handle) == "locked"
    assert handle.locked_amount == Money(Decimal("30"), usd)
    assert handle.ref == "sla-1"


def test_release_transfers_to_destination(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))
    handle = adapter.lock(
        Money(Decimal("30"), usd),
        ref="sla-1",
        nonce="n1",
        principal="alice",
    )
    receipt = adapter.release(handle, to="bob")

    assert receipt.outcome == "released"
    assert receipt.to == "bob"
    assert receipt.transferred == Money(Decimal("30"), usd)
    assert receipt.burned == Money.zero(usd)
    assert _TS_RE.match(receipt.ts), f"ts not canonical UTC-Z: {receipt.ts!r}"
    assert receipt.handle_id == handle.handle_id

    assert adapter.balance("bob", usd) == Money(Decimal("30"), usd)
    assert adapter.get_status(handle) == "released"


def test_lock_insufficient_balance_raises(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("10"), usd))
    with pytest.raises(ValueError):
        adapter.lock(
            Money(Decimal("50"), usd),
            ref="sla-1",
            nonce="n1",
            principal="alice",
        )


# ---------------------------------------------------------------------------
# Slash — burn and beneficiary
# ---------------------------------------------------------------------------
def test_slash_with_burn_beneficiary_none(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))
    handle = adapter.lock(
        Money(Decimal("40"), usd),
        ref="sla-1",
        nonce="n1",
        principal="alice",
    )

    receipt = adapter.slash(handle, percent=25, beneficiary=None)

    # 25% of 40 = 10 burned; remainder 30 returns to alice.
    assert receipt.outcome == "slashed"
    assert receipt.to == ""
    assert receipt.transferred == Money.zero(usd)
    assert receipt.burned == Money(Decimal("10"), usd)
    assert _TS_RE.match(receipt.ts)

    # Alice started with 100, locked 40 (balance 60), got 30 back.
    assert adapter.balance("alice", usd) == Money(Decimal("90"), usd)
    assert adapter.get_status(handle) == "slashed"


def test_slash_with_beneficiary(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))
    handle = adapter.lock(
        Money(Decimal("40"), usd),
        ref="sla-1",
        nonce="n1",
        principal="alice",
    )

    receipt = adapter.slash(handle, percent=25, beneficiary="carol")

    assert receipt.outcome == "slashed"
    assert receipt.to == "carol"
    assert receipt.transferred == Money(Decimal("10"), usd)
    assert receipt.burned == Money.zero(usd)

    assert adapter.balance("carol", usd) == Money(Decimal("10"), usd)
    # Alice: 100 - 40 locked + 30 returned = 90
    assert adapter.balance("alice", usd) == Money(Decimal("90"), usd)
    assert adapter.get_status(handle) == "slashed"


def test_slash_percent_out_of_range_raises(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))
    handle = adapter.lock(
        Money(Decimal("40"), usd),
        ref="sla-1",
        nonce="n1",
        principal="alice",
    )
    with pytest.raises(ValueError):
        adapter.slash(handle, percent=150, beneficiary=None)
    with pytest.raises(ValueError):
        adapter.slash(handle, percent=-1, beneficiary=None)


# ---------------------------------------------------------------------------
# Error transitions
# ---------------------------------------------------------------------------
def test_double_release_raises(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))
    handle = adapter.lock(
        Money(Decimal("30"), usd),
        ref="sla-1",
        nonce="n1",
        principal="alice",
    )
    adapter.release(handle, to="bob")
    with pytest.raises(EscrowStateError):
        adapter.release(handle, to="bob")


def test_slash_after_release_raises(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))
    handle = adapter.lock(
        Money(Decimal("30"), usd),
        ref="sla-1",
        nonce="n1",
        principal="alice",
    )
    adapter.release(handle, to="bob")
    with pytest.raises(EscrowStateError):
        adapter.slash(handle, percent=50, beneficiary=None)


def test_get_status_unknown_handle_raises(asset_registry):
    from core.primitives.settlement_adapters import EscrowHandle, EscrowHandleId
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    bogus = EscrowHandle(
        handle_id=EscrowHandleId("deadbeef"),
        asset=usd,
        locked_amount=Money(Decimal("1"), usd),
        ref="x",
    )
    with pytest.raises(EscrowStateError):
        adapter.get_status(bogus)


def test_release_unknown_handle_raises(asset_registry):
    from core.primitives.settlement_adapters import EscrowHandle, EscrowHandleId
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    bogus = EscrowHandle(
        handle_id=EscrowHandleId("deadbeef"),
        asset=usd,
        locked_amount=Money(Decimal("1"), usd),
        ref="x",
    )
    with pytest.raises(EscrowStateError):
        adapter.release(bogus, to="bob")


# ---------------------------------------------------------------------------
# Replay resistance
# ---------------------------------------------------------------------------
def test_nonce_replay_rejected(asset_registry):
    """Second lock with the same nonce must raise, even with other fields different."""
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))

    adapter.lock(
        Money(Decimal("10"), usd),
        ref="sla-1",
        nonce="same-nonce",
        principal="alice",
    )
    with pytest.raises(EscrowStateError) as excinfo:
        adapter.lock(
            Money(Decimal("20"), usd),  # different amount
            ref="sla-2",                  # different ref
            nonce="same-nonce",          # reused nonce
            principal="alice",
        )
    assert "nonce replay" in str(excinfo.value)


def test_distinct_nonces_succeed(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))

    h1 = adapter.lock(
        Money(Decimal("10"), usd),
        ref="sla-1",
        nonce="nonce-a",
        principal="alice",
    )
    h2 = adapter.lock(
        Money(Decimal("15"), usd),
        ref="sla-2",
        nonce="nonce-b",
        principal="alice",
    )
    assert h1.handle_id != h2.handle_id
    assert adapter.get_status(h1) == "locked"
    assert adapter.get_status(h2) == "locked"


def test_nonce_consumed_even_when_lock_other_checks_would_fail(asset_registry):
    """Design choice: replay check fires FIRST. This prevents attackers
    from probing whether a nonce was used by looking at error ordering."""
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    adapter.fund("alice", Money(Decimal("100"), usd))
    adapter.lock(
        Money(Decimal("10"), usd),
        ref="sla-1",
        nonce="nonce-x",
        principal="alice",
    )
    # Reuse nonce AND unsupported asset. Must raise replay, not unsupported.
    eur = AssetRef(asset_id="mock-eur", contract="EUR", decimals=2)
    with pytest.raises(EscrowStateError) as excinfo:
        adapter.lock(
            Money(Decimal("1"), eur),
            ref="sla-2",
            nonce="nonce-x",
            principal="alice",
        )
    assert "nonce replay" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Multi-asset support
# ---------------------------------------------------------------------------
def test_multi_asset_adapter_keeps_balances_separate(asset_registry):
    usd = asset_registry.get("mock-usd")
    eur = asset_registry.get("mock-eur")
    adapter = MockSettlementAdapter((usd, eur))

    adapter.fund("alice", Money(Decimal("100"), usd))
    adapter.fund("alice", Money(Decimal("50"), eur))

    h_usd = adapter.lock(
        Money(Decimal("30"), usd),
        ref="sla-usd",
        nonce="n-usd",
        principal="alice",
    )
    h_eur = adapter.lock(
        Money(Decimal("20"), eur),
        ref="sla-eur",
        nonce="n-eur",
        principal="alice",
    )

    assert adapter.balance("alice", usd) == Money(Decimal("70"), usd)
    assert adapter.balance("alice", eur) == Money(Decimal("30"), eur)

    rcpt_usd = adapter.release(h_usd, to="bob")
    rcpt_eur = adapter.release(h_eur, to="bob")

    assert rcpt_usd.transferred == Money(Decimal("30"), usd)
    assert rcpt_eur.transferred == Money(Decimal("20"), eur)
    assert adapter.balance("bob", usd) == Money(Decimal("30"), usd)
    assert adapter.balance("bob", eur) == Money(Decimal("20"), eur)


# ---------------------------------------------------------------------------
# Forward-compat kwargs
# ---------------------------------------------------------------------------
def test_ledger_kwarg_default_none_no_writes(asset_registry):
    """Ticket 9: default `ledger=None` means no event emission. The
    adapter behaves identically to the pre-Ticket-9 code path."""
    usd = asset_registry.get("mock-usd")
    adapter = MockSettlementAdapter((usd,))
    assert adapter._ledger is None
    adapter.fund("alice", Money(Decimal("10"), usd))
    handle = adapter.lock(
        Money(Decimal("5"), usd),
        ref="sla-1",
        nonce="n1",
        principal="alice",
    )
    # Release and slash paths also work without a ledger.
    adapter.release(handle, to="bob")
