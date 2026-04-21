"""Drift guard composition (Phase 7.4)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.dispatch.drift_guard import (
    DriftGuardReport,
    evaluate_dispatch,
)
from core.primitives.drift import WatchdogMode
from core.primitives.state import AuthorityPriority, Claim
from core.primitives.turn_cap import TurnCapLedger, TurnCapStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
VALID_REFERENCED_MSG = (
    "Body of the referenced message. "
    "Maine has zero documented alternating-proprietor host wineries "
    "per the TTB roster ingested 2026-04-17."
)

VALID_CLAIM = "Maine has zero documented alternating-proprietor host wineries"


def _write_ref(vault_dir: Path, rel_path: str, body: str = VALID_REFERENCED_MSG) -> str:
    target = vault_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return rel_path


def _message(referenced_path: str, claim: str = VALID_CLAIM) -> str:
    return f"""---
agent: marketing-manager
references_another_agent: true
references:
  - referenced_message: {referenced_path}
    referenced_claims:
      - claim: "{claim}"
        original_citation:
          type: priority_3_kb
          ref: knowledge-base/chunks/x.md#c0
          provenance:
            updated_at: 2026-04-17T00:00:00+00:00
            updated_by: kb.ingest
            source_path: knowledge-base/source/x.md
            ingested_at: 2026-04-17T00:00:00+00:00
    how_used: as input
