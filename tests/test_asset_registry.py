"""
tests/test_asset_registry.py — Ticket 1 coverage
================================================
Structural tests for `core.primitives.asset.{AssetRef, AssetRegistry}`.

Covered:
- load round-trip across all three seed YAMLs
- missing asset raises KeyError
- malformed YAML raises ValueError with filename in message
- two registries in the same process are state-isolated
- ids() returns all seeded asset_ids
- AssetRef is frozen (FrozenInstanceError) and hashable
- decimals default (6) and per-file override (mock-eur = 2)
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from core.primitives.asset import AssetRef, AssetRegistry


# ---------------------------------------------------------------------------
# Load round-trip
# ---------------------------------------------------------------------------
def test_load_round_trip_mock_usd(asset_registry):
    ref = asset_registry.get("mock-usd")
    assert ref.asset_id == "mock-usd"
    assert ref.chain_id == ""
    assert ref.contract == "USD"
    assert ref.decimals == 6


def test_load_round_trip_mock_eur_decimals_variance(asset_registry):
    ref = asset_registry.get("mock-eur")
    assert ref.asset_id == "mock-eur"
    assert ref.chain_id == ""
    assert ref.contract == "EUR"
    # Proves decimals variance works — eur has 2 decimals, not the 6 default.
    assert ref.decimals == 2


def test_load_round_trip_usdc_base(asset_registry):
    ref = asset_registry.get("usdc-base")
    assert ref.asset_id == "usdc-base"
    assert ref.chain_id == "base-mainnet"
    assert ref.contract == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert ref.decimals == 6


# ---------------------------------------------------------------------------
# Miss / malformed
# ---------------------------------------------------------------------------
def test_missing_asset_raises_key_error(asset_registry):
    with pytest.raises(KeyError):
        asset_registry.get("unknown")


def test_malformed_yaml_missing_required_field_rejected(tmp_path: Path):
    bad = tmp_path / "no-id.yaml"
    bad.write_text("chain_id: somewhere\ndecimals: 6\n", encoding="utf-8")
    reg = AssetRegistry()
    with pytest.raises(ValueError) as excinfo:
        reg.load(tmp_path)
    msg = str(excinfo.value)
    assert "asset_id" in msg
    assert "no-id.yaml" in msg


def test_malformed_yaml_parse_error_rejected(tmp_path: Path):
    bad = tmp_path / "broken.yaml"
    bad.write_text("asset_id: x\n  : : bad\n::::\n", encoding="utf-8")
    reg = AssetRegistry()
    with pytest.raises(ValueError) as excinfo:
        reg.load(tmp_path)
    assert "broken.yaml" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Isolation + ids
# ---------------------------------------------------------------------------
def test_two_registries_are_isolated(tmp_path: Path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "only-a.yaml").write_text(
        "asset_id: only-a\ndecimals: 6\n", encoding="utf-8"
    )
    (dir_b / "only-b.yaml").write_text(
        "asset_id: only-b\ndecimals: 2\n", encoding="utf-8"
    )

    reg_a = AssetRegistry()
    reg_b = AssetRegistry()
    reg_a.load(dir_a)
    reg_b.load(dir_b)

    assert reg_a.ids() == ["only-a"]
    assert reg_b.ids() == ["only-b"]
    with pytest.raises(KeyError):
        reg_a.get("only-b")
    with pytest.raises(KeyError):
        reg_b.get("only-a")


def test_ids_returns_all_seeded(asset_registry):
    assert asset_registry.ids() == ["mock-eur", "mock-usd", "usdc-base"]


# ---------------------------------------------------------------------------
# Dataclass behavior
# ---------------------------------------------------------------------------
def test_asset_ref_is_frozen():
    ref = AssetRef(asset_id="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.asset_id = "y"  # type: ignore[misc]


def test_asset_ref_is_hashable_usable_in_sets_and_dicts():
    a = AssetRef(asset_id="mock-usd", contract="USD", decimals=6)
    b = AssetRef(asset_id="mock-usd", contract="USD", decimals=6)
    c = AssetRef(asset_id="mock-eur", contract="EUR", decimals=2)

    s = {a, b, c}
    assert len(s) == 2  # a and b are value-equal
    d = {a: "usd", c: "eur"}
    assert d[b] == "usd"  # hash/eq work across equivalent instances


def test_asset_ref_decimals_default_is_six():
    ref = AssetRef(asset_id="defaulty")
    assert ref.decimals == 6
    assert ref.chain_id == ""
    assert ref.contract == ""


# ---------------------------------------------------------------------------
# Load mechanics
# ---------------------------------------------------------------------------
def test_load_requires_explicit_root():
    reg = AssetRegistry()
    with pytest.raises(ValueError):
        reg.load(None)


def test_load_returns_count_of_new_assets(tmp_path: Path):
    (tmp_path / "a.yaml").write_text("asset_id: a\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("asset_id: b\n", encoding="utf-8")
    reg = AssetRegistry()
    assert reg.load(tmp_path) == 2
    # Re-loading the same dir adds zero new entries.
    assert reg.load(tmp_path) == 0


def test_load_missing_directory_returns_zero(tmp_path: Path):
    reg = AssetRegistry()
    assert reg.load(tmp_path / "nonexistent") == 0
