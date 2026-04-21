"""Operator-initiated department creation (Phase 13.5 GUI-backing primitive)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from core.onboarding.dept_creation import (
    DeptCreationResult,
    add_department,
)


def _seed_company(tmp_path: Path, *, active_depts: list[str] | None = None) -> Path:
    """Create a minimal valid company dir: config.json + empty context.md."""
    company_dir = tmp_path / "Test Company LLC"
    company_dir.mkdir()
    config = {
        "company_id": "test-co",
        "company_name": "Test Company LLC",
        "industry": "test",
        "active_departments": active_depts or [],
    }
    (company_dir / "config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8",
    )
    (company_dir / "context.md").write_text("Context.", encoding="utf-8")
    return company_dir


# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------
def test_rejects_empty_slug(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    with pytest.raises(ValueError, match="non-empty"):
        add_department(company, "")


def test_rejects_uppercase_slug(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    with pytest.raises(ValueError, match="kebab-case"):
        add_department(company, "Legal")


def test_rejects_underscored_slug(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    with pytest.raises(ValueError, match="kebab-case"):
        add_department(company, "sub_ops")


def test_rejects_leading_digit(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    with pytest.raises(ValueError, match="kebab-case"):
        add_department(company, "1legal")


def test_rejects_reserved_names(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    for reserved in ("decisions", "board", "sessions", "knowledge-base"):
        with pytest.raises(ValueError, match="reserved"):
            add_department(company, reserved)


def test_accepts_standard_kebab_slug(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    result = add_department(company, "subscriber-ops")
    assert result.dept_dir == (company / "subscriber-ops")
    assert result.dept_dir.is_dir()


# ---------------------------------------------------------------------------
# Company-dir checks
# ---------------------------------------------------------------------------
def test_rejects_nonexistent_company_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="company dir"):
        add_department(tmp_path / "nonexistent", "legal")


def test_rejects_collision_with_existing_folder(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    (company / "legal").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        add_department(company, "legal")


# ---------------------------------------------------------------------------
# Files written
# ---------------------------------------------------------------------------
def test_creates_department_md_with_frontmatter(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    result = add_department(company, "legal", display_name="Legal")
    assert result.department_md.exists()
    content = result.department_md.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "display_name: Legal" in content


def test_creates_empty_manager_memory(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    result = add_department(company, "legal")
    assert result.manager_memory.exists()
    assert result.manager_memory.read_text(encoding="utf-8") == ""


def test_writes_custom_prompt_body(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    result = add_department(
        company, "legal",
        display_name="Legal",
        prompt_body="# Legal\n\nThis dept handles contracts and disputes.\n",
    )
    content = result.department_md.read_text(encoding="utf-8")
    assert "This dept handles contracts and disputes." in content


def test_fallback_prompt_body_when_empty(tmp_path: Path) -> None:
    """Empty prompt_body produces a placeholder scaffold the founder is
    expected to edit. Honest default — don't fabricate a prompt."""
    company = _seed_company(tmp_path)
    result = add_department(company, "legal")
    content = result.department_md.read_text(encoding="utf-8")
    assert "Legal" in content
    assert "(Replace with" in content


