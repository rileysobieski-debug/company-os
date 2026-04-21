"""
core/primitives/signer.py -- Signer protocol abstraction (v1b)
==============================================================
Ticket B0-b of the v1b Oracle build. Defines the `Signer` protocol so
`Oracle.founder_override` accepts HSM/KMS-backed signing in the future
without changing call shapes.

Design
------
`Signer` is a `typing.Protocol` (runtime-checkable) with two members:
  - `public_key: Ed25519PublicKey` (property)
  - `sign(canonical_bytes: bytes) -> Signature`

This keeps the crypto surface narrow: callers that hold a `Signer` never
need to know whether the private key lives in memory or behind a remote
KMS endpoint.

Concrete implementations
------------------------
`LocalKeypairSigner`
    Frozen dataclass wrapping an `Ed25519Keypair`. Delegates `sign` to
    `identity.sign`. Covers all current (v1b) production paths.

`KMSSignerStub`
    Frozen dataclass that accepts an ARN for forward-compat import in
    v1c code. Both `.public_key` and `.sign` raise `NotImplementedError`
    so any accidental runtime use fails loudly rather than silently.

Import cycle check
------------------
`signer.py` imports from `identity.py` (which imports `exceptions.py`).
`oracle.py` imports from `signer.py`. No reverse imports exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.primitives.identity import (
    Ed25519Keypair,
    Ed25519PublicKey,
    Signature,
    sign as _identity_sign,
)


# ---------------------------------------------------------------------------
# Signer protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class Signer(Protocol):
    """Protocol for objects that can produce an Ed25519 signature.

    Runtime-checkable so `Oracle.founder_override` can distinguish a
    `Signer` from a raw `Ed25519Keypair` at runtime via `isinstance`.

    Note: `@runtime_checkable` only checks attribute *names*, not call
    signatures. That is acceptable here because no other primitive in
    this package carries both a `public_key` attribute and a `sign`
    method.
    """

    @property
    def public_key(self) -> Ed25519PublicKey: ...

    def sign(self, canonical_bytes: bytes) -> Signature: ...


# ---------------------------------------------------------------------------
# LocalKeypairSigner
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LocalKeypairSigner:
    """A `Signer` backed by an in-memory `Ed25519Keypair`.

    This is the standard signer for all current (v1b) production paths.
    It is a frozen dataclass so instances are hashable and comparable.

    Usage::

        signer = LocalKeypairSigner(Ed25519Keypair.generate())
        sig = signer.sign(b"some bytes")

    The `.sign` method delegates directly to `identity.sign`, so there
    is no crypto duplication.
    """

    keypair: Ed25519Keypair

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.keypair.public_key

    def sign(self, canonical_bytes: bytes) -> Signature:
        return _identity_sign(self.keypair, canonical_bytes)


# ---------------------------------------------------------------------------
# KMSSignerStub
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KMSSignerStub:
    """Forward-compat stub for a KMS-backed signer (v1c placeholder).

    Accepts an AWS/GCP KMS ARN at construction time so v1c code can
    already write against this interface. Running production code against
    an instance raises `NotImplementedError` loudly; the stub is
    intentionally not usable until v1c ships the real implementation.

    Usage (v1c authors only)::

        signer = KMSSignerStub(arn="arn:aws:kms:us-east-1:123:key/abc")
        # Will raise NotImplementedError until v1c lands.
        signer.sign(b"bytes")
    """

    arn: str

    @property
    def public_key(self) -> Ed25519PublicKey:
        raise NotImplementedError("KMS signer deferred to v1c")

    def sign(self, canonical_bytes: bytes) -> Signature:
        raise NotImplementedError("KMS signer deferred to v1c")


__all__ = [
    "Signer",
    "LocalKeypairSigner",
    "KMSSignerStub",
]
