"""Skill → AgentDefinition builder (Phase 9.4b — §13 Phase 9)."""
from __future__ import annotations

import pytest
from claude_agent_sdk import AgentDefinition

from core.managers.skill_agents import build_skill_agent, build_skill_agents
from core.skill_registry import SkillRegistry, SkillSpec


def _spec(**overrides) -> SkillSpec:
    base = dict(
        skill_id="web-researcher",
        description="Searches the public web and synthesizes findings with citations.",
        mode="agentic",
        tools=("WebSearch", "WebFetch"),
        max_tool_iterations=5,
        max_tokens=4096,
        model="claude-haiku-4-5-20251001",
        inputs=("query", "focus"),
        outputs=("findings", "citations"),
        benchmarks_yaml_path="",
        rubric="Pass conditions:\n- Every non-trivial claim carries a citation URL.",
        synthesis_difficulty=None,
    )
    base.update(overrides)
    return SkillSpec(**base)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------
def test_build_returns_agent_definition() -> None:
    agent = build_skill_agent(_spec())
    assert isinstance(agent, AgentDefinition)


def test_agent_tools_match_spec() -> None:
    agent = build_skill_agent(_spec())
    assert list(agent.tools) == ["WebSearch", "WebFetch"]


def test_agent_description_mentions_skill_id() -> None:
    agent = build_skill_agent(_spec())
    assert "web-researcher" in agent.description


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------
def test_prompt_opens_with_skill_role() -> None:
    agent = build_skill_agent(_spec())
    assert agent.prompt.startswith("You are the `web-researcher` skill-agent.")


def test_prompt_includes_description() -> None:
    agent = build_skill_agent(_spec())
    assert "Searches the public web" in agent.prompt


def test_prompt_includes_inputs_and_outputs() -> None:
    agent = build_skill_agent(_spec())
    assert "query" in agent.prompt
    assert "findings" in agent.prompt


def test_prompt_includes_rubric_verbatim() -> None:
    agent = build_skill_agent(_spec())
    assert "Every non-trivial claim carries a citation URL" in agent.prompt
    assert "=== RUBRIC" in agent.prompt


def test_prompt_includes_iteration_budget() -> None:
    agent = build_skill_agent(_spec(max_tool_iterations=3))
    assert "at most 3 tool iterations" in agent.prompt


def test_prompt_handles_missing_rubric() -> None:
    agent = build_skill_agent(_spec(rubric=""))
    assert "RUBRIC" not in agent.prompt
    assert "web-researcher" in agent.prompt


def test_prompt_handles_missing_inputs_outputs() -> None:
    agent = build_skill_agent(_spec(inputs=(), outputs=()))
    assert "Expected inputs" not in agent.prompt
    assert "Expected outputs" not in agent.prompt


# ---------------------------------------------------------------------------
# Batch builder
# ---------------------------------------------------------------------------
def test_batch_builder_uses_custom_registry() -> None:
    reg = SkillRegistry()
    reg.register(_spec(skill_id="alpha"))
    reg.register(_spec(skill_id="beta", tools=("Read",)))
    agents = build_skill_agents(["alpha", "beta"], registry=reg)
    assert set(agents.keys()) == {"alpha", "beta"}
    assert list(agents["beta"].tools) == ["Read"]


def test_batch_builder_unknown_id_raises_keyerror() -> None:
    reg = SkillRegistry()
    reg.register(_spec(skill_id="alpha"))
    with pytest.raises(KeyError, match="nonexistent"):
        build_skill_agents(["alpha", "nonexistent"], registry=reg)


def test_batch_builder_preserves_insertion_order() -> None:
    reg = SkillRegistry()
    reg.register(_spec(skill_id="alpha"))
    reg.register(_spec(skill_id="beta", tools=("Read",)))
    reg.register(_spec(skill_id="gamma", tools=("Write",)))
    agents = build_skill_agents(["gamma", "alpha", "beta"], registry=reg)
    assert list(agents.keys()) == ["gamma", "alpha", "beta"]


# ---------------------------------------------------------------------------
# Real Old Press skill catalogue loads
# ---------------------------------------------------------------------------
def test_builds_agents_from_real_registry() -> None:
    """Load the actual skills/employees/*.yaml and make sure every skill
    produces a well-formed AgentDefinition."""
    reg = SkillRegistry()
    count = reg.load()
    assert count >= 1, "expected at least one skill registered from skills/employees/"
    agents = build_skill_agents(reg.ids(), registry=reg)
    assert set(agents.keys()) == set(reg.ids())
    for aid, agent in agents.items():
        assert isinstance(agent, AgentDefinition)
        assert agent.prompt  # non-empty
        assert aid in agent.description
