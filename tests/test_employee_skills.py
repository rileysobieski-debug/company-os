"""Chunk 1b.2 — schema validation for the first three employee YAMLs.

These tests load the YAMLs via `SkillRegistry` and assert the loaded
`SkillSpec` objects carry the fields required by plan §4.1.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def registry():
    """Fresh registry loaded from the real skills/employees/ directory."""
    from core.skill_registry import SkillRegistry
    r = SkillRegistry()
    root = Path(__file__).resolve().parent.parent / "skills" / "employees"
    n = r.load(root=root)
    assert n >= 6, f"expected ≥6 employee YAMLs (1b.2 + 1b.3), got {n}"
    return r


def _assert_schema(spec) -> None:
    """Assert a loaded SkillSpec has every Phase 1b-required field populated."""
    assert spec.mode == "agentic"
    assert "haiku" in spec.model, f"{spec.skill_id} model must be a haiku variant"
    assert isinstance(spec.max_tool_iterations, int)
    assert spec.max_tool_iterations > 0
    assert isinstance(spec.max_tokens, int)
    assert spec.max_tokens > 0
    assert spec.tools, f"{spec.skill_id} must declare tools"
    assert spec.inputs, f"{spec.skill_id} must declare inputs"
    assert spec.outputs, f"{spec.skill_id} must declare outputs"
    assert spec.benchmarks_yaml_path, f"{spec.skill_id} must declare benchmarks_yaml_path"
    assert spec.rubric.strip(), f"{spec.skill_id} must declare rubric"


def test_file_fetcher_yaml_loads_and_validates_schema(registry) -> None:
    spec = registry.get("file-fetcher")
    _assert_schema(spec)
    assert "Read" in spec.tools
    assert "Glob" in spec.tools
    assert "Grep" in spec.tools


def test_doc_writer_yaml_loads_and_validates_schema(registry) -> None:
    spec = registry.get("doc-writer")
    _assert_schema(spec)
    assert "Read" in spec.tools
    assert "Write" in spec.tools
    assert "Edit" in spec.tools


def test_schema_validator_yaml_loads_and_validates_schema(registry) -> None:
    spec = registry.get("schema-validator")
    _assert_schema(spec)
    assert "Read" in spec.tools
    assert "Grep" in spec.tools


# Chunk 1b.3 — three additional employees -----------------------------------
def test_web_researcher_yaml_loads_and_validates_schema(registry) -> None:
    spec = registry.get("web-researcher")
    _assert_schema(spec)
    # synthesis_difficulty must be declared so escalation works.
    assert spec.synthesis_difficulty in ("low", "high")


def test_kb_retriever_yaml_loads_and_validates_schema(registry) -> None:
    spec = registry.get("kb-retriever")
    _assert_schema(spec)
    assert spec.synthesis_difficulty in ("low", "high")


def test_calc_yaml_loads_with_sandboxed_exec_tool(registry) -> None:
    spec = registry.get("calc")
    _assert_schema(spec)
    # Sandboxed python exec tool — chunk 1b.3 handoff note.
    assert any("exec" in t.lower() or "python" in t.lower() for t in spec.tools), (
        f"calc must declare a python-exec tool, got {spec.tools}"
    )
