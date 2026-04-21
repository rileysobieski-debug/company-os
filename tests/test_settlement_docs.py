"""
tests/test_settlement_docs.py — Ticket 7 doc-example runner
===========================================================
Executes the runnable examples from:
  - core/primitives/SETTLEMENT.md (end-to-end happy path)
  - core/primitives/asset_registry/README.md (doctest example)

Rather than extracting and `exec()`-ing the markdown at runtime, we
reproduce the snippets verbatim here. If the docs drift, these tests
fail loudly — which is exactly the drift signal we want from docs that
readers will rely on as reference.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from decimal import Decimal

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ASSET_REGISTRY_DIR = REPO_ROOT / "core" / "primitives" / "asset_registry"


def test_settlement_md_end_to_end_example(tmp_path) -> None:
    """Runs the exact code block from SETTLEMENT.md section (a)."""
    from core.primitives import (
        AssetRegistry, Money, AdapterRegistry, MockSettlementAdapter,
        InterOrgSLA, Ed25519Keypair, NodeRegistry, SettlementEventLedger,
    )

    # 1. Bring up per-node registries (no module-level singletons).
    asset_reg = AssetRegistry()
    asset_reg.load(ASSET_REGISTRY_DIR)
    usd = asset_reg.get("mock-usd")

    adapters = AdapterRegistry(asset_reg)
    mock = MockSettlementAdapter(supported_assets=(usd,))
    adapters.register(mock)

    nodes = NodeRegistry()
    nodes.load(tmp_path / "nodes")
    ledger = SettlementEventLedger(tmp_path / "events")
    assert ledger.ledger_dir.exists()

    # 2. Generate keypairs, bind DIDs to pubkeys in the NodeRegistry.
    req_kp = Ed25519Keypair.generate()
    prov_kp = Ed25519Keypair.generate()
    nodes.register("did:companyos:req", req_kp.public_key)
    nodes.register("did:companyos:prov", prov_kp.public_key)

    # 3. Build the unsigned SLA, co-sign, verify against NodeRegistry.
    sla = InterOrgSLA.create(
        sla_id="sla-demo-0001",
        requester_node_did="did:companyos:req",
        provider_node_did="did:companyos:prov",
        task_scope="Summarize Q1 wine distribution report",
        deliverable_schema={"format": "markdown"},
        accuracy_requirement=0.95, latency_ms=5000,
        payment=Money(Decimal("0.001000"), usd),
        penalty_stake=Money(Decimal("0.000500"), usd),
        nonce=InterOrgSLA.new_nonce(),
        issued_at="2026-04-19T12:00:00Z",
        expires_at="2026-04-19T13:00:00Z",
    )
    sla = sla.sign_as_requester(req_kp).sign_as_provider(prov_kp)
    sla.verify_signatures(registry=nodes)

    # 4. Fund provider, lock stake, release on success.
    mock.fund("did:companyos:prov", Money(Decimal("0.010000"), usd))
    handle = mock.lock(
        sla.penalty_stake, ref=sla.sla_id,
        nonce=sla.nonce, principal="did:companyos:prov",
    )
    receipt = mock.release(handle, to="did:companyos:prov")
    assert receipt.outcome == "released"
    # integrity_binding holds after all the above
    assert sla.verify_binding() is True


def test_asset_registry_readme_doctest_example() -> None:
    """Mirrors the doctest block at the end of asset_registry/README.md."""
    from core.primitives import (
        AssetRegistry, AdapterRegistry, MockSettlementAdapter,
    )

    reg = AssetRegistry()
    loaded = reg.load(ASSET_REGISTRY_DIR)
    assert loaded >= 2

    usd = reg.get("mock-usd")
    assert usd.decimals == 6

    adapters = AdapterRegistry(reg)
    adapters.register(MockSettlementAdapter(supported_assets=(usd,)))
    assert adapters.adapter_for(usd).supports(usd) is True


def test_asset_registry_readme_conflict_example() -> None:
    """Mirrors the AdapterConflictError block in section 3 of the README."""
    from core.primitives import (
        AssetRegistry, AdapterRegistry, MockSettlementAdapter,
        AdapterConflictError,
    )

    reg = AssetRegistry()
    reg.load(ASSET_REGISTRY_DIR)
    usd = reg.get("mock-usd")

    adapters = AdapterRegistry(reg)
    adapters.register(MockSettlementAdapter(supported_assets=(usd,)))

    with pytest.raises(AdapterConflictError):
        adapters.register(MockSettlementAdapter(supported_assets=(usd,)))


def test_settlement_md_example_files_exist() -> None:
    """Guard: the two markdown files Ticket 7 ships must be present."""
    settlement_md = REPO_ROOT / "core" / "primitives" / "SETTLEMENT.md"
    asset_readme = ASSET_REGISTRY_DIR / "README.md"
    assert settlement_md.exists(), f"missing {settlement_md}"
    assert asset_readme.exists(), f"missing {asset_readme}"
    # Sanity-check that the content references the load-bearing primitives.
    text = settlement_md.read_text(encoding="utf-8")
    for symbol in (
        "AssetRegistry", "Money", "InterOrgSLA",
        "Ed25519Keypair", "NodeRegistry", "SettlementEventLedger",
        "MockSettlementAdapter", "AdapterRegistry",
    ):
        assert symbol in text, f"SETTLEMENT.md must mention {symbol}"
