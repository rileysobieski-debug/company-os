"""tests/test_oracle_adversarial.py -- Adversarial scenarios for the Oracle.

Unit tests for attacks the Oracle must resist and for gaps that v1a
accepted but v1b now closes via NodeRegistry-backed authorization.

Scenarios covered:
- Post-signing evidence tamper -> SignatureError (closes tamper path).
- Post-signing evaluator_did tamper -> SignatureError (closes tamper path).
- Signer / signature.signer drift -> SignatureError (closes drift path).
- Registry-backed authorization (v1b):
  - No-registry path: v1a behavior preserved (spoof passes verify_signature).
  - Registry happy path: valid verdict with registered keypair passes.
  - Registry spoof gap: spoof verdict raises SignatureError (GAP CLOSED IN V1B).
  - Registry unknown DID: unregistered evaluator_did raises SignatureError.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from core.primitives.asset import AssetRef
from core.primitives.exceptions import SignatureError
from core.primitives.identity import Ed25519Keypair
from core.primitives.money import Money
from core.primitives.node_registry import NodeRegistry
from core.primitives.oracle import Oracle, OracleVerdict
from core.primitives.schema_verifier import SchemaVerifier
from core.primitives.sla import InterOrgSLA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def usd() -> AssetRef:
    return AssetRef(asset_id="mock-usd", contract="USD", decimals=6)


@pytest.fixture
def requester_keypair() -> Ed25519Keypair:
    return Ed25519Keypair.generate()


@pytest.fixture
def provider_keypair() -> Ed25519Keypair:
    return Ed25519Keypair.generate()


@pytest.fixture
def requester_oracle(requester_keypair: Ed25519Keypair) -> Oracle:
    return Oracle(
        node_did="did:companyos:requester",
        node_keypair=requester_keypair,
        schema_verifier=SchemaVerifier(),
    )


def _schema_envelope() -> dict:
    return {
        "kind": "json_schema",
        "spec_version": "2020-12",
        "schema": {
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
        },
    }


def _make_sla(usd: AssetRef, artifact_bytes: bytes) -> InterOrgSLA:
    sla = InterOrgSLA.create(
        sla_id="test-sla-adversarial",
        requester_node_did="did:companyos:requester",
        provider_node_did="did:companyos:provider",
        task_scope="adversarial",
        deliverable_schema=_schema_envelope(),
        accuracy_requirement=0.9,
        latency_ms=60_000,
        payment=Money(Decimal("100.000000"), usd),
        penalty_stake=Money(Decimal("10.000000"), usd),
        nonce=InterOrgSLA.new_nonce(),
        issued_at="2026-04-21T00:00:00Z",
        expires_at="2026-04-28T00:00:00Z",
    )
    return sla.with_delivery_hash(hashlib.sha256(artifact_bytes).hexdigest())


# ---------------------------------------------------------------------------
# Tamper tests: these MUST fail verify_signature.
# ---------------------------------------------------------------------------
class TestPostSigningTamper:
    def test_evidence_kind_tamper_breaks_signature(
        self, requester_oracle: Oracle, usd: AssetRef
    ):
        """Post-signing mutation of evidence.kind must invalidate the signature."""
        artifact = json.dumps({"summary": "ok"}).encode()
        sla = _make_sla(usd, artifact)
        verdict = requester_oracle.evaluate_tier0(sla, artifact)
        verdict.verify_signature()  # clean baseline

        # Replace evidence with a spoofed "founder_override" kind.
        tampered_evidence = dict(verdict.evidence)
        tampered_evidence["kind"] = "founder_override"
        tampered = dataclasses.replace(verdict, evidence=tampered_evidence)
        with pytest.raises(SignatureError):
            tampered.verify_signature()

    def test_evaluator_did_tamper_breaks_signature(
        self, requester_oracle: Oracle, usd: AssetRef
    ):
        """Swapping evaluator_did on a signed verdict must break the sig.

        The field participates in canonical bytes, so mutation changes
        verdict_hash and the Ed25519 signature no longer covers the new
        byte shape.
        """
        artifact = json.dumps({"summary": "ok"}).encode()
        sla = _make_sla(usd, artifact)
        verdict = requester_oracle.evaluate_tier0(sla, artifact)
        spoofed = dataclasses.replace(verdict, evaluator_did="did:companyos:attacker")
        with pytest.raises(SignatureError):
            spoofed.verify_signature()

    def test_signer_and_signature_signer_drift_raises(
        self,
        requester_oracle: Oracle,
        provider_keypair: Ed25519Keypair,
        usd: AssetRef,
    ):
        """Top-level signer swapped to a different pubkey than the one
        embedded in signature.signer -> SignatureError.

        This is the "signer / signature drift" path from the consistency
        check in OracleVerdict.verify_signature.
        """
        artifact = json.dumps({"summary": "ok"}).encode()
        sla = _make_sla(usd, artifact)
        verdict = requester_oracle.evaluate_tier0(sla, artifact)
        drifted = dataclasses.replace(verdict, signer=provider_keypair.public_key)
        with pytest.raises(SignatureError, match="signer"):
            drifted.verify_signature()


# ---------------------------------------------------------------------------
# Helper: build an in-memory NodeRegistry from a dict of did -> Ed25519Keypair.
# ---------------------------------------------------------------------------
def _make_registry(entries: dict[str, Ed25519Keypair]) -> NodeRegistry:
    """Build a NodeRegistry with one entry per keypair, using a temp directory."""
    registry = NodeRegistry()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        registry.load(root)
        for did, keypair in entries.items():
            registry.register(did, keypair.public_key)
    return registry


# ---------------------------------------------------------------------------
# Registry-backed authorization tests (v1b).
# ---------------------------------------------------------------------------
class TestRegistryBackedAuthorization:
    """Verify_signature registry-mode authorization introduced in v1b.

    Split from TestKnownV1aGaps to document both the preserved v1a
    no-registry behavior and the new registry-mode closure of the
    evaluator_did spoof gap.
    """

    def test_no_registry_path_preserves_v1a_behavior(
        self,
        requester_keypair: Ed25519Keypair,
        provider_keypair: Ed25519Keypair,
        usd: AssetRef,
    ):
        """No-registry path: v1a behavior preserved (spoof verdict still passes).

        When verify_signature() is called without a registry argument,
        only signer-consistency and cryptographic checks run. A verdict
        where evaluator_did claims one DID but is signed by a different
        keypair will still pass, just as it did in v1a.

        This test confirms we have NOT broken the no-registry call path.
        Callers that do not supply a NodeRegistry continue to receive
        the same guarantees as in v1a.
        """
        oracle_claiming_requester = Oracle(
            node_did="did:companyos:requester",  # spoofed claim
            node_keypair=provider_keypair,        # but signs with provider's key
            schema_verifier=SchemaVerifier(),
        )
        artifact = json.dumps({"summary": "attack"}).encode()
        sla = _make_sla(usd, artifact)
        spoof_verdict = oracle_claiming_requester.evaluate_tier0(sla, artifact)

        assert spoof_verdict.signer == provider_keypair.public_key
        assert spoof_verdict.signer != requester_keypair.public_key
        assert spoof_verdict.evaluator_did == "did:companyos:requester"

        # No registry: v1a behavior, does not raise.
        spoof_verdict.verify_signature()

    def test_registry_mode_closes_spoof_gap_in_v1b(
        self,
        requester_keypair: Ed25519Keypair,
        provider_keypair: Ed25519Keypair,
        usd: AssetRef,
    ):
        """CLOSED IN V1B by registry-mode verify_signature.

        Scenario: provider claims evaluator_did is the requester's DID,
        but signs with its own keypair. With a NodeRegistry that maps
        "did:companyos:requester" to the REAL requester pubkey, the
        registry check detects the pubkey mismatch and raises
        SignatureError.

        v1a decision (documented in ORACLE.md section (e)): deferred
        to v1b. This test documents the closure.
        """
        oracle_claiming_requester = Oracle(
            node_did="did:companyos:requester",  # spoofed claim
            node_keypair=provider_keypair,        # but signs with provider's key
            schema_verifier=SchemaVerifier(),
        )
        artifact = json.dumps({"summary": "attack"}).encode()
        sla = _make_sla(usd, artifact)
        spoof_verdict = oracle_claiming_requester.evaluate_tier0(sla, artifact)

        # Registry maps the requester DID to the REAL requester pubkey.
        registry = _make_registry({"did:companyos:requester": requester_keypair})

        with pytest.raises(
            SignatureError,
            match="does not match registered pubkey",
        ):
            spoof_verdict.verify_signature(registry=registry)

    def test_registry_mode_valid_verdict_passes(
        self,
        requester_keypair: Ed25519Keypair,
        usd: AssetRef,
    ):
        """Registry happy path: a legitimate verdict by a registered node passes."""
        oracle = Oracle(
            node_did="did:companyos:requester",
            node_keypair=requester_keypair,
            schema_verifier=SchemaVerifier(),
        )
        artifact = json.dumps({"summary": "legitimate"}).encode()
        sla = _make_sla(usd, artifact)
        verdict = oracle.evaluate_tier0(sla, artifact)

        registry = _make_registry({"did:companyos:requester": requester_keypair})

        # Should not raise.
        verdict.verify_signature(registry=registry)

    def test_registry_mode_unknown_evaluator_did_raises(
        self,
        requester_keypair: Ed25519Keypair,
        usd: AssetRef,
    ):
        """Registry mode: verdict with an evaluator_did not in the registry raises
        SignatureError with 'unknown evaluator DID' in the message.
        """
        oracle = Oracle(
            node_did="did:companyos:unknown-node",
            node_keypair=requester_keypair,
            schema_verifier=SchemaVerifier(),
        )
        artifact = json.dumps({"summary": "test"}).encode()
        sla = _make_sla(usd, artifact)
        verdict = oracle.evaluate_tier0(sla, artifact)

        # Registry is empty (no entries for "did:companyos:unknown-node").
        registry = _make_registry({"did:companyos:other-node": requester_keypair})

        with pytest.raises(
            SignatureError,
            match="unknown evaluator DID",
        ):
            verdict.verify_signature(registry=registry)
