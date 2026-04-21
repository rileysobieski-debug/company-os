"""
core/primitives/node_registry.py — DID → Ed25519 pubkey binding
================================================================
Ticket 10 of the v0 Currency-Agnostic Settlement Architecture.

Why this exists
---------------
Ed25519 signatures alone prove that SOME keypair signed the bytes. They
do not prove that the keypair belongs to the entity named in
`requester_node_did` / `provider_node_did`. Without a DID → pubkey
binding, v0 is Sybil-vulnerable: anyone can generate a keypair and claim
to be `did:companyos:old-press-wine`. `NodeRegistry` closes this hole
locally by mapping each known DID to its canonical pubkey on disk.

Storage format (one entry per file)
-----------------------------------
Each `*.yaml` file under `root` holds exactly one node binding:

    node_did: did:companyos:abc123
    public_key_hex: "<64 hex chars>"
    first_seen: "2026-04-19T12:00:00Z"
    notes: "Old Press Wine Company primary node"

Filenames are `sha256(node_did)[:12].yaml` — we derive the filename from
the DID so callers never need to pick one, AND we dodge the fact that
Windows filenames cannot contain the colons that DIDs use (`did:...`).
The registry still indexes by the `node_did` value inside the file.

Rebinding policy
----------------
Registering a DID that is already known with a DIFFERENT pubkey raises
`SignatureError` — the v0 stance is "revoke and re-register explicitly,
don't silently overwrite". Registering the SAME pubkey again is an
idempotent no-op (returns without touching the file).

Mirrors `core.primitives.asset.AssetRegistry` for YAML loading style.
No module-level singleton — every caller builds its own registry.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from core.primitives.exceptions import SignatureError
from core.primitives.identity import Ed25519PublicKey


# ---------------------------------------------------------------------------
# Filename derivation
# ---------------------------------------------------------------------------
def _filename_for_did(did: str) -> str:
    """Derive a filesystem-safe filename for a DID.

    DIDs contain colons (`did:companyos:abc`), which are illegal in
    Windows filenames. We take the first 12 hex chars of the SHA-256
    of the DID — collision-safe at this scale and stable across runs.
    """
    digest = hashlib.sha256(did.encode("utf-8")).hexdigest()
    return f"{digest[:12]}.yaml"


# ---------------------------------------------------------------------------
# NodeRegistry
# ---------------------------------------------------------------------------
class NodeRegistry:
    """In-memory index of DID → Ed25519PublicKey bindings.

    The registry owns its own state — independent instances never share
    a dict, so tests can build isolated registries freely.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._root: Path | None = None

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    def load(self, root: Path) -> int:
        """Walk `root/*.yaml`, register each node binding.

        Returns the number of NEW DIDs registered on this call. A
        missing `root` directory returns 0 (empty registry is a valid
        initial state). Malformed YAML or missing required fields
        raises `ValueError` with the offending file path.
        """
        if root is None:
            raise ValueError(
                "NodeRegistry.load requires an explicit root Path — "
                "no module-level singleton exists."
            )
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
                    f"node YAML parse failed at {path}: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise ValueError(
                    f"node YAML at {path} must be a mapping, "
                    f"got {type(data).__name__}"
                )
            for key in ("node_did", "public_key_hex"):
                if key not in data:
                    raise ValueError(
                        f"node YAML at {path} missing required field {key!r}"
                    )
            did = str(data["node_did"])
            record = {
                "public_key_hex": str(data["public_key_hex"]),
                "first_seen": str(data.get("first_seen", "")),
                "notes": str(data.get("notes", "")),
            }
            if did not in self._nodes:
                added += 1
            self._nodes[did] = record
        return added

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get(self, did: str) -> Ed25519PublicKey:
        """Return the registered `Ed25519PublicKey` for `did`.

        Raises `KeyError("unknown node_did: <did>")` on miss. The
        explicit message lets callers one layer up (`verify_signatures`)
        re-raise as `SignatureError` with a precise failure mode.
        """
        try:
            record = self._nodes[did]
        except KeyError as exc:
            raise KeyError(f"unknown node_did: {did}") from exc
        return Ed25519PublicKey(bytes_hex=record["public_key_hex"])

    def ids(self) -> list[str]:
        """Return the sorted list of registered DIDs."""
        return sorted(self._nodes.keys())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def register(
        self,
        did: str,
        pubkey: Ed25519PublicKey,
        *,
        notes: str = "",
        first_seen: str = "",
    ) -> None:
        """Write a new DID → pubkey binding to disk atomically.

        - Rejects overwriting an existing DID with a DIFFERENT pubkey
          (`SignatureError("DID rebinding forbidden; revoke and
          re-register explicitly")`).
        - Overwriting with the SAME pubkey is idempotent — no error,
          no YAML write (preserves mtime).

        Atomicity: we write to a tempfile in the same directory and
        then `Path.replace` onto the target path. The rename is atomic
        on POSIX and "best-effort atomic" on Windows; if `replace`
        fails, the tempfile is cleaned up and the registry on-disk
        state is unchanged.
        """
        if self._root is None:
            raise ValueError(
                "NodeRegistry.register requires load() to have been called "
                "with an explicit root Path first."
            )
        if not isinstance(did, str) or not did:
            raise ValueError("did must be a non-empty string")
        if not isinstance(pubkey, Ed25519PublicKey):
            raise TypeError(
                f"pubkey must be Ed25519PublicKey, got {type(pubkey).__name__}"
            )

        existing = self._nodes.get(did)
        if existing is not None:
            if existing["public_key_hex"] == pubkey.bytes_hex:
                # Idempotent — same binding, don't touch disk.
                return
            raise SignatureError(
                "DID rebinding forbidden; revoke and re-register explicitly"
            )

        # Ensure the directory exists (register into an empty registry
        # dir is a supported path — no surprise mkdir failures).
        self._root.mkdir(parents=True, exist_ok=True)

        record_yaml = {
            "node_did": did,
            "public_key_hex": pubkey.bytes_hex,
            "first_seen": first_seen,
            "notes": notes,
        }
        target = self._root / _filename_for_did(did)

        # Atomic write: tempfile in the same dir, then replace.
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=".node-", suffix=".yaml.tmp", dir=str(self._root)
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(record_yaml, fh, sort_keys=True)
            tmp_path.replace(target)
        except Exception:
            # Best-effort cleanup — if replace failed, tmp still sits
            # in the dir and we don't want it to be picked up by load().
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise

        self._nodes[did] = {
            "public_key_hex": pubkey.bytes_hex,
            "first_seen": first_seen,
            "notes": notes,
        }


__all__ = ["NodeRegistry"]
