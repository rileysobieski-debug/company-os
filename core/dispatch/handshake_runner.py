"""
core/dispatch/handshake_runner.py — Priority 5 handshake audit trail
====================================================================
A Handshake records one dispatch event: sender asks receiver to do
something (`intent`) and receiver commits to return something
(`deliverable`). Handshakes live at

    <company>/handshakes/<session_id>/<ts>-<sender>-to-<receiver>.json

per §1.5 row 5. They are append-only and never a source of new claims —
Priority 5 exists so the citation contract (§7.2) can still reference a
handshake the same way it references any other provenanced record, but
the authority ranking guarantees handshakes can't outvote KB or Brand.

Filename convention: sortable timestamp prefix → lexicographic sort
matches chronological order without parsing.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from core.primitives.state import AuthorityPriority, Claim

HANDSHAKES_SUBDIR = "handshakes"
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class Handshake:
    session_id: str
    ts: str              # ISO-8601 UTC
    sender: str
    receiver: str
    intent: str
    deliverable: str
    references: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(token: str) -> str:
    """Make a token safe for use in a filename segment."""
    cleaned = _FILENAME_SAFE_RE.sub("-", token.strip()).strip("-")
    return cleaned or "unknown"


def _filename_ts(ts: str) -> str:
    """Pack an ISO timestamp into a sortable filename-safe prefix."""
    return _FILENAME_SAFE_RE.sub("-", ts)


def _handshake_path(company_dir: Path, hs: Handshake) -> Path:
    return (
        company_dir
        / HANDSHAKES_SUBDIR
        / _safe(hs.session_id)
        / f"{_filename_ts(hs.ts)}-{_safe(hs.sender)}-to-{_safe(hs.receiver)}.json"
    )


def _relative_ref(company_dir: Path, target: Path) -> str:
    try:
        rel = target.resolve().relative_to(company_dir.resolve())
    except ValueError:
        rel = target
    return rel.as_posix() if hasattr(rel, "as_posix") else str(rel).replace("\\", "/")


# ---------------------------------------------------------------------------
# Read / Write
# ---------------------------------------------------------------------------
def write_handshake(company_dir: Path, hs: Handshake) -> Path:
    """Persist `hs` under the company's handshakes dir. Returns the path.

    Idempotent per (session_id, ts, sender, receiver): writing the same
    Handshake twice overwrites byte-identical content. Writing two
    Handshakes that differ only in content at the same ts collides —
    callers must stamp unique timestamps (the default helper uses
    microsecond ISO, which is sufficient for sequential sends).
    """
    path = _handshake_path(company_dir, hs)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(hs)
    payload["references"] = list(hs.references)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_handshake(path: Path) -> Handshake | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    required = ("session_id", "ts", "sender", "receiver", "intent", "deliverable")
    if not all(k in raw for k in required):
        return None
    refs = raw.get("references") or []
    if not isinstance(refs, list):
        refs = []
    return Handshake(
        session_id=str(raw["session_id"]),
        ts=str(raw["ts"]),
        sender=str(raw["sender"]),
        receiver=str(raw["receiver"]),
        intent=str(raw["intent"]),
        deliverable=str(raw["deliverable"]),
        references=tuple(str(r) for r in refs),
    )


def iter_session_handshakes(
    company_dir: Path, session_id: str
) -> Iterator[Handshake]:
    """Yield every Handshake in `<company>/handshakes/<session_id>/`, in
    chronological order (filenames are timestamp-prefixed)."""
    session_dir = company_dir / HANDSHAKES_SUBDIR / _safe(session_id)
    if not session_dir.exists():
        return
    for p in sorted(session_dir.glob("*.json")):
        hs = load_handshake(p)
        if hs is not None:
            yield hs


# ---------------------------------------------------------------------------
# High-level runner API (called from dispatch hooks)
# ---------------------------------------------------------------------------
def record_handshake(
    company_dir: Path,
    *,
    session_id: str,
    sender: str,
    receiver: str,
    intent: str,
    deliverable: str,
    references: tuple[str, ...] = (),
    now: str | None = None,
) -> Handshake:
    """Convenience: build + persist + return a Handshake in one call.
    This is the method dispatch_manager pre/post hooks wire into."""
    hs = Handshake(
        session_id=session_id,
        ts=now or _now_iso(),
        sender=sender,
        receiver=receiver,
        intent=intent,
        deliverable=deliverable,
        references=tuple(references),
    )
    write_handshake(company_dir, hs)
    return hs


# ---------------------------------------------------------------------------
# Claim adapter (Priority 5)
# ---------------------------------------------------------------------------
def handshake_to_claim(hs: Handshake, company_dir: Path | None = None) -> Claim:
    """Wrap a handshake as a Priority 5 Claim. Priority 5 is pure audit —
    it loses to every higher tier and is never a source of new claims,
    but the envelope flows through `resolve_conflict()` identically.

    If `company_dir` is provided, the claim ref is vault-relative; otherwise
    it's the sessions-dir-relative path (still sortable, still unique)."""
    if not hs.ts:
        raise ValueError(
            f"Handshake {hs.session_id!r} missing ts — cannot produce a valid Claim"
        )
    if company_dir is not None:
        source_path = _relative_ref(company_dir, _handshake_path(company_dir, hs))
    else:
        source_path = (
            f"{HANDSHAKES_SUBDIR}/{_safe(hs.session_id)}/"
            f"{_filename_ts(hs.ts)}-{_safe(hs.sender)}-to-{_safe(hs.receiver)}.json"
        )
    provenance = {
        "updated_at": hs.ts,
        "updated_by": hs.sender,
        "source_path": source_path,
        "ingested_at": hs.ts,
    }
    content = {
        "session_id": hs.session_id,
        "sender": hs.sender,
        "receiver": hs.receiver,
        "intent": hs.intent,
        "deliverable": hs.deliverable,
        "references": list(hs.references),
    }
    return Claim(
        priority=AuthorityPriority.HANDSHAKE,
        content=content,
        ref=f"priority_5_handshake:{source_path}",
        provenance=provenance,
    )
