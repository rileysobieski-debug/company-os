"""
core/primitives/tool_skill_map.py — Phase 9.4a — §13 Phase 9 migration
======================================================================
Plan §13 Phase 9 / line 686:

  "migration script converts each specialist's declared tools to
   agentic-skill invocations; shim removed in the same release — not
   'between phases.' Per-dept rollout with rollback path."

This primitive is the translation layer. Given a specialist's declared
tool list (Read/Write/WebSearch/...) and the agentic-skill catalogue
(skills/employees/*.yaml), it determines which skills are available to
that specialist and which raw tools are replaced.

Translation rule: a skill is granted iff every tool it requires is
present in the specialist's declared tool list. After granting, each
declared tool is classified:

  * `retained` — explicitly kept (default: Agent, because it's the
    invocation channel specialists use to call skills).
  * `dropped`  — covered by at least one granted skill; the raw tool
    goes away at shim removal (9.5).
  * `gap`      — no granted skill requires this tool; the migration
    needs human review (either add a skill or keep the raw tool).

The output (`ToolSkillTranslation`) is pure data — the caller decides
how to apply it (rewrite specialist.md frontmatter, gate behind a flag,
etc.).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from core.skill_registry import SkillSpec


DEFAULT_RETAINED_TOOLS: tuple[str, ...] = ("Agent",)


@dataclass(frozen=True)
class ToolSkillTranslation:
    granted_skills: tuple[str, ...]
    retained_tools: tuple[str, ...]
    dropped_tools: tuple[str, ...]
    coverage_gaps: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        """True when every non-retained declared tool is covered by a skill."""
        return not self.coverage_gaps

    def as_report(self) -> str:
        """Human-readable summary for migration review."""
        lines = [
            "Tool → skill migration",
            f"  granted skills : {', '.join(self.granted_skills) or '(none)'}",
            f"  retained tools : {', '.join(self.retained_tools) or '(none)'}",
            f"  dropped tools  : {', '.join(self.dropped_tools) or '(none)'}",
        ]
        if self.coverage_gaps:
            lines.append(
                f"  GAPS (manual review): {', '.join(self.coverage_gaps)}"
            )
        else:
            lines.append("  clean: every declared tool is covered")
        return "\n".join(lines)


def translate_tools_to_skills(
    declared_tools: Iterable[str],
    skill_specs: Sequence[SkillSpec],
    *,
    always_retain: Iterable[str] = DEFAULT_RETAINED_TOOLS,
) -> ToolSkillTranslation:
    """Map `declared_tools` onto `skill_specs` by tool-subset cover.

    A skill is granted iff every tool in its `tools` field appears in
    the specialist's `declared_tools`. The specialist's Agent tool
    (and anything else in `always_retain`) survives as-is.
    """
    tool_set = set(declared_tools)
    retain_set = set(always_retain)

    granted: list[str] = []
    tools_covered: set[str] = set()
    for spec in skill_specs:
        spec_tools = set(spec.tools)
        if not spec_tools:
            # A skill with no tool requirements is always grantable.
            granted.append(spec.skill_id)
            continue
        if spec_tools.issubset(tool_set):
            granted.append(spec.skill_id)
            tools_covered.update(spec_tools)

    retained: list[str] = [t for t in declared_tools if t in retain_set]
    # De-duplicate while preserving order.
    retained = list(dict.fromkeys(retained))

    dropped: list[str] = []
    gaps: list[str] = []
    seen: set[str] = set()
    for t in declared_tools:
        if t in seen or t in retain_set:
            seen.add(t)
            continue
        seen.add(t)
        if t in tools_covered:
            dropped.append(t)
        else:
            gaps.append(t)

    return ToolSkillTranslation(
        granted_skills=tuple(dict.fromkeys(granted)),
        retained_tools=tuple(retained),
        dropped_tools=tuple(dropped),
        coverage_gaps=tuple(gaps),
    )
