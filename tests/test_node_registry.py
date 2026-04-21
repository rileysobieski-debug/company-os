"""
tests/test_node_registry.py — Ticket 10 unit coverage
=====================================================
Covers `core.primitives.node_registry.NodeRegistry` and the new
registry-mode path in `InterOrgSLA.verify_signatures`.

Contract under test
-------------------
- YAML round-trip: write files → load() → get() / ids() return the
  registered binding.
- Missing DID → KeyError.
- Rebinding a DID with a DIFFERENT pubkey is forbidden (SignatureError).
- Rebinding with the SAME pubkey is idempotent — no file write, mtime
  unchanged.
- Two registries in one process don't share state.
- Atomic write: if `Path.replace` fails, the YAML dir is unchanged and
  no stray tempfiles remain visible to `load()`.
- SLA signed by the correct DID's keypair verifies under registry mode.
- SLA signed by a DIFFERENT keypair claiming the registered DID fails
  (Sybil defense).
- SLA whose DID is not in the registry fails with "unknown counterparty".
- Ambiguous mode (registry + explicit pubkeys) → TypeError.
"""
from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from core.primitives.asset import AssetRef
from core.primitives.exceptions import SignatureError
from core.primitives.identity import Ed25519Keypair, Ed25519PublicKey
from core.primitives.money import Money
from core.primitives.node_registry import NodeRegistry, _filename_for_did
from core.primitives.sla import InterOrgSLA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def usd() -> AssetRef:
    return AssetRef(asset_id="mock-usd", contract="USD", decimals=6)


def _base_sla_kwargs(usd: AssetRef) -> dict:
    return {
        "sla_id": "sla-010",
        "requester_node_did": "did:companyos:requester",
        "provider_node_did": "did:companyos:provider",
        "task_scope": "extract tasting notes",
        "deliverable_schema": {"type": "object"},
        "accuracy_requirement": 0.9,
        "latency_ms": 60_000,
        "payment": Money(Decimal("5"), usd),
        "penalty_stake": Money(Decimal("1"), usd),
        "nonce": "abcdef0123456789abcdef0123456789",
        "issued_at": "2026-04-19T12:00:00Z",
        "expires_at": "2026-04-19T13:00:00Z",
    }


def _write_node_yaml(
    root: Path,
    did: str,
    pubkey_hex: str,
    notes: str = "",
    first_seen: str = "",
) -> Path:
    """Write a node YAML directly (simulates a registry populated
    out-of-band, e.g. by hand or by another node).
    """
    root.mkdir(parents=True, exist_ok=True)
    target = root / _filename_for_did(did)
    payload = {
        "node_did": did,
        "public_key_hex": pubkey_hex,
        "first_seen": first_seen,
        "notes": notes,
    }
    target.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Load / get / ids
# ---------------------------------------------------------------------------
class TestLoadRoundTrip:
    def test_load_returns_count_and_populates_index(self, tmp_path):
        kp_a = Ed25519Keypair.generate()
        kp_b = Ed25519Keypair.generate()
        _write_node_yaml(tmp_path, "did:companyos:a", kp_a.public_key.bytes_hex)
        _write_node_yaml(tmp_path, "did:companyos:b", kp_b.public_key.bytes_hex)

        reg = NodeRegistry()
        assert reg.load(tmp_path) == 2
        assert reg.get("did:companyos:a") == kp_a.public_key
        assert reg.get("did:companyos:b") == kp_b.public_key
        assert reg.ids() == ["did:companyos:a", "did:companyos:b"]

    def test_load_of_missing_dir_returns_zero(self, tmp_path):
        reg = NodeRegistry()
        missing = tmp_path / "nope"
        assert reg.load(missing) == 0
        assert reg.ids() == []

    def test_load_of_empty_dir_returns_zero(self, tmp_path):
        reg = NodeRegistry()
        assert reg.load(tmp_path) == 0
        assert reg.ids() == []

    def test_load_rejects_none_root(self):
        reg = NodeRegistry()
        with pytest.raises(ValueError):
            reg.load(None)  # type: ignore[arg-type]

    def test_load_rejects_yaml_missing_required_field(self, tmp_path):
        # Missing public_key_hex.
        (tmp_path / "bad.yaml").write_text(
            "node_did: did:companyos:x\n", encoding="utf-8"
        )
        reg = NodeRegistry()
        with pytest.raises(ValueError, match="public_key_hex"):
            reg.load(tmp_path)

    def test_load_rejects_non_mapping_yaml(self, tmp_path):
        (tmp_path / "bad.yaml").write_text("- just\n- a list\n", encoding="utf-8")
        reg = NodeRegistry()
        with pytest.raises(ValueError, match="must be a mapping"):
            reg.load(tmp_path)


