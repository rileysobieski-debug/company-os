"""
Phase 14 ambient awareness tests — consolidated-2026-04-18 §10.2.
==================================================================

Exercises the three strengthened validations recommended by the
reviewers in §7a:

  (a) Quality gate — reject hyper-generic observations at write-time.
  (b) Evidence verification — ID exists AND observer-authored AND
      mentions subject.
  (c) Relevance filter — TF-IDF beats keyword-only matching.

Plus lifecycle (TTL, confirmation extension) and preamble rendering.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.primitives.awareness import (
    AwarenessNote,
    EvidenceCheck,
    MAX_TTL_DAYS,
    ValidationResult,
    build_note,
    extend,
    iter_active_notes,
    iter_notes,
    preamble_for_dispatch,
    relevant_notes,
    render_preamble,
    tick,
    validate_observation,
    verify_evidence,
    write_note,
)


# ---------------------------------------------------------------------------
# Quality gate (§7a.b)
# ---------------------------------------------------------------------------
class TestObservationQualityGate:
    """Reject the "administrative garbage" noise pattern both
    reviewers flagged ("Agent B completed task on time")."""

    def test_hyper_generic_agent_did_task_rejected(self):
        result = validate_observation("Agent B completed task on time.")
        assert not result.ok
        assert result.reason == "hyper-generic"

    def test_single_word_ok_rejected(self):
        result = validate_observation("Noted.")
        assert not result.ok

    def test_no_action_verb_rejected(self):
        # 40-char string, concrete-looking, but no verb.
        text = "the 2026-04-18 quarterly review thing now"
        result = validate_observation(text)
        assert not result.ok
        assert result.reason == "no-action-verb"

    def test_no_concrete_signal_rejected(self):
        text = "I noticed the process behaves somewhat erratically lately."
        result = validate_observation(text)
        assert not result.ok
        assert result.reason == "no-concrete-signal"

    def test_well_formed_observation_accepted(self):
        text = (
            "Observed evaluator verdict FAIL on 3 of 10 marketing dispatches "
            "at 2026-04-15; autoresearch budget exceeded $50."
        )
        result = validate_observation(text)
        assert result.ok, result.details

    def test_too_short_rejected(self):
        result = validate_observation("observed x.")
        assert not result.ok

    def test_too_long_rejected(self):
        text = "observed " + ("x " * 500) + "2026-04-18"
        result = validate_observation(text)
        assert not result.ok
        assert result.reason == "too-long"


# ---------------------------------------------------------------------------
# Evidence verification (§7a.c)
# ---------------------------------------------------------------------------
class TestEvidenceVerification:
    """An evidence ref must (1) resolve inside vault, (2) be fresh,
    (3) mention the observer, (4) mention the subject."""

    def _write(self, p: Path, body: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def test_missing_file_rejected(self, tmp_path: Path):
        r = verify_evidence("nonexistent.md", "agent-a", "marketing", tmp_path)
        assert not r.ok
        assert r.reason == "missing-file"

    def test_path_traversal_rejected(self, tmp_path: Path):
        r = verify_evidence("../outside.md", "agent-a", "x", tmp_path)
        assert not r.ok
        assert r.reason == "path-escapes-vault"

    def test_observer_not_mentioned_rejected(self, tmp_path: Path):
        self._write(tmp_path / "sessions" / "x.md", "marketing review details")
        r = verify_evidence("sessions/x.md", "agent-a", "marketing", tmp_path)
        assert not r.ok
        assert r.reason == "observer-not-mentioned"

    def test_subject_not_mentioned_rejected(self, tmp_path: Path):
        self._write(
            tmp_path / "sessions" / "x.md",
            "agent-a wrote something completely unrelated here",
        )
        r = verify_evidence("sessions/x.md", "agent-a", "marketing", tmp_path)
        assert not r.ok
        assert r.reason == "subject-not-mentioned"

    def test_happy_path(self, tmp_path: Path):
        self._write(
            tmp_path / "sessions" / "x.md",
            "agent-a reviewed marketing dispatch results",
        )
        r = verify_evidence("sessions/x.md", "agent-a", "marketing", tmp_path)
        assert r.ok

    def test_stale_mtime_rejected(self, tmp_path: Path):
        import os
        p = tmp_path / "sessions" / "old.md"
        self._write(p, "agent-a mentions marketing")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=45)).timestamp()
        os.utime(p, (old_ts, old_ts))
        r = verify_evidence("sessions/old.md", "agent-a", "marketing", tmp_path)
        assert not r.ok
        assert "stale" in r.reason


# ---------------------------------------------------------------------------
# Write path: validation gate applied end-to-end
# ---------------------------------------------------------------------------
class TestWriteNote:
    def test_rejects_hypergeneric_at_write(self, tmp_path: Path):
        note = build_note(
            observer="manager-marketing",
            subject="dispatch throughput",
            observation="Agent B completed task on time.",
            evidence_refs=("sessions/x.md",),
        )
        with pytest.raises(ValueError, match="observation rejected"):
            write_note(note, tmp_path)

    def test_rejects_without_evidence(self, tmp_path: Path):
        note = build_note(
            observer="manager-marketing",
            subject="evaluator pattern",
            observation=(
                "Observed evaluator verdict FAIL on 3 of 10 marketing "
                "dispatches at 2026-04-15; autoresearch budget exceeded $50."
            ),
            evidence_refs=(),
        )
        with pytest.raises(ValueError, match="evidence-required"):
            write_note(note, tmp_path)

    def test_persists_valid_note(self, tmp_path: Path):
        session = tmp_path / "sessions" / "2026-04-18" / "run.md"
        session.parent.mkdir(parents=True)
        session.write_text(
            "manager-marketing ran dispatches; evaluator pattern observed",
            encoding="utf-8",
        )
        note = build_note(
            observer="manager-marketing",
            subject="evaluator pattern",
            observation=(
                "Observed evaluator verdict FAIL on 3 of 10 marketing "
                "dispatches at 2026-04-15; autoresearch budget exceeded $50."
            ),
            evidence_refs=("sessions/2026-04-18/run.md",),
        )
        written = write_note(note, tmp_path)
        assert written.id == note.id
        loaded = list(iter_notes(tmp_path))
        assert len(loaded) == 1
        assert loaded[0].observation == note.observation


# ---------------------------------------------------------------------------
# Lifecycle: tick + extend
# ---------------------------------------------------------------------------
class TestLifecycle:
    def _write_valid(self, tmp_path: Path, observer: str = "manager-mkt", subject: str = "throughput") -> AwarenessNote:
        session = tmp_path / "sessions" / "2026-04-18" / "run.md"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text(
            f"{observer} observed {subject} behavior during marketing run",
            encoding="utf-8",
        )
        note = build_note(
            observer=observer,
            subject=subject,
            observation=(
                f"Observed {subject} at 15 dispatches per hour on 2026-04-18, "
                "down 40% from baseline in sessions/x.md."
            ),
            evidence_refs=(f"sessions/2026-04-18/run.md",),
        )
        return write_note(note, tmp_path)

    def test_tick_expires_stale_notes(self, tmp_path: Path):
        note = self._write_valid(tmp_path)
        future = datetime.now(timezone.utc) + timedelta(days=30)
        expired = tick(tmp_path, now=future)
        assert expired == 1
        active = list(iter_active_notes(tmp_path, now=future))
        assert active == []

    def test_extend_pushes_expiry_forward(self, tmp_path: Path):
        note = self._write_valid(tmp_path)
        updated = extend(note.id, confirmer="manager-finance", vault_dir=tmp_path)
        assert updated is not None
        assert updated.confirmation_count == 1
        assert updated.expires_at > note.expires_at

    def test_extend_refuses_self_confirmation(self, tmp_path: Path):
        note = self._write_valid(tmp_path)
        updated = extend(note.id, confirmer=note.observer, vault_dir=tmp_path)
        assert updated is None

    def test_extend_caps_at_max_ttl(self, tmp_path: Path):
        note = self._write_valid(tmp_path)
        # Extend many times — expiry should not exceed created_at + MAX_TTL_DAYS
        for _ in range(20):
            extend(note.id, confirmer=f"manager-{_}", vault_dir=tmp_path)
        from core.primitives.awareness import _parse_iso
        active = list(iter_notes(tmp_path))
        refreshed = [n for n in active if n.id == note.id][0]
        created = _parse_iso(refreshed.created_at)
        expires = _parse_iso(refreshed.expires_at)
        assert (expires - created) <= timedelta(days=MAX_TTL_DAYS)


# ---------------------------------------------------------------------------
# Relevance scoring (§7a.a)
# ---------------------------------------------------------------------------
class TestRelevance:
    def _note(self, subject: str, observation: str, *, obs: str = "a") -> AwarenessNote:
        return build_note(
            observer=obs,
            subject=subject,
            observation=observation,
            evidence_refs=("sessions/x.md",),
        )

    def test_prefers_subject_match(self):
        notes = [
            self._note("finance reconciliation", "Observed reconciliation drift at $200 on 2026-04-18."),
            self._note("marketing evaluator pattern", "Observed evaluator FAIL on 3 of 10 dispatches at 2026-04-15."),
            self._note("product-design palette", "Observed palette mismatch on 2 drafts at 2026-04-16."),
        ]
        picks = relevant_notes("marketing campaign planning dispatch", notes, k=2)
        assert picks
        assert picks[0].subject == "marketing evaluator pattern"

    def test_no_match_returns_empty(self):
        notes = [
            self._note("finance reconciliation", "Observed reconciliation drift at $200 on 2026-04-18."),
        ]
        picks = relevant_notes("unrelated brand voice palette question", notes, k=2, min_score=1.0)
        assert picks == []

    def test_empty_query_returns_empty(self):
        notes = [
            self._note("finance reconciliation", "Observed reconciliation drift at $200 on 2026-04-18."),
        ]
        assert relevant_notes("", notes) == []


# ---------------------------------------------------------------------------
# Preamble rendering + end-to-end helper
# ---------------------------------------------------------------------------
class TestPreamble:
    def test_empty_notes_empty_preamble(self):
        assert render_preamble([]) == ""

    def test_rendered_contains_observer_and_subject(self):
        note = build_note(
            observer="manager-mkt",
            subject="evaluator pattern",
            observation="Observed 3 FAIL verdicts at 2026-04-15.",
            evidence_refs=("sessions/x.md",),
        )
        out = render_preamble([note])
        assert "manager-mkt" in out
        assert "evaluator pattern" in out
        assert "Ambient notes" in out

    def test_preamble_for_dispatch_no_log(self, tmp_path: Path):
        assert preamble_for_dispatch("anything", tmp_path) == ""
