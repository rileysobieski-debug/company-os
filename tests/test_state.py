"""Tests for core/primitives/state.py.

Covers:
  * provenance validation (chunk 1a.4)
  * resolve_conflict() deterministic resolver (Phase 2.1)
  * state-authority.md generator (Phase 2.2)

No LLM calls. Pure-Python structural verification per the plan §16
principle that test suites verify wiring, not model behavior.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Provenance validation
# ---------------------------------------------------------------------------
_VALID_ENTRY = {
    "updated_at": "2026-04-16T14:00:00Z",
    "updated_by": "riley",
    "source_path": "Old Press Wine Company LLC/sales/notes.md",
    "ingested_at": "2026-04-16T13:58:00Z",
}


def test_entry_with_all_required_fields_returns_valid() -> None:
    from core.primitives.state import ProvenanceStatus, check_provenance
    assert check_provenance(_VALID_ENTRY) == ProvenanceStatus.VALID


def test_entry_missing_updated_at_returns_invalid() -> None:
    from core.primitives.state import ProvenanceStatus, check_provenance
    entry = {k: v for k, v in _VALID_ENTRY.items() if k != "updated_at"}
    assert check_provenance(entry) == ProvenanceStatus.INVALID


def test_entry_missing_updated_by_returns_invalid() -> None:
    from core.primitives.state import ProvenanceStatus, check_provenance
    entry = {**_VALID_ENTRY, "updated_by": ""}
    assert check_provenance(entry) == ProvenanceStatus.INVALID


# ---------------------------------------------------------------------------
# resolve_conflict() — Phase 2.1
# ---------------------------------------------------------------------------
def _prov(ts: str, by: str = "riley", src: str = "x.md", ingest: str | None = None) -> dict:
    return {
        "updated_at": ts,
        "updated_by": by,
        "source_path": src,
        "ingested_at": ingest or ts,
    }


def test_resolve_conflict_lower_priority_number_wins() -> None:
    from core.primitives.state import AuthorityPriority, Claim, resolve_conflict

    founder = Claim(
        priority=AuthorityPriority.FOUNDER,
        content="base is Maine",
        ref="context.md#base",
        provenance=_prov("2026-04-10T00:00:00Z", src="context.md"),
    )
    memory = Claim(
        priority=AuthorityPriority.MEMORY,
        content="base is Virginia",
        ref="departments/marketing/manager-memory.md#base",
        provenance=_prov("2026-04-15T00:00:00Z", src="departments/marketing/manager-memory.md"),
    )
    result = resolve_conflict(founder, memory)
    assert result.winner is founder
    assert result.loser is memory
    assert "priority_1_founder" in result.reason


def test_resolve_conflict_symmetric_regardless_of_argument_order() -> None:
    from core.primitives.state import AuthorityPriority, Claim, resolve_conflict

    founder = Claim(
        priority=AuthorityPriority.FOUNDER, content="X", ref="context.md",
        provenance=_prov("2026-04-10T00:00:00Z", src="context.md"),
    )
    kb = Claim(
        priority=AuthorityPriority.KB, content="Y", ref="knowledge-base/chunks/c1.md",
        provenance=_prov("2026-04-15T00:00:00Z", src="knowledge-base/chunks/c1.md"),
    )
    r1 = resolve_conflict(founder, kb)
    r2 = resolve_conflict(kb, founder)
    assert r1.winner is r2.winner is founder


def test_resolve_conflict_decision_supersedes_founder_when_explicit() -> None:
    from core.primitives.state import AuthorityPriority, Claim, resolve_conflict

    founder = Claim(
        priority=AuthorityPriority.FOUNDER, content="old stance",
        ref="priorities.md#P2",
        provenance=_prov("2026-04-01T00:00:00Z", src="priorities.md"),
    )
    decision = Claim(
        priority=AuthorityPriority.DECISION, content="new stance",
        ref="decisions/2026-04-10-new-priority.md",
        provenance=_prov("2026-04-10T00:00:00Z", src="decisions/2026-04-10-new-priority.md"),
        supersedes=("priorities.md#P2",),
    )
    result = resolve_conflict(founder, decision)
    assert result.winner is decision
    assert "supersedes" in result.reason


def test_resolve_conflict_decision_without_supersedes_loses_to_founder() -> None:
    from core.primitives.state import AuthorityPriority, Claim, resolve_conflict

    founder = Claim(
        priority=AuthorityPriority.FOUNDER, content="old stance",
        ref="priorities.md#P2",
        provenance=_prov("2026-04-01T00:00:00Z", src="priorities.md"),
    )
    decision = Claim(
        priority=AuthorityPriority.DECISION, content="new stance",
        ref="decisions/2026-04-10-other.md",
        provenance=_prov("2026-04-10T00:00:00Z", src="decisions/2026-04-10-other.md"),
        supersedes=(),
    )
    result = resolve_conflict(founder, decision)
    assert result.winner is founder


def test_resolve_conflict_older_decision_cannot_supersede_newer_founder() -> None:
    from core.primitives.state import AuthorityPriority, Claim, resolve_conflict

    founder = Claim(
        priority=AuthorityPriority.FOUNDER, content="latest",
        ref="priorities.md#P2",
        provenance=_prov("2026-04-15T00:00:00Z", src="priorities.md"),
    )
    stale_decision = Claim(
        priority=AuthorityPriority.DECISION, content="old override attempt",
        ref="decisions/2026-04-10-old.md",
        provenance=_prov("2026-04-10T00:00:00Z", src="decisions/2026-04-10-old.md"),
        supersedes=("priorities.md#P2",),
    )
    result = resolve_conflict(founder, stale_decision)
    assert result.winner is founder, "founder file refreshed after decision should win"


def test_resolve_conflict_same_priority_newer_wins() -> None:
    from core.primitives.state import AuthorityPriority, Claim, resolve_conflict

    older = Claim(
        priority=AuthorityPriority.KB, content="X",
        ref="knowledge-base/chunks/a.md",
        provenance=_prov("2026-04-10T00:00:00Z", src="knowledge-base/chunks/a.md"),
    )
    newer = Claim(
        priority=AuthorityPriority.KB, content="Y",
        ref="knowledge-base/chunks/b.md",
        provenance=_prov("2026-04-15T00:00:00Z", src="knowledge-base/chunks/b.md"),
    )
    result = resolve_conflict(older, newer)
    assert result.winner is newer
    assert "newer updated_at" in result.reason


def test_resolve_conflict_identical_priority_and_timestamp_breaks_lexicographically() -> None:
    from core.primitives.state import AuthorityPriority, Claim, resolve_conflict

    a = Claim(
        priority=AuthorityPriority.MEMORY, content="first",
        ref="departments/a/manager-memory.md",
        provenance=_prov("2026-04-15T00:00:00Z", src="departments/a/manager-memory.md"),
    )
    b = Claim(
        priority=AuthorityPriority.MEMORY, content="second",
        ref="departments/b/manager-memory.md",
        provenance=_prov("2026-04-15T00:00:00Z", src="departments/b/manager-memory.md"),
    )
    result = resolve_conflict(a, b)
    # a.ref < b.ref lexicographically
    assert result.winner is a
    assert "lexicographic" in result.reason


def test_resolve_conflict_rejects_invalid_provenance() -> None:
    from core.primitives.state import AuthorityPriority, Claim, resolve_conflict

    good = Claim(
        priority=AuthorityPriority.FOUNDER, content="x",
        ref="context.md",
        provenance=_prov("2026-04-10T00:00:00Z", src="context.md"),
    )
    bad = Claim(
        priority=AuthorityPriority.KB, content="y",
        ref="kb.md",
        provenance={"updated_at": "", "updated_by": "", "source_path": "", "ingested_at": ""},
    )
    with pytest.raises(ValueError, match="invalid provenance"):
        resolve_conflict(good, bad)


# ---------------------------------------------------------------------------
# state-authority.md generator — Phase 2.2
# ---------------------------------------------------------------------------
def test_render_state_authority_doc_contains_all_eight_priorities() -> None:
    from core.primitives.state import render_state_authority_doc

    doc = render_state_authority_doc("Old Press Wine Company LLC")
    for token in ("Priority", "| 1 |", "| 2 |", "| 3 |", "| 4 |",
                  "| 5 |", "| 6 |", "| 7 |", "| 8 |"):
        assert token in doc, f"missing {token!r} in rendered doc"


def test_render_state_authority_doc_mentions_provenance_fields() -> None:
    from core.primitives.state import render_state_authority_doc

    doc = render_state_authority_doc("Old Press Wine Company LLC")
    for field_name in ("updated_at", "updated_by", "source_path", "ingested_at"):
        assert field_name in doc, f"provenance field {field_name!r} missing from doc"


def test_render_state_authority_doc_interpolates_company_name() -> None:
    from core.primitives.state import render_state_authority_doc

    doc = render_state_authority_doc("Acme Coffee Co")
    assert "Acme Coffee Co" in doc


def test_write_state_authority_doc_creates_file(tmp_path: Path) -> None:
    from core.primitives.state import write_state_authority_doc

    target = write_state_authority_doc(tmp_path, "Test Co")
    assert target == tmp_path / "state-authority.md"
    assert target.exists()
    contents = target.read_text(encoding="utf-8")
    assert "Test Co" in contents
    assert "Priority" in contents


def test_write_state_authority_doc_is_idempotent(tmp_path: Path) -> None:
    from core.primitives.state import write_state_authority_doc

    first = write_state_authority_doc(tmp_path, "Test Co").read_text(encoding="utf-8")
    second = write_state_authority_doc(tmp_path, "Test Co").read_text(encoding="utf-8")
    assert first == second