class TestGet:
    def test_missing_did_raises_keyerror(self, tmp_path):
        reg = NodeRegistry()
        reg.load(tmp_path)
        with pytest.raises(KeyError, match="unknown node_did"):
            reg.get("did:companyos:ghost")


class TestIsolation:
    def test_two_registries_do_not_share_state(self, tmp_path):
        kp = Ed25519Keypair.generate()
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_node_yaml(dir_a, "did:companyos:only-in-a", kp.public_key.bytes_hex)

        reg_a = NodeRegistry()
        reg_b = NodeRegistry()
        reg_a.load(dir_a)
        reg_b.load(dir_b)

        assert reg_a.ids() == ["did:companyos:only-in-a"]
        assert reg_b.ids() == []


# ---------------------------------------------------------------------------
# Register (write path, atomicity, rebinding policy)
# ---------------------------------------------------------------------------
class TestRegister:
    def test_register_writes_yaml_and_indexes(self, tmp_path):
        reg = NodeRegistry()
        reg.load(tmp_path)
        kp = Ed25519Keypair.generate()
        did = "did:companyos:new-node"
        reg.register(did, kp.public_key, notes="primary")

        # Indexed in memory.
        assert reg.get(did) == kp.public_key
        # And landed on disk at the derived filename.
        target = tmp_path / _filename_for_did(did)
        assert target.exists()
        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert loaded["node_did"] == did
        assert loaded["public_key_hex"] == kp.public_key.bytes_hex
        assert loaded["notes"] == "primary"

    def test_register_before_load_raises(self, tmp_path):
        reg = NodeRegistry()
        kp = Ed25519Keypair.generate()
        with pytest.raises(ValueError, match="load"):
            reg.register("did:companyos:x", kp.public_key)

    def test_register_same_pubkey_is_idempotent_no_file_write(self, tmp_path):
        reg = NodeRegistry()
        reg.load(tmp_path)
        kp = Ed25519Keypair.generate()
        did = "did:companyos:idempotent"
        reg.register(did, kp.public_key)

        target = tmp_path / _filename_for_did(did)
        first_mtime = target.stat().st_mtime_ns

        # Sleep briefly so a second write would produce a distinct mtime.
        time.sleep(0.01)
        reg.register(did, kp.public_key)

        second_mtime = target.stat().st_mtime_ns
        assert first_mtime == second_mtime

        # And exactly one yaml on disk.
        yamls = list(tmp_path.glob("*.yaml"))
        assert len(yamls) == 1

    def test_register_different_pubkey_rejected(self, tmp_path):
        reg = NodeRegistry()
        reg.load(tmp_path)
        kp1 = Ed25519Keypair.generate()
        kp2 = Ed25519Keypair.generate()
        did = "did:companyos:rebind"
        reg.register(did, kp1.public_key)

        with pytest.raises(SignatureError, match="rebinding forbidden"):
            reg.register(did, kp2.public_key)

        # On-disk state still reflects the original pubkey.
        assert reg.get(did) == kp1.public_key

    def test_register_atomic_write_failure_leaves_dir_clean(
        self, tmp_path, monkeypatch
    ):
        reg = NodeRegistry()
        reg.load(tmp_path)
        kp = Ed25519Keypair.generate()
        did = "did:companyos:atomic"

        original_replace = Path.replace

        def _boom(self: Path, target):  # type: ignore[no-untyped-def]
            raise OSError("simulated rename failure")

        monkeypatch.setattr(Path, "replace", _boom)
        with pytest.raises(OSError, match="simulated rename"):
            reg.register(did, kp.public_key)

        # Restore so fixture teardown doesn't get hung up.
        monkeypatch.setattr(Path, "replace", original_replace)

        # The target file was never created.
        target = tmp_path / _filename_for_did(did)
        assert not target.exists()
        # No *.yaml files at all — the tempfile used .yaml.tmp which
        # glob("*.yaml") deliberately skips, and cleanup removed it.
        assert list(tmp_path.glob("*.yaml")) == []
        # And the registry did NOT retain the DID in-memory either —
        # the partial write must not leave phantom state.
        assert did not in reg.ids()


