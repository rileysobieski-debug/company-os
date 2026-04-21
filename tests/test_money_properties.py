"""
tests/test_money_properties.py — Ticket 2 Hypothesis properties
==============================================================
Property-based tests for `core.primitives.money.Money`.

The strategies generate only values that the strict constructor accepts:
non-negative Decimals with fractional precision <= asset.decimals. That
lets us test algebraic properties (commutativity, associativity,
round-trip, precision invariant) without fighting the constructor.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from core.primitives.asset import AssetRef
from core.primitives.money import Money


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
@st.composite
def valid_decimals(draw, decimals: int = 6, max_units: int = 1000) -> Decimal:
    """Draw a non-negative Decimal with at most `decimals` fractional digits.

    Internally we pick an integer in [0, max_units * 10**decimals] and
    scale by 10**-decimals, so every drawn value is at the asset's
    precision by construction.
    """
    scale = 10 ** decimals
    scaled = draw(st.integers(min_value=0, max_value=max_units * scale))
    return (Decimal(scaled) / Decimal(scale)).quantize(Decimal(10) ** -decimals)


USD_6D = AssetRef(asset_id="mock-usd", contract="USD", decimals=6)
EUR_2D = AssetRef(asset_id="mock-eur", contract="EUR", decimals=2)


# ---------------------------------------------------------------------------
# 1. Round-trip — to_dict / from_dict is the identity
# ---------------------------------------------------------------------------
@given(valid_decimals(decimals=6))
@settings(max_examples=200)
def test_to_dict_from_dict_roundtrip_usd(q):
    m = Money(q, USD_6D)
    rehydrated = Money.from_dict(m.to_dict(), USD_6D)
    assert rehydrated == m
    # And equality propagates to the stored quantity.
    assert rehydrated.quantity == m.quantity


@given(valid_decimals(decimals=2, max_units=10_000))
@settings(max_examples=200)
def test_to_dict_from_dict_roundtrip_eur(q):
    m = Money(q, EUR_2D)
    rehydrated = Money.from_dict(m.to_dict(), EUR_2D)
    assert rehydrated == m


# ---------------------------------------------------------------------------
# 2. Commutativity of addition within an asset
# ---------------------------------------------------------------------------
@given(valid_decimals(), valid_decimals())
@settings(max_examples=200)
def test_add_commutes(a_q, b_q):
    a = Money(a_q, USD_6D)
    b = Money(b_q, USD_6D)
    assert (a + b) == (b + a)


# ---------------------------------------------------------------------------
# 3. Associativity of addition within an asset
# ---------------------------------------------------------------------------
@given(valid_decimals(), valid_decimals(), valid_decimals())
@settings(max_examples=200)
def test_add_associates(a_q, b_q, c_q):
    a = Money(a_q, USD_6D)
    b = Money(b_q, USD_6D)
    c = Money(c_q, USD_6D)
    assert ((a + b) + c) == (a + (b + c))


# ---------------------------------------------------------------------------
# 4. Arithmetic precision invariant — results at asset.decimals
# ---------------------------------------------------------------------------
def _fractional_digits(q: Decimal) -> int:
    """Return the count of digits after the point for a canonical Decimal."""
    exp = q.as_tuple().exponent
    # After quantize to N decimals, exponent == -N (or higher, e.g.
    # zero values can canonicalize to exponent 0). Either way, -exp
    # gives an upper bound on fractional digits.
    return max(-exp, 0) if isinstance(exp, int) else 0


@given(valid_decimals(), valid_decimals())
@settings(max_examples=200)
def test_add_never_exceeds_asset_precision(a_q, b_q):
    a = Money(a_q, USD_6D)
    b = Money(b_q, USD_6D)
    result = a + b
    # result.quantity carries the asset's exact exponent after quantize.
    assert _fractional_digits(result.quantity) <= USD_6D.decimals


@given(valid_decimals(), valid_decimals())
@settings(max_examples=200)
def test_sub_never_exceeds_asset_precision(a_q, b_q):
    if a_q < b_q:
        a_q, b_q = b_q, a_q  # avoid negative results
    a = Money(a_q, USD_6D)
    b = Money(b_q, USD_6D)
    result = a - b
    assert _fractional_digits(result.quantity) <= USD_6D.decimals


@given(valid_decimals(), st.integers(min_value=0, max_value=100))
@settings(max_examples=200)
def test_mul_never_exceeds_asset_precision(a_q, scalar_int):
    a = Money(a_q, USD_6D)
    result = a * Decimal(scalar_int)
    assert _fractional_digits(result.quantity) <= USD_6D.decimals
