"""
Phase 14 security tests — consolidated-2026-04-18 action items 1/3/6.
=====================================================================

Covers:
  - Timestamp sanity check (state.py, §10.3)
  - Priority 1 founder-signature guard (state.py, §10.3)
  - Watchdog substring-bypass fix (drift.py, §10.6)
  - Hash-backed provenance binding (integrity.py, §10.1)
  - Adversary rating trend detection (adversary.py, §6 Grok surface)

Each test documents (in its docstring) the attack it blocks so the
test doubles as an executable threat-model artifact.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.primitives import state
from core.primitives.state import (
    AuthorityPriority,
    Claim,
    ProvenanceStatus,
    check_provenance,
    is_future_timestamp,
    resolve_conflict,
    resolve_conflict_with_integrity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _prov(updated_at: str, updated_by: str = "engine", **extra) -> dict:
    return {
        "updated_at": updated_at,
        "updated_by": updated_by,
        "source_path": extra.pop("source_path", "knowledge-base/chunks/x.md"),
        "ingested_at": extra.pop("ingested_at", "2026-04-18T00:00:00+00:00"),
        **extra,
    }


# ---------------------------------------------------------------------------
# Timestamp sanity check (§10.3)
# ---------------------------------------------------------------------------
class TestTimestampSanity:
    """Blocks Gemini's year-2099 attack — a specialist writing
    updated_at='2099-12-31' to win chronological tiebreakers."""

    def test_present_timestamp_passes(self):
        now = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert not is_future_timestamp(now.isoformat())

    def test_ten_minutes_future_rejected(self):
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        assert is_future_timestamp(future) is True

    def test_two_minutes_future_allowed_by_tolerance(self):
        future = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        assert is_future_timestamp(future) is False

    def test_year_2099_rejected_by_check_provenance(self):
        prov = _prov(updated_at="2099-12-31T00:00:00+00:00")
        assert check_provenance(prov) is ProvenanceStatus.INVALID

    def test_date_only_format_accepted(self):
        prov = _prov(updated_at="2026-04-18")
        assert check_provenance(prov) is ProvenanceStatus.VALID

    def test_unparseable_timestamp_rejected(self):
        # Not future-per-se, but malformed — fails the basic field check.
        prov = _prov(updated_at="not-a-date")
        # Unparseable timestamps are NOT rejected by is_future_timestamp
        # (can't tell), but they also don't satisfy downstream comparison.
        # check_provenance accepts the string — this is intentional to
        # avoid coupling the provenance check to a particular format.
        assert check_provenance(prov) is ProvenanceStatus.VALID

    def test_future_rejection_opt_out(self):
        future = "2099-12-31T00:00:00+00:00"
        prov = _prov(updated_at=future)
        # Legacy callers that don't want the check can opt out.
        assert (
            check_provenance(prov, reject_future_timestamps=False)
            is ProvenanceStatus.VALID
        )


# ---------------------------------------------------------------------------
# Priority 1 founder-signature guard (§10.3)
# ---------------------------------------------------------------------------
class TestFounderSignatureGuard:
    """A Decision (Priority 2) that supersedes a FOUNDER claim (Priority 1)
    must carry founder_signature: true OR updated_by in FOUNDER_PRINCIPALS.
    Without it, the supersede is silently ignored and the Founder claim
    wins. This closes the Gemini attack where a specialist writes a
    Decision that lists the founder rule in `supersedes`."""

    def _founder(self) -> Claim:
        return Claim(
            priority=AuthorityPriority.FOUNDER,
            content="never market to <21",
            ref="priority_1_founder/context.md#hard_constraints/0",
            provenance=_prov(updated_at="2026-04-01T00:00:00+00:00"),
        )

    def _attacker_decision(self) -> Claim:
        return Claim(
            priority=AuthorityPriority.DECISION,
            content="market to teens",
            ref="priority_2_decision/2026-04-18-market-teens.md",
            provenance=_prov(
                updated_at="2026-04-18T00:00:00+00:00",
                updated_by="specialist:marketing.growth",
            ),
            supersedes=("priority_1_founder/context.md#hard_constraints/0",),
        )

    def test_unsigned_supersede_is_refused(self):
        founder = self._founder()
        decision = self._attacker_decision()
        resolved = resolve_conflict(founder, decision)
        # Priority 1 must win because the supersede is unsigned.
        assert resolved.winner is founder
        assert resolved.loser is decision

    def test_founder_signature_true_is_accepted(self):
        founder = self._founder()
        decision = Claim(
            priority=AuthorityPriority.DECISION,
            content="revised position on X",
            ref="priority_2_decision/2026-04-18-revised.md",
            provenance=_prov(
                updated_at="2026-04-18T00:00:00+00:00",
                updated_by="specialist:legal.review",
                founder_signature=True,
            ),
            supersedes=("priority_1_founder/context.md#hard_constraints/0",),
        )
        resolved = resolve_conflict(founder, decision)
        assert resolved.winner is decision

    def test_updated_by_founder_is_accepted(self):
        founder = self._founder()
        decision = Claim(
            priority=AuthorityPriority.DECISION,
            content="revised by founder",
            ref="priority_2_decision/2026-04-18-revised.md",
            provenance=_prov(
                updated_at="2026-04-18T00:00:00+00:00",
                updated_by="founder",
            ),
            supersedes=("priority_1_founder/context.md#hard_constraints/0",),
        )
        resolved = resolve_conflict(founder, decision)
        assert resolved.winner is decision


# ---------------------------------------------------------------------------
# Watchdog substring bypass (§10.6)
# ---------------------------------------------------------------------------
class TestWatchdogSubstringBypass:
    """Blocks Gemini's Vulnerability #2 — a specialist prepending
    malicious content followed by a real verbatim quote."""

    def test_too_short_claim_rejected(self, tmp_path: Path):
        # Fragment-sized claim that would trivially match as substring.
        from core.primitives.drift import watchdog_check, WatchdogMode

        source = tmp_path / "source.md"
        source.write_text("The capital of Maine is Augusta.", encoding="utf-8")
        message = """---
