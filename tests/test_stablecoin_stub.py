"""
tests/test_stablecoin_stub.py — Ticket 4 coverage
=================================================
Tests for `core.primitives.settlement_adapters.stablecoin_stub.
StablecoinStubAdapter`.

The stub exists to prove the `SettlementAdapter` Protocol and the
`AdapterRegistry` survive a realistic real-chain shape without any
schema change. These tests therefore focus on:

- structural Protocol conformance (runtime_checkable isinstance check)
- `supports()` semantics over the supported_assets tuple
- every network op raising the exact v0 NotImplementedError message
- constructor storing rpc_url and sender_address on public attributes
- end-to-end routing through an AdapterRegistry, then confirming a
  routed `lock()` raises NotImplementedError (the wiring works even
  though the ops don't)
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.primitives.asset import AssetRef
from core.primitives.money import Money
from core.primitives.settlement_adapters import (
    AdapterRegistry,
    EscrowHandle,
    EscrowHandleId,
    SettlementAdapter,
    StablecoinStubAdapter,
)


_NOT_IMPL_MSG = "stablecoin adapter v0: network ops out of scope"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_adapter(asset_registry, asset_ids=("mock-usd",)):
    """Build a StablecoinStubAdapter covering the given asset ids."""
    assets = tuple(asset_registry.get(aid) for aid in asset_ids)
    return StablecoinStubAdapter(
        supported_assets=assets,
        rpc_url="https://rpc.example.test/v1",
        sender_address="0xDEADBEEF00000000000000000000000000000000",
    )


def _fake_handle(asset: AssetRef) -> EscrowHandle:
    """Construct a dummy EscrowHandle for calls that raise before use."""
    return EscrowHandle(
        handle_id=EscrowHandleId("deadbeef"),
        asset=asset,
        locked_amount=Money(Decimal("1"), asset),
        ref="sla-x",
    )


# ---------------------------------------------------------------------------
# Structural Protocol conformance
# ---------------------------------------------------------------------------
def test_stablecoin_stub_satisfies_settlement_adapter_protocol(asset_registry):
    """runtime_checkable Protocol isinstance check passes."""
    adapter = _make_adapter(asset_registry)
    assert isinstance(adapter, SettlementAdapter)


# ---------------------------------------------------------------------------
# Constructor attribute storage
# ---------------------------------------------------------------------------
def test_constructor_stores_rpc_url_and_sender_address(asset_registry):
    usd = asset_registry.get("mock-usd")
    adapter = StablecoinStubAdapter(
        supported_assets=(usd,),
        rpc_url="https://rpc.example.test/v1",
        sender_address="0xABC",
    )
    assert adapter.rpc_url == "https://rpc.example.test/v1"
    assert adapter.sender_address == "0xABC"
    assert adapter.supported_assets == (usd,)


def test_constructor_rejects_empty_supported_assets():
    with pytest.raises(ValueError):
        StablecoinStubAdapter(
            supported_assets=(),
            rpc_url="https://rpc.example.test/v1",
            sender_address="0xABC",
        )


# ---------------------------------------------------------------------------
# supports() semantics
# ---------------------------------------------------------------------------
def test_supports_true_for_registered_asset(asset_registry):
    adapter = _make_adapter(asset_registry, ("mock-usd",))
    usd = asset_registry.get("mock-usd")
    assert adapter.supports(usd) is True


def test_supports_false_for_unregistered_asset(asset_registry):
    adapter = _make_adapter(asset_registry, ("mock-usd",))
    eur = asset_registry.get("mock-eur")
    assert adapter.supports(eur) is False


def test_supports_multi_asset(asset_registry):
    """A single stub may claim several assets (EVM-style forward compat)."""
    adapter = _make_adapter(
        asset_registry, ("mock-usd", "mock-eur", "usdc-base")
    )
    for aid in ("mock-usd", "mock-eur", "usdc-base"):
        assert adapter.supports(asset_registry.get(aid)) is True


# ---------------------------------------------------------------------------
# Network ops all raise NotImplementedError with the expected message
# ---------------------------------------------------------------------------
def test_lock_raises_not_implemented(asset_registry):
    adapter = _make_adapter(asset_registry)
    usd = asset_registry.get("mock-usd")
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.lock(Money(Decimal("5"), usd), "sla-1", nonce="n1")
    assert str(excinfo.value) == _NOT_IMPL_MSG


def test_release_raises_not_implemented(asset_registry):
    adapter = _make_adapter(asset_registry)
    usd = asset_registry.get("mock-usd")
    handle = _fake_handle(usd)
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.release(handle, to="bob")
    assert str(excinfo.value) == _NOT_IMPL_MSG


def test_slash_raises_not_implemented(asset_registry):
    adapter = _make_adapter(asset_registry)
    usd = asset_registry.get("mock-usd")
    handle = _fake_handle(usd)
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.slash(handle, percent=50, beneficiary=None)
    assert str(excinfo.value) == _NOT_IMPL_MSG


def test_balance_raises_not_implemented(asset_registry):
    adapter = _make_adapter(asset_registry)
    usd = asset_registry.get("mock-usd")
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.balance("alice", usd)
    assert str(excinfo.value) == _NOT_IMPL_MSG


def test_get_status_raises_not_implemented(asset_registry):
    adapter = _make_adapter(asset_registry)
    usd = asset_registry.get("mock-usd")
    handle = _fake_handle(usd)
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.get_status(handle)
    assert str(excinfo.value) == _NOT_IMPL_MSG


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------
def test_stub_registers_in_adapter_registry_and_routes(asset_registry):
    """The stub is routable: registry.adapter_for returns it by asset."""
    adapter = _make_adapter(asset_registry, ("mock-usd",))
    reg = AdapterRegistry(asset_registry)
    reg.register(adapter)

    usd = asset_registry.get("mock-usd")
    resolved = reg.adapter_for(usd)
    assert resolved is adapter


def test_routed_lock_still_raises_not_implemented(asset_registry):
    """Registry wiring works, but the actual call goes nowhere in v0."""
    adapter = _make_adapter(asset_registry, ("mock-usd",))
    reg = AdapterRegistry(asset_registry)
    reg.register(adapter)

    usd = asset_registry.get("mock-usd")
    resolved = reg.adapter_for(usd)
    with pytest.raises(NotImplementedError) as excinfo:
        resolved.lock(Money(Decimal("10"), usd), "sla-x", nonce="n-x")
    assert str(excinfo.value) == _NOT_IMPL_MSG
