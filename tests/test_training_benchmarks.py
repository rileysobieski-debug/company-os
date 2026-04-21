"""Benchmark authoring from training transcripts (Phase 10.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.training import (
    Benchmark,
    TrainingExample,
    TrainingSession,
    author_benchmarks,
    load_benchmarks,
    write_benchmarks,
)


def _session(*examples: TrainingExample) -> TrainingSession:
    return TrainingSession(
        specialist_id="copywriter",
        started_at="2026-04-18T12:00:00+00:00",
        ended_at="2026-04-18T12:30:00+00:00",
        examples=tuple(examples),
    )


# ---------------------------------------------------------------------------
# Kind assignment
# ---------------------------------------------------------------------------
def test_positive_rank_becomes_positive_benchmark() -> None:
    session = _session(
        TrainingExample("brief", "output", founder_rank=2),
    )
    bms = author_benchmarks(session)
    assert len(bms) == 1
    assert bms[0].kind == "positive"
    assert bms[0].rank == 2


def test_negative_rank_becomes_negative_benchmark() -> None:
    session = _session(
        TrainingExample("brief", "output", founder_rank=-1),
    )
    bms = author_benchmarks(session)
    assert bms[0].kind == "negative"
    assert bms[0].rank == -1


def test_zero_rank_is_skipped() -> None:
    session = _session(
        TrainingExample("brief-0", "out-0", founder_rank=0),
        TrainingExample("brief-pos", "out-pos", founder_rank=1),
    )
    bms = author_benchmarks(session)
    assert len(bms) == 1
    assert bms[0].input_brief == "brief-pos"


def test_notes_and_source_carried_through() -> None:
    session = _session(
        TrainingExample(
            "brief", "output", founder_rank=2, notes="Perfect voice."
        ),
    )
    bms = author_benchmarks(session, source_session="training-2026-04-18.md")
    assert bms[0].notes == "Perfect voice."
    assert bms[0].source_session == "training-2026-04-18.md"


def test_skill_id_defaults_to_specialist_id() -> None:
    session = _session(
        TrainingExample("brief", "output", founder_rank=2),
    )
    bms = author_benchmarks(session)
    assert bms[0].skill_id == "copywriter"


def test_skill_id_override() -> None:
    session = _session(
        TrainingExample("brief", "output", founder_rank=2),
    )
    bms = author_benchmarks(session, skill_id="web-researcher")
    assert bms[0].skill_id == "web-researcher"


def test_empty_session_yields_empty_benchmarks() -> None:
    session = _session()
    assert author_benchmarks(session) == []


# ---------------------------------------------------------------------------
# JSONL persistence
# ---------------------------------------------------------------------------
def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deep" / "bench.jsonl"
    bms = [Benchmark("copywriter", "positive", "brief", "out", rank=2)]
    n = write_benchmarks(bms, path)
    assert n == 1
    assert path.exists()


def test_write_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "bench.jsonl"
    bms = [
        Benchmark("copywriter", "positive", "b1", "o1", rank=2, notes="n1"),
        Benchmark("copywriter", "negative", "b2", "o2", rank=-1),
    ]
    write_benchmarks(bms, path, append=False)
    loaded = load_benchmarks(path)
    assert len(loaded) == 2
    assert loaded[0].input_brief == "b1"
    assert loaded[0].notes == "n1"
    assert loaded[1].kind == "negative"


def test_append_preserves_existing_records(tmp_path: Path) -> None:
    path = tmp_path / "bench.jsonl"
    write_benchmarks(
        [Benchmark("copywriter", "positive", "b1", "o1", rank=1)],
        path, append=False,
    )
    write_benchmarks(
        [Benchmark("copywriter", "positive", "b2", "o2", rank=2)],
        path, append=True,
    )
    loaded = load_benchmarks(path)
    assert len(loaded) == 2
    assert [b.input_brief for b in loaded] == ["b1", "b2"]


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_benchmarks(tmp_path / "nonexistent.jsonl") == []


def test_load_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "bench.jsonl"
    path.write_text(
        '{"skill_id": "copywriter", "kind": "positive", "input_brief": "b", '
        '"expected_output": "o", "rank": 1}\n'
        'not json at all\n'
        '{"malformed": true}\n'
        '\n'
        '{"skill_id": "copywriter", "kind": "negative", "input_brief": "b2", '
        '"expected_output": "o2", "rank": -1}\n',
        encoding="utf-8",
    )
    loaded = load_benchmarks(path)
    assert len(loaded) == 2
    assert loaded[0].rank == 1
    assert loaded[1].rank == -1


def test_write_truncates_when_append_false(tmp_path: Path) -> None:
    path = tmp_path / "bench.jsonl"
    write_benchmarks(
        [Benchmark("copywriter", "positive", "old", "o", rank=1)],
        path, append=False,
    )
    write_benchmarks(
        [Benchmark("copywriter", "positive", "new", "o", rank=2)],
        path, append=False,
    )
    loaded = load_benchmarks(path)
    assert len(loaded) == 1
    assert loaded[0].input_brief == "new"


# ---------------------------------------------------------------------------
# End-to-end: transcript → benchmarks → file → back
# ---------------------------------------------------------------------------
def test_full_flow_from_session_to_disk(tmp_path: Path) -> None:
    session = TrainingSession(
        specialist_id="copywriter",
        started_at="2026-04-18T12:00:00+00:00",
        ended_at="2026-04-18T12:30:00+00:00",
        examples=(
            TrainingExample("brief-exemplar", "out-exemplar", founder_rank=2),
            TrainingExample("brief-neutral", "out-neutral", founder_rank=0),
            TrainingExample("brief-anti", "out-anti", founder_rank=-2),
        ),
    )
    bms = author_benchmarks(session, source_session="sess1")
    path = tmp_path / "skills" / "benchmarks" / "copywriter.jsonl"
    write_benchmarks(bms, path, append=False)
    loaded = load_benchmarks(path)
    kinds = sorted(b.kind for b in loaded)
    assert kinds == ["negative", "positive"]
    assert all(b.source_session == "sess1" for b in loaded)