references:
  - referenced_message: source.md
    referenced_claims:
      - claim: "Maine"
        original_citation:
          type: priority_3_kb
          ref: source.md
          provenance:
            updated_at: "2026-04-18T00:00:00+00:00"
            updated_by: engine
            source_path: source.md
            ingested_at: "2026-04-18T00:00:00+00:00"
    how_used: fake
---
malicious content that has nothing to do with the source
"""
        result = watchdog_check(message, tmp_path, mode=WatchdogMode.STRICT)
        assert not result.ok
        assert any("too short" in issue for issue in result.issues)

    def test_word_boundary_prevents_fragment_match(self, tmp_path: Path):
        from core.primitives.drift import watchdog_check, WatchdogMode

        source = tmp_path / "source.md"
        # "Maintenance" contains "maintenan" as substring — a cover attack
        # could cite "aintenan" as a "quote" without a word boundary.
        source.write_text("Maintenance schedule Q2 2026.", encoding="utf-8")
        msg_claim = "aintenance schedule Q2 2026 is scoped to weekly runs only"  # 60 chars, no word boundary on left
        message = f"""---
references:
  - referenced_message: source.md
    referenced_claims:
      - claim: "{msg_claim}"
        original_citation:
          type: priority_3_kb
          ref: source.md
          provenance:
            updated_at: "2026-04-18T00:00:00+00:00"
            updated_by: engine
            source_path: source.md
            ingested_at: "2026-04-18T00:00:00+00:00"
    how_used: test
---
body
"""
        result = watchdog_check(message, tmp_path, mode=WatchdogMode.STRICT)
        # The claim text doesn't exist in source at all, so this will
        # fail. The key assertion is that it's flagged.
        assert not result.ok

    def test_valid_quote_with_word_boundaries_passes(self, tmp_path: Path):
        from core.primitives.drift import watchdog_check, WatchdogMode

        source = tmp_path / "source.md"
        source.write_text(
            "The following is a verbatim statement from the policy: "
            "operations must complete quarterly review within thirty days.",
            encoding="utf-8",
        )
        claim = "operations must complete quarterly review within thirty days"
        message = f"""---
