"""core.primitives — shared building blocks used across the orchestrator.

Each module in this package is intentionally dependency-light: it may
import from `core.config`, `core.llm_client`, and stdlib only. It must
NOT import from `board`, `meeting`, `orchestrator`, or `onboarding` —
those import FROM primitives, not the other way around. Chunk 1a.3's
acceptance test guards this by attempting `from core.primitives.cost
import check_budget` in isolation.

Ticket 7 of the currency-agnostic settlement architecture adds top-level
re-exports for the full settlement primitive surface so callers can
write:

    from core.primitives import (
        Money, AssetRef, AssetRegistry,
        InterOrgSLA,
        SettlementAdapter, AdapterRegistry,
        EscrowHandle, SettlementReceipt, EscrowStatus,
        Ed25519Keypair, Ed25519PublicKey, Signature, sign, verify,
        SettlementEvent, SettlementEventLedger,
        NodeRegistry,
        SettlementError, AssetMismatchError, UnsupportedAssetError,
        EscrowStateError, InexactQuantizationError,
        AdapterConflictError, SignatureError,
        MockSettlementAdapter, StablecoinStubAdapter,
    )

See `core/primitives/SETTLEMENT.md` for the end-to-end flow and threat
model, and `core/primitives/asset_registry/README.md` for the
"add a new asset" walkthrough.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Settlement primitives (Tickets 0 – 10)
# ---------------------------------------------------------------------------
from core.primitives.asset import AssetRef, AssetRegistry
from core.primitives.money import Money
from core.primitives.sla import InterOrgSLA
from core.primitives.identity import (
    Ed25519Keypair,
    Ed25519PublicKey,
    Signature,
    sign,
    verify,
)
from core.primitives.node_registry import NodeRegistry
from core.primitives.settlement_adapters import (
    AdapterRegistry,
    EscrowHandle,
    EscrowHandleId,
    EscrowStatus,
    MockSettlementAdapter,
    SettlementAdapter,
    SettlementReceipt,
    StablecoinStubAdapter,
)
from core.primitives.settlement_ledger import (
    SettlementEvent,
    SettlementEventLedger,
)
from core.primitives.exceptions import (
    AdapterConflictError,
    AssetMismatchError,
    EscrowStateError,
    InexactQuantizationError,
    SettlementError,
    SignatureError,
    UnsupportedAssetError,
)

__all__ = [
    # Money / assets
    "AssetRef",
    "AssetRegistry",
    "Money",
    # SLA
    "InterOrgSLA",
    # Identity / signing
    "Ed25519Keypair",
    "Ed25519PublicKey",
    "Signature",
    "sign",
    "verify",
    # Node registry
    "NodeRegistry",
    # Settlement adapters
    "AdapterRegistry",
    "EscrowHandle",
    "EscrowHandleId",
    "EscrowStatus",
    "MockSettlementAdapter",
    "SettlementAdapter",
    "SettlementReceipt",
    "StablecoinStubAdapter",
    # Ledger
    "SettlementEvent",
    "SettlementEventLedger",
    # Exceptions
    "AdapterConflictError",
    "AssetMismatchError",
    "EscrowStateError",
    "InexactQuantizationError",
    "SettlementError",
    "SignatureError",
    "UnsupportedAssetError",
]
