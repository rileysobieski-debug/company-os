"""Scope matrix data layer + per-dept validation (Phase 9.1 — §6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.primitives.scope_matrix import (
    DepartmentScope,
    OverlapReport,
    ScopeContradiction,
    ScopeMatrix,
    ScopeOverlap,
    ScopeValidation,
    find_overlaps,
    load_scope_matrix,
    parse_scope_matrix,
    validate_output_in_scope,
)


_YAML_FIXTURE = """
departments:
  marketing:
    owns:
      - brand positioning
      - audience building
      - launch messaging
    never:
      - regulatory filings
      - TTB compliance
  finance:
    owns:
      - budget
      - pricing
      - cash flow
    never:
      - brand voice
  operations:
    owns:
      - TTB compliance
      - supplier logistics
    never: []
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_parse_builds_matrix_with_expected_depts() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    assert matrix.names() == ("marketing", "finance", "operations")
    assert "marketing" in matrix


def test_parse_captures_owns_and_never() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    mkt = matrix["marketing"]
    assert isinstance(mkt, DepartmentScope)
    assert "brand positioning" in mkt.owns
    assert "TTB compliance" in mkt.never


def test_parse_handles_empty_never_list() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    ops = matrix["operations"]
    assert ops.never == ()


def test_parse_rejects_non_mapping_top_level() -> None:
    with pytest.raises(ValueError, match="departments"):
        parse_scope_matrix("departments: not_a_mapping")


def test_parse_rejects_non_string_entries() -> None:
    bad = """
departments:
  marketing:
    owns:
      - 123
"""
    with pytest.raises(ValueError, match="strings"):
        parse_scope_matrix(bad)


def test_load_from_file_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "scope_matrix.yaml"
    path.write_text(_YAML_FIXTURE, encoding="utf-8")
    matrix = load_scope_matrix(path)
    assert matrix.has("finance")
    assert "budget" in matrix["finance"].owns


def test_unknown_dept_lookup_raises_keyerror() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    with pytest.raises(KeyError):
        matrix["nonexistent"]


# ---------------------------------------------------------------------------
# Per-dept validation
# ---------------------------------------------------------------------------
def test_validate_owns_hit_passes() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    v = validate_output_in_scope(matrix, "marketing", "Draft launch messaging for Q3.")
    assert v.ok is True
    assert "launch messaging" in v.matched_owns
    assert v.matched_never == ()


def test_validate_never_hit_fails_even_when_owns_also_matches() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    # This topic hits both marketing's OWNS (brand) and NEVER (TTB).
    v = validate_output_in_scope(
        matrix, "marketing", "Handle TTB compliance and update brand positioning."
    )
    assert v.ok is False
    assert v.matched_never  # hard stop
    assert "NEVER" in v.reason


def test_validate_no_match_fails_with_clear_reason() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    v = validate_output_in_scope(matrix, "finance", "Write brand positioning copy.")
    assert v.ok is False
    assert v.matched_owns == ()
    assert v.matched_never == ()
    assert "not in finance's OWNS" in v.reason


def test_validate_unknown_dept_fails_with_explicit_reason() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    v = validate_output_in_scope(matrix, "sales", "Anything.")
    assert v.ok is False
    assert "not present" in v.reason


def test_validate_case_insensitive_matching() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    v = validate_output_in_scope(matrix, "marketing", "BRAND POSITIONING DRAFT")
    assert v.ok is True
    assert "brand positioning" in v.matched_owns


# ---------------------------------------------------------------------------
# Capability menu rendering
# ---------------------------------------------------------------------------
def test_render_menu_marks_active_and_dormant() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    menu = matrix.render_capability_menu(
        active_departments=["marketing", "operations"],
    )
    assert "## marketing [ACTIVE]" in menu
    assert "## operations [ACTIVE]" in menu
    assert "## finance [DORMANT]" in menu


def test_render_menu_lists_owns_for_dormant_dept() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    menu = matrix.render_capability_menu(active_departments=["marketing"])
    # finance is dormant but still shows owns so founder can decide.
    assert "budget" in menu
    assert "pricing" in menu