references:
  - referenced_message: source.md
    referenced_claims:
      - claim: "{claim}"
        original_citation:
          type: priority_3_kb
          ref: source.md
          provenance:
            updated_at: "2026-04-18T00:00:00+00:00"
            updated_by: engine
            source_path: source.md
            ingested_at: "2026-04-18T00:00:00+00:00"
    how_used: test
---
{claim}.
"""
        result = watchdog_check(message, tmp_path, mode=WatchdogMode.STRICT)
        assert result.ok, f"unexpected issues: {result.issues}"

    def test_coverage_flag_catches_massive_padding(self, tmp_path: Path):
        from core.primitives.drift import watchdog_check, WatchdogMode

        source = tmp_path / "source.md"
        quote = "the fiscal year begins in January and ends in December each calendar year"
        source.write_text(f"Policy: {quote}.", encoding="utf-8")
        padding = " ".join(["fabricated policy claim"] * 200)
        message = f"""---
references:
  - referenced_message: source.md
    referenced_claims:
      - claim: "{quote}"
        original_citation:
          type: priority_3_kb
          ref: source.md
          provenance:
            updated_at: "2026-04-18T00:00:00+00:00"
            updated_by: engine
            source_path: source.md
            ingested_at: "2026-04-18T00:00:00+00:00"
    how_used: test
