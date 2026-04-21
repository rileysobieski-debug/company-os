"""Tests for CompanyConfig + department/specialist loaders.

These run against the real Old Press folder on disk. They do NOT make LLM calls.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


REQUIRED_CONFIG_KEYS = [
    "company_id",
    "company_name",
    "active_departments",
    "settled_convictions",
    "hard_constraints",
    "priorities",
]


# ---------------------------------------------------------------------------
# CompanyConfig
# ---------------------------------------------------------------------------
def test_old_press_config_present(old_press_dir: Path) -> None:
    cfg = old_press_dir / "config.json"
    assert cfg.exists(), f"Missing config.json at {cfg}"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    for key in REQUIRED_CONFIG_KEYS:
        assert key in data, f"config.json missing required key: {key}"


def test_old_press_context_present(old_press_dir: Path) -> None:
    ctx = old_press_dir / "context.md"
    assert ctx.exists(), f"Missing context.md at {ctx}"
    text = ctx.read_text(encoding="utf-8")
    assert len(text.strip()) > 100, "context.md is suspiciously short"


def test_company_loads(company) -> None:
    assert company.name == "Old Press Wine Co. LLC"
    assert company.company_id == "old-press"
    # Wine/beverage is kept as the PRIMARY expertise anchor and
    # regulatory vertical. Managers are expected to ALSO develop a
    # secondary area of expertise determined per-department via the
    # Scope Calibration interview (see config `industry_note`).
    assert company.industry == "wine / beverage alcohol"


def test_active_departments_have_9(company) -> None:
    assert len(company.active_departments) == 9, (
        f"Expected 9 active depts, got {len(company.active_departments)}: "
        f"{company.active_departments}"
    )


def test_settled_convictions_block_includes_riley_directive(company) -> None:
    block = company.settled_convictions_block()
    assert "SETTLED CONVICTIONS" in block
    assert "DO NOT RE-EXAMINE" in block
    assert "Riley" in block


def test_hard_constraints_block_lists_all_rules(company) -> None:
    block = company.hard_constraints_block()
    assert "HARD CONSTRAINTS" in block
    # Spot-check a known constraint
    assert "TTB" in block or "labeling" in block.lower()


def test_priorities_block_present(company) -> None:
    block = company.priorities_block()
    assert "CURRENT PRIORITIES" in block
    assert len(block.splitlines()) > 1


def test_load_company_missing_dir_raises(tmp_path: Path) -> None:
    from core.company import load_company
    fake = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        load_company(fake)


def test_load_company_missing_config_raises(tmp_path: Path) -> None:
    from core.company import load_company
    (tmp_path / "context.md").write_text("hi", encoding="utf-8")
    with pytest.raises(FileNotFoundError) as ei:
        load_company(tmp_path)
    assert "config.json" in str(ei.value)


# ---------------------------------------------------------------------------
# Department + specialist loader
# ---------------------------------------------------------------------------
def test_departments_load_at_least_one(departments) -> None:
    assert len(departments) >= 1, "No departments discovered"


def test_every_active_dept_loaded(company, departments) -> None:
    loaded_names = {d.name for d in departments}
    for dept_name in company.active_departments:
        # department.md must exist for the dept to load
        dept_md = company.company_dir / dept_name / "department.md"
        if dept_md.exists():
            assert dept_name in loaded_names, (
                f"Dept '{dept_name}' has department.md but did not load"
            )


def test_each_dept_has_display_name_and_dir(departments) -> None:
    for dept in departments:
        assert dept.display_name, f"Dept {dept.name} missing display_name"
        assert dept.dept_dir.exists(), f"Dept dir missing: {dept.dept_dir}"


def test_each_dept_has_specialists(departments) -> None:
    """Every loaded dept should have at least one specialist (otherwise the
    manager has nothing to dispatch to)."""
    empty = [d.name for d in departments if not d.specialists]
    assert not empty, f"Departments with zero specialists: {empty}"


def test_specialist_frontmatter_minimum(departments) -> None:
    """Every specialist should have name, model, and a non-empty prompt body."""
    bad: list[str] = []
    for dept in departments:
        for s in dept.specialists:
            if not s.name:
                bad.append(f"{dept.name}/{s.specialist_dir.name}: missing name")
            if not s.model:
                bad.append(f"{dept.name}/{s.name}: missing model")
            if not s.prompt_body or len(s.prompt_body) < 20:
                bad.append(f"{dept.name}/{s.name}: prompt_body too short")
    assert not bad, "Specialist frontmatter problems:\n" + "\n".join(bad)


def test_no_duplicate_specialist_names_within_dept(departments) -> None:
    for dept in departments:
        names = [s.name for s in dept.specialists]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"Duplicate specialist names in {dept.name}: {dupes}"


def test_frontmatter_parser_handles_inline_lists() -> None:
    from core.managers.loader import _parse_frontmatter
    text = "---\nname: foo\ntools: [Read, Grep, Edit]\nmodel: claude-haiku-4-5\n---\nbody here"
    fm, body = _parse_frontmatter(text)
    assert fm["name"] == "foo"
    assert fm["tools"] == ["Read", "Grep", "Edit"]
    assert body.strip() == "body here"


def test_frontmatter_parser_handles_block_lists() -> None:
    from core.managers.loader import _parse_frontmatter
    text = "---\nname: foo\ntools:\n  - Read\n  - Grep\n---\nbody"
    fm, body = _parse_frontmatter(text)
    assert fm["tools"] == ["Read", "Grep"]


def test_frontmatter_parser_no_frontmatter_returns_full_text() -> None:
    from core.managers.loader import _parse_frontmatter
    fm, body = _parse_frontmatter("just plain markdown\n\n# header")
    assert fm == {}
    assert "plain markdown" in body
