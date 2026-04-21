"""
core/primitives/settlement_adapters/mock_adapter.py — in-memory settlement
==========================================================================

Ticket 3 of the v0 Currency-Agnostic Settlement Architecture.

`MockSettlementAdapter` is a pure-Python, single-process, in-memory
adapter used by tests and the scenario simulator. It conforms to the
`SettlementAdapter` protocol from `base.py` with two deliberate
extensions for mock-only use:

1. `fund(principal, amount)` — credits a principal's balance out of
   thin air. Real adapters infer balances from on-chain state; the mock
   needs an explicit seed path so tests can set up initial positions.
2. `lock(..., *, principal: str)` — adds a keyword-only `principal`
   parameter the Protocol does not require. Real adapters infer the
   locker from wallet context (msg.sender, session key, etc.); the mock
   has no wallet, so the caller names the locker explicitly. The
   scenario ledger (Ticket 6) will wire this.

Replay resistance: every `lock` must carry a nonce. The adapter tracks
consumed nonces in `_consumed_nonces` and rejects any reuse with
`EscrowStateError("nonce replay detected")`, even if other fields
differ. Nonces are never removed — this is append-only.

The optional `ledger=None` constructor kwarg is forward-compat for
Ticket 9's `SettlementEventLedger`. It is stored but not used; the
import of the ledger class intentionally lives in Ticket 9 to avoid
reverse-dependency.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.primitives.asset import AssetRef
from core.primitives.exceptions import (
    EscrowStateError,
    UnsupportedAssetError,
)
from core.primitives.money import Money
from core.primitives.settlement_adapters.base import (
    EscrowHandle,
    EscrowHandleId,
    EscrowStatus,
    SettlementReceipt,
)


# ---------------------------------------------------------------------------
# Internal escrow record
# ---------------------------------------------------------------------------
@dataclass
class _EscrowRecord:
    """Per-escrow state the mock adapter maintains.

    Callers must never touch this — exposed only for internal bookkeeping
    within the adapter. The adapter surfaces state exclusively through
    `get_status`, `balance`, and `SettlementReceipt` return values.
    """

    handle: EscrowHandle
    locker: str                  # principal who funded the lock
    status: EscrowStatus         # "locked" | "released" | "slashed"


def _utc_z_now() -> str:
    """Return the current time as `YYYY-MM-DDTHH:MM:SSZ` (no sub-seconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MockSettlementAdapter:
    """In-memory settlement adapter. Structurally implements `SettlementAdapter`.

    Construct with the tuple of assets it handles. Fund principals
    explicitly via `fund` before any lock; `lock` deducts from the named
    principal's balance. `release` credits the destination principal;
    `slash` sends a fraction to burn or a beneficiary, and the remainder
    back to the original locker.

    Single-threaded by design: no locks, no reentrancy defense. The
    scenario simulator dispatches sequentially.
    """

    def __init__(
        self,
        supported_assets: tuple[AssetRef, ...],
        *,
        ledger: Any = None,
    ) -> None:
        if not supported_assets:
            raise ValueError(
                "MockSettlementAdapter requires at least one supported AssetRef"
            )
        self._supported_ids: set[str] = {a.asset_id for a in supported_assets}
        self._supported_refs: dict[str, AssetRef] = {
            a.asset_id: a for a in supported_assets
        }
        # Wired in Ticket 9 by SettlementEventLedger; intentionally unused here.
        self._ledger = ledger

        # In-memory state.
        self.balances: dict[tuple[str, str], Money] = {}
        self.escrows: dict[EscrowHandleId, _EscrowRecord] = {}
        self._consumed_nonces: set[str] = set()

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------
    def supports(self, asset: AssetRef) -> bool:
        return asset.asset_id in self._supported_ids

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------
    def balance(self, principal: str, asset: AssetRef) -> Money:
        """Return the current balance for `(principal, asset)`. Zero if unseen."""
        key = (principal, asset.asset_id)
        if key in self.balances:
            return self.balances[key]
        # Use the registered ref — but the caller's `asset` is fine too,
        # since equality is asset_id-driven.
        return Money.zero(asset)

    def fund(self, principal: str, amount: Money) -> None:
        """MOCK-only helper: credit `principal` with `amount`.

        Real adapters derive balances from chain state; the mock needs
        a seeded entry point so tests can establish starting positions.
        Not part of the SettlementAdapter Protocol.
        """
        if not self.supports(amount.asset):
            raise UnsupportedAssetError(
                f"MockSettlementAdapter does not support {amount.asset.asset_id!r}"
            )
        key = (principal, amount.asset.asset_id)
        current = self.balances.get(key, Money.zero(amount.asset))
        self.balances[key] = current + amount

    # ------------------------------------------------------------------
    # Lock
    # ------------------------------------------------------------------
    def lock(
        self,
        amount: Money,
        ref: str,
        *,
        nonce: str,
        principal: str,
    ) -> EscrowHandle:
        """Lock `amount` from `principal` under external `ref`.

        Extends the SettlementAdapter protocol with a mock-only
        `principal` kwarg; real adapters infer the locker from wallet
        context.

        Raises:
            EscrowStateError: nonce was already consumed (replay).
            UnsupportedAssetError: adapter does not support the asset.
            ValueError: insufficient balance.
        """
        if nonce in self._consumed_nonces:
            raise EscrowStateError("nonce replay detected")
        if not self.supports(amount.asset):
            raise UnsupportedAssetError(
                f"MockSettlementAdapter does not support {amount.asset.asset_id!r}"
            )

        key = (principal, amount.asset.asset_id)
        current = self.balances.get(key, Money.zero(amount.asset))
        if current.quantity < amount.quantity:
            raise ValueError(
                f"insufficient balance for {principal!r}: have "
                f"{current.to_dict()}, need {amount.to_dict()}"
            )

        # Debit the locker; consume nonce; record escrow.
        self.balances[key] = current - amount
        self._consumed_nonces.add(nonce)

        handle_id = EscrowHandleId(uuid.uuid4().hex)
        handle = EscrowHandle(
            handle_id=handle_id,
            asset=amount.asset,
            locked_amount=amount,
            ref=ref,
        )
        self.escrows[handle_id] = _EscrowRecord(
            handle=handle,
            locker=principal,
            status="locked",
        )

        self._record_event(
            kind="lock",
            handle_id=str(handle_id),
            asset_id=amount.asset.asset_id,
            amount_quantity_str=str(amount.quantity),
            sla_id=ref,
            outcome_receipt=None,
            metadata={"locker": principal, "nonce": nonce},
        )
        return handle

    # ------------------------------------------------------------------
    # Release
    # ------------------------------------------------------------------
    def release(self, handle: EscrowHandle, to: str) -> SettlementReceipt:
        """Release the escrow to principal `to`. Credits their balance."""
        record = self.escrows.get(handle.handle_id)
        if record is None:
            raise EscrowStateError(
                f"unknown escrow handle {handle.handle_id!r}"
            )
        if record.status != "locked":
            raise EscrowStateError(
                f"cannot release escrow {handle.handle_id!r} in state "
                f"{record.status!r}"
            )

        amount = record.handle.locked_amount
        asset = record.handle.asset

        # Credit destination principal.
        dest_key = (to, asset.asset_id)
        current = self.balances.get(dest_key, Money.zero(asset))
        self.balances[dest_key] = current + amount

        record.status = "released"

        receipt = SettlementReceipt(
            handle_id=handle.handle_id,
            outcome="released",
            to=to,
            transferred=amount,
            burned=Money.zero(asset),
            ts=_utc_z_now(),
        )
        self._record_event(
            kind="release",
            handle_id=str(handle.handle_id),
            asset_id=asset.asset_id,
            amount_quantity_str=str(amount.quantity),
            sla_id=handle.ref,
            outcome_receipt=receipt.to_dict(),
            metadata={"locker": record.locker, "to": to},
        )
        return receipt

    # ------------------------------------------------------------------
    # Slash
    # ------------------------------------------------------------------
    def slash(
        self,
        handle: EscrowHandle,
        percent: int,
        beneficiary: str | None,
    ) -> SettlementReceipt:
        """Slash `percent`% of the escrow. Remainder returns to original locker.

        If `beneficiary is None`, the slashed fraction is burned.
        Otherwise it is transferred to `beneficiary`.
        """
        record = self.escrows.get(handle.handle_id)
        if record is None:
            raise EscrowStateError(
                f"unknown escrow handle {handle.handle_id!r}"
            )
        if record.status != "locked":
            raise EscrowStateError(
                f"cannot slash escrow {handle.handle_id!r} in state "
                f"{record.status!r}"
            )
        if not (0 <= percent <= 100):
            raise ValueError(f"slash percent must be in [0, 100], got {percent}")

        amount = record.handle.locked_amount
        asset = record.handle.asset

        # Compute split. Money * Decimal quantizes to asset precision.
        from decimal import Decimal
        slashed_fraction = Decimal(percent) / Decimal(100)
        remainder_fraction = Decimal(100 - percent) / Decimal(100)
        slashed_amount = amount * slashed_fraction
        remainder_amount = amount * remainder_fraction

        # Credit remainder back to the original locker.
        locker_key = (record.locker, asset.asset_id)
        locker_bal = self.balances.get(locker_key, Money.zero(asset))
        self.balances[locker_key] = locker_bal + remainder_amount

        if beneficiary is None:
            transferred = Money.zero(asset)
            burned = slashed_amount
            to = ""
        else:
            # Credit beneficiary's balance.
            ben_key = (beneficiary, asset.asset_id)
            ben_bal = self.balances.get(ben_key, Money.zero(asset))
            self.balances[ben_key] = ben_bal + slashed_amount
            transferred = slashed_amount
            burned = Money.zero(asset)
            to = beneficiary

        record.status = "slashed"

        receipt = SettlementReceipt(
            handle_id=handle.handle_id,
            outcome="slashed",
            to=to,
            transferred=transferred,
            burned=burned,
            ts=_utc_z_now(),
        )
        self._record_event(
            kind="slash",
            handle_id=str(handle.handle_id),
            asset_id=asset.asset_id,
            amount_quantity_str=str(amount.quantity),
            sla_id=handle.ref,
            outcome_receipt=receipt.to_dict(),
            metadata={
                "locker": record.locker,
                "beneficiary": beneficiary or "",
                "percent": percent,
            },
        )
        return receipt

    # ------------------------------------------------------------------
    # Ledger wiring (Ticket 9)
    # ------------------------------------------------------------------
    def _record_event(
        self,
        *,
        kind: str,
        handle_id: str,
        asset_id: str,
        amount_quantity_str: str,
        sla_id: str,
        outcome_receipt: dict | None,
        metadata: dict,
    ) -> None:
        """Emit a `SettlementEvent` to the attached ledger, if any.

        The ledger argument is optional (see constructor); when absent
        this is a no-op and the adapter behaves identically to the
        pre-Ticket-9 implementation. The import is lazy to keep the
        adapter import cycle-safe with `core.primitives.settlement_ledger`.
        """
        if self._ledger is None:
            return
        # Lazy import — ledger module may not be loaded yet and we want
        # to avoid a reverse dep at module-import time.
        from core.primitives.settlement_ledger import SettlementEvent

        event = SettlementEvent(
            kind=kind,  # type: ignore[arg-type]
            handle_id=handle_id,
            asset_id=asset_id,
            amount_quantity_str=amount_quantity_str,
            sla_id=sla_id,
            principals={
                "requester_did": "",
                "provider_did": "",
                "counterparty_pubkey_hex": "",
            },
            outcome_receipt=outcome_receipt,
            metadata=dict(metadata),
        )
        self._ledger.record(event)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def get_status(self, handle: EscrowHandle) -> EscrowStatus:
        """Lifecycle state of `handle`. Unknown raises EscrowStateError."""
        record = self.escrows.get(handle.handle_id)
        if record is None:
            raise EscrowStateError(
                f"unknown escrow handle {handle.handle_id!r}"
            )
        return record.status


__all__ = ["MockSettlementAdapter"]
