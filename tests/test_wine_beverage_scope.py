"""Canonical wine-beverage scope matrix (Phase 9.3 — §6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.onboarding.dept_selection import VERTICAL_DEPARTMENTS
from core.primitives.scope_matrix import (
    find_overlaps,
    load_scope_matrix,
    validate_output_in_scope,
)


@pytest.fixture(scope="module")
def matrix_path() -> Path:
    p = Path(__file__).resolve().parent.parent / "verticals" / "wine-beverage" / "scope_matrix.yaml"
    assert p.exists(), f"missing canonical matrix at {p}"
    return p


@pytest.fixture(scope="module")
def matrix(matrix_path: Path):
    return load_scope_matrix(matrix_path)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------
def test_matrix_has_all_nine_wine_beverage_depts(matrix) -> None:
    assert set(matrix.names()) == set(VERTICAL_DEPARTMENTS)


def test_matrix_depts_in_canonical_order(matrix) -> None:
    """The YAML preserves vertical-pack order for predictable menu output."""
    assert matrix.names() == VERTICAL_DEPARTMENTS


def test_every_dept_has_nonempty_owns(matrix) -> None:
    for name in matrix.names():
        assert matrix[name].owns, f"{name} has no OWNS entries"


# ---------------------------------------------------------------------------
# Coherence
# ---------------------------------------------------------------------------
def test_matrix_is_overlap_free(matrix) -> None:
    report = find_overlaps(matrix)
    assert report.ok, (
        "canonical wine-beverage matrix is incoherent — fix before shipping:\n"
        + "\n".join(report.as_messages())
    )


def test_matrix_no_self_contradictions(matrix) -> None:
    report = find_overlaps(matrix)
    assert report.contradictions == ()


# ---------------------------------------------------------------------------
# Validation against real-world Old Press topics
# ---------------------------------------------------------------------------
def test_positioning_routes_to_marketing(matrix) -> None:
    v = validate_output_in_scope(
        matrix, "marketing",
        "Draft brand positioning for Ironbound Island wine pack.",
    )
    assert v.ok


def test_ttb_compliance_blocked_from_marketing(matrix) -> None:
    v = validate_output_in_scope(
        matrix, "marketing",
        "File TTB compliance paperwork for Q3.",
    )
    assert not v.ok
    assert "NEVER" in v.reason


def test_ttb_compliance_routes_to_operations(matrix) -> None:
    v = validate_output_in_scope(
        matrix, "operations",
        "Prepare the TTB compliance filing.",
    )
    assert v.ok


def test_buyer_list_routes_to_community(matrix) -> None:
    v = validate_output_in_scope(
        matrix, "community",
        "Grow the pre-order list by 50 names.",
    )
    assert v.ok


# ---------------------------------------------------------------------------
# Capability menu integrates with VERTICAL_DEPARTMENTS
# ---------------------------------------------------------------------------
def test_capability_menu_shows_all_nine_depts(matrix) -> None:
    menu = matrix.render_capability_menu(
        active_departments=["marketing", "finance", "operations"],
    )
    for dept in VERTICAL_DEPARTMENTS:
        assert f"## {dept}" in menu


def test_capability_menu_distinguishes_active_and_dormant(matrix) -> None:
    menu = matrix.render_capability_menu(
        active_departments=["marketing", "finance", "operations"],
    )
    assert "## marketing [ACTIVE]" in menu
    assert "## finance [ACTIVE]" in menu
    assert "## operations [ACTIVE]" in menu
    # The remaining 6 are dormant.
    assert "## product-design [DORMANT]" in menu
    assert "## community [DORMANT]" in menu
    assert "## editorial [DORMANT]" in menu
    assert "## data [DORMANT]" in menu
    assert "## ai-workflow [DORMANT]" in menu
    assert "## ai-architecture [DORMANT]" in menu
