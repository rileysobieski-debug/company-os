"""
core/managers/skill_agents.py — Phase 9.4b — §13 Phase 9 migration
===================================================================
Converts `SkillSpec` entries from the skill registry into
`AgentDefinition` instances that a manager can drop into its
`ClaudeAgentOptions(agents=...)` dict. Specialists invoke skills by
calling these skill-agents through the `Agent` tool.

This replaces the legacy `core.employees.build_workers()` path where
workers (research-worker, writer-worker, analyst-worker, data-collector)
were hardcoded AgentDefinitions with raw tool grants. The skill-agent
builder is the NEW path; the existing worker path is the shim that
Chunk 9.5 removes.

The per-skill prompt bakes in the skill's rubric + iteration cap so the
skill-agent stops when the rubric is satisfied or when iterations run
out. We don't enforce the iteration cap at the SDK level — the SDK
passes `max_tool_iterations` indirectly through the skill runner; at
the manager-dispatch tier we trust the prompt contract + the
post-evaluator.

Public surface:

  * `build_skill_agent(spec)` — one SkillSpec → one AgentDefinition
  * `build_skill_agents(skill_ids, *, registry=None)` — batch form;
    registry defaults to `core.skill_registry.default_registry`
"""
from __future__ import annotations

from typing import Iterable

from claude_agent_sdk import AgentDefinition

from core.skill_registry import SkillRegistry, SkillSpec, default_registry


def _skill_prompt(spec: SkillSpec) -> str:
    """Assemble the per-skill agent prompt from a SkillSpec.

    The prompt opens with the role (from description), declares the
    expected inputs/outputs, surfaces the rubric verbatim, and ends
    with an iteration budget reminder. Skill-runner's max_tool_iterations
    is advisory here; the prompt tells the agent to finish early and
    honor the rubric.
    """
    parts: list[str] = []
    parts.append(f"You are the `{spec.skill_id}` skill-agent.")
    parts.append("")
    if spec.description:
        parts.append(spec.description.strip())
        parts.append("")
    if spec.inputs:
        parts.append(f"Expected inputs: {', '.join(spec.inputs)}")
    if spec.outputs:
        parts.append(f"Expected outputs: {', '.join(spec.outputs)}")
    if spec.inputs or spec.outputs:
        parts.append("")
    if spec.rubric.strip():
        parts.append("=== RUBRIC (your pass/fail criteria) ===")
        parts.append(spec.rubric.strip())
        parts.append("")
    parts.append(
        f"Budget: at most {spec.max_tool_iterations} tool iterations. "
        "Stop as soon as the rubric is satisfied — do not pad output."
    )
    return "\n".join(parts)


def _skill_description(spec: SkillSpec) -> str:
    """Short description surfaced to the calling specialist (via Agent tool)."""
    base = spec.description.strip().splitlines()[0] if spec.description else ""
    return (
        f"{spec.skill_id} skill — {base}".strip().rstrip("—").strip()
        if base
        else f"{spec.skill_id} skill"
    )


def build_skill_agent(spec: SkillSpec) -> AgentDefinition:
    """Convert one SkillSpec to an AgentDefinition."""
    return AgentDefinition(
        description=_skill_description(spec),
        prompt=_skill_prompt(spec),
        tools=list(spec.tools),
    )


def build_skill_agents(
    skill_ids: Iterable[str],
    *,
    registry: SkillRegistry | None = None,
) -> dict[str, AgentDefinition]:
    """Build `{skill_id: AgentDefinition}` for each id in `skill_ids`.

    Unknown ids raise `KeyError` via the registry's `get()`. Callers
    that tolerate missing skills should filter upstream.
    """
    reg = registry or default_registry
    out: dict[str, AgentDefinition] = {}
    for skill_id in skill_ids:
        spec = reg.get(skill_id)
        out[skill_id] = build_skill_agent(spec)
    return out
