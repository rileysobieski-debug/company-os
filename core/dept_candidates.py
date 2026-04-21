"""
core/dept_candidates.py

Candidate-slate data model for the manager hire flow.

Instead of dispatching one arrival note at a time and forcing the
founder to re-roll until they like what they see, the hire flow now
dispatches THREE candidates in parallel. Each candidate draws an
independent personality-seed sample and writes its own arrival note in
its own conversation thread. The founder then reviews all three side by
side and selects one. Selecting a candidate synthesizes its thread into
skill-scope.md (normal flow) and marks the other two as discarded.

A slate lives at `<dept>/candidates.json`. During scope_calibration
phase, the department's current_artifact path points at this file.
When the founder selects a candidate, the current_artifact is replaced
with the synthesized skill-scope.md (existing semantics preserved).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional


STATUS_DRAFTING = "drafting"      # job dispatched, arrival note not yet landed
STATUS_READY = "ready"            # arrival note written, awaiting review
STATUS_SELECTED = "selected"      # founder picked this one
STATUS_DISCARDED = "discarded"    # founder picked another; this candidate archived

VALID_CANDIDATE_STATUSES = frozenset({
    STATUS_DRAFTING, STATUS_READY, STATUS_SELECTED, STATUS_DISCARDED,
})

# How many candidates we show at a time.
DEFAULT_SLATE_SIZE = 3


@dataclass(frozen=True)
class Candidate:
    """One of the N parallel candidates in a slate."""

    candidate_id: str               # short hash, used in URLs
    label: str                      # "Candidate A", "Candidate B", ...
    thread_path: str                # vault-relative, conversations/xxx.json
    job_id: str = ""                # background job that wrote the arrival note
    status: str = STATUS_DRAFTING
    created_at: str = ""


@dataclass(frozen=True)
class CandidateSlate:
    """Slate of candidates currently under review for a department's manager hire."""

    dept: str
    created_at: str = ""
    last_updated_at: str = ""
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    selected_candidate_id: str = ""

    def find(self, candidate_id: str) -> Optional[Candidate]:
        for c in self.candidates:
            if c.candidate_id == candidate_id:
                return c
        return None

    @property
    def any_drafting(self) -> bool:
        return any(c.status == STATUS_DRAFTING for c in self.candidates)

    @property
    def all_ready(self) -> bool:
        return all(c.status in {STATUS_READY, STATUS_SELECTED, STATUS_DISCARDED} for c in self.candidates)


# ---------------------------------------------------------------------------
# Path helpers + persistence
# ---------------------------------------------------------------------------

def candidates_path(company_dir: Path, dept: str) -> Path:
    return company_dir / dept / "candidates.json"


def new_candidate_id() -> str:
    return uuid.uuid4().hex[:8]


def _candidate_to_dict(c: Candidate) -> dict:
    return {
        "candidate_id": c.candidate_id,
        "label": c.label,
        "thread_path": c.thread_path,
        "job_id": c.job_id,
        "status": c.status,
        "created_at": c.created_at,
    }


def _candidate_from_dict(d: dict) -> Candidate:
    return Candidate(
        candidate_id=d.get("candidate_id", ""),
        label=d.get("label", ""),
        thread_path=d.get("thread_path", ""),
        job_id=d.get("job_id", ""),
        status=d.get("status", STATUS_DRAFTING),
        created_at=d.get("created_at", ""),
    )


def load_slate(company_dir: Path, dept: str) -> Optional[CandidateSlate]:
    p = candidates_path(company_dir, dept)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return CandidateSlate(
        dept=raw.get("dept", dept),
        created_at=raw.get("created_at", ""),
        last_updated_at=raw.get("last_updated_at", ""),
        candidates=tuple(_candidate_from_dict(c) for c in raw.get("candidates", [])),
        selected_candidate_id=raw.get("selected_candidate_id", ""),
    )


def persist_slate(company_dir: Path, slate: CandidateSlate) -> Path:
    p = candidates_path(company_dir, slate.dept)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dept": slate.dept,
        "created_at": slate.created_at,
        "last_updated_at": slate.last_updated_at,
        "selected_candidate_id": slate.selected_candidate_id,
        "candidates": [_candidate_to_dict(c) for c in slate.candidates],
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def delete_slate(company_dir: Path, dept: str) -> None:
    """Remove the slate file if it exists. Used on re-roll."""
    p = candidates_path(company_dir, dept)
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def upsert_candidate(slate: CandidateSlate, candidate: Candidate) -> CandidateSlate:
    """Return a new slate with the given candidate inserted or replaced by id."""
    new_cands = []
    found = False
    for c in slate.candidates:
        if c.candidate_id == candidate.candidate_id:
            new_cands.append(candidate)
            found = True
        else:
            new_cands.append(c)
    if not found:
        new_cands.append(candidate)
    return replace(slate, candidates=tuple(new_cands))
