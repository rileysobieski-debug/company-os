"""
core/primitives/exceptions.py — Settlement exception hierarchy
==============================================================

Ticket 0 of the v0 Currency-Agnostic Settlement Architecture.

One flat `SettlementError` root with subclasses that name specific failure
modes. Callers catch the root when they want "any settlement failure"; they
catch a subclass when they want to recover from one specific mode (e.g.
`UnsupportedAssetError` triggers a fallback to a different asset).

Keep this module import-light: stdlib only. Settlement adapters and the
`Money` / `AssetRef` primitives raise these at their boundaries, but none
of them live here — a shallow module protects us from circular imports
when the book is eventually wired through.
"""
from __future__ import annotations


class SettlementError(Exception):
    """Root of the settlement exception hierarchy.

    Any exception raised by a settlement adapter, the adapter registry,
    or the Money / AssetRef primitives when enforcing settlement rules
    should subclass this. External callers can `except SettlementError`
    once to get them all.
    """


class AssetMismatchError(SettlementError):
    """Two `Money` operands disagree on `AssetRef`.

    Raised by `Money.__add__`, `__sub__`, comparison operators, and any
    aggregate that expects a homogeneous asset set. The error message
    should identify both assets so the caller can diagnose the mix.
    """


class UnsupportedAssetError(SettlementError):
    """An adapter was asked to handle an asset it does not support.

    Each adapter advertises the assets it can settle. The registry
    raises this when no registered adapter claims the requested asset,
    or when an adapter is invoked on an asset outside its declared set.
    """


class EscrowStateError(SettlementError):
    """An escrow operation was attempted in an incompatible state.

    Examples: releasing an already-released handle, slashing a
    never-locked handle, double-finalizing a receipt. Carries enough
    context for an operator to tell which transition was rejected.
    """


class InexactQuantizationError(SettlementError):
    """Quantization to an asset's `decimals` would lose precision.

    `Money` uses `Decimal` throughout and refuses to silently round.
    When an arithmetic result has more fractional digits than the
    asset's `decimals` allows, the primitive raises this rather than
    round-tripping through float.
    """


class AdapterConflictError(SettlementError):
    """Two adapters claim the same asset in the registry.

    The registry enforces a unique adapter per asset at registration
    time. Conflicts here indicate a build / configuration error, not a
    runtime recoverable condition.
    """


class SignatureError(SettlementError):
    """A receipt or escrow handle failed signature verification.

    V0 uses hash-backed integrity (see `core.primitives.integrity`). V1
    upgrades to cryptographic signatures over receipts. This error type
    is forward-compatible with both regimes.
    """


__all__ = [
    "SettlementError",
    "AssetMismatchError",
    "UnsupportedAssetError",
    "EscrowStateError",
    "InexactQuantizationError",
    "AdapterConflictError",
    "SignatureError",
]
