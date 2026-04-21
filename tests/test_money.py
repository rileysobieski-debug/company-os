"""
tests/test_money.py — Ticket 2 unit coverage
============================================
Structural tests for `core.primitives.money.Money`.

Covers the full strict-constructor contract, arithmetic quantization,
cross-asset rejection, zero(), to_dict / from_dict round-trip (including
scientific-notation suppression), and asset-decimals variance.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.primitives.asset import AssetRef
from core.primitives.exceptions import (
    AssetMismatchError,
    InexactQuantizationError,
)
from core.primitives.money import Money


# ---------------------------------------------------------------------------
# Local fixtures — a 6-decimal USD, a 2-decimal EUR, and an alt-id USD
# ---------------------------------------------------------------------------
@pytest.fixture
def usd_6d() -> AssetRef:
    return AssetRef(asset_id="mock-usd", contract="USD", decimals=6)


@pytest.fixture
def eur_2d() -> AssetRef:
    return AssetRef(asset_id="mock-eur", contract="EUR", decimals=2)


@pytest.fixture
def usdc_6d() -> AssetRef:
    return AssetRef(asset_id="usdc-base", chain_id="base-mainnet", decimals=6)


# ---------------------------------------------------------------------------
# 1. Arithmetic happy path (same asset)
# ---------------------------------------------------------------------------
def test_add_same_asset(usd_6d):
    a = Money(Decimal("1.250000"), usd_6d)
    b = Money(Decimal("0.750000"), usd_6d)
    assert (a + b).quantity == Decimal("2.000000")
    assert (a + b).asset == usd_6d


def test_sub_same_asset(usd_6d):
    a = Money(Decimal("2.000000"), usd_6d)
    b = Money(Decimal("0.500000"), usd_6d)
    assert (a - b).quantity == Decimal("1.500000")


def test_mul_scalar_decimal(usd_6d):
    a = Money(Decimal("1.250000"), usd_6d)
    result = a * Decimal("2")
    assert result.quantity == Decimal("2.500000")


def test_mul_scalar_int(usd_6d):
    a = Money(Decimal("1.250000"), usd_6d)
    result = a * 3
    assert result.quantity == Decimal("3.750000")


def test_mul_scalar_rounds_half_even(usd_6d):
    # 1 / 3 * 3 — truly hard rounding test.
    # 0.333333 (already at 6d) * 3 = 0.999999 — clean.
    a = Money(Decimal("0.333333"), usd_6d)
    result = a * Decimal("3")
    assert result.quantity == Decimal("0.999999")


def test_mul_produces_high_precision_intermediate_then_quantizes(usd_6d):
    # Force a result with more than 6 decimals prior to quantization.
    # 0.000001 * 0.5 = 0.0000005 -> banker's round to 0.000000 (tie-to-even,
    # 0 is even).
    a = Money(Decimal("0.000001"), usd_6d)
    result = a * Decimal("0.5")
    assert result.quantity == Decimal("0.000000")


# ---------------------------------------------------------------------------
# 2. Cross-asset rejection
# ---------------------------------------------------------------------------
def test_add_cross_asset_raises(usd_6d, eur_2d):
    a = Money(Decimal("1"), usd_6d)
    b = Money(Decimal("1"), eur_2d)
    with pytest.raises(AssetMismatchError):
        _ = a + b


def test_sub_cross_asset_raises(usd_6d, eur_2d):
    a = Money(Decimal("2"), usd_6d)
    b = Money(Decimal("1"), eur_2d)
    with pytest.raises(AssetMismatchError):
        _ = a - b


# ---------------------------------------------------------------------------
# 3. Strict quantization
# ---------------------------------------------------------------------------
def test_too_precise_raises_inexact_quantization(usdc_6d):
    with pytest.raises(InexactQuantizationError):
        Money(Decimal("1.0000001"), usdc_6d)


def test_too_precise_eur_raises(eur_2d):
    with pytest.raises(InexactQuantizationError):
        Money(Decimal("1.234"), eur_2d)


# ---------------------------------------------------------------------------
# 4. Identical-at-precision equality
# ---------------------------------------------------------------------------
def test_equal_under_different_input_representations(usdc_6d):
    a = Money(Decimal("1"), usdc_6d)
    b = Money(Decimal("1.000000"), usdc_6d)
    assert a == b
    assert hash(a) == hash(b)


def test_canonical_quantity_after_construction(usdc_6d):
    a = Money(Decimal("1"), usdc_6d)
    # Stored with the asset's exact exponent so to_dict emits "1.000000".
    assert str(a.quantity) == "1.000000"


# ---------------------------------------------------------------------------
# 5. Float-input rejection
# ---------------------------------------------------------------------------
def test_float_input_rejected(usdc_6d):
    with pytest.raises(TypeError) as excinfo:
        Money(1.5, usdc_6d)
    # Ensure message mentions the rationale.
    assert "float" in str(excinfo.value).lower()


def test_mul_float_scalar_rejected(usdc_6d):
    a = Money(Decimal("1"), usdc_6d)
    with pytest.raises(TypeError):
        _ = a * 1.5


# ---------------------------------------------------------------------------
# 6-8. Negative / NaN / Infinity rejection
# ---------------------------------------------------------------------------
def test_negative_rejected(usdc_6d):
    with pytest.raises(ValueError):
        Money(Decimal("-1"), usdc_6d)


def test_nan_rejected(usdc_6d):
    with pytest.raises(ValueError):
        Money(Decimal("NaN"), usdc_6d)


def test_infinity_rejected(usdc_6d):
    with pytest.raises(ValueError):
        Money(Decimal("Infinity"), usdc_6d)


# ---------------------------------------------------------------------------
# 9. zero(asset) classmethod
# ---------------------------------------------------------------------------
def test_zero_returns_zero_money(usdc_6d):
    z = Money.zero(usdc_6d)
    assert z.quantity == Decimal("0.000000")
    assert z.asset == usdc_6d


def test_zero_decimals_variance(eur_2d):
    z = Money.zero(eur_2d)
    assert str(z.quantity) == "0.00"


# ---------------------------------------------------------------------------
# 10. to_dict output shape
# ---------------------------------------------------------------------------
def test_to_dict_emits_string_quantity_at_asset_precision(usdc_6d):
    a = Money(Decimal("1"), usdc_6d)
    d = a.to_dict()
    assert d == {"quantity": "1.000000", "asset_id": "usdc-base"}
    assert isinstance(d["quantity"], str)


def test_to_dict_never_emits_float(usdc_6d):
    a = Money(Decimal("1.234567"), usdc_6d)
    d = a.to_dict()
    assert isinstance(d["quantity"], str)
    assert "." in d["quantity"]


# ---------------------------------------------------------------------------
# 11. Scientific-notation suppression
# ---------------------------------------------------------------------------
def test_tiny_value_emits_fixed_notation(usdc_6d):
    a = Money(Decimal("1E-6"), usdc_6d)
    assert a.to_dict()["quantity"] == "0.000001"


def test_large_value_emits_fixed_notation(usdc_6d):
    # 1e9 with 6 decimals — make sure no scientific form leaks.
    a = Money(Decimal("1000000000"), usdc_6d)
    assert a.to_dict()["quantity"] == "1000000000.000000"


# ---------------------------------------------------------------------------
# 12. from_dict round-trip
# ---------------------------------------------------------------------------
def test_from_dict_round_trip(usdc_6d):
    original = Money(Decimal("1.234567"), usdc_6d)
    rehydrated = Money.from_dict(original.to_dict(), usdc_6d)
    assert rehydrated == original


def test_from_dict_round_trip_eur(eur_2d):
    original = Money(Decimal("1.23"), eur_2d)
    rehydrated = Money.from_dict(original.to_dict(), eur_2d)
    assert rehydrated == original
    assert original.to_dict()["quantity"] == "1.23"


# ---------------------------------------------------------------------------
# 13. from_dict with too-precise input
# ---------------------------------------------------------------------------
def test_from_dict_too_precise_raises(usdc_6d):
    with pytest.raises(InexactQuantizationError):
        Money.from_dict({"quantity": "1.0000001", "asset_id": "usdc-base"}, usdc_6d)


def test_from_dict_asset_id_mismatch_raises(usdc_6d, eur_2d):
    d = {"quantity": "1.000000", "asset_id": "mock-eur"}
    with pytest.raises(AssetMismatchError):
        Money.from_dict(d, usdc_6d)


# ---------------------------------------------------------------------------
# 14. Decimal variance — EUR (2 dp) vs USD (6 dp)
# ---------------------------------------------------------------------------
def test_eur_2_decimals_to_dict(eur_2d):
    a = Money(Decimal("1.23"), eur_2d)
    assert a.to_dict() == {"quantity": "1.23", "asset_id": "mock-eur"}


def test_eur_arithmetic_quantizes_to_two_dp(eur_2d):
    a = Money(Decimal("0.50"), eur_2d)
    b = Money(Decimal("0.25"), eur_2d)
    result = a + b
    assert result.quantity == Decimal("0.75")
    assert str(result.quantity) == "0.75"


# ---------------------------------------------------------------------------
# 15. String-input accepted
# ---------------------------------------------------------------------------
def test_string_input_accepted(usdc_6d):
    a = Money("1.5", usdc_6d)
    assert a.quantity == Decimal("1.500000")


def test_string_input_too_precise_rejected(usdc_6d):
    with pytest.raises(InexactQuantizationError):
        Money("1.0000001", usdc_6d)


def test_invalid_string_rejected(usdc_6d):
    with pytest.raises(ValueError):
        Money("not-a-number", usdc_6d)


# ---------------------------------------------------------------------------
# Bonus — invariants and misuse
# ---------------------------------------------------------------------------
def test_money_is_hashable(usdc_6d):
    a = Money(Decimal("1"), usdc_6d)
    b = Money(Decimal("1.000000"), usdc_6d)
    assert {a, b} == {a}


def test_sub_producing_negative_raises(usdc_6d):
    a = Money(Decimal("1"), usdc_6d)
    b = Money(Decimal("2"), usdc_6d)
    with pytest.raises(ValueError):
        _ = a - b


def test_add_returns_notimplemented_for_non_money(usdc_6d):
    a = Money(Decimal("1"), usdc_6d)
    with pytest.raises(TypeError):
        _ = a + 5  # int on RHS: __add__ returns NotImplemented, Python raises


def test_two_assetrefs_with_same_id_treated_as_same_asset():
    # Compare by asset_id — two AssetRefs with the same id should work
    # together even if other fields differ.
    a = AssetRef(asset_id="mock-usd", contract="USD", decimals=6)
    b = AssetRef(asset_id="mock-usd", contract="USD", decimals=6)
    m1 = Money(Decimal("1"), a)
    m2 = Money(Decimal("2"), b)
    result = m1 + m2
    assert result.quantity == Decimal("3.000000")
