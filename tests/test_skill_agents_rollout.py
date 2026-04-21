"""Per-dept skill-agent rollout + rollback (Phase 9.5 — §13 Phase 9)."""
from __future__ import annotations

import pytest

from core import config
from core.managers import base as managers_base


_ENV = "COMPANY_OS_SKILL_AGENTS_DEPTS"


# ---------------------------------------------------------------------------
# config.is_dept_on_skill_agents
# ---------------------------------------------------------------------------
def test_default_no_depts_migrated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    assert config.get_skill_agent_depts() == frozenset()
    assert not config.is_dept_on_skill_agents("marketing")
    assert not config.is_dept_on_skill_agents("finance")


def test_empty_env_string_means_no_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "")
    assert config.get_skill_agent_depts() == frozenset()
    assert not config.is_dept_on_skill_agents("marketing")


def test_single_dept_migrated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "marketing")
    assert config.is_dept_on_skill_agents("marketing")
    assert not config.is_dept_on_skill_agents("finance")


def test_comma_list_migrates_listed_depts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "marketing, finance ,operations")
    assert config.is_dept_on_skill_agents("marketing")
    assert config.is_dept_on_skill_agents("finance")
    assert config.is_dept_on_skill_agents("operations")
    assert not config.is_dept_on_skill_agents("community")


def test_star_migrates_all_depts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "*")
    for dept in ("marketing", "finance", "editorial", "nonexistent"):
        assert config.is_dept_on_skill_agents(dept)


def test_env_var_reread_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """No caching: changing the env var mid-process takes effect immediately."""
    monkeypatch.setenv(_ENV, "marketing")
    assert config.is_dept_on_skill_agents("marketing")
    monkeypatch.setenv(_ENV, "finance")
    assert not config.is_dept_on_skill_agents("marketing")
    assert config.is_dept_on_skill_agents("finance")


def test_rollback_by_deleting_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing the env var fully reverts to legacy behavior."""
    monkeypatch.setenv(_ENV, "marketing")
    assert config.is_dept_on_skill_agents("marketing")
    monkeypatch.delenv(_ENV)
    assert not config.is_dept_on_skill_agents("marketing")


# ---------------------------------------------------------------------------
# Manager roster block reflects migration
# ---------------------------------------------------------------------------
def _fake_dept(name: str):
    """Minimal DepartmentConfig stand-in for prompt-assembly tests."""
    from pathlib import Path

    from core.managers.loader import DepartmentConfig, SpecialistConfig

    spec = SpecialistConfig(
        name="copywriter",
        description="Writes copy.",
        prompt_body="Prompt.",
        attribute="CRAFT",
        tools=["Read", "Write", "Agent"],
        model="claude-haiku-4-5-20251001",
        department=name,
        is_scout=False,
        memory_path=Path("/tmp/memory.md"),
        reference_dir=Path("/tmp/reference"),
        specialist_dir=Path("/tmp/spec"),
    )
    return DepartmentConfig(
        name=name,
        display_name=name.title(),
        prompt_body="Dept prompt.",
        manager_model="claude-haiku-4-5-20251001",
        manager_tools=["Read", "Agent"],
        dept_dir=Path("/tmp/dept"),
        manager_memory_path=Path("/tmp/mem.md"),
        reference_dir=Path("/tmp/ref"),
        knowledge_base_dir=Path("/tmp/kb"),
        output_dirs={},
        specialists=[spec],
    )


def test_legacy_roster_lists_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    dept = _fake_dept("marketing")
    block = managers_base._manager_roster_block(dept)
    assert "research-worker" in block
    assert "Skill-agents" not in block


def test_migrated_roster_lists_skill_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure the default registry has at least one loaded skill.
    from core.skill_registry import default_registry
    if not default_registry.ids():
        default_registry.load()
    monkeypatch.setenv(_ENV, "marketing")
    dept = _fake_dept("marketing")
    block = managers_base._manager_roster_block(dept)
    assert "Skill-agents" in block or "skill-agents" in block
    # And does NOT list the legacy workers.
    assert "research-worker" not in block
    assert "writer-worker" not in block


def test_migrated_roster_names_at_least_one_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.skill_registry import default_registry
    if not default_registry.ids():
        default_registry.load()
    monkeypatch.setenv(_ENV, "*")
    dept = _fake_dept("finance")
    block = managers_base._manager_roster_block(dept)
    # Any of the canonical skill IDs should appear when the catalogue is loaded.
    assert any(sid in block for sid in default_registry.ids())


# ---------------------------------------------------------------------------
# Specialist-delegation block adapts to migration
# ---------------------------------------------------------------------------
def _fake_company():
    """Minimal CompanyConfig for prompt assembly — just enough for _company_preamble."""
    from pathlib import Path

    from core.company import CompanyConfig

    return CompanyConfig(
        company_dir=Path("/tmp/co"),
        raw_config={
            "company_id": "test-co",
            "company_name": "Test Co",
            "industry": "test",
            "active_departments": ["marketing"],
            "priorities": [],
            "settled_convictions": [],
            "hard_constraints": [],
        },
        context="Context.",
        domain="Domain.",
    )


def test_specialist_delegation_legacy_mentions_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    dept = _fake_dept("marketing")
    spec = dept.specialists[0]
    prompt = managers_base._build_specialist_prompt(_fake_company(), dept, spec)
    assert "DELEGATION TO WORKERS" in prompt
    assert "research-worker" in prompt


def test_specialist_delegation_migrated_mentions_skill_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.skill_registry import default_registry
    if not default_registry.ids():
        default_registry.load()
    monkeypatch.setenv(_ENV, "marketing")
    dept = _fake_dept("marketing")
    spec = dept.specialists[0]
    prompt = managers_base._build_specialist_prompt(_fake_company(), dept, spec)
    assert "DELEGATION TO SKILL-AGENTS" in prompt
    # Should NOT reference legacy worker names.
    assert "research-worker" not in prompt
    assert "writer-worker" not in prompt
