"""
tests/test_signer.py -- Ticket B0-b coverage for the Signer protocol
=====================================================================
Unit tests for `core.primitives.signer`:

- `LocalKeypairSigner.sign` produces identical bytes to `identity.sign`
  with the same keypair and message.
- `LocalKeypairSigner.public_key` returns the keypair's `public_key`.
- `isinstance(LocalKeypairSigner(...), Signer)` is True (runtime_checkable
  Protocol).
- `isinstance(Ed25519Keypair.generate(), Signer)` is False. `Ed25519Keypair`
  has a `public_key` dataclass field but does NOT have a `.sign(bytes)`
  method, so the runtime_checkable check fails.
- `KMSSignerStub(arn=...)` constructs without raising.
- `KMSSignerStub.public_key` raises `NotImplementedError`.
- `KMSSignerStub.sign` raises `NotImplementedError`.
"""
from __future__ import annotations

import pytest

from core.primitives.identity import Ed25519Keypair, sign as identity_sign
from core.primitives.signer import KMSSignerStub, LocalKeypairSigner, Signer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def keypair() -> Ed25519Keypair:
    return Ed25519Keypair.generate()


@pytest.fixture
def local_signer(keypair: Ed25519Keypair) -> LocalKeypairSigner:
    return LocalKeypairSigner(keypair=keypair)


# ---------------------------------------------------------------------------
# LocalKeypairSigner
# ---------------------------------------------------------------------------
class TestLocalKeypairSigner:
    def test_sign_produces_same_bytes_as_identity_sign(
        self, keypair: Ed25519Keypair, local_signer: LocalKeypairSigner
    ):
        """LocalKeypairSigner.sign delegates to identity.sign with no mutation.

        Ed25519 is deterministic, so the same (keypair, message) pair always
        produces the same signature bytes regardless of call path.
        """
        message = b"canonical test bytes"
        via_signer = local_signer.sign(message)
        via_identity = identity_sign(keypair, message)
        assert via_signer.sig_hex == via_identity.sig_hex
        assert via_signer.signer == via_identity.signer

    def test_sign_different_messages_produce_different_sigs(
        self, local_signer: LocalKeypairSigner
    ):
        """Sanity: distinct messages produce distinct signatures."""
        sig_a = local_signer.sign(b"message a")
        sig_b = local_signer.sign(b"message b")
        assert sig_a.sig_hex != sig_b.sig_hex

    def test_public_key_matches_keypair_public_key(
        self, keypair: Ed25519Keypair, local_signer: LocalKeypairSigner
    ):
        assert local_signer.public_key == keypair.public_key

    def test_signer_embeds_correct_public_key(
        self, keypair: Ed25519Keypair, local_signer: LocalKeypairSigner
    ):
        """The Signature returned by .sign has the correct signer pubkey."""
        sig = local_signer.sign(b"check embedded signer")
        assert sig.signer == keypair.public_key

    def test_is_instance_of_signer_protocol(self, local_signer: LocalKeypairSigner):
        """runtime_checkable Protocol: LocalKeypairSigner satisfies Signer."""
        assert isinstance(local_signer, Signer)

    def test_is_frozen_dataclass(self, local_signer: LocalKeypairSigner):
        """LocalKeypairSigner is frozen; mutation raises FrozenInstanceError."""
        import dataclasses

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            local_signer.keypair = Ed25519Keypair.generate()  # type: ignore[misc]

    def test_two_signers_with_same_keypair_are_equal(self, keypair: Ed25519Keypair):
        """Frozen dataclass equality: same keypair -> same signer."""
        a = LocalKeypairSigner(keypair=keypair)
        b = LocalKeypairSigner(keypair=keypair)
        assert a == b

    def test_two_signers_with_different_keypairs_are_not_equal(self):
        kp1 = Ed25519Keypair.generate()
        kp2 = Ed25519Keypair.generate()
        assert LocalKeypairSigner(keypair=kp1) != LocalKeypairSigner(keypair=kp2)


# ---------------------------------------------------------------------------
# Ed25519Keypair is NOT a Signer
# ---------------------------------------------------------------------------
class TestRawKeypairIsNotSigner:
    def test_raw_keypair_is_not_instance_of_signer(self):
        """Ed25519Keypair has a `public_key` field but no `.sign(bytes)` method.

        runtime_checkable checks attribute names only. Because `Ed25519Keypair`
        has no `sign` method on its instances, `isinstance(kp, Signer)` is
        False. This is the property that makes Oracle.founder_override's guard
        reliable.
        """
        kp = Ed25519Keypair.generate()
        assert not isinstance(kp, Signer)


# ---------------------------------------------------------------------------
# KMSSignerStub
# ---------------------------------------------------------------------------
class TestKMSSignerStub:
    def test_constructs_with_arn(self):
        stub = KMSSignerStub(arn="arn:aws:kms:us-east-1:123456789012:key/test-key-id")
        assert stub.arn == "arn:aws:kms:us-east-1:123456789012:key/test-key-id"

    def test_sign_raises_not_implemented(self):
        stub = KMSSignerStub(arn="arn:aws:kms:us-east-1:123:key/abc")
        with pytest.raises(NotImplementedError, match="v1c"):
            stub.sign(b"some bytes")

    def test_public_key_raises_not_implemented(self):
        stub = KMSSignerStub(arn="arn:aws:kms:us-east-1:123:key/abc")
        with pytest.raises(NotImplementedError, match="v1c"):
            _ = stub.public_key

    def test_is_frozen_dataclass(self):
        stub = KMSSignerStub(arn="arn:example")
        import dataclasses

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            stub.arn = "arn:mutated"  # type: ignore[misc]

    def test_is_instance_of_signer_protocol(self):
        """KMSSignerStub satisfies the Signer Protocol structurally
        (it has the right attribute names), even though calling them raises."""
        stub = KMSSignerStub(arn="arn:example")
        assert isinstance(stub, Signer)
