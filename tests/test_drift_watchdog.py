"""Drift watchdog primitive (Phase 5.3 — §7.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.primitives.drift import (
    DriftAssessment,
    WatchdogMode,
    watchdog_check,
)


def _write_referenced_message(vault_dir: Path, rel_path: str, body: str) -> None:
    target = vault_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


VALID_REFERENCED_MSG = (
    "Body of the referenced message. "
    "Maine has zero documented alternating-proprietor host wineries "
    "per the TTB roster ingested 2026-04-17."
)


def _message_with_refs(referenced_path: str, claim: str) -> str:
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
Body.
"""


def test_watchdog_passes_when_references_resolve(tmp_path: Path) -> None:
    rel = "sessions/abc/marketing-turn-3.md"
    _write_referenced_message(tmp_path, rel, VALID_REFERENCED_MSG)
    claim = "Maine has zero documented alternating-proprietor host wineries"
    assessment = watchdog_check(_message_with_refs(rel, claim), tmp_path)
    assert assessment.ok is True
    assert assessment.issues == ()
    assert assessment.references_checked == 1


def test_watchdog_flags_missing_referenced_message(tmp_path: Path) -> None:
    msg = _message_with_refs(
        "sessions/missing/does-not-exist.md",
        "some claim",
    )
    assessment = watchdog_check(msg, tmp_path)
    assert assessment.ok is False
    assert any("not found" in issue for issue in assessment.issues)


def test_watchdog_flags_claim_not_in_referenced_body(tmp_path: Path) -> None:
    rel = "sessions/abc/marketing-turn-3.md"
    _write_referenced_message(tmp_path, rel, VALID_REFERENCED_MSG)
    msg = _message_with_refs(rel, "totally fabricated claim not in the body")
    assessment = watchdog_check(msg, tmp_path)
    assert assessment.ok is False
    assert any("claim not found" in issue for issue in assessment.issues)


def test_watchdog_blocks_path_traversal(tmp_path: Path) -> None:
    # An attacker-crafted referenced_message that tries to escape the vault.
    msg = _message_with_refs("../../../etc/passwd", "claim")
    assessment = watchdog_check(msg, tmp_path)
    assert assessment.ok is False
    assert any("outside vault_dir" in issue for issue in assessment.issues)


def test_watchdog_message_without_references_passes(tmp_path: Path) -> None:
    # Agent-internal output: no references block → nothing to check.
    assessment = watchdog_check("plain body, no frontmatter", tmp_path)
    assert assessment.ok is True
    assert assessment.references_checked == 0


def test_watchdog_strict_mode_blocks_on_shape_issue(tmp_path: Path) -> None:
    # Missing claims → shape invalid → strict mode blocks.
    msg = """---
references:
  - referenced_message: sessions/x/y.md
    referenced_claims: []
    how_used: x
---
Body.
"""
    assessment = watchdog_check(msg, tmp_path, mode=WatchdogMode.STRICT)
    assert assessment.ok is False
    assert any("no referenced_claims" in i for i in assessment.issues)


def test_watchdog_permissive_default(tmp_path: Path) -> None:
    assessment = watchdog_check("plain body", tmp_path)
    assert assessment.mode is WatchdogMode.PERMISSIVE


def test_watchdog_counts_references(tmp_path: Path) -> None:
    # Phase 14 — consolidated §10.6: claims must be >= MIN_CLAIM_LENGTH (40)
    # chars to defeat the short-fragment cover-attack vector. Rewrite the
    # body so both claims are real verbatim quotes of load-bearing length.
    claim_one = "claim one is a substantive statement of meaningful length"
    claim_two = "claim two is a separate substantive statement of meaningful length"
    body = f"multi-claim body: {claim_one} and {claim_two}."
    _write_referenced_message(tmp_path, "sessions/a/m1.md", body)
    _write_referenced_message(tmp_path, "sessions/a/m2.md", body)
    msg = f"""---
references:
  - referenced_message: sessions/a/m1.md
    referenced_claims:
      - claim: "{claim_one}"
        original_citation: {{type: priority_3_kb, ref: r, provenance: {{}}}}
    how_used: x
  - referenced_message: sessions/a/m2.md
    referenced_claims:
      - claim: "{claim_two}"
        original_citation: {{type: priority_3_kb, ref: r, provenance: {{}}}}
    how_used: y
---
Body.
"""
    assessment = watchdog_check(msg, tmp_path)
    assert assessment.references_checked == 2
    assert assessment.ok is True