def test_display_name_defaults_from_slug(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    result = add_department(company, "subscriber-ops")
    content = result.department_md.read_text(encoding="utf-8")
    assert "display_name: Subscriber Ops" in content


def test_manager_model_in_frontmatter_when_supplied(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    result = add_department(
        company, "legal", manager_model="claude-sonnet-4-6",
    )
    assert "manager_model: claude-sonnet-4-6" in result.department_md.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# active_departments update
# ---------------------------------------------------------------------------
def test_appends_to_active_departments_by_default(tmp_path: Path) -> None:
    company = _seed_company(tmp_path, active_depts=["marketing", "finance"])
    result = add_department(company, "legal")
    assert result.config_updated is True
    config = json.loads((company / "config.json").read_text(encoding="utf-8"))
    assert config["active_departments"] == ["marketing", "finance", "legal"]


def test_idempotent_on_already_active(tmp_path: Path) -> None:
    company = _seed_company(tmp_path, active_depts=["legal"])
    # First, create a dept folder so the caller's call doesn't trip the
    # collision check — simulate a prior creation that left state.
    (company / "legal").mkdir()
    # Now use a DIFFERENT slug that happens to already be in the list
    # via a stale config to test the idempotent append behavior:
    # Actually the collision check fires first. Test the idempotent
    # behavior directly via _append helper instead.
    from core.onboarding.dept_creation import _append_to_active_departments
    changed = _append_to_active_departments(company / "config.json", "legal")
    assert changed is False


def test_activate_false_skips_config_update(tmp_path: Path) -> None:
    company = _seed_company(tmp_path, active_depts=["marketing"])
    result = add_department(company, "legal", activate=False)
    assert result.config_updated is False
    config = json.loads((company / "config.json").read_text(encoding="utf-8"))
    assert "legal" not in config["active_departments"]


def test_missing_config_json_is_not_fatal(tmp_path: Path) -> None:
    """A dept can be created in a bare folder without a config.json."""
    company = tmp_path / "Bare Co"
    company.mkdir()
    result = add_department(company, "legal")
    assert result.dept_dir.exists()
    assert result.config_updated is False


# ---------------------------------------------------------------------------
# Scope matrix update
# ---------------------------------------------------------------------------
def test_scope_matrix_untouched_when_owns_never_empty(tmp_path: Path) -> None:
    """Caller didn't specify scope → matrix is not updated (even if the
    matrix file exists)."""
    company = _seed_company(tmp_path)
    result = add_department(company, "legal")
    assert result.scope_matrix_updated is False


def test_scope_matrix_updated_when_owns_supplied(tmp_path: Path) -> None:
    # Build a fake repo root with a scope_matrix.yaml.
    repo = tmp_path / "repo"
    matrix_path = repo / "verticals" / "wine-beverage" / "scope_matrix.yaml"
    matrix_path.parent.mkdir(parents=True)
    matrix_path.write_text(
        "departments:\n"
        "  marketing:\n"
        "    owns: [brand positioning]\n"
        "    never: []\n",
        encoding="utf-8",
    )
    company = _seed_company(tmp_path)

    result = add_department(
        company, "legal",
        owns=["contract review", "NDA approval"],
        never=["brand positioning"],
        repo_root=repo,
    )
    assert result.scope_matrix_updated is True
    data = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    assert data["departments"]["legal"]["owns"] == ["contract review", "NDA approval"]
    assert data["departments"]["legal"]["never"] == ["brand positioning"]
    # Existing entries preserved.
    assert "marketing" in data["departments"]


def test_scope_matrix_merges_existing_entry(tmp_path: Path) -> None:
    """Calling add_department on an existing dept (not via CLI path —
    direct primitive call) should merge owns/never union-style."""
    repo = tmp_path / "repo"
    matrix_path = repo / "verticals" / "wine-beverage" / "scope_matrix.yaml"
    matrix_path.parent.mkdir(parents=True)
    matrix_path.write_text(
        "departments:\n"
        "  legal:\n"
        "    owns: [contract review]\n"
        "    never: []\n",
        encoding="utf-8",
    )
    from core.onboarding.dept_creation import _append_to_scope_matrix
    changed = _append_to_scope_matrix(
        matrix_path, "legal",
        owns=["NDA approval"], never=["brand voice"],
    )
    assert changed is True
    data = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    assert set(data["departments"]["legal"]["owns"]) == {
        "contract review", "NDA approval",
    }
    assert data["departments"]["legal"]["never"] == ["brand voice"]


def test_scope_matrix_missing_file_is_not_fatal(tmp_path: Path) -> None:
    company = _seed_company(tmp_path)
    # Point repo_root at a dir with no scope_matrix.yaml.
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    result = add_department(
        company, "legal",
        owns=["x"], never=["y"],
        repo_root=empty_repo,
    )
    assert result.scope_matrix_updated is False
    # Dept creation itself still succeeded.
    assert result.dept_dir.exists()


# ---------------------------------------------------------------------------
# End-to-end: created dept is loadable by the existing loader
# ---------------------------------------------------------------------------
def test_created_dept_is_visible_to_loader(tmp_path: Path) -> None:
    """The acceptance test: after add_department, load_departments
    (which the dispatcher uses) picks up the new dept."""
    company = _seed_company(tmp_path, active_depts=["marketing"])
    add_department(
        company, "legal",
        display_name="Legal",
        prompt_body="Handles contracts.",
    )
    # Now simulate a load.
    from core.company import load_company
    from core.managers.loader import load_departments

    loaded_company = load_company(company)
    depts = load_departments(loaded_company)
    dept_names = {d.name for d in depts}
    assert "legal" in dept_names
    # And the display_name round-trips.
    legal = next(d for d in depts if d.name == "legal")
    assert legal.display_name == "Legal"
