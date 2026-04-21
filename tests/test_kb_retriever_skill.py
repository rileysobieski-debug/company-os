"""kb-retriever skill contract (Phase 4.3).

Confirms the YAML spec loads cleanly, declares the fields `kb.query`
(core/kb/retrieve.py) is designed to satisfy, and stays agentic so
downstream synthesis can still escalate per §4.1.
"""
from __future__ import annotations

from core.skill_registry import SkillRegistry


def test_kb_retriever_skill_is_registered() -> None:
    reg = SkillRegistry()
    reg.load()
    assert "kb-retriever" in reg.ids()


def test_kb_retriever_skill_declares_expected_inputs_outputs() -> None:
    reg = SkillRegistry()
    reg.load()
    spec = reg.get("kb-retriever")
    assert "query" in spec.inputs
    assert "departments" in spec.inputs
    assert "passages" in spec.outputs
    assert "sources" in spec.outputs


def test_kb_retriever_skill_is_agentic() -> None:
    reg = SkillRegistry()
    reg.load()
    spec = reg.get("kb-retriever")
    assert spec.mode == "agentic"


def test_kb_retriever_skill_default_difficulty_is_low() -> None:
    """Per plan §4.1 — straight retrieval stays haiku; sonnet only when the
    caller lifts synthesis_difficulty=high per invocation."""
    reg = SkillRegistry()
    reg.load()
    spec = reg.get("kb-retriever")
    assert spec.synthesis_difficulty == "low"
