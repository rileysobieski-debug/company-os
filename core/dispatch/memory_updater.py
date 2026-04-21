"""
core/dispatch/memory_updater.py — Phase 7.3 — dated memory append + output routing
==================================================================================
After an evaluator Verdict lands, this module:

1. Writes the synthesised output artifact to the correct dept subfolder,
   gated by status:
     - PASS                    → <dept>/output/approved/<ts>-<session>-<spec>.md
     - NEEDS_FOUNDER_REVIEW    → <dept>/output/pending-approval/...
     - FAIL                    → <dept>/output/rejected/...
   (Plan §4.1 constraint 5 + Phase 7 spec: downstream synthesis is ALWAYS
   written — work is never discarded — but it is gated at the router
   before it reaches `approved/`.)

2. Appends a dated entry to `<dept>/manager-memory.md` AND
   `<dept>/<specialist>/memory.md`. Entries carry the §1.5 Priority-6
   required provenance fields: `entry_date` and `derived_from_session`.

The module is deterministic and idempotent per Verdict ts — re-running the
same record for the same (specialist, session, ts) is a no-op on the
memory files (the artifact file is overwritten with identical bytes).

Subdir name canonicalisation goes through `core.config.get_output_subdirs()`
so the folder names stay in one place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core import config
from core.dispatch.evaluator import Verdict, VerdictStatus

MANAGER_MEMORY_FILENAME = "manager-memory.md"
SPECIALIST_MEMORY_FILENAME = "memory.md"
OUTPUT_SUBDIR = "output"

# VerdictStatus → key into config.get_output_subdirs()
_ROUTE_KEY_BY_STATUS: dict[VerdictStatus, str] = {
    VerdictStatus.PASS: "approved",
    VerdictStatus.NEEDS_FOUNDER_REVIEW: "pending_approval",
    VerdictStatus.FAIL: "rejected",
}

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MemoryEntry:
    """One append-only entry in a manager- or specialist-memory file.

    Carries the Priority-6 provenance fields required by §1.5:
    `entry_date` (YYYY-MM-DD) and `derived_from_session`.
    """

    entry_date: str
    derived_from_session: str
    specialist_id: str
    skill_id: str
    ts: str                       # full ISO — disambiguates entries on same day
    status: VerdictStatus
    total_score: float
    summary: str
    references: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RouteResult:
    """What the updater touched on disk for a single verdict."""

    artifact_path: Path
    manager_memory_path: Path
    specialist_memory_path: Path
    destination: str              # one of "approved" | "pending-approval" | "rejected"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_component(token: str) -> str:
    cleaned = _FILENAME_SAFE_RE.sub("-", token.strip()).strip("-")
    return cleaned or "unknown"


def _safe_ts(ts: str) -> str:
    return _FILENAME_SAFE_RE.sub("-", ts)


def _destination_folder_name(status: VerdictStatus) -> str:
    subdirs = config.get_output_subdirs()
    return subdirs[_ROUTE_KEY_BY_STATUS[status]]


def route_output_dir(dept_dir: Path, status: VerdictStatus) -> Path:
    """Return the output subdir a given VerdictStatus routes to."""
    return dept_dir / OUTPUT_SUBDIR / _destination_folder_name(status)


def _artifact_filename(verdict: Verdict) -> str:
    return (
        f"{_safe_ts(verdict.ts)}"
        f"-{_safe_component(verdict.session_id)}"
        f"-{_safe_component(verdict.specialist_id)}.md"
    )


# ---------------------------------------------------------------------------
# Output-artifact routing
# ---------------------------------------------------------------------------
def write_output_artifact(dept_dir: Path, verdict: Verdict, content: str) -> Path:
    """Write `content` to the output subdir picked by `verdict.status`.

    Idempotent: writing the same verdict twice produces byte-identical
    files. The filename encodes the full verdict timestamp, so repeats
    are only possible for exact duplicates."""
    dest = route_output_dir(dept_dir, verdict.status)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / _artifact_filename(verdict)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Memory-file append
# ---------------------------------------------------------------------------
def _format_entry(entry: MemoryEntry) -> str:
    ref_block = ""
    if entry.references:
        bullet = "\n".join(f"  - {r}" for r in entry.references)
        ref_block = f"**References:**\n{bullet}\n\n"
    return (
        f"\n## {entry.entry_date} — session `{entry.derived_from_session}` — "
        f"specialist `{entry.specialist_id}` — skill `{entry.skill_id}`\n\n"
        f"**Verdict:** {entry.status.value} (score {entry.total_score:.2f})\n\n"
        f"**Summary:** {entry.summary}\n\n"
        f"{ref_block}"
        f"<!-- entry_date: {entry.entry_date} -->\n"
        f"<!-- derived_from_session: {entry.derived_from_session} -->\n"
        f"<!-- derived_from_ts: {entry.ts} -->\n"
    )


def _already_written(existing: str, entry: MemoryEntry) -> bool:
    """Idempotency marker — same ts → already appended."""
    return f"<!-- derived_from_ts: {entry.ts} -->" in existing


def _append_to_memory_file(path: Path, entry: MemoryEntry) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if _already_written(existing, entry):
        return path
    path.write_text(existing + _format_entry(entry), encoding="utf-8")
    return path


def append_manager_memory(dept_dir: Path, entry: MemoryEntry) -> Path:
    """Append a dated entry to `<dept>/manager-memory.md`. Idempotent by ts."""
    return _append_to_memory_file(dept_dir / MANAGER_MEMORY_FILENAME, entry)


def append_specialist_memory(
    dept_dir: Path, specialist_id: str, entry: MemoryEntry
) -> Path:
    """Append a dated entry to `<dept>/<specialist>/memory.md`. Idempotent by ts.

    `specialist_id` is sanitised for use as a directory segment, defending
    against path traversal the same way handshake_runner does."""
    safe = _safe_component(specialist_id)
    return _append_to_memory_file(
        dept_dir / safe / SPECIALIST_MEMORY_FILENAME, entry
    )


# ---------------------------------------------------------------------------
# High-level composition (called from dispatch post-hook)
# ---------------------------------------------------------------------------
def record_dispatch_outcome(
    dept_dir: Path,
    *,
    verdict: Verdict,
    output_content: str,
    summary: str,
    references: tuple[str, ...] = (),
) -> RouteResult:
    """Do all three writes for one verdict: artifact + manager-mem + spec-mem.

    Returns a RouteResult with the three paths and the destination-folder
    label. The caller is the dispatch post-hook; this function has no
    side-effects beyond these three file writes."""
    entry = MemoryEntry(
        entry_date=_date_from_ts(verdict.ts),
        derived_from_session=verdict.session_id,
        specialist_id=verdict.specialist_id,
        skill_id=verdict.skill_id,
        ts=verdict.ts,
        status=verdict.status,
        total_score=verdict.total_score,
        summary=summary,
        references=tuple(references),
    )
    artifact_path = write_output_artifact(dept_dir, verdict, output_content)
    manager_mem = append_manager_memory(dept_dir, entry)
    specialist_mem = append_specialist_memory(
        dept_dir, verdict.specialist_id, entry
    )
    return RouteResult(
        artifact_path=artifact_path,
        manager_memory_path=manager_mem,
        specialist_memory_path=specialist_mem,
        destination=_destination_folder_name(verdict.status),
    )


def _date_from_ts(ts: str) -> str:
    """Best-effort YYYY-MM-DD from an ISO ts. Falls back to the first 10 chars."""
    return ts.split("T", 1)[0] if "T" in ts else ts[:10]
