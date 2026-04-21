"""Handshake runner + Priority 5 claim adapter (Phase 7.1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.dispatch.handshake_runner import (
    HANDSHAKES_SUBDIR,
    Handshake,
    handshake_to_claim,
    iter_session_handshakes,
    load_handshake,
    record_handshake,
    write_handshake,
)
from core.primitives.state import (
    AuthorityPriority,
    Claim,
    ProvenanceStatus,
    check_provenance,
    resolve_conflict,
)

ISO = "2026-04-17T10:00:00+00:00"


def _make(sender: str = "orchestrator", receiver: str = "marketing-manager") -> Handshake:
    return Handshake(
        session_id="abc-123",
        ts=ISO,
        sender=sender,
        receiver=receiver,
        intent="Draft positioning statement",
        deliverable="1-page positioning doc",
    )


# ---------------------------------------------------------------------------
# Write / Read
# ---------------------------------------------------------------------------
def test_write_then_load_roundtrip(tmp_path: Path) -> None:
    hs = _make()
    path = write_handshake(tmp_path, hs)
    assert path.exists()
    assert path.parent.name == "abc-123"
    assert path.suffix == ".json"

    loaded = load_handshake(path)
    assert loaded == hs


def test_write_places_file_in_session_dir(tmp_path: Path) -> None:
    hs = _make()
    path = write_handshake(tmp_path, hs)
    assert HANDSHAKES_SUBDIR in path.parts
    # session_id must appear as a directory segment
    assert "abc-123" in path.parts


def test_filename_contains_sender_and_receiver(tmp_path: Path) -> None:
    hs = _make(sender="orch", receiver="marketing-manager")
    path = write_handshake(tmp_path, hs)
    assert "orch-to-marketing-manager" in path.name


def test_load_handshake_returns_none_on_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"session_id": "x"}', encoding="utf-8")
    assert load_handshake(path) is None


def test_load_handshake_returns_none_on_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("not json at all", encoding="utf-8")
    assert load_handshake(path) is None


def test_iter_session_handshakes_chronological(tmp_path: Path) -> None:
    # Timestamps chosen so lexicographic = chronological.
    earlier = Handshake(
        session_id="s1", ts="2026-04-17T09:00:00+00:00",
        sender="o", receiver="m", intent="i1", deliverable="d1",
    )
    later = Handshake(
        session_id="s1", ts="2026-04-17T11:00:00+00:00",
        sender="o", receiver="m2", intent="i2", deliverable="d2",
    )
    # Write later first to prove order doesn't come from write order.
    write_handshake(tmp_path, later)
    write_handshake(tmp_path, earlier)
    hss = list(iter_session_handshakes(tmp_path, "s1"))
    assert [hs.ts for hs in hss] == [earlier.ts, later.ts]


def test_iter_session_handshakes_empty_when_no_session(tmp_path: Path) -> None:
    assert list(iter_session_handshakes(tmp_path, "missing")) == []


def test_unsafe_session_id_is_sanitized(tmp_path: Path) -> None:
    hs = Handshake(
        session_id="../../etc/passwd",
        ts=ISO, sender="o", receiver="m", intent="i", deliverable="d",
    )
    path = write_handshake(tmp_path, hs)
    # Resolves INSIDE the tmp_path (no escape).
    assert tmp_path.resolve() in path.resolve().parents


# ---------------------------------------------------------------------------
# record_handshake
# ---------------------------------------------------------------------------
def test_record_handshake_stamps_timestamp(tmp_path: Path) -> None:
    hs = record_handshake(
        tmp_path,
        session_id="s1", sender="o", receiver="m",
        intent="i", deliverable="d",
    )
    assert hs.ts  # non-empty
    # ts should be parseable
    from datetime import datetime
    datetime.fromisoformat(hs.ts.replace("Z", "+00:00"))


def test_record_handshake_respects_explicit_now(tmp_path: Path) -> None:
    hs = record_handshake(
        tmp_path,
        session_id="s1", sender="o", receiver="m",
        intent="i", deliverable="d",
        now=ISO,
    )
    assert hs.ts == ISO


def test_record_handshake_persists_references(tmp_path: Path) -> None:
    hs = record_handshake(
        tmp_path,
        session_id="s1", sender="o", receiver="m",
        intent="i", deliverable="d",
        references=("decisions/2026-04-10-x.md",),
        now=ISO,
    )
    loaded = load_handshake(
        tmp_path / HANDSHAKES_SUBDIR / "s1"
        / f"2026-04-17T10-00-00-00-00-o-to-m.json"
    )
    assert loaded is not None
    assert loaded.references == ("decisions/2026-04-10-x.md",)
    del hs  # silence unused


# ---------------------------------------------------------------------------
# Claim adapter
# ---------------------------------------------------------------------------
def test_handshake_to_claim_priority_5(tmp_path: Path) -> None:
    hs = _make()
    claim = handshake_to_claim(hs, company_dir=tmp_path)
    assert claim.priority is AuthorityPriority.HANDSHAKE
    assert claim.priority.value == 5
    assert claim.ref.startswith("priority_5_handshake:")
    assert check_provenance(claim.provenance) is ProvenanceStatus.VALID


def test_handshake_to_claim_without_company_dir() -> None:
    hs = _make()
    claim = handshake_to_claim(hs)
    assert "handshakes/abc-123/" in claim.ref


def test_handshake_to_claim_rejects_missing_ts() -> None:
    bad = Handshake(
        session_id="x", ts="", sender="o", receiver="m",
        intent="i", deliverable="d",
    )
    with pytest.raises(ValueError, match="missing ts"):
        handshake_to_claim(bad)


def test_handshake_loses_to_every_higher_priority() -> None:
    """Priority 5 handshakes lose to Priority 1-4 and win over 6-8."""
    hs = handshake_to_claim(_make())
    prov = {
        "updated_at": ISO, "updated_by": "x", "source_path": "s", "ingested_at": ISO,
    }
    for pri in (
        AuthorityPriority.FOUNDER,
        AuthorityPriority.DECISION,
        AuthorityPriority.KB,
        AuthorityPriority.BRAND,
    ):
        higher = Claim(priority=pri, content="x", ref=f"h-{pri.value}", provenance=prov)
        assert resolve_conflict(hs, higher).winner is higher

    for pri in (
        AuthorityPriority.MEMORY,
        AuthorityPriority.TASTE,
        AuthorityPriority.ASSUMPTION,
    ):
        lower = Claim(priority=pri, content="x", ref=f"l-{pri.value}", provenance=prov)
        assert resolve_conflict(hs, lower).winner is hs
