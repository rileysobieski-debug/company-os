"""Adversary review data model + render + persistence (Phase 12.1 — §0.5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.adversary import (
    ADVERSARY_REVIEWS_SUBDIR,
    ActivationReason,
    AdversaryReview,
    iter_reviews,
    load_review,
    render_review,
    review_path,
    write_review,
)


def _sample_review() -> AdversaryReview:
    return AdversaryReview(
        milestone="commit-inaugural-varietal",
        thesis="Coastal Maine vineyard economics support launching our first vintage in 2026.",
        activation_reason=ActivationReason.MILESTONE,
        created_at="2026-04-18T12:00:00+00:00",
        objections=(
            "Maine's growing degree days run cold even in warm summers.",
            "TTB alternating-proprietor path has no local precedent.",
        ),
        premortem_quote="Ran out of cash before first vintage; W-2 fell through.",
        citations=(
            "kb:wine-beverage/growing-season-analysis.md",
            "assumption:maine-gdds-trending-warmer",
        ),
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
def test_review_roundtrips_through_dict() -> None:
    original = _sample_review()
    restored = AdversaryReview.from_dict(original.to_dict())
    assert restored == original


def test_activation_reason_serialises_as_string() -> None:
    review = _sample_review()
    data = review.to_dict()
    assert data["activation_reason"] == "milestone"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_render_opens_with_milestone_heading() -> None:
    md = render_review(_sample_review())
    assert "# Adversary review — commit-inaugural-varietal" in md


def test_render_includes_activation_reason() -> None:
    md = render_review(_sample_review())
    assert "**Activation:** milestone" in md


def test_render_includes_thesis_blockquote() -> None:
    md = render_review(_sample_review())
    assert "> Coastal Maine vineyard economics" in md


def test_render_includes_premortem_when_present() -> None:
    md = render_review(_sample_review())
    assert "Pre-mortem context" in md
    assert "Ran out of cash" in md


def test_render_omits_premortem_section_when_absent() -> None:
    review = AdversaryReview(
        milestone="m", thesis="t",
        activation_reason=ActivationReason.MANUAL,
        created_at="2026-04-18T12:00:00+00:00",
    )
    md = render_review(review)
    assert "Pre-mortem" not in md


def test_render_lists_objections_numbered() -> None:
    md = render_review(_sample_review())
    assert "1. Maine's growing degree days" in md
    assert "2. TTB alternating-proprietor" in md


def test_render_shows_no_objections_when_empty() -> None:
    review = AdversaryReview(
        milestone="m", thesis="t",
        activation_reason=ActivationReason.MANUAL,
        created_at="2026-04-18T12:00:00+00:00",
    )
    md = render_review(review)
    assert "_(no objections recorded)_" in md


def test_render_includes_founder_override_when_present() -> None:
    review = AdversaryReview(
        milestone="m", thesis="t",
        activation_reason=ActivationReason.MILESTONE,
        created_at="2026-04-18T12:00:00+00:00",
        founder_override="Proceeding anyway — coastal Maine is non-negotiable.",
    )
    md = render_review(review)
    assert "Founder override" in md
    assert "non-negotiable" in md


def test_render_omits_override_when_empty() -> None:
    md = render_review(_sample_review())
    assert "Founder override" not in md


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------
def test_review_path_encodes_date_and_slug() -> None:
    review = _sample_review()
    p = review_path(Path("/co"), review)
    assert p.name == "2026-04-18-commit-inaugural-varietal.md"
    assert p.parent.as_posix().endswith(ADVERSARY_REVIEWS_SUBDIR)


def test_write_creates_markdown_and_json_sidecar(tmp_path: Path) -> None:
    review = _sample_review()
    path = write_review(tmp_path, review)
    assert path.exists()
    assert path.suffix == ".md"
    sidecar = path.with_suffix(".json")
    assert sidecar.exists()


def test_load_review_reads_json_sidecar(tmp_path: Path) -> None:
    review = _sample_review()
    md_path = write_review(tmp_path, review)
    loaded = load_review(md_path)
    assert loaded == review


def test_load_review_accepts_json_path_directly(tmp_path: Path) -> None:
    review = _sample_review()
    md_path = write_review(tmp_path, review)
    loaded = load_review(md_path.with_suffix(".json"))
    assert loaded == review


def test_iter_reviews_reads_all(tmp_path: Path) -> None:
    r1 = AdversaryReview(
        milestone="m1", thesis="t1",
        activation_reason=ActivationReason.MILESTONE,
        created_at="2026-04-18T12:00:00+00:00",
    )
    r2 = AdversaryReview(
        milestone="m2", thesis="t2",
        activation_reason=ActivationReason.MANUAL,
        created_at="2026-04-19T12:00:00+00:00",
    )
    write_review(tmp_path, r1)
    write_review(tmp_path, r2)
    loaded = iter_reviews(tmp_path)
    assert len(loaded) == 2
    milestones = {r.milestone for r in loaded}
    assert milestones == {"m1", "m2"}


def test_iter_empty_dir_returns_empty_list(tmp_path: Path) -> None:
    assert iter_reviews(tmp_path) == []


# ---------------------------------------------------------------------------
# Slug handling
# ---------------------------------------------------------------------------
def test_slugifies_milestone_with_spaces_and_punctuation(tmp_path: Path) -> None:
    review = AdversaryReview(
        milestone="Fund the launch / Q3 2026!",
        thesis="t",
        activation_reason=ActivationReason.MANUAL,
        created_at="2026-04-18T12:00:00+00:00",
    )
    path = write_review(tmp_path, review)
    assert path.name == "2026-04-18-fund-the-launch-q3-2026.md"