# ---------------------------------------------------------------------------
# SLA integration — Sybil defense
# ---------------------------------------------------------------------------
class TestVerifySignaturesWithRegistry:
    def _prime(self, reg: NodeRegistry, did: str, pubkey: Ed25519PublicKey) -> None:
        """Prime the registry's in-memory index directly — avoids
        depending on register()'s disk path in these SLA-focused tests.
        """
        reg._nodes[did] = {  # type: ignore[attr-defined]
            "public_key_hex": pubkey.bytes_hex,
            "first_seen": "",
            "notes": "",
        }

    def test_correct_keypair_verifies(self, usd):
        sla = InterOrgSLA.create(**_base_sla_kwargs(usd))
        req_kp = Ed25519Keypair.generate()
        prov_kp = Ed25519Keypair.generate()
        signed = sla.sign_as_requester(req_kp).sign_as_provider(prov_kp)

        reg = NodeRegistry()
        self._prime(reg, sla.requester_node_did, req_kp.public_key)
        self._prime(reg, sla.provider_node_did, prov_kp.public_key)

        # Should not raise.
        signed.verify_signatures(registry=reg)

    def test_sybil_different_keypair_claiming_registered_did_rejected(self, usd):
        """Registry says requester DID binds to pubkey_A, but the SLA
        was signed by keypair_B claiming that DID — reject."""
        sla = InterOrgSLA.create(**_base_sla_kwargs(usd))
        real_req_kp = Ed25519Keypair.generate()
        sybil_req_kp = Ed25519Keypair.generate()
        prov_kp = Ed25519Keypair.generate()

        signed = sla.sign_as_requester(sybil_req_kp).sign_as_provider(prov_kp)

        reg = NodeRegistry()
        # Registry binds the REAL pubkey for that DID.
        self._prime(reg, sla.requester_node_did, real_req_kp.public_key)
        self._prime(reg, sla.provider_node_did, prov_kp.public_key)

        with pytest.raises(
            SignatureError, match="does not match registered pubkey"
        ):
            signed.verify_signatures(registry=reg)

    def test_unregistered_did_rejected(self, usd):
        sla = InterOrgSLA.create(**_base_sla_kwargs(usd))
        req_kp = Ed25519Keypair.generate()
        prov_kp = Ed25519Keypair.generate()
        signed = sla.sign_as_requester(req_kp).sign_as_provider(prov_kp)

        reg = NodeRegistry()
        # Only register the provider; requester DID unknown.
        self._prime(reg, sla.provider_node_did, prov_kp.public_key)

        with pytest.raises(
            SignatureError, match="unknown counterparty"
        ):
            signed.verify_signatures(registry=reg)

    def test_mixed_mode_rejected(self, usd):
        """Providing BOTH registry AND explicit pubkeys is ambiguous."""
        sla = InterOrgSLA.create(**_base_sla_kwargs(usd))
        req_kp = Ed25519Keypair.generate()
        prov_kp = Ed25519Keypair.generate()
        signed = sla.sign_as_requester(req_kp).sign_as_provider(prov_kp)

        reg = NodeRegistry()
        self._prime(reg, sla.requester_node_did, req_kp.public_key)
        self._prime(reg, sla.provider_node_did, prov_kp.public_key)

        with pytest.raises(TypeError, match="not both|either"):
            signed.verify_signatures(
                registry=reg,
                requester_pubkey=req_kp.public_key,
                provider_pubkey=prov_kp.public_key,
            )

    def test_full_disk_roundtrip_with_register(self, tmp_path, usd):
        """End-to-end: use `register()` to persist bindings, then
        reload from disk in a fresh registry and verify an SLA through
        it. This exercises YAML serialization + the atomic write path.
        """
        req_kp = Ed25519Keypair.generate()
        prov_kp = Ed25519Keypair.generate()

        writer = NodeRegistry()
        writer.load(tmp_path)
        writer.register("did:companyos:requester", req_kp.public_key)
        writer.register("did:companyos:provider", prov_kp.public_key)

        reader = NodeRegistry()
        assert reader.load(tmp_path) == 2

        sla = InterOrgSLA.create(**_base_sla_kwargs(usd))
        signed = sla.sign_as_requester(req_kp).sign_as_provider(prov_kp)
        signed.verify_signatures(registry=reader)
