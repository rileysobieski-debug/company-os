"""
tests/test_primitives_exports.py — Ticket 7 smoke test
======================================================
Imports every name that Ticket 7 adds to the `core.primitives`
public surface and asserts each resolved to a non-None object.

If any of these fall out of sync with `core/primitives/__init__.py`,
this test fails fast — which is exactly what downstream callers who
depend on the top-level exports want.
"""
from __future__ import annotations


def test_primitives_top_level_exports_resolve() -> None:
    """Every name in the Ticket 7 import block must import cleanly."""
    from core.primitives import (
        Money,
        AssetRef,
        AssetRegistry,
        InterOrgSLA,
        SettlementAdapter,
        AdapterRegistry,
        EscrowHandle,
        SettlementReceipt,
        EscrowStatus,
        Ed25519Keypair,
        Ed25519PublicKey,
        Signature,
        sign,
        verify,
        SettlementEvent,
        SettlementEventLedger,
        NodeRegistry,
        SettlementError,
        AssetMismatchError,
        UnsupportedAssetError,
        EscrowStateError,
        InexactQuantizationError,
        AdapterConflictError,
        SignatureError,
    )

    # Each symbol must resolve to a non-None object (classes, functions,
    # or type aliases). This catches accidental `= None` placeholders.
    exports = {
        "Money": Money,
        "AssetRef": AssetRef,
        "AssetRegistry": AssetRegistry,
        "InterOrgSLA": InterOrgSLA,
        "SettlementAdapter": SettlementAdapter,
        "AdapterRegistry": AdapterRegistry,
        "EscrowHandle": EscrowHandle,
        "SettlementReceipt": SettlementReceipt,
        "EscrowStatus": EscrowStatus,
        "Ed25519Keypair": Ed25519Keypair,
        "Ed25519PublicKey": Ed25519PublicKey,
        "Signature": Signature,
        "sign": sign,
        "verify": verify,
        "SettlementEvent": SettlementEvent,
        "SettlementEventLedger": SettlementEventLedger,
        "NodeRegistry": NodeRegistry,
        "SettlementError": SettlementError,
        "AssetMismatchError": AssetMismatchError,
        "UnsupportedAssetError": UnsupportedAssetError,
        "EscrowStateError": EscrowStateError,
        "InexactQuantizationError": InexactQuantizationError,
        "AdapterConflictError": AdapterConflictError,
        "SignatureError": SignatureError,
    }
    for name, obj in exports.items():
        assert obj is not None, f"core.primitives.{name} resolved to None"


def test_exception_hierarchy_shared_root() -> None:
    """All settlement-error subclasses share the SettlementError root."""
    from core.primitives import (
        SettlementError,
        AssetMismatchError,
        UnsupportedAssetError,
        EscrowStateError,
        InexactQuantizationError,
        AdapterConflictError,
        SignatureError,
    )
    for cls in (
        AssetMismatchError,
        UnsupportedAssetError,
        EscrowStateError,
        InexactQuantizationError,
        AdapterConflictError,
        SignatureError,
    ):
        assert issubclass(cls, SettlementError), (
            f"{cls.__name__} must subclass SettlementError"
        )


def test_settlement_adapter_runtime_checkable() -> None:
    """MockSettlementAdapter structurally implements SettlementAdapter."""
    from core.primitives import (
        AssetRef,
        MockSettlementAdapter,
        SettlementAdapter,
    )
    usd = AssetRef(asset_id="mock-usd", decimals=6)
    adapter = MockSettlementAdapter(supported_assets=(usd,))
    assert isinstance(adapter, SettlementAdapter)