---
Body of the new message.
"""


def _valid_provenance() -> dict:
    return {
        "updated_at": "2026-04-17T00:00:00+00:00",
        "updated_by": "test",
        "source_path": "sessions/x.md",
        "ingested_at": "2026-04-17T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_evaluate_dispatch_all_clean_returns_ok(tmp_path: Path) -> None:
    rel = _write_ref(tmp_path, "sessions/a/m1.md")
    report = evaluate_dispatch(_message(rel), tmp_path)
    assert isinstance(report, DriftGuardReport)
    assert report.ok is True
    assert report.watchdog.ok is True
    assert report.issues == ()


def test_evaluate_dispatch_message_with_no_references(tmp_path: Path) -> None:
    """A raw message with no `references:` block still runs — watchdog
    reports zero refs checked, the rest of the bundle is empty, verdict ok."""
    report = evaluate_dispatch("plain body, no frontmatter", tmp_path)
    assert report.ok is True
    assert report.watchdog.references_checked == 0


# ---------------------------------------------------------------------------
# Watchdog failures
# ---------------------------------------------------------------------------
def test_evaluate_dispatch_flags_missing_reference(tmp_path: Path) -> None:
    report = evaluate_dispatch(_message("sessions/x/missing.md"), tmp_path)
    assert report.ok is False
    assert any("not found" in i for i in report.watchdog.issues)
    assert any("not found" in i for i in report.issues)


def test_evaluate_dispatch_flags_claim_not_in_body(tmp_path: Path) -> None:
    rel = _write_ref(tmp_path, "sessions/a/m2.md")
    msg = _message(rel, claim="This text is not in the referenced message")
    report = evaluate_dispatch(msg, tmp_path)
    assert report.ok is False
    assert any("claim not found" in i for i in report.watchdog.issues)


# ---------------------------------------------------------------------------
# Turn cap integration
# ---------------------------------------------------------------------------
def test_evaluate_dispatch_turn_cap_ok_when_under_limit(tmp_path: Path) -> None:
    rel = _write_ref(tmp_path, "sessions/a/m3.md")
    ledger = TurnCapLedger(cap=3)
    ledger.record_turn("cap-a")
    report = evaluate_dispatch(
        _message(rel), tmp_path,
        turn_ledger=ledger, capability="cap-a",
    )
    assert report.ok is True
    assert report.turn_cap is not None
    assert report.turn_cap.status is TurnCapStatus.OK
    assert report.turn_cap.turns_used == 1


def test_evaluate_dispatch_turn_cap_escalate_fails_report(tmp_path: Path) -> None:
    rel = _write_ref(tmp_path, "sessions/a/m4.md")
    ledger = TurnCapLedger(cap=2)
    ledger.record_turn("cap-a")
    ledger.record_turn("cap-a")
    report = evaluate_dispatch(
        _message(rel), tmp_path,
        turn_ledger=ledger, capability="cap-a",
    )
    assert report.ok is False
    assert report.turn_cap.status is TurnCapStatus.ESCALATE
    assert any("turn_cap escalate" in i for i in report.issues)


def test_evaluate_dispatch_ignores_turn_cap_when_not_supplied(tmp_path: Path) -> None:
    rel = _write_ref(tmp_path, "sessions/a/m5.md")
    report = evaluate_dispatch(_message(rel), tmp_path)
    assert report.turn_cap is None
    assert report.ok is True


# ---------------------------------------------------------------------------
# Provenance check
# ---------------------------------------------------------------------------
def test_evaluate_dispatch_provenance_clean(tmp_path: Path) -> None:
    rel = _write_ref(tmp_path, "sessions/a/m6.md")
    good_claim = Claim(
        priority=AuthorityPriority.KB,
        content="x", ref="kb:some-chunk",
        provenance=_valid_provenance(),
    )
    # Phase 14 — disable integrity check for this legacy fixture; the
    # synthetic claim doesn't point to a hash-stamped chunk on disk.
    report = evaluate_dispatch(
        _message(rel), tmp_path, claims=[good_claim],
        integrity_required_priorities=(),
    )
    assert report.ok is True
    assert report.provenance_issues == ()


def test_evaluate_dispatch_flags_missing_provenance_fields(tmp_path: Path) -> None:
    rel = _write_ref(tmp_path, "sessions/a/m7.md")
    # Pass a raw provenance dict missing `source_path`.
    bad = {
        "updated_at": "2026-04-17T00:00:00+00:00",
        "updated_by": "x",
        "ingested_at": "2026-04-17T00:00:00+00:00",
    }
    report = evaluate_dispatch(_message(rel), tmp_path, claims=[bad])
    assert report.ok is False
    assert any("provenance missing" in i for i in report.provenance_issues)


def test_evaluate_dispatch_accepts_mix_of_claims_and_dicts(tmp_path: Path) -> None:
    rel = _write_ref(tmp_path, "sessions/a/m8.md")
    claim = Claim(
        priority=AuthorityPriority.KB,
        content="x", ref="kb:a",
        provenance=_valid_provenance(),
    )
    dict_prov = _valid_provenance()
    report = evaluate_dispatch(
        _message(rel), tmp_path,
        claims=[claim, dict_prov],
        integrity_required_priorities=(),
    )
    assert report.ok is True
    assert report.provenance_issues == ()


# ---------------------------------------------------------------------------
# Combined failures
# ---------------------------------------------------------------------------
def test_evaluate_dispatch_multiple_failure_sources_surface_all(
    tmp_path: Path,
) -> None:
    """Missing ref + cap-hit + bad provenance — `issues` carries all three."""
    ledger = TurnCapLedger(cap=1)
    ledger.record_turn("cap-a")
    bad_prov = {"updated_at": "2026-04-17T00:00:00+00:00"}  # missing 3 fields
    report = evaluate_dispatch(
        _message("sessions/x/missing.md"),
        tmp_path,
        turn_ledger=ledger, capability="cap-a",
        claims=[bad_prov],
    )
    assert report.ok is False
    flat = report.issues
    assert any("not found" in i for i in flat)
    assert any("turn_cap escalate" in i for i in flat)
    assert any("provenance missing" in i for i in flat)


def test_evaluate_dispatch_summary_reports_counts(tmp_path: Path) -> None:
    rel = _write_ref(tmp_path, "sessions/a/m9.md")
    report = evaluate_dispatch(_message(rel), tmp_path)
    assert "watchdog" in report.summary
    assert "provenance" in report.summary
    assert "verdict: ok" in report.summary


# ---------------------------------------------------------------------------
# Watchdog mode propagation
# ---------------------------------------------------------------------------
def test_evaluate_dispatch_strict_mode_propagates(tmp_path: Path) -> None:
    """Strict mode is passed through to the watchdog."""
    report = evaluate_dispatch(
        _message("sessions/x/missing.md"),
        tmp_path,
        watchdog_mode=WatchdogMode.STRICT,
    )
    assert report.ok is False
    assert report.watchdog.mode is WatchdogMode.STRICT
