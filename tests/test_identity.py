"""
tests/test_identity.py — Ticket 8 coverage for Ed25519 sign/verify
==================================================================
Covers the cryptographic additions to `core.primitives.identity`:

- `Ed25519Keypair.generate()` produces distinct keypairs across calls.
- `sign()` is deterministic (Ed25519 invariant).
- `verify()` returns True for valid, False for tamper / wrong-key /
  malformed hex / truncated bytes.
- `verify()` raises `SignatureError` only on type-level programmer errors.

We intentionally don't test the backend-selection path (pynacl vs
cryptography) — either backend must satisfy the same contract, and the
import-time branch is covered by every test below implicitly.
"""
from __future__ import annotations

import pytest

from core.primitives.exceptions import SignatureError
from core.primitives.identity import (
    Ed25519Keypair,
    Ed25519PublicKey,
    Signature,
    sign,
    verify,
)


# ---------------------------------------------------------------------------
# Keypair generation
# ---------------------------------------------------------------------------
class TestKeypairGeneration:
    def test_generate_produces_distinct_keypairs(self):
        kp_a = Ed25519Keypair.generate()
        kp_b = Ed25519Keypair.generate()
        # Both pubkey and privkey should differ — overwhelmingly
        # probable given the CSPRNG backing each backend.
        assert kp_a.public_key.bytes_hex != kp_b.public_key.bytes_hex
        assert kp_a.private_key_hex != kp_b.private_key_hex

    def test_generated_keys_have_expected_hex_length(self):
        """Ed25519 keys are 32 bytes (pub) + 32 bytes (seed/priv). Hex
        encoding doubles the length."""
        kp = Ed25519Keypair.generate()
        assert len(kp.public_key.bytes_hex) == 64
        # Both pynacl and cryptography expose 32-byte private seeds.
        assert len(kp.private_key_hex) == 64

    def test_public_key_is_ed25519publickey(self):
        kp = Ed25519Keypair.generate()
        assert isinstance(kp.public_key, Ed25519PublicKey)


# ---------------------------------------------------------------------------
# sign() — determinism and shape
# ---------------------------------------------------------------------------
class TestSign:
    def test_sign_returns_signature_with_embedded_signer(self):
        kp = Ed25519Keypair.generate()
        sig = sign(kp, b"hello")
        assert isinstance(sig, Signature)
        assert sig.signer == kp.public_key
        # 64-byte Ed25519 sig → 128 hex chars.
        assert len(sig.sig_hex) == 128

    def test_sign_is_deterministic(self):
        """Ed25519 has no RNG in its sign path — same inputs, same sig."""
        kp = Ed25519Keypair.generate()
        body = b"deterministic body"
        sig_1 = sign(kp, body)
        sig_2 = sign(kp, body)
        assert sig_1.sig_hex == sig_2.sig_hex

    def test_sign_rejects_non_bytes_body(self):
        kp = Ed25519Keypair.generate()
        with pytest.raises(SignatureError):
            sign(kp, "not bytes")  # type: ignore[arg-type]

    def test_sign_rejects_non_keypair(self):
        with pytest.raises(SignatureError):
            sign("not a keypair", b"body")  # type: ignore[arg-type]

    def test_sign_accepts_bytearray(self):
        kp = Ed25519Keypair.generate()
        body = bytearray(b"mutable bytes")
        sig = sign(kp, body)
        assert verify(sig, bytes(body)) is True


# ---------------------------------------------------------------------------
# verify() — accept / reject matrix
# ---------------------------------------------------------------------------
class TestVerify:
    def test_verify_valid_signature(self):
        kp = Ed25519Keypair.generate()
        body = b"authentic body"
        sig = sign(kp, body)
        assert verify(sig, body) is True

    def test_verify_tampered_bytes_returns_false(self):
        kp = Ed25519Keypair.generate()
        sig = sign(kp, b"original")
        assert verify(sig, b"TAMPERED") is False

    def test_verify_wrong_keypair_returns_false(self):
        """Sig from keypair A should not validate under pubkey from B.

        We construct a `Signature` with A's sig but B's embedded
        pubkey — the cryptographic check fails because the sig was
        produced under A's private key.
        """
        kp_a = Ed25519Keypair.generate()
        kp_b = Ed25519Keypair.generate()
        sig_by_a = sign(kp_a, b"body")
        # Tamper: keep A's sig bytes but swap the signer pubkey to B.
        forged = Signature(sig_hex=sig_by_a.sig_hex, signer=kp_b.public_key)
        assert verify(forged, b"body") is False

    def test_verify_malformed_sig_hex_returns_false(self):
        kp = Ed25519Keypair.generate()
        bad = Signature(sig_hex="zz" * 64, signer=kp.public_key)
        assert verify(bad, b"body") is False

    def test_verify_truncated_sig_returns_false(self):
        kp = Ed25519Keypair.generate()
        sig = sign(kp, b"body")
        truncated = Signature(sig_hex=sig.sig_hex[:-2], signer=sig.signer)
        assert verify(truncated, b"body") is False

    def test_verify_bad_pubkey_length_returns_false(self):
        kp = Ed25519Keypair.generate()
        sig = sign(kp, b"body")
        short_pubkey = Signature(
            sig_hex=sig.sig_hex,
            signer=Ed25519PublicKey(bytes_hex="ab" * 10),  # 20 bytes, not 32
        )
        assert verify(short_pubkey, b"body") is False

    def test_verify_rejects_non_signature(self):
        with pytest.raises(SignatureError):
            verify({"sig_hex": "00" * 64}, b"body")  # type: ignore[arg-type]

    def test_verify_rejects_non_bytes(self):
        kp = Ed25519Keypair.generate()
        sig = sign(kp, b"body")
        with pytest.raises(SignatureError):
            verify(sig, "body")  # type: ignore[arg-type]

    def test_signature_dict_round_trip_preserves_verify(self):
        """to_dict/from_dict must round-trip cleanly — Ticket 5 contract."""
        kp = Ed25519Keypair.generate()
        body = b"round trip body"
        sig = sign(kp, body)
        restored = Signature.from_dict(sig.to_dict())
        assert restored == sig
        assert verify(restored, body) is True
