"""core.primitives.settlement_adapters — settlement adapter surface.

Ticket 0 seeded this package with support types + `EscrowStatus`.
Ticket 3 adds the `SettlementAdapter` Protocol, the `AdapterRegistry`,
and the in-memory `MockSettlementAdapter` used by tests and the
scenario simulator.
"""
from __future__ import annotations

from core.primitives.settlement_adapters.base import (
    AdapterRegistry,
    EscrowHandle,
    EscrowHandleId,
    EscrowStatus,
    SettlementAdapter,
    SettlementReceipt,
)
from core.primitives.settlement_adapters.mock_adapter import MockSettlementAdapter
from core.primitives.settlement_adapters.stablecoin_stub import StablecoinStubAdapter

__all__ = [
    "AdapterRegistry",
    "EscrowHandle",
    "EscrowHandleId",
    "EscrowStatus",
    "MockSettlementAdapter",
    "SettlementAdapter",
    "SettlementReceipt",
    "StablecoinStubAdapter",
]