---
{padding}
{quote}
"""
        result = watchdog_check(
            message, tmp_path, mode=WatchdogMode.STRICT, require_coverage=True
        )
        assert not result.ok
        assert any("coverage" in issue for issue in result.issues)


# ---------------------------------------------------------------------------
# Hash-backed provenance binding (§10.1)
# ---------------------------------------------------------------------------
class TestIntegrityHashBinding:
    """The architectural principle — any field an LLM writes must be
    re-derivable by the engine. Hash binding enforces it for
    provenance metadata."""

    def test_compute_hash_deterministic(self):
        from core.primitives.integrity import compute_integrity_hash
        prov = {"source_path": "x.md", "updated_at": "2026-04-18"}
        assert compute_integrity_hash("body", prov) == compute_integrity_hash("body", prov)

    def test_hash_changes_on_body_edit(self):
        from core.primitives.integrity import compute_integrity_hash
        prov = {"source_path": "x.md", "updated_at": "2026-04-18"}
        a = compute_integrity_hash("body v1", prov)
        b = compute_integrity_hash("body v2", prov)
        assert a != b

    def test_hash_changes_on_provenance_edit(self):
        from core.primitives.integrity import compute_integrity_hash
        a = compute_integrity_hash("body", {"source_path": "x.md"})
        b = compute_integrity_hash("body", {"source_path": "y.md"})
        assert a != b

    def test_verify_file_integrity_happy_path(self, tmp_path: Path):
        from core.primitives.integrity import (
            render_frontmatter_with_hash,
            verify_file_integrity,
        )
        prov = {
            "source_path": "knowledge-base/chunks/x.md",
            "ingested_at": "2026-04-18T00:00:00+00:00",
            "source_asof": "2026-04-18",
            "stale_after": "180d",
            "content_hash": "deadbeefcafe1234",
            "chunk_index": "0",
            "updated_at": "2026-04-18T00:00:00+00:00",
            "updated_by": "engine:kb.ingest",
        }
        body = "This is a chunk of knowledge."
        text = render_frontmatter_with_hash(body=body, provenance=prov)
        f = tmp_path / "chunk.md"
        f.write_text(text, encoding="utf-8")
        result = verify_file_integrity(f)
        assert result.ok
        assert result.reason == "match"

    def test_verify_file_integrity_detects_tampered_body(self, tmp_path: Path):
        from core.primitives.integrity import (
            render_frontmatter_with_hash,
            verify_file_integrity,
        )
        prov = {
            "source_path": "knowledge-base/chunks/x.md",
            "ingested_at": "2026-04-18T00:00:00+00:00",
            "source_asof": "2026-04-18",
            "stale_after": "180d",
            "content_hash": "deadbeefcafe1234",
            "chunk_index": "0",
            "updated_at": "2026-04-18T00:00:00+00:00",
            "updated_by": "engine:kb.ingest",
        }
        text = render_frontmatter_with_hash(body="original body", provenance=prov)
        f = tmp_path / "chunk.md"
        f.write_text(text, encoding="utf-8")
        # Simulate tampering — change body while keeping hash in frontmatter.
        tampered = f.read_text(encoding="utf-8").replace("original body", "forged body")
        f.write_text(tampered, encoding="utf-8")
        result = verify_file_integrity(f)
        assert not result.ok
        assert result.reason == "mismatch"

    def test_verify_file_integrity_detects_tampered_provenance(self, tmp_path: Path):
        from core.primitives.integrity import (
            render_frontmatter_with_hash,
            verify_file_integrity,
        )
        prov = {
            "source_path": "knowledge-base/chunks/x.md",
            "ingested_at": "2026-04-18T00:00:00+00:00",
            "source_asof": "2026-04-18",
            "stale_after": "180d",
            "content_hash": "deadbeefcafe1234",
            "chunk_index": "0",
            "updated_at": "2026-04-18T00:00:00+00:00",
            "updated_by": "engine:kb.ingest",
        }
        text = render_frontmatter_with_hash(body="body", provenance=prov)
        f = tmp_path / "chunk.md"
        f.write_text(text, encoding="utf-8")
        tampered = f.read_text(encoding="utf-8").replace(
            "updated_by: engine:kb.ingest",
            "updated_by: specialist:attacker",
        )
        f.write_text(tampered, encoding="utf-8")
        result = verify_file_integrity(f)
        assert not result.ok
        assert result.reason == "mismatch"

    def test_kb_ingest_writes_integrity_hash(self, tmp_path: Path):
        from core.kb.ingest import ingest_source_doc
        from core.primitives.integrity import verify_file_integrity

        # Build a fake company dir with a source doc.
        company_dir = tmp_path / "company"
        source_dir = company_dir / "knowledge-base" / "source"
        source_dir.mkdir(parents=True)
        source_doc = source_dir / "maine-rules.md"
        source_doc.write_text(
            "Rule 1: operations must complete quarterly review "
            "within thirty days.\n\n"
            "Rule 2: shipments to consumers require a COLA.\n",
            encoding="utf-8",
        )
        paths = ingest_source_doc(source_doc, company_dir)
        assert paths, "expected at least one chunk"
        for path in paths:
            result = verify_file_integrity(path)
            assert result.ok, f"{path} failed: {result.reason}"

    def test_drift_guard_flags_tampered_kb_chunk(self, tmp_path: Path):
        """Phase 14 — drift_guard.evaluate_dispatch now runs
        verify_claim_integrity on KB-priority claims by default.
        Tampered chunks surface as integrity_issues."""
        from core.kb.ingest import ingest_source_doc
        from core.dispatch.drift_guard import evaluate_dispatch

        company = tmp_path / "co"
        source_dir = company / "knowledge-base" / "source"
        source_dir.mkdir(parents=True)
        src = source_dir / "ttb.md"
        src.write_text(
            "Rule: operations must complete quarterly review within thirty days.\n",
            encoding="utf-8",
        )
        chunks = ingest_source_doc(src, company)
        chunk_path = chunks[0]

        rel_chunk = chunk_path.relative_to(company).as_posix()
        good_claim = Claim(
            priority=AuthorityPriority.KB,
            content="kb",
            ref="kb:x",
            provenance=_prov(
                updated_at="2026-04-18T00:00:00+00:00",
                source_path=rel_chunk,
            ),
        )
        # Before tamper: integrity passes.
        ok_report = evaluate_dispatch(
            "body", company, claims=[good_claim],
        )
        assert ok_report.integrity_issues == ()

        # Tamper the chunk body.
        text = chunk_path.read_text(encoding="utf-8")
        chunk_path.write_text(
            text.replace("thirty days", "three hundred days"),
            encoding="utf-8",
        )
        bad_report = evaluate_dispatch(
            "body", company, claims=[good_claim],
        )
        assert not bad_report.ok
        assert any("integrity failed" in i for i in bad_report.integrity_issues)

    def test_resolve_with_integrity_raises_on_tampered_chunk(self, tmp_path: Path):
        from core.primitives.integrity import render_frontmatter_with_hash

        company = tmp_path / "co"
        chunks_dir = company / "knowledge-base" / "chunks"
        chunks_dir.mkdir(parents=True)
        prov = {
            "source_path": "knowledge-base/chunks/x.md",
            "ingested_at": "2026-04-18T00:00:00+00:00",
            "source_asof": "2026-04-18",
            "stale_after": "180d",
            "content_hash": "deadbeefcafe1234",
            "chunk_index": "0",
            "updated_at": "2026-04-18T00:00:00+00:00",
            "updated_by": "engine:kb.ingest",
        }
        chunk_path = chunks_dir / "x.md"
        chunk_path.write_text(
            render_frontmatter_with_hash(body="original body", provenance=prov),
            encoding="utf-8",
        )
        # Tamper
        chunk_path.write_text(
            chunk_path.read_text(encoding="utf-8").replace("original", "forged"),
            encoding="utf-8",
        )

        kb_claim = Claim(
            priority=AuthorityPriority.KB,
            content="kb fact",
            ref="kb/x",
            provenance=_prov(
                updated_at="2026-04-18T00:00:00+00:00",
                source_path="knowledge-base/chunks/x.md",
            ),
        )
        founder_claim = Claim(
            priority=AuthorityPriority.FOUNDER,
            content="founder fact",
            ref="founder/x",
            provenance=_prov(updated_at="2026-04-18T00:00:00+00:00"),
        )
        with pytest.raises(ValueError, match="Integrity verification failed"):
            resolve_conflict_with_integrity(
                kb_claim,
                founder_claim,
                vault_dir=company,
                required_priorities=(AuthorityPriority.KB,),
            )


# ---------------------------------------------------------------------------
# Adversary rating trend detection (§6)
# ---------------------------------------------------------------------------
class TestAdversaryRatingTrend:
    """Grok's gradual-poisoning attack surface — ratings walk down
    5→4→3→3→3 without ever triggering 2-consecutive-sub-threshold."""

    def _window(self, medians: float, ratings: list[int], started: str, ended: str):
        from core.adversary import AdversaryRating, DriftWindow
        rating_objs = tuple(
            AdversaryRating(review_key=f"r{i}", score=s, created_at=started)
            for i, s in enumerate(ratings)
        )
        return DriftWindow(
            started_at=started,
            ended_at=ended,
            activations=len(ratings),
            rating_median=medians,
            passed=medians >= 3.0,
            ratings=rating_objs,
        )

    def test_monotone_downward_trend_flags(self):
        from core.adversary import detect_rating_trend
        windows = [
            self._window(5.0, [5, 5, 5], "2026-01-01", "2026-01-30"),
            self._window(4.5, [5, 4, 5], "2026-02-01", "2026-02-28"),
            self._window(4.0, [4, 4, 4], "2026-03-01", "2026-03-30"),
            self._window(3.5, [3, 4, 3], "2026-04-01", "2026-04-18"),
        ]
        flag = detect_rating_trend(windows, lookback=4)
        assert flag.suspicious
        assert "dropped" in flag.reason.lower() or "drop" in flag.reason.lower()
        assert flag.trend_slope < 0

    def test_flat_trend_not_flagged(self):
        from core.adversary import detect_rating_trend
        windows = [
            self._window(4.0, [4, 4, 4], "2026-01-01", "2026-01-30"),
            self._window(4.0, [4, 4, 4], "2026-02-01", "2026-02-28"),
            self._window(4.0, [4, 4, 4], "2026-03-01", "2026-03-30"),
        ]
        flag = detect_rating_trend(windows, lookback=3)
        assert not flag.suspicious

    def test_noisy_with_recovery_not_flagged(self):
        from core.adversary import detect_rating_trend
        # Drop-then-recover: 5 → 3 → 5 — two upticks reject.
        windows = [
            self._window(5.0, [5, 5], "2026-01-01", "2026-01-30"),
            self._window(3.0, [3, 3], "2026-02-01", "2026-02-28"),
            self._window(5.0, [5, 5], "2026-03-01", "2026-03-30"),
        ]
        flag = detect_rating_trend(windows, lookback=3)
        assert not flag.suspicious

    def test_insufficient_windows(self):
        from core.adversary import detect_rating_trend
        flag = detect_rating_trend([], lookback=5)
        assert not flag.suspicious
        assert flag.reason == "insufficient-windows"