def test_render_menu_hides_never_block_when_empty() -> None:
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    menu = matrix.render_capability_menu()
    # operations has never: [] — the Never heading should not appear for it.
    # Find the operations section body.
    ops_section = menu.split("## operations")[1]
    ops_section_until_next = ops_section.split("##")[0]
    assert "**Never:**" not in ops_section_until_next


# ---------------------------------------------------------------------------
# Cross-dept overlap validator (Phase 9.2)
# ---------------------------------------------------------------------------
def test_find_overlaps_detects_shared_owns_topic() -> None:
    bad = """
departments:
  marketing:
    owns: [positioning, audience]
  community:
    owns: [positioning, pre-order list]
"""
    matrix = parse_scope_matrix(bad)
    report = find_overlaps(matrix)
    assert not report.ok
    assert len(report.overlaps) == 1
    o = report.overlaps[0]
    assert o.topic.lower() == "positioning"
    assert set(o.departments) == {"marketing", "community"}


def test_find_overlaps_case_insensitive() -> None:
    bad = """
departments:
  marketing:
    owns: [Brand Positioning]
  community:
    owns: [brand positioning]
"""
    matrix = parse_scope_matrix(bad)
    report = find_overlaps(matrix)
    assert len(report.overlaps) == 1


def test_find_overlaps_detects_self_contradiction() -> None:
    bad = """
departments:
  marketing:
    owns: [paid ads]
    never: [paid ads]
"""
    matrix = parse_scope_matrix(bad)
    report = find_overlaps(matrix)
    assert not report.ok
    assert len(report.contradictions) == 1
    c = report.contradictions[0]
    assert c.dept == "marketing"
    assert c.topic == "paid ads"


def test_cross_dept_never_of_other_dept_owns_is_not_contradiction() -> None:
    """Interpretation: NEVER is a self-disclaimer. It's expected (and
    correct) for dept B to NEVER a topic that dept A OWNS."""
    matrix = parse_scope_matrix("""
departments:
  marketing:
    owns: [TTB compliance]
  operations:
    never: [TTB compliance]
""")
    report = find_overlaps(matrix)
    # No overlaps (only one OWNS) and no contradictions (cross-dept is fine).
    assert report.ok
    assert report.overlaps == ()
    assert report.contradictions == ()


def test_find_overlaps_fixture_matrix_is_not_clean() -> None:
    """The fixture has no OWNS overlap and no self-contradiction —
    but marketing NEVERs 'TTB compliance' which operations OWNS. That
    is the intended shape and must NOT be flagged."""
    matrix = parse_scope_matrix(_YAML_FIXTURE)
    report = find_overlaps(matrix)
    assert report.ok


def test_find_overlaps_genuinely_clean_matrix_ok() -> None:
    clean = """
departments:
  marketing:
    owns: [brand positioning]
    never: []
  finance:
    owns: [pricing]
    never: [brand voice]
  operations:
    owns: [TTB compliance]
    never: []
"""
    matrix = parse_scope_matrix(clean)
    report = find_overlaps(matrix)
    assert report.ok
    assert report.overlaps == ()
    assert report.contradictions == ()


def test_overlap_messages_are_human_readable() -> None:
    bad = """
departments:
  marketing:
    owns: [launch messaging]
  editorial:
    owns: [launch messaging]
"""
    matrix = parse_scope_matrix(bad)
    report = find_overlaps(matrix)
    msgs = report.as_messages()
    assert any("OWNS overlap" in m for m in msgs)
    assert any("marketing" in m and "editorial" in m for m in msgs)


def test_self_contradiction_message_mentions_dept_and_topic() -> None:
    matrix = parse_scope_matrix("""
departments:
  marketing:
    owns: [paid ads]
    never: [paid ads]
""")
    report = find_overlaps(matrix)
    msgs = report.as_messages()
    joined = " | ".join(msgs)
    assert "marketing" in joined
    assert "paid ads" in joined
    assert "Self-contradiction" in joined
