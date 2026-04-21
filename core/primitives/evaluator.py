"""
core/primitives/evaluator.py -- EvaluatorRegistry + PrimaryEvaluator + EvaluationOutput
========================================================================================
Tickets B1-a and B1-b of the v1b Oracle build. These land as a single unit
because the registry, protocol, and output type form a tight dependency ring:
the registry stores the canonical_hash that EvaluationOutput must carry, and
the protocol defines the interface that both the registry and the output type
exist to service.

Why a separate registry rather than reusing NodeRegistry
---------------------------------------------------------
`NodeRegistry` maps a DID to a public key for signature verification.
`EvaluatorRegistry` maps a DID to BOTH a public key AND a canonical_hash.
The canonical_hash is an artifact of the evaluator's versioned algorithm --
it pins the exact algorithm implementation, not just the identity. A node
can swap its keypair; an evaluator's canonical_hash is an immutable fact
about which code version signed the verdict.

DID path sanitization (vs NodeRegistry's SHA-256 filename approach)
--------------------------------------------------------------------
NodeRegistry uses SHA-256(DID)[:12] as the filename because it never needs
humans to read the filenames. EvaluatorRegistry uses a sanitized DID string
directly -- colons replaced with underscores, traversal characters rejected --
so operators can inspect the registry directory and immediately know which
file belongs to which evaluator. The sanitization rules mirror Decision 1
of the B1 architectural record.

EvaluationResult sharing
------------------------
`EvaluationResult` carries the same Literal values as `OracleResult`. We
re-declare it here rather than importing from oracle.py. This keeps
evaluator.py importable without pulling in the full oracle module (which
imports signer, state, sla, etc.) and avoids a potential future divergence
trap where evaluator results and oracle results accidentally conflate.

_VALID_EVIDENCE_KINDS coupling
-------------------------------
`EvaluationOutput.__post_init__` imports `_VALID_EVIDENCE_KINDS` from
oracle.py to validate `evidence["kind"]`. This is an intentional semi-public
coupling: the evaluator output must use the SAME kind vocabulary as
OracleVerdict so that evidence can flow from EvaluationOutput into a verdict
without a translation step. The coupling is documented here and in oracle.py.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import yaml

from core.primitives.exceptions import SignatureError
from core.primitives.identity import Ed25519PublicKey
from core.primitives.sla import InterOrgSLA


# ---------------------------------------------------------------------------
# EvaluationResult
# ---------------------------------------------------------------------------
# Re-declared (not imported from oracle.py) to keep this module's import graph
# shallow. Same Literal values as OracleResult by design -- they represent the
# same semantic outcomes at the evaluation boundary.
EvaluationResult = Literal["accepted", "rejected", "refunded"]


# ---------------------------------------------------------------------------
# EvaluationOutput
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EvaluationOutput:
    """Immutable output produced by a PrimaryEvaluator after inspecting an artifact.

    `evidence["kind"]` must belong to the extended EvidenceKind set defined in
    oracle.py. The validation runs at construction time via __post_init__ so
    every EvaluationOutput in circulation is structurally sound.

    `evaluator_canonical_hash` pins the exact evaluator algorithm version that
    produced this output, enabling a downstream OracleVerdict to commit to
    BOTH the evaluator identity (via evaluator_did) and its algorithm version.
    """

    result: EvaluationResult
    score: Decimal
    evidence: dict
    evaluator_canonical_hash: str

    def __post_init__(self) -> None:
        # Import here to avoid a module-level circular dependency:
        # oracle.py imports sla.py; evaluator.py imports sla.py.
        # Both are safe. The import-time cycle risk is oracle -> evaluator
        # if we put this at module level, which we avoid by deferring.
        # _VALID_EVIDENCE_KINDS is a frozenset -- the import is cheap.
        from core.primitives.oracle import _VALID_EVIDENCE_KINDS  # noqa: PLC0415

        kind = self.evidence.get("kind")
        if kind not in _VALID_EVIDENCE_KINDS:
            raise ValueError(
                f"EvaluationOutput evidence has unknown kind {kind!r}. "
                f"Valid kinds: {sorted(_VALID_EVIDENCE_KINDS)}"
            )


# ---------------------------------------------------------------------------
# PrimaryEvaluator Protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class PrimaryEvaluator(Protocol):
    """Structural protocol for Tier 1 evaluator objects.

    Evaluators are runtime-checkable so test code can assert isinstance()
    without subclassing. Production evaluators that satisfy these three
    members (two properties + one method) are automatically conformant.

    `evaluator_did` identifies the evaluator node; `canonical_hash` pins
    the exact algorithm version in use. Both are stamped into
    EvaluationOutput so downstream code can reconstruct WHAT evaluated and
    WHICH VERSION of the algorithm it used.
    """

    @property
    def evaluator_did(self) -> str:
        """The DID of this evaluator node."""
        ...

    @property
    def canonical_hash(self) -> str:
        """SHA-256 hex digest (or similar stable hash) of the evaluator's
        algorithm artifact, pinning the version that produced this output."""
        ...

    def evaluate(
        self,
        sla: InterOrgSLA,
        artifact_bytes: bytes,
        *,
        artifact_properties: dict | None = None,
    ) -> EvaluationOutput:
        """Evaluate an artifact against an SLA and return an EvaluationOutput."""
        ...


# ---------------------------------------------------------------------------
# Path sanitization
# ---------------------------------------------------------------------------
def _sanitize_did_for_path(evaluator_did: str) -> str:
    """Produce a filesystem-safe filename component from an evaluator DID.

    Rules (Decision 1 of the B1 architectural record):
      - Replace `:` with `_` (DIDs contain colons; Windows forbids them).
      - Reject strings containing `..` (directory traversal).
      - Reject strings containing path separators `/ or \\`.

    Returns the sanitized string to use as `<sanitized_did>.yaml`.

    Raises:
        ValueError: if the DID contains `..` or path separator characters.
    """
    if ".." in evaluator_did:
        raise ValueError(
            f"evaluator_did must not contain '..': {evaluator_did!r}"
        )
    if "/" in evaluator_did or "\\" in evaluator_did:
        raise ValueError(
            f"evaluator_did must not contain path separators: {evaluator_did!r}"
        )
    return evaluator_did.replace(":", "_")


# ---------------------------------------------------------------------------
# EvaluatorRegistry
# ---------------------------------------------------------------------------
class EvaluatorRegistry:
    """In-memory index of evaluator DID -> (Ed25519PublicKey, canonical_hash) bindings.

    Mirrors `NodeRegistry` in shape (constructor with optional root, load,
    register, get) but carries one additional field per entry: the
    `canonical_hash` that pins the evaluator's algorithm version.

    Constructor accepts an optional `root` path. When provided, `load(root)`
    is called immediately so the registry is populated from any existing YAML
    files in that directory. This lets callers build a ready-to-use registry
    in a single expression:

        reg = EvaluatorRegistry(root=Path("data/evaluators"))

    Rebinding policy
    ----------------
    Registering a DID that is already present with the SAME pubkey and
    canonical_hash is idempotent -- no error, no file write. Registering with
    a DIFFERENT canonical_hash raises ValueError; registering with a DIFFERENT
    pubkey also raises ValueError. Intentional: changing either fact about an
    evaluator requires an explicit revoke-and-re-register step.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._root: Path | None = None
        if root is not None:
            self.load(Path(root))

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    def load(self, root: Path) -> int:
        """Walk `root/*.yaml`, register each evaluator binding.

        Returns the number of NEW DIDs loaded. Missing `root` returns 0.
        Malformed YAML or missing required fields raises ValueError.
        """
        root = Path(root)
        self._root = root
        if not root.exists() or not root.is_dir():
            return 0

        added = 0
        for path in sorted(root.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ValueError(
                    f"evaluator YAML parse failed at {path}: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise ValueError(
                    f"evaluator YAML at {path} must be a mapping, "
                    f"got {type(data).__name__}"
                )
            for key in ("evaluator_did", "public_key_hex", "canonical_hash"):
                if key not in data:
                    raise ValueError(
                        f"evaluator YAML at {path} missing required field {key!r}"
                    )
            did = str(data["evaluator_did"])
            record: dict[str, Any] = {
                "public_key_hex": str(data["public_key_hex"]),
                "canonical_hash": str(data["canonical_hash"]),
                "first_seen": str(data.get("first_seen", "")),
                "notes": str(data.get("notes", "")),
            }
            if did not in self._entries:
                added += 1
            self._entries[did] = record
        return added

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get(self, evaluator_did: str) -> tuple[Ed25519PublicKey, str]:
        """Return `(pubkey, canonical_hash)` for the given evaluator DID.

        Raises KeyError with a descriptive message on miss, matching the
        NodeRegistry.get contract so callers can re-raise as SignatureError.
        """
        try:
            record = self._entries[evaluator_did]
        except KeyError as exc:
            raise KeyError(f"unknown evaluator_did: {evaluator_did}") from exc
        pubkey = Ed25519PublicKey(bytes_hex=record["public_key_hex"])
        return pubkey, record["canonical_hash"]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def register(
        self,
        evaluator_did: str,
        public_key: Ed25519PublicKey,
        canonical_hash: str,
        *,
        notes: str = "",
    ) -> None:
        """Write a new evaluator DID -> (pubkey, canonical_hash) binding to disk.

        Path traversal defense: the sanitized DID is resolved against `root`
        and the result is checked to ensure it stays inside `root`. Any DID
        that would escape (e.g. via encoded separators that survive sanitization
        when joined) raises ValueError.

        Immutability policy:
          - Same DID, same pubkey, same canonical_hash: idempotent no-op.
          - Same DID, different canonical_hash: raises ValueError.
          - Same DID, different pubkey: raises ValueError.

        Atomicity: write to a tempfile, then Path.replace onto target.

        Raises:
            ValueError: if evaluator_did is empty, contains `..` or path
                separators, or if the resolved path escapes root.
            ValueError: if the DID is already registered with different fields.
            TypeError: if public_key is not an Ed25519PublicKey.
        """
        if self._root is None:
            raise ValueError(
                "EvaluatorRegistry.register requires load() to have been called "
                "with an explicit root Path first, or pass root= to the constructor."
            )
        if not isinstance(evaluator_did, str) or not evaluator_did:
            raise ValueError("evaluator_did must be a non-empty string")
        if not isinstance(public_key, Ed25519PublicKey):
            raise TypeError(
                f"public_key must be Ed25519PublicKey, got {type(public_key).__name__}"
            )

        # Sanitize and check for path traversal.
        sanitized = _sanitize_did_for_path(evaluator_did)
        target = (self._root / f"{sanitized}.yaml").resolve()
        root_resolved = self._root.resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError:
            raise ValueError(
                f"evaluator_did {evaluator_did!r} resolves outside registry root "
                f"after sanitization: {target}"
            )

        # Idempotency / conflict check.
        existing = self._entries.get(evaluator_did)
        if existing is not None:
            same_key = existing["public_key_hex"] == public_key.bytes_hex
            same_hash = existing["canonical_hash"] == canonical_hash
            if same_key and same_hash:
                # Exact same binding -- idempotent no-op.
                return
            if not same_hash:
                raise ValueError(
                    f"evaluator DID {evaluator_did!r} already registered with "
                    f"canonical_hash {existing['canonical_hash']!r}; "
                    f"cannot re-register with {canonical_hash!r}"
                )
            # not same_key (regardless of hash match)
            raise ValueError(
                f"evaluator DID {evaluator_did!r} already registered with a "
                f"different public_key; revoke and re-register explicitly"
            )

        self._root.mkdir(parents=True, exist_ok=True)

        first_seen = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record_yaml: dict[str, Any] = {
            "evaluator_did": evaluator_did,
            "public_key_hex": public_key.bytes_hex,
            "canonical_hash": canonical_hash,
            "first_seen": first_seen,
            "notes": notes,
        }

        # Atomic write: tempfile in same dir, then replace.
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=".evaluator-", suffix=".yaml.tmp", dir=str(self._root)
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(record_yaml, fh, sort_keys=True)
            tmp_path.replace(target)
        except Exception:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise

        self._entries[evaluator_did] = {
            "public_key_hex": public_key.bytes_hex,
            "canonical_hash": canonical_hash,
            "first_seen": first_seen,
            "notes": notes,
        }

    def ids(self) -> list[str]:
        """Return the sorted list of registered evaluator DIDs."""
        return sorted(self._entries.keys())


__all__ = [
    "EvaluationResult",
    "EvaluationOutput",
    "PrimaryEvaluator",
    "EvaluatorRegistry",
]
