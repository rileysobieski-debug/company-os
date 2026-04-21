"""
core/primitives/money.py — Money value type
============================================
Ticket 2 of the v0 Currency-Agnostic Settlement Architecture.

`Money` is a frozen dataclass pairing a `Decimal` quantity with an
`AssetRef`. The public constructor is STRICT:

    - float input raises TypeError (precision loss is not recoverable)
    - NaN / Infinity / negative quantities raise ValueError
    - input with more fractional digits than `asset.decimals` raises
      `InexactQuantizationError` — no silent rounding on the boundary

Arithmetic (`+`, `-`, `*`) is allowed ONLY between Money values of the
same asset. Scalar multiplication takes a `Decimal`. All three operators
compute in full Decimal precision then quantize the result to
`asset.decimals` via `ROUND_HALF_EVEN`. They route through the private
`Money._from_decimal_unchecked` factory so arithmetic results (which are
guaranteed to already be at asset precision after quantize) are not
re-validated by the strict constructor.

`to_dict()` emits fixed-notation strings — never float, never `1E-6`.
`from_dict()` is pure: the caller resolves `asset_id -> AssetRef`
through their own registry and passes the ref in.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

from core.primitives.asset import AssetRef
from core.primitives.exceptions import (
    AssetMismatchError,
    InexactQuantizationError,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _quantum(decimals: int) -> Decimal:
    """Return the Decimal exponent used to canonicalize `decimals` places.

    E.g. decimals=6 -> Decimal('0.000001'); decimals=0 -> Decimal('1').
    """
    if decimals < 0:
        raise ValueError(f"asset.decimals must be non-negative, got {decimals}")
    return Decimal(10) ** -decimals


def _coerce_to_decimal(quantity: Any) -> Decimal:
    """Strictly coerce `Decimal` or `str` into a `Decimal`.

    Floats are rejected outright — the precision-loss concern is the
    whole reason this primitive exists, so we refuse to paper over it.
    """
    if isinstance(quantity, float):
        raise TypeError(
            "Money rejects float input: binary floating-point loses "
            "precision for decimal currencies. Pass Decimal or str."
        )
    if isinstance(quantity, Decimal):
        return quantity
    if isinstance(quantity, str):
        try:
            return Decimal(quantity)
        except Exception as exc:
            raise ValueError(f"invalid Decimal literal: {quantity!r}") from exc
    if isinstance(quantity, int):
        # ints are exact — safe to accept.
        return Decimal(quantity)
    raise TypeError(
        f"Money quantity must be Decimal or str, got {type(quantity).__name__}"
    )


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Money:
    """Immutable quantity-of-asset value.

    The public constructor enforces the strict rules described in the
    module docstring. Arithmetic results bypass the strict constructor
    through `Money._from_decimal_unchecked`, which quantizes the value
    to `asset.decimals` with `ROUND_HALF_EVEN` before construction.
    """

    quantity: Decimal
    asset: AssetRef

    def __post_init__(self) -> None:
        # Normalize + validate the quantity. Because the dataclass is frozen,
        # we go through object.__setattr__ to rewrite `quantity` after
        # validation passes.
        q = _coerce_to_decimal(self.quantity)

        if q.is_nan():
            raise ValueError("Money quantity cannot be NaN")
        if q.is_infinite():
            raise ValueError("Money quantity cannot be Infinity")
        if q < 0:
            raise ValueError(f"Money quantity cannot be negative, got {q}")

        # Strict quantization: reject input with more fractional precision
        # than the asset allows. `Decimal("1")` and `Decimal("1.000000")`
        # both pass because their quantize() to the asset's precision ==
        # themselves numerically.
        quantum = _quantum(self.asset.decimals)
        if q != q.quantize(quantum, rounding=ROUND_HALF_EVEN):
            raise InexactQuantizationError(
                f"quantity {q} has more precision than asset "
                f"{self.asset.asset_id!r} allows "
                f"({self.asset.decimals} decimals)"
            )

        # Canonicalize: re-quantize so the stored Decimal carries the
        # asset's exact exponent. This makes `to_dict()` output stable
        # (no scientific notation for tiny values like 1E-6) without
        # changing numeric equality.
        object.__setattr__(self, "quantity", q.quantize(quantum, rounding=ROUND_HALF_EVEN))

    # ------------------------------------------------------------------
    # Internal factory — bypasses strict constructor
    # ------------------------------------------------------------------
    @classmethod
    def _from_decimal_unchecked(cls, qty: Decimal, asset: AssetRef) -> "Money":
        """Build a Money from a raw Decimal, rounding to asset precision.

        System-generated arithmetic results use this path so full-precision
        intermediate results don't trip the strict constructor. The
        result is still non-negative and finite — we validate those here
        even on the "unchecked" path, because arithmetic can legitimately
        produce negatives (via subtraction) and we want a crisp error.
        """
        if qty.is_nan():
            raise ValueError("Money arithmetic result cannot be NaN")
        if qty.is_infinite():
            raise ValueError("Money arithmetic result cannot be Infinity")
        if qty < 0:
            raise ValueError(
                f"Money arithmetic result cannot be negative, got {qty}"
            )
        quantum = _quantum(asset.decimals)
        quantized = qty.quantize(quantum, rounding=ROUND_HALF_EVEN)
        # Construct directly by bypassing __init__'s strict checks:
        # we already validated above and quantized to the asset's precision.
        obj = cls.__new__(cls)
        object.__setattr__(obj, "quantity", quantized)
        object.__setattr__(obj, "asset", asset)
        return obj

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------
    @classmethod
    def zero(cls, asset: AssetRef) -> "Money":
        """Zero-quantity Money at the asset's precision."""
        return cls(Decimal(0), asset)

    # ------------------------------------------------------------------
    # Arithmetic — same-asset only
    # ------------------------------------------------------------------
    def _require_same_asset(self, other: "Money") -> None:
        if self.asset.asset_id != other.asset.asset_id:
            raise AssetMismatchError(
                f"cannot combine Money of {self.asset.asset_id!r} "
                f"with Money of {other.asset.asset_id!r}"
            )

    def __add__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_asset(other)
        return Money._from_decimal_unchecked(self.quantity + other.quantity, self.asset)

    def __sub__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_asset(other)
        return Money._from_decimal_unchecked(self.quantity - other.quantity, self.asset)

    def __mul__(self, scalar: Decimal) -> "Money":
        if isinstance(scalar, float):
            raise TypeError(
                "Money * float rejected: use Decimal for scalar multiplication."
            )
        if isinstance(scalar, int):
            scalar = Decimal(scalar)
        if not isinstance(scalar, Decimal):
            return NotImplemented
        if scalar.is_nan() or scalar.is_infinite():
            raise ValueError("Money scalar cannot be NaN or Infinity")
        return Money._from_decimal_unchecked(self.quantity * scalar, self.asset)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Emit a pure-string dict: `{"quantity": "...", "asset_id": "..."}`.

        The stored `quantity` already carries the asset's exact exponent,
        so plain `str()` gives fixed-notation output for all magnitudes.
        `f"{q:f}"` is used as a belt-and-suspenders guarantee against
        scientific notation for very small or very large values.
        """
        return {
            "quantity": f"{self.quantity:f}",
            "asset_id": self.asset.asset_id,
        }

    @classmethod
    def from_dict(cls, d: dict, asset: AssetRef) -> "Money":
        """Rehydrate a Money from a `to_dict` payload + resolved AssetRef.

        Pure: the caller owns asset-id resolution. Strict: applies the
        same quantization rules as the constructor.
        """
        if "quantity" not in d:
            raise ValueError(f"Money.from_dict missing 'quantity' key: {d!r}")
        if "asset_id" in d and d["asset_id"] != asset.asset_id:
            raise AssetMismatchError(
                f"from_dict asset_id {d['asset_id']!r} does not match "
                f"provided AssetRef {asset.asset_id!r}"
            )
        return cls(Decimal(str(d["quantity"])), asset)


__all__ = ["Money"]
