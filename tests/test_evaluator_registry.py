"""
tests/test_evaluator_registry.py -- Ticket B1-a unit coverage
=============================================================
Covers `core.primitives.evaluator.EvaluatorRegistry`.

Contract under test
-------------------
- register + get round-trips pubkey and canonical_hash.
- Re-register same DID + same fields: idempotent (no error, no rewrite).
- Re-register same DID + different canonical_hash: raises ValueError.
- Re-register same DID + different pubkey: raises ValueError.
- Sanitized DID paths reject `../` traversal.
- Sanitized DID paths reject absolute paths (contain path separators).
- YAML on-disk format round-trips through a fresh EvaluatorRegistry(root=...).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from core.primitives.evaluator import EvaluatorRegistry, _sanitize_did_for_path
from core.primitives.identity import Ed25519Keypair, Ed25519PublicKey


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_keypair() -> Ed25519Keypair:
    return Ed25519Keypair.generate()


def _reg(tmp_path: Path) -> EvaluatorRegistry:
    """Return an EvaluatorRegistry loaded against tmp_path."""
    reg = EvaluatorRegistry(root=tmp_path)
    return reg


# ---------------------------------------------------------------------------
# Register + get round-trips
# ---------------------------------------------------------------------------
class TestRegisterGet:
    def test_register_and_get_round_trip(self, tmp_path: Path) -> None:
        kp = _make_keypair()
        reg = _reg(tmp_path)
        reg.register("did:companyos:eval-a", kp.public_key, "hash-abc")

        pubkey, canonical_hash = reg.get("did:companyos:eval-a")
        assert pubkey == kp.public_key
        assert canonical_hash == "hash-abc"

    def test_get_unknown_did_raises_keyerror(self, tmp_path: Path) -> None:
        reg = _reg(tmp_path)
        with pytest.raises(KeyError, match="unknown evaluator_did"):
            reg.get("did:companyos:ghost")

    def test_register_creates_yaml_on_disk(self, tmp_path: Path) -> None:
        kp = _make_keypair()
        reg = _reg(tmp_path)
        reg.register("did:companyos:eval-b", kp.public_key, "hash-xyz", notes="test")

        yamls = list(tmp_path.glob("*.yaml"))
        assert len(yamls) == 1

        data = yaml.safe_load(yamls[0].read_text(encoding="utf-8"))
        assert data["evaluator_did"] == "did:companyos:eval-b"
        assert data["public_key_hex"] == kp.public_key.bytes_hex
        assert data["canonical_hash"] == "hash-xyz"
        assert data["notes"] == "test"

    def test_notes_field_persisted(self, tmp_path: Path) -> None:
        kp = _make_keypair()
        reg = _reg(tmp_path)
        reg.register("did:companyos:noted", kp.public_key, "hash-noted", notes="wine evaluator")

        _, _ = reg.get("did:companyos:noted")
        yamls = list(tmp_path.glob("*.yaml"))
        data = yaml.safe_load(yamls[0].read_text(encoding="utf-8"))
        assert data["notes"] == "wine evaluator"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
class TestIdempotency:
    def test_reregister_same_fields_is_noop(self, tmp_path: Path) -> None:
        kp = _make_keypair()
        reg = _reg(tmp_path)
        reg.register("did:companyos:idem", kp.public_key, "hash-idem")

        target = next(tmp_path.glob("*.yaml"))
        first_mtime = target.stat().st_mtime_ns

        time.sleep(0.01)
        # Should not raise and should not rewrite the file.
        reg.register("did:companyos:idem", kp.public_key, "hash-idem")

        second_mtime = target.stat().st_mtime_ns
        assert first_mtime == second_mtime

    def test_reregister_different_canonical_hash_raises(self, tmp_path: Path) -> None:
        kp = _make_keypair()
        reg = _reg(tmp_path)
        reg.register("did:companyos:conflict-hash", kp.public_key, "hash-v1")

        with pytest.raises(ValueError, match="canonical_hash"):
            reg.register("did:companyos:conflict-hash", kp.public_key, "hash-v2")

    def test_reregister_different_pubkey_raises(self, tmp_path: Path) -> None:
        kp1 = _make_keypair()
        kp2 = _make_keypair()
        reg = _reg(tmp_path)
        reg.register("did:companyos:conflict-key", kp1.public_key, "hash-same")

        with pytest.raises(ValueError, match="different public_key|public_key"):
            reg.register("did:companyos:conflict-key", kp2.public_key, "hash-same")

    def test_original_pubkey_preserved_after_conflict(self, tmp_path: Path) -> None:
        """The in-memory state must not be mutated when a conflict error fires."""
        kp1 = _make_keypair()
        kp2 = _make_keypair()
        reg = _reg(tmp_path)
        reg.register("did:companyos:state-guard", kp1.public_key, "hash-v1")

        with pytest.raises(ValueError):
            reg.register("did:companyos:state-guard", kp2.public_key, "hash-v1")

        pubkey, _ = reg.get("did:companyos:state-guard")
        assert pubkey == kp1.public_key


# ---------------------------------------------------------------------------
# Path sanitization and traversal defense
# ---------------------------------------------------------------------------
class TestPathSanitization:
    def test_colon_replaced_with_underscore(self) -> None:
        result = _sanitize_did_for_path("did:companyos:eval-a")
        assert result == "did_companyos_eval-a"

    def test_dotdot_traversal_rejected_at_sanitize(self) -> None:
        with pytest.raises(ValueError, match=r"\.\.|traversal"):
            _sanitize_did_for_path("../evil")

    def test_dotdot_inside_string_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\.\.|traversal"):
            _sanitize_did_for_path("did:companyos:..evil")

    def test_forward_slash_rejected_at_sanitize(self) -> None:
        with pytest.raises(ValueError, match="path separator"):
            _sanitize_did_for_path("did/companyos/eval")

    def test_backslash_rejected_at_sanitize(self) -> None:
        with pytest.raises(ValueError, match="path separator"):
            _sanitize_did_for_path("did\\companyos\\eval")

    def test_register_rejects_traversal_did(self, tmp_path: Path) -> None:
        kp = _make_keypair()
        reg = _reg(tmp_path)
        with pytest.raises(ValueError):
            reg.register("../evil", kp.public_key, "hash-bad")

    def test_register_rejects_slash_did(self, tmp_path: Path) -> None:
        kp = _make_keypair()
        reg = _reg(tmp_path)
        with pytest.raises(ValueError):
            reg.register("did/has/slashes", kp.public_key, "hash-bad")

    def test_register_rejects_absolute_path_did(self, tmp_path: Path) -> None:
        """A DID containing a path separator looks like an absolute path after join."""
        kp = _make_keypair()
        reg = _reg(tmp_path)
        # On Windows the separator is \; on POSIX it is /. Both are blocked.
        with pytest.raises(ValueError):
            reg.register("/etc/passwd", kp.public_key, "hash-bad")


# ---------------------------------------------------------------------------
# YAML disk round-trip
# ---------------------------------------------------------------------------
class TestYAMLRoundTrip:
    def test_fresh_registry_loads_existing_yaml(self, tmp_path: Path) -> None:
        """Write via one registry instance, reload into a fresh instance."""
        kp_a = _make_keypair()
        kp_b = _make_keypair()

        writer = EvaluatorRegistry(root=tmp_path)
        writer.register("did:companyos:alpha", kp_a.public_key, "hash-alpha")
        writer.register("did:companyos:beta", kp_b.public_key, "hash-beta")

        reader = EvaluatorRegistry(root=tmp_path)
        pk_a, ch_a = reader.get("did:companyos:alpha")
        pk_b, ch_b = reader.get("did:companyos:beta")

        assert pk_a == kp_a.public_key
        assert ch_a == "hash-alpha"
        assert pk_b == kp_b.public_key
        assert ch_b == "hash-beta"

    def test_load_count_matches_registered(self, tmp_path: Path) -> None:
        writer = EvaluatorRegistry(root=tmp_path)
        kp = _make_keypair()
        writer.register("did:companyos:count-test", kp.public_key, "hash-c")

        # load() returns the number of NEW entries added.
        fresh = EvaluatorRegistry()
        count = fresh.load(tmp_path)
        assert count == 1

    def test_load_missing_dir_returns_zero(self, tmp_path: Path) -> None:
        reg = EvaluatorRegistry()
        missing = tmp_path / "does-not-exist"
        assert reg.load(missing) == 0

    def test_load_empty_dir_returns_zero(self, tmp_path: Path) -> None:
        reg = EvaluatorRegistry()
        assert reg.load(tmp_path) == 0

    def test_load_rejects_yaml_missing_canonical_hash(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text(
            "evaluator_did: did:x\npublic_key_hex: aabb\n", encoding="utf-8"
        )
        reg = EvaluatorRegistry()
        with pytest.raises(ValueError, match="canonical_hash"):
            reg.load(tmp_path)

    def test_load_rejects_non_mapping_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text("- just\n- a list\n", encoding="utf-8")
        reg = EvaluatorRegistry()
        with pytest.raises(ValueError, match="must be a mapping"):
            reg.load(tmp_path)

    def test_ids_returns_sorted_list(self, tmp_path: Path) -> None:
        kp = _make_keypair()
        reg = EvaluatorRegistry(root=tmp_path)
        reg.register("did:companyos:z", kp.public_key, "hash-z")
        reg.register("did:companyos:a", kp.public_key, "hash-a")
        assert reg.ids() == ["did:companyos:a", "did:companyos:z"]

    def test_two_registries_do_not_share_state(self, tmp_path: Path) -> None:
        kp = _make_keypair()
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        reg_a = EvaluatorRegistry(root=dir_a)
        reg_b = EvaluatorRegistry(root=dir_b)
        reg_a.register("did:companyos:only-in-a", kp.public_key, "hash-a")

        assert reg_a.ids() == ["did:companyos:only-in-a"]
        assert reg_b.ids() == []

    def test_register_without_load_raises(self) -> None:
        reg = EvaluatorRegistry()  # no root
        kp = _make_keypair()
        with pytest.raises(ValueError, match="load"):
            reg.register("did:companyos:x", kp.public_key, "hash-x")
