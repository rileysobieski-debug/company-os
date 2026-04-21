"""Notify log append behavior (Phase 3.2).

Verifies that `_append_log` writes entries in O(1) by appending to the file
rather than reloading + rewriting the full log on every call. Also verifies
the amortized trim kicks in when the file grows past the trigger threshold.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    """Redirect the notify log to a tmp path for the duration of the test."""
    from core import notify
    log_path = tmp_path / "notify-log.jsonl"
    state_dir = tmp_path
    monkeypatch.setattr(notify, "LOG_PATH", log_path)
    monkeypatch.setattr(notify, "STATE_DIR", state_dir)
    return log_path


def test_append_log_writes_single_line(tmp_log: Path) -> None:
    from core.notify import _append_log
    _append_log({"hash": "abc", "kind": "digest", "title": "x"})
    content = tmp_log.read_text(encoding="utf-8")
    # Exactly one newline-terminated line
    assert content.count("\n") == 1
    row = json.loads(content.strip())
    assert row["hash"] == "abc"


def test_append_log_appends_not_rewrites(tmp_log: Path) -> None:
    """Two appends must leave both entries on disk, in order."""
    from core.notify import _append_log
    _append_log({"hash": "a", "title": "first"})
    _append_log({"hash": "b", "title": "second"})
    lines = [l for l in tmp_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["title"] == "first"
    assert json.loads(lines[1])["title"] == "second"


def test_append_log_does_not_reload_on_each_write(tmp_log: Path, monkeypatch) -> None:
    """Core invariant of the O(N)→O(1) fix: _append_log must not call
    _load_log on every append. That was the quadratic-cost hot path."""
    from core import notify
    call_counter = {"n": 0}
    real_load = notify._load_log

    def counting_load():
        call_counter["n"] += 1
        return real_load()

    monkeypatch.setattr(notify, "_load_log", counting_load)
    for i in range(10):
        notify._append_log({"hash": f"h{i}", "title": f"t{i}"})
    # At most one trim (and therefore one _load_log call) may occur. For
    # typical small entries, zero is expected.
    assert call_counter["n"] <= 1, (
        f"_append_log called _load_log {call_counter['n']} times in 10 "
        "appends — the O(N) regression is back."
    )


def test_append_log_trims_when_oversize(tmp_log: Path, monkeypatch) -> None:
    """When the file grows past the byte trigger, the amortized trim
    reduces it back to LOG_RETAIN entries."""
    from core import notify
    # Shrink the trigger so we don't need thousands of writes to cross it.
    monkeypatch.setattr(notify, "LOG_RETAIN", 10)
    monkeypatch.setattr(notify, "_LOG_TRIM_BYTES", 500)  # ~very small
    for i in range(100):
        notify._append_log({"hash": f"h{i}", "title": f"t{i}", "x": "y" * 20})
    lines = [l for l in tmp_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    # After trim we should be at or below LOG_RETAIN (10), never way above.
    assert len(lines) <= 10, f"trim failed: {len(lines)} lines remain"
    # And the surviving entries must be the newest ones.
    titles = [json.loads(l)["title"] for l in lines]
    assert titles[-1] == "t99"
