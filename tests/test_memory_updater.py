"""Memory updater + output-dir routing (Phase 7.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.dispatch.evaluator import (
    CriterionResult,
    Verdict,
    VerdictStatus,
)
from core.dispatch.memory_updater import (
    MANAGER_MEMORY_FILENAME,
    SPECIALIST_MEMORY_FILENAME,
    MemoryEntry,
    RouteResult,
    append_manager_memory,
    append_specialist_memory,
    record_dispatch_outcome,
    route_output_dir,
    write_output_artifact,
)

ISO = "2026-04-17T10:00:00+00:00"


def _verdict(
    *, status: VerdictStatus = VerdictStatus.PASS,
    specialist_id: str = "positioning-writer",
    skill_id: str = "positioning-draft",
    session_id: str = "sess-1", ts: str = ISO,
    total_score: float = 0.92,
) -> Verdict:
    return Verdict(
        specialist_id=specialist_id, skill_id=skill_id,
        session_id=session_id, ts=ts, status=status,
        total_score=total_score,
        criterion_results=(
            CriterionResult(criterion_id="clarity", score=total_score, passed=True),
        ),
    )


# ---------------------------------------------------------------------------
# route_output_dir
# ---------------------------------------------------------------------------
def test_route_pass_to_approved(tmp_path: Path) -> None:
    p = route_output_dir(tmp_path, VerdictStatus.PASS)
    assert p.name == "approved"
    assert "output" in p.parts


def test_route_needs_review_to_pending_approval(tmp_path: Path) -> None:
    p = route_output_dir(tmp_path, VerdictStatus.NEEDS_FOUNDER_REVIEW)
    assert p.name == "pending-approval"


def test_route_fail_to_rejected(tmp_path: Path) -> None:
    p = route_output_dir(tmp_path, VerdictStatus.FAIL)
    assert p.name == "rejected"


# ---------------------------------------------------------------------------
# write_output_artifact
# ---------------------------------------------------------------------------
def test_write_output_artifact_creates_dir_and_file(tmp_path: Path) -> None:
    v = _verdict()
    path = write_output_artifact(tmp_path, v, "# positioning draft\n\n...")
    assert path.exists()
    assert path.parent.name == "approved"
    assert path.read_text(encoding="utf-8").startswith("# positioning draft")


def test_write_output_artifact_filename_includes_session_and_specialist(
    tmp_path: Path,
) -> None:
    v = _verdict(session_id="s1", specialist_id="writer")
    path = write_output_artifact(tmp_path, v, "content")
    assert "s1" in path.name
    assert "writer" in path.name
    assert path.suffix == ".md"


def test_write_output_artifact_fail_routes_to_rejected(tmp_path: Path) -> None:
    v = _verdict(status=VerdictStatus.FAIL, total_score=0.1)
    path = write_output_artifact(tmp_path, v, "bad output")
    assert path.parent.name == "rejected"


def test_write_output_artifact_needs_review_routes_to_pending(tmp_path: Path) -> None:
    v = _verdict(status=VerdictStatus.NEEDS_FOUNDER_REVIEW)
    path = write_output_artifact(tmp_path, v, "partial output")
    assert path.parent.name == "pending-approval"


def test_write_output_artifact_is_idempotent(tmp_path: Path) -> None:
    v = _verdict()
    p1 = write_output_artifact(tmp_path, v, "hello")
    p2 = write_output_artifact(tmp_path, v, "hello")
    assert p1 == p2
    assert p1.read_text(encoding="utf-8") == "hello"


# ---------------------------------------------------------------------------
# append_manager_memory
# ---------------------------------------------------------------------------
def _entry(**kw) -> MemoryEntry:
    defaults = dict(
        entry_date="2026-04-17",
        derived_from_session="sess-1",
        specialist_id="positioning-writer",
        skill_id="positioning-draft",
        ts=ISO,
        status=VerdictStatus.PASS,
        total_score=0.92,
        summary="Drafted positioning emphasising coastal provenance.",
    )
    defaults.update(kw)
    return MemoryEntry(**defaults)


def test_append_manager_memory_creates_file(tmp_path: Path) -> None:
    path = append_manager_memory(tmp_path, _entry())
    assert path.name == MANAGER_MEMORY_FILENAME
    body = path.read_text(encoding="utf-8")
    assert "2026-04-17" in body
    assert "sess-1" in body
    assert "positioning-writer" in body
    assert "pass" in body
    assert "<!-- entry_date: 2026-04-17 -->" in body
    assert "<!-- derived_from_session: sess-1 -->" in body


def test_append_manager_memory_appends_to_existing(tmp_path: Path) -> None:
    path = tmp_path / MANAGER_MEMORY_FILENAME
    path.write_text("# Manager memory\n\nExisting content.\n", encoding="utf-8")
    append_manager_memory(tmp_path, _entry())
    body = path.read_text(encoding="utf-8")
    assert "Existing content." in body
    assert "sess-1" in body


def test_append_manager_memory_idempotent_on_same_ts(tmp_path: Path) -> None:
    entry = _entry()
    append_manager_memory(tmp_path, entry)
    append_manager_memory(tmp_path, entry)
    body = (tmp_path / MANAGER_MEMORY_FILENAME).read_text(encoding="utf-8")
    # The derived_from_ts marker appears once per entry.
    assert body.count(f"<!-- derived_from_ts: {ISO} -->") == 1


def test_append_manager_memory_two_distinct_entries_both_appear(tmp_path: Path) -> None:
    e1 = _entry(ts="2026-04-17T10:00:00+00:00")
    e2 = _entry(ts="2026-04-17T11:00:00+00:00", derived_from_session="sess-2")
    append_manager_memory(tmp_path, e1)
    append_manager_memory(tmp_path, e2)
    body = (tmp_path / MANAGER_MEMORY_FILENAME).read_text(encoding="utf-8")
    assert "sess-1" in body
    assert "sess-2" in body


def test_append_manager_memory_writes_references(tmp_path: Path) -> None:
    entry = _entry(references=("decisions/2026-04-10-x.md",))
    append_manager_memory(tmp_path, entry)
    body = (tmp_path / MANAGER_MEMORY_FILENAME).read_text(encoding="utf-8")
    assert "decisions/2026-04-10-x.md" in body
    assert "**References:**" in body


# ---------------------------------------------------------------------------
# append_specialist_memory
# ---------------------------------------------------------------------------
def test_append_specialist_memory_places_file_in_spec_dir(tmp_path: Path) -> None:
    path = append_specialist_memory(tmp_path, "positioning-writer", _entry())
    assert path.name == SPECIALIST_MEMORY_FILENAME
    assert path.parent.name == "positioning-writer"


def test_append_specialist_memory_sanitises_traversal(tmp_path: Path) -> None:
    """Malicious specialist_id must not escape the dept dir."""
    path = append_specialist_memory(tmp_path, "../../etc/passwd", _entry())
    assert tmp_path.resolve() in path.resolve().parents


def test_append_specialist_memory_idempotent_on_same_ts(tmp_path: Path) -> None:
    entry = _entry()
    append_specialist_memory(tmp_path, "writer", entry)
    append_specialist_memory(tmp_path, "writer", entry)
    body = (tmp_path / "writer" / SPECIALIST_MEMORY_FILENAME).read_text(encoding="utf-8")
    assert body.count(f"<!-- derived_from_ts: {ISO} -->") == 1


# ---------------------------------------------------------------------------
# record_dispatch_outcome
# ---------------------------------------------------------------------------
def test_record_dispatch_outcome_pass_writes_three_files(tmp_path: Path) -> None:
    v = _verdict()
    result = record_dispatch_outcome(
        tmp_path, verdict=v, output_content="# draft\n\nbody",
        summary="Drafted positioning.",
    )
    assert isinstance(result, RouteResult)
    assert result.destination == "approved"
    assert result.artifact_path.exists()
    assert result.manager_memory_path.exists()
    assert result.specialist_memory_path.exists()


def test_record_dispatch_outcome_needs_review_gates_at_pending(tmp_path: Path) -> None:
    v = _verdict(status=VerdictStatus.NEEDS_FOUNDER_REVIEW)
    result = record_dispatch_outcome(
        tmp_path, verdict=v, output_content="partial",
        summary="Partial output — max iterations hit.",
    )
    # Artifact is written (work isn't lost, per §4.1 constraint 5) but routes
    # to pending-approval, NOT approved.
    assert result.destination == "pending-approval"
    assert "pending-approval" in result.artifact_path.parts


def test_record_dispatch_outcome_fail_routes_to_rejected(tmp_path: Path) -> None:
    v = _verdict(status=VerdictStatus.FAIL, total_score=0.2)
    result = record_dispatch_outcome(
        tmp_path, verdict=v, output_content="rejected draft",
        summary="Failed clarity criterion.",
    )
    assert result.destination == "rejected"
    # Memory updates still happen — the failure itself is a lesson.
    body = result.manager_memory_path.read_text(encoding="utf-8")
    assert "fail" in body
    assert "Failed clarity criterion." in body


def test_record_dispatch_outcome_memory_shared_across_sessions(tmp_path: Path) -> None:
    """Two verdicts in the same dept → both appear in manager-memory."""
    v1 = _verdict(session_id="s1", ts="2026-04-17T10:00:00+00:00")
    v2 = _verdict(session_id="s2", ts="2026-04-17T11:00:00+00:00")
    record_dispatch_outcome(
        tmp_path, verdict=v1, output_content="c1", summary="first",
    )
    record_dispatch_outcome(
        tmp_path, verdict=v2, output_content="c2", summary="second",
    )
    body = (tmp_path / MANAGER_MEMORY_FILENAME).read_text(encoding="utf-8")
    assert "s1" in body and "s2" in body
    assert "first" in body and "second" in body


def test_record_dispatch_outcome_propagates_references(tmp_path: Path) -> None:
    v = _verdict()
    result = record_dispatch_outcome(
        tmp_path, verdict=v, output_content="c",
        summary="s", references=("kb/chunks/x.md",),
    )
    body = result.manager_memory_path.read_text(encoding="utf-8")
    assert "kb/chunks/x.md" in body
