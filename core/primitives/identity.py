"""
core/primitives/identity.py — Ed25519 identity wire-shape types + signing
=========================================================================
Ticket 5 introduced the dumb wire-shape containers. **Ticket 8** extends
this module with the cryptographic signing / verification path:

    - `Ed25519Keypair` — private-key-carrying counterpart of `Ed25519PublicKey`
    - `sign(keypair, canonical_bytes) -> Signature`
    - `verify(signature, canonical_bytes) -> bool`

All signing uses Ed25519, which is deterministic by construction (no RNG
leak risk across runs — signing the same bytes with the same keypair
always produces an identical signature). That property is what lets the
`InterOrgSLA` signature path stay deterministic end-to-end.

Library choice
--------------
We use ``pynacl`` (libsodium bindings) when available and fall back to
``cryptography`` (OpenSSL bindings) otherwise. Both are pure-Python
bindings to mature C libraries; either satisfies the correctness and
determinism requirements. The fallback is transparent — callers never
see the difference.

The ``Signature`` wire shape carries the signer's pubkey inline so
verification does NOT require out-of-band key lookup: a receiver can
verify a signature end-to-end just by holding the canonical bytes + the
``Signature`` dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.primitives.exceptions import SignatureError


# ---------------------------------------------------------------------------
# Backend selection — pynacl preferred, cryptography fallback.
# ---------------------------------------------------------------------------
# We expose three backend primitives:
#   - `_backend_generate() -> (private_bytes: bytes, public_bytes: bytes)`
#   - `_backend_sign(private_bytes, message) -> sig_bytes`
#   - `_backend_verify(public_bytes, message, sig_bytes) -> bool`
#
# Each backend either succeeds or returns False / raises a verify-domain
# exception that we swallow into False (so callers can treat "invalid
# signature" as a boolean outcome rather than a control-flow exception).

_BACKEND: str
try:  # pragma: no cover - import-time branch selection
    import nacl.signing  # type: ignore
    import nacl.exceptions  # type: ignore

    _BACKEND = "pynacl"

    def _backend_generate() -> tuple[bytes, bytes]:
        sk = nacl.signing.SigningKey.generate()
        return bytes(sk), bytes(sk.verify_key)

    def _backend_sign(private_bytes: bytes, message: bytes) -> bytes:
        sk = nacl.signing.SigningKey(private_bytes)
        signed = sk.sign(message)
        return signed.signature  # 64 raw bytes

    def _backend_verify(public_bytes: bytes, message: bytes, sig_bytes: bytes) -> bool:
        try:
            vk = nacl.signing.VerifyKey(public_bytes)
            vk.verify(message, sig_bytes)
            return True
        except (nacl.exceptions.BadSignatureError, ValueError, TypeError):
            return False

except ImportError:  # pragma: no cover - fallback path only hit in envs without pynacl
    from cryptography.exceptions import InvalidSignature  # type: ignore
    from cryptography.hazmat.primitives.asymmetric import ed25519  # type: ignore
    from cryptography.hazmat.primitives import serialization  # type: ignore

    _BACKEND = "cryptography"

    def _backend_generate() -> tuple[bytes, bytes]:
        sk = ed25519.Ed25519PrivateKey.generate()
        priv = sk.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub = sk.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return priv, pub

    def _backend_sign(private_bytes: bytes, message: bytes) -> bytes:
        sk = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)
        return sk.sign(message)

    def _backend_verify(public_bytes: bytes, message: bytes, sig_bytes: bytes) -> bool:
        try:
            vk = ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)
            vk.verify(sig_bytes, message)
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Ed25519PublicKey:
    """Hex-encoded 32-byte Ed25519 public key.

    Wire shape:
        {"bytes_hex": "<64 hex chars>"}

    Ticket 5 left this as a dumb container. Ticket 8 still does NOT
    validate length / hex-ness here because downstream consumers
    (`verify`) surface those as `SignatureError` when a malformed value
    is used in a crypto operation. Keeping the constructor permissive
    matches the `from_dict` rehydration contract — never reject data at
    load time; reject it at use time.
    """

    bytes_hex: str

    def to_dict(self) -> dict:
        return {"bytes_hex": self.bytes_hex}

    @classmethod
    def from_dict(cls, d: dict) -> "Ed25519PublicKey":
        if "bytes_hex" not in d:
            raise ValueError(
                f"Ed25519PublicKey.from_dict missing 'bytes_hex': {d!r}"
            )
        return cls(bytes_hex=str(d["bytes_hex"]))


@dataclass(frozen=True)
class Signature:
    """Hex-encoded 64-byte Ed25519 signature + the signer's public key.

    Wire shape:
        {"sig_hex": "<128 hex chars>",
         "signer":  {"bytes_hex": "<64 hex chars>"}}

    Because the signer pubkey travels with the signature, a verifier
    does not need to consult any external registry to check the
    signature — holding the canonical bytes + the Signature is enough.
    The separate question of "is this signer AUTHORIZED for this role"
    is handled one layer up (see `InterOrgSLA.verify_signatures`).
    """

    sig_hex: str
    signer: Ed25519PublicKey

    def to_dict(self) -> dict:
        return {"sig_hex": self.sig_hex, "signer": self.signer.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "Signature":
        if "sig_hex" not in d or "signer" not in d:
            raise ValueError(
                f"Signature.from_dict missing required keys "
                f"(need 'sig_hex' and 'signer'): {d!r}"
            )
        return cls(
            sig_hex=str(d["sig_hex"]),
            signer=Ed25519PublicKey.from_dict(d["signer"]),
        )


@dataclass(frozen=True)
class Ed25519Keypair:
    """A full Ed25519 keypair — private + public.

    `private_key_hex` is secret: it MUST NEVER leave the node.
    `public_key` is safe to publish (that's what gets stamped into
    `Signature.signer` when this keypair signs something).

    Construction is via `Ed25519Keypair.generate()` — callers should not
    build these from arbitrary hex strings in application code. The raw
    constructor is kept available for test fixtures and for loading a
    keypair from local secure storage, but production nodes should
    generate their keypairs through `.generate()`.
    """

    private_key_hex: str
    public_key: Ed25519PublicKey

    @classmethod
    def generate(cls) -> "Ed25519Keypair":
        """Generate a fresh Ed25519 keypair using the active backend.

        The private key is random; two successive calls return
        cryptographically distinct keypairs (this is what the backend's
        CSPRNG guarantees). We assert that at the test layer rather than
        here to keep this path hot.
        """
        priv_bytes, pub_bytes = _backend_generate()
        return cls(
            private_key_hex=priv_bytes.hex(),
            public_key=Ed25519PublicKey(bytes_hex=pub_bytes.hex()),
        )


# ---------------------------------------------------------------------------
# Sign / verify
# ---------------------------------------------------------------------------
def sign(keypair: Ed25519Keypair, canonical_bytes: bytes) -> Signature:
    """Sign ``canonical_bytes`` with ``keypair``, returning a `Signature`.

    Ed25519 is deterministic: the same (keypair, canonical_bytes) pair
    always produces the same sig bytes. The returned `Signature` stamps
    the signer's pubkey alongside the sig so the verifier can check it
    without a registry lookup.

    Raises:
        SignatureError: if `keypair` is not an `Ed25519Keypair` or
            `canonical_bytes` is not `bytes`. These are programmer
            errors — we raise rather than return a sentinel so callers
            fail loudly at the type boundary.
    """
    if not isinstance(keypair, Ed25519Keypair):
        raise SignatureError(
            f"sign() requires Ed25519Keypair, got {type(keypair).__name__}"
        )
    if not isinstance(canonical_bytes, (bytes, bytearray)):
        raise SignatureError(
            f"sign() requires bytes for canonical_bytes, got "
            f"{type(canonical_bytes).__name__}"
        )
    try:
        priv_bytes = bytes.fromhex(keypair.private_key_hex)
    except ValueError as exc:
        raise SignatureError(
            f"Ed25519Keypair.private_key_hex is not valid hex: {exc}"
        ) from exc
    sig_bytes = _backend_sign(priv_bytes, bytes(canonical_bytes))
    return Signature(
        sig_hex=sig_bytes.hex(),
        signer=keypair.public_key,
    )


def verify(signature: Signature, canonical_bytes: bytes) -> bool:
    """Verify ``signature`` over ``canonical_bytes``.

    Returns True iff the signature is a valid Ed25519 signature of
    ``canonical_bytes`` under the pubkey embedded in
    ``signature.signer``. Returns False on:
        - malformed sig hex / pubkey hex
        - wrong-keypair signature
        - tampered canonical_bytes

    Raises `SignatureError` ONLY on programmer errors (type mismatch —
    e.g., passing a dict instead of a `Signature`, or a str instead of
    bytes). This keeps the happy path branchless for callers who want
    to treat "invalid sig" as a boolean.
    """
    if not isinstance(signature, Signature):
        raise SignatureError(
            f"verify() requires Signature, got {type(signature).__name__}"
        )
    if not isinstance(canonical_bytes, (bytes, bytearray)):
        raise SignatureError(
            f"verify() requires bytes for canonical_bytes, got "
            f"{type(canonical_bytes).__name__}"
        )
    try:
        sig_bytes = bytes.fromhex(signature.sig_hex)
        pub_bytes = bytes.fromhex(signature.signer.bytes_hex)
    except ValueError:
        # Malformed hex in the wire shape — a tampered or corrupt
        # signature. Treat as invalid, not as a programmer error.
        return False
    # Length mismatch (e.g., truncated bytes) → False, not SignatureError.
    if len(sig_bytes) != 64 or len(pub_bytes) != 32:
        return False
    return _backend_verify(pub_bytes, bytes(canonical_bytes), sig_bytes)


__all__ = [
    "Ed25519PublicKey",
    "Ed25519Keypair",
    "Signature",
    "sign",
    "verify",
]
