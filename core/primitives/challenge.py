"""
core/primitives/challenge.py -- Challenge primitive (v1b)
=========================================================
Ticket B1-c of the v1b Oracle build. Defines the `Challenge` frozen
dataclass that lets a counterparty challenge a Tier 1 OracleVerdict
within its challenge window.

Design overview
---------------
A Challenge is a signed attestation that a counterparty disputes a
prior OracleVerdict. It carries:

  - `prior_verdict_hash`  -- the `verdict_hash` of the disputed verdict
  - `challenger_did`      -- the DID of the challenging party
  - `reason`              -- human-readable dispute rationale (<=2000 chars)
  - `challenge_hash`      -- sha256 hex of canonical bytes (sig excluded)
  - `signer`              -- the challenger's Ed25519 public key
  - `signature`           -- Ed25519 signature over canonical bytes
  - `issued_at`           -- UTC timestamp stamped at create-time
  - `protocol_version`    -- "companyos-challenge/0.1"

No separate ChallengeLedger primitive is created here. Challenges flow
through `SettlementEventLedger` as `challenge_raised` / `challenge_resolved`
events per B3 (future). This ticket is strictly the Challenge primitive.

Authorization policy
--------------------
The Challenge primitive does NOT enforce that `challenger_did` is a
counterparty to the underlying SLA. Authorization is checked at the
adapter boundary (B3). This keeps the primitive reusable for Tier 2
escalation paths.

Canonical serialization rules
------------------------------
Mirror the OracleVerdict discipline exactly:

1. `sort_keys=True`, `separators=(",",":")`, `ensure_ascii=False`.
2. `signer` serialized as `{"bytes_hex": "..."}` dict.
3. `signature` ALWAYS excluded from canonical bytes.
4. `challenge_hash` excluded DURING its own computation (chicken-and-egg,
   same as `verdict_hash` in oracle.py), then INCLUDED in the bytes that
   are signed so the signature commits to the hash.

`_challenge_canonical_bytes` is a SEPARATE function from oracle.py's
`_canonical_bytes`: the field sets differ.

Registration
------------
`default_canonicalizer_registry.register("companyos-challenge/0.1", ...)`
runs at module-load time, exactly as oracle.py does for its own version.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.primitives.canonicalizer_registry import (
    default_canonicalizer_registry,
    extract_protocol_version,
)
from core.primitives.exceptions import SignatureError
from core.primitives.identity import (
    Ed25519PublicKey,
    Signature,
    verify as _identity_verify,
)
from core.primitives.oracle import OracleVerdict
from core.primitives.signer import Signer

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------
_PROTOCOL_VERSION_DEFAULT = "companyos-challenge/0.1"
_REASON_MAX_CHARS = 2000

# Fields always excluded from canonical bytes.
_ALWAYS_EXCLUDED_FROM_CANONICAL = frozenset({"signature"})


# ---------------------------------------------------------------------------
# Private serialization helpers
# ---------------------------------------------------------------------------
def _challenge_shell_dict(
    challenge: "Challenge | dict[str, Any]",
    *,
    exclude_challenge_hash: bool = False,
) -> dict[str, Any]:
    """Build the dict that feeds into `_challenge_canonical_bytes`.

    Excluded fields:
      - `signature` (always, for signing-body invariance).
      - `challenge_hash` (only when `exclude_challenge_hash=True`, used
        during initial hash computation to avoid chicken-and-egg).

    `signer` is serialized as `{"bytes_hex": "..."}` so the canonical bytes
    include a stable dict representation.
    """
    if isinstance(challenge, Challenge):
        raw: dict[str, Any] = {
            "prior_verdict_hash": challenge.prior_verdict_hash,
            "challenger_did": challenge.challenger_did,
            "reason": challenge.reason,
            "challenge_hash": challenge.challenge_hash,
            "signer": challenge.signer,
            "signature": challenge.signature,
            "issued_at": challenge.issued_at,
            "protocol_version": challenge.protocol_version,
        }
    else:
        raw = dict(challenge)

    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _ALWAYS_EXCLUDED_FROM_CANONICAL:
            continue
        if exclude_challenge_hash and key == "challenge_hash":
            continue
        if isinstance(value, Ed25519PublicKey):
            out[key] = value.to_dict()
        else:
            out[key] = value
    return out


def _challenge_canonical_bytes(
    challenge: "Challenge | dict[str, Any]",
    exclude_challenge_hash: bool = False,
) -> bytes:
    """Produce canonical UTF-8 JSON bytes for a challenge.

    `signature` is always excluded. `challenge_hash` excluded when
    `exclude_challenge_hash=True` (used when computing the hash itself).

    `sort_keys=True` handles recursive key-sorting of nested dicts,
    matching the OracleVerdict canonical idiom.

    Registered into `default_canonicalizer_registry` under
    "companyos-challenge/0.1" at module-load time.

    Note: the registry CanonicalizerFn signature passes the second
    positional argument as a bool. For challenges the parameter is
    named `exclude_challenge_hash` (not `exclude_verdict_hash`), but
    the positional position is the same so registry dispatch works
    transparently.
    """
    shell = _challenge_shell_dict(
        challenge, exclude_challenge_hash=exclude_challenge_hash
    )
    return json.dumps(
        shell,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# Register the challenge canonicalization rules at module-load time.
# challenge.py imports the registry (not the reverse), so there is no
# circular import. The registry key is distinct from oracle.py's keys.
default_canonicalizer_registry.register(
    _PROTOCOL_VERSION_DEFAULT,
    _challenge_canonical_bytes,
)


# ---------------------------------------------------------------------------
# Challenge
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Challenge:
    """Immutable signed challenge against a prior OracleVerdict.

    Field ordering note: Python frozen dataclasses require default-less
    fields first. The `protocol_version` default comes last.

    Construction: prefer `Challenge.create(...)`. The raw constructor
    is available for `from_dict` rehydration but skips all validation.
    """

    # --- required fields ----------------------------------------------------
    prior_verdict_hash: str
    challenger_did: str
    reason: str
    challenge_hash: str
    signer: Ed25519PublicKey
    signature: Signature
    issued_at: str

    # --- defaulted fields ---------------------------------------------------
    protocol_version: str = _PROTOCOL_VERSION_DEFAULT

    # -----------------------------------------------------------------------
    # Factory
    # -----------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        prior_verdict: OracleVerdict,
        challenger_did: str,
        reason: str,
        signer: Signer,
    ) -> "Challenge":
        """Strict factory: validate, hash, sign, and return a frozen challenge.

        `signer` provides both the signature and the embedded `signer`
        public key via its `.public_key` property and `.sign()` method.

        `challenge_hash` is the sha256 hex digest of canonical bytes with
        BOTH `signature` and `challenge_hash` excluded. The signing body
        includes `challenge_hash` so the signature commits to all content.

        Parameters
        ----------
        prior_verdict:
            The OracleVerdict being challenged. Only `verdict_hash` is
            stored in the Challenge; the full verdict object is not kept.
        challenger_did:
            DID of the challenging party. Not validated for authorization
            here -- that check belongs at the adapter boundary.
        reason:
            Human-readable dispute rationale. Must be non-empty and at most
            2000 characters (Python `len`, not byte count).
        signer:
            A `Signer` instance used to sign the challenge.

        Raises
        ------
        ValueError
            If `challenger_did` is empty, `reason` is empty, or `reason`
            exceeds 2000 characters.
        TypeError
            If `prior_verdict` is not an `OracleVerdict`, or `signer` is
            not a `Signer`.
        """
        # --- scalar validation ----------------------------------------------
        if not isinstance(prior_verdict, OracleVerdict):
            raise TypeError(
                f"prior_verdict must be an OracleVerdict, "
                f"got {type(prior_verdict).__name__}"
            )
        if not isinstance(challenger_did, str) or not challenger_did:
            raise ValueError("challenger_did must be a non-empty string")
        if not isinstance(reason, str) or not reason:
            raise ValueError("reason must be a non-empty string")
        if len(reason) > _REASON_MAX_CHARS:
            raise ValueError(
                f"reason must be at most {_REASON_MAX_CHARS} characters, "
                f"got {len(reason)}"
            )
        if not isinstance(signer, Signer):
            raise TypeError(
                f"signer must be a Signer, got {type(signer).__name__}"
            )

        # --- stamp issued_at ------------------------------------------------
        issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- build shell dict (no challenge_hash yet) -----------------------
        shell: dict[str, Any] = {
            "prior_verdict_hash": prior_verdict.verdict_hash,
            "challenger_did": challenger_did,
            "reason": reason,
            # challenge_hash placeholder excluded in first pass
            "signer": signer.public_key,
            # signature excluded by canonicalizer
            "issued_at": issued_at,
            "protocol_version": _PROTOCOL_VERSION_DEFAULT,
        }

        # Protocol-constant step: read version, then dispatch canonicalizer.
        _version = extract_protocol_version(shell)
        canonicalize = default_canonicalizer_registry.get(_version)

        # Step 1: compute challenge_hash over bytes with hash excluded.
        body_no_hash = canonicalize(shell, True)
        challenge_hash = hashlib.sha256(body_no_hash).hexdigest()

        # Step 2: sign bytes that include challenge_hash.
        shell_with_hash = dict(shell, challenge_hash=challenge_hash)
        signing_body = canonicalize(shell_with_hash, False)
        sig = signer.sign(signing_body)

        return cls(
            prior_verdict_hash=prior_verdict.verdict_hash,
            challenger_did=challenger_did,
            reason=reason,
            challenge_hash=challenge_hash,
            signer=signer.public_key,
            signature=sig,
            issued_at=issued_at,
            protocol_version=_PROTOCOL_VERSION_DEFAULT,
        )

    # -----------------------------------------------------------------------
    # Signature verification
    # -----------------------------------------------------------------------
    def verify_signature(self) -> None:
        """Verify the embedded Ed25519 signature over canonical bytes.

        Recomputes canonical bytes (signature excluded, challenge_hash
        included) and checks the signature against `self.signer`.

        Raises
        ------
        SignatureError
            If the signer embedded in `self.signature` does not match
            `self.signer`, or if cryptographic verification fails
            (tampered fields, wrong keypair, etc.).
        ValueError
            If `self.protocol_version` is not registered in the
            canonicalizer registry (unknown version).
        """
        # Step 1: Signer consistency. The pubkey in the Signature must equal
        # the top-level `signer` field.
        if self.signature.signer != self.signer:
            raise SignatureError(
                "signature.signer does not match top-level signer field"
            )

        # Step 2: Cryptographic verify. Dispatch through registry so future
        # protocol versions can substitute different byte rules.
        _version = self.protocol_version
        canonicalize = default_canonicalizer_registry.get(_version)

        # Recompute the bytes that were signed in `create`: canonical bytes
        # with challenge_hash included and signature excluded.
        signing_body = canonicalize(self, False)
        if not _identity_verify(self.signature, signing_body):
            raise SignatureError(
                "Challenge signature failed cryptographic verification"
            )

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Emit a fully-serializable dict including all fields."""
        return {
            "prior_verdict_hash": self.prior_verdict_hash,
            "challenger_did": self.challenger_did,
            "reason": self.reason,
            "challenge_hash": self.challenge_hash,
            "signer": self.signer.to_dict(),
            "signature": self.signature.to_dict(),
            "issued_at": self.issued_at,
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Challenge":
        """Rehydrate from a `to_dict` payload.

        Does NOT re-verify the signature or recompute `challenge_hash`.
        Callers must call `verify_signature()` explicitly if they need
        cryptographic assurance after load.

        If `protocol_version` is unknown, construction still succeeds;
        `verify_signature()` will raise `ValueError` when it tries to
        dispatch through the registry. This matches `OracleVerdict.from_dict`
        behavior.

        Raises
        ------
        ValueError
            If a required field is missing from `d`.
        """
        required = (
            "prior_verdict_hash",
            "challenger_did",
            "reason",
            "challenge_hash",
            "signer",
            "signature",
            "issued_at",
        )
        for key in required:
            if key not in d:
                raise ValueError(f"Challenge.from_dict missing field {key!r}")

        return cls(
            prior_verdict_hash=str(d["prior_verdict_hash"]),
            challenger_did=str(d["challenger_did"]),
            reason=str(d["reason"]),
            challenge_hash=str(d["challenge_hash"]),
            signer=Ed25519PublicKey.from_dict(d["signer"]),
            signature=Signature.from_dict(d["signature"]),
            issued_at=str(d["issued_at"]),
            protocol_version=str(
                d.get("protocol_version", _PROTOCOL_VERSION_DEFAULT)
            ),
        )


__all__ = [
    "Challenge",
]
