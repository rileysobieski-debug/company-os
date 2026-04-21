"""
Manager class + dispatcher
==========================
A Manager is a department lead. It receives a brief from the Orchestrator,
reads its department's accumulated memory, selects + dispatches specialists
(which in turn may call workers), and returns a synthesis to the caller.

Architecture:
  - Uses claude_agent_sdk.query() with ClaudeAgentOptions (not raw anthropic)
    because managers use the Agent tool to call specialists.
  - cwd is set to the company folder so every Read/Write/Glob is scoped.
  - Specialists are built as AgentDefinitions from loaded SpecialistConfigs
    (see core.managers.loader). Workers are built once via employees.build_workers
    and passed into every specialist so they can delegate further.

SDK nesting note:
  Current Claude Agent SDK supports specialists-calling-workers via the Agent
  tool (2-level nesting from the manager's perspective: manager → specialist,
  then specialist → worker). A 3-level chain (manager → specialist → worker)
  is what we need. The current sdk flattens this: the manager defines
  specialists and workers both as AgentDefinition entries in its agents dict,
  and specialists are granted the Agent tool so they can dispatch to workers.
  At runtime the harness treats the manager's agent dict as a pool; specialists
  can call any agent in that dict via the Agent tool (including workers).
  If a future SDK release changes this nesting contract, switch specialists
  to their own Python query() calls from a dispatch_specialist() wrapper.

Public:
  Manager class
  build_flex_specialist(company) → AgentDefinition
  dispatch_manager(manager_name, brief, company) → ManagerResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import anyio
from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)

from core import config
from core.company import CompanyConfig
from core.employees import build_workers
from core.managers.loader import (
    DepartmentConfig,
    SpecialistConfig,
    load_departments,
)
from core.managers.skill_agents import build_skill_agents
from core.skill_registry import default_registry


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MANAGER_MAX_TURNS = 30  # hard cap on the manager's internal SDK loop
FLEX_SPECIALIST_NAME = "flex-specialist"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
@dataclass
class SpecialistResult:
    """Typed attribution record for one specialist invocation.

    Chunk 1b.4 replaced the raw string heuristic in `_run_sdk_loop` with
    this dataclass. `name` is the specialist's identifier as reported by
    the SDK; `attribution_source` names the SDK payload key that supplied
    it so downstream consumers (Phase 7 memory updater, Phase 9 auto-
    approve gate) can weight confidence:

      "subagent_type" — canonical SDK Agent tool payload
      "agent"         — legacy Agent tool payload
      "name"          — fallback (block.input["name"])
      "unknown"       — none of the above; attribution is lost

    `tools_used` is reserved for Phase 7 when the SDK exposes nested tool
    calls made BY the specialist back to the manager; kept as an empty
    list in Phase 1b.
    """

    name: str
    attribution_source: str  # "subagent_type" | "agent" | "name" | "unknown"
    tools_used: list[dict[str, Any]] = field(default_factory=list)

    def __str__(self) -> str:
        # Preserves `f"- {spec}"` formatting that treats the field as a string.
        return self.name


def _extract_specialist_attribution(tool_use_block: Any) -> SpecialistResult:
    """Build a SpecialistResult from an `Agent` ToolUseBlock input.

    Replaces the former three-way `inp.get()` heuristic at
    `core/managers/base.py:498-502`. Phase 9 entry-gate requirement.
    """
    inp = getattr(tool_use_block, "input", None) or {}
    for key in ("subagent_type", "agent", "name"):
        value = inp.get(key)
        if value:
            return SpecialistResult(name=str(value), attribution_source=key)
    return SpecialistResult(name="<unknown>", attribution_source="unknown")


@dataclass
class ManagerResult:
    """Structured return from one manager dispatch."""

    manager_name: str
    brief: str
    final_text: str
    specialists_called: list[SpecialistResult] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw_messages_count: int = 0


# ---------------------------------------------------------------------------
# Prompt assembly helpers
# ---------------------------------------------------------------------------
def _company_preamble(company: CompanyConfig) -> str:
    """Context block all managers and specialists see — name, context, domain,
    settled convictions, hard constraints, priorities."""
    parts = [
        f"You are working for {company.name}.",
        "",
        "=== COMPANY CONTEXT ===",
        company.context.strip(),
    ]
    if company.domain.strip():
        parts.extend(["", "=== INDUSTRY / DOMAIN KNOWLEDGE ===", company.domain.strip()])
    sc = company.settled_convictions_block()
    if sc:
        parts.extend(["", sc])
    hc = company.hard_constraints_block()
    if hc:
        parts.extend(["", hc])
    pri = company.priorities_block()
    if pri:
        parts.extend(["", pri])
    return "\n".join(parts)


def _manager_roster_block(dept: DepartmentConfig) -> str:
    """The specialist roster the manager sees when selecting who to dispatch.

    Phase 9.5: when the dept is on the skill-agent path, the "helpers for
    specialists" line lists the available skill-agents (loaded from the
    skill registry) instead of the legacy worker roster. Un-migrated
    depts keep the original worker listing for backwards compatibility
    until the shim is fully retired.
    """
    if not dept.specialists:
        return "No specialists configured for this department."
    lines = ["Available specialists (call by name via the Agent tool):", ""]
    for s in dept.specialists:
        attr = f" [{s.attribute}]" if s.attribute else ""
        lines.append(f"- `{s.name}`{attr} — {s.description}")
    lines.append(f"- `{FLEX_SPECIALIST_NAME}` [FLEX] — Generic on-demand specialist for novel needs that don't fit any named role above.")
    lines.append("")
    if config.is_dept_on_skill_agents(dept.name):
        skill_ids = default_registry.ids()
        if skill_ids:
            lines.append(
                "Skill-agents available (for specialists to invoke via the Agent tool):"
            )
            lines.append("- " + ", ".join(f"`{s}`" for s in skill_ids))
        else:
            lines.append(
                "No skill-agents registered — load the skill catalogue before dispatch."
            )
    else:
        lines.append("Workers available (for specialists to call, not you directly):")
        lines.append("- `research-worker`, `writer-worker`, `analyst-worker`, `data-collector`")
    return "\n".join(lines)


def _manager_reference_block(dept: DepartmentConfig) -> str:
    """Lists dept-level reference files so the manager knows what Riley has
    dropped in the department's reference folder without the loader reading
    contents (files may be large)."""
    if not dept.reference_dir.exists():
        return ""
    files = sorted(
        p for p in dept.reference_dir.iterdir() if p.is_file() and not p.name.startswith(".")
    )
    if not files:
        return ""
    lines = [
        "Riley has left these department-level reference files (read as needed):"
    ]
    for p in files:
        lines.append(f"  - {p.relative_to(dept.dept_dir.parent)}")
    return "\n".join(lines)


def _build_peer_directory_block(
    company: CompanyConfig,
    all_departments: list[DepartmentConfig],
    current_dept_name: str,
) -> str:
    """Coworker directory: lists all other department managers with their
    manager-memory.md paths. Tells agents they may Read these files directly
    to get a peer's current context before making cross-department decisions."""
    other_depts = [d for d in all_departments if d.name != current_dept_name]
    if not other_depts:
        return ""
    lines = [
        "=== COWORKER DIRECTORY — REACHING PEERS ===",
        "Before making a decision that affects another department, or when you need",
        "domain knowledge a peer has, READ their working memory directly using the",
        "Read tool. This is the equivalent of walking over to ask a coworker a question.",
        "",
        "Other department managers and their working memory:",
    ]
    for dept in other_depts:
        try:
            mem_rel = dept.manager_memory_path.relative_to(company.company_dir)
        except ValueError:
            mem_rel = dept.manager_memory_path
        mem_exists = dept.manager_memory_path.exists()
        status = "" if mem_exists else " (empty — not yet active)"
        lines.append(f"  - {dept.display_name} Manager  →  Read '{mem_rel}'{status}")
    lines.extend([
        "",
        "If you need to loop in another department on something your manager didn't",
        "anticipate, note it explicitly in your output so the Orchestrator can act on it.",
    ])
    return "\n".join(lines)


def _build_specialist_sibling_block(
    company: CompanyConfig,
    dept: DepartmentConfig,
    current_spec_name: str,
) -> str:
    """Lists sibling specialists in the same department with their memory paths.
    Allows a specialist to read a peer's memory before acting on shared work."""
    siblings = [s for s in dept.specialists if s.name != current_spec_name]
    if not siblings:
        return ""
    lines = [
        "Specialist peers in your department (read their memory for context):",
    ]
    for spec in siblings:
        try:
            mem_rel = spec.memory_path.relative_to(company.company_dir)
        except ValueError:
            mem_rel = spec.memory_path
        attr = f" [{spec.attribute}]" if spec.attribute else ""
        mem_exists = spec.memory_path.exists()
        status = "" if mem_exists else " (no memory yet)"
        lines.append(f"  - {spec.name}{attr}  →  Read '{mem_rel}'{status}")
    return "\n".join(lines)


def _manager_memory_preview(dept: DepartmentConfig, max_chars: int = 6000) -> str:
    """Inline preview of manager-memory.md (truncated). The manager is also
    told to Read the file fully at start if it needs more."""
    if not dept.manager_memory_path.exists():
        return "(manager-memory.md does not exist yet — this is a fresh department.)"
    content = dept.manager_memory_path.read_text(encoding="utf-8").strip()
    if not content:
        return "(manager-memory.md is empty — this is a fresh department.)"
    if len(content) > max_chars:
        return content[:max_chars] + f"\n\n[...truncated at {max_chars} chars — Read the file directly for the full memory.]"
    return content


def _build_manager_prompt(
    company: CompanyConfig,
    dept: DepartmentConfig,
    all_departments: list[DepartmentConfig] | None = None,
) -> str:
    """Full system prompt for the manager."""
    parts = [
        _company_preamble(company),
        "",
        f"=== YOUR ROLE: {dept.display_name} Manager ===",
        "",
        f"You lead the {dept.name} department. You receive a brief from the Orchestrator, "
        f"consult department memory, dispatch the right specialist(s) for the work, and "
        f"return a synthesized response.",
        "",
        "=== DEPARTMENT CHARTER ===",
        dept.prompt_body,
        "",
        "=== SPECIALIST ROSTER ===",
        _manager_roster_block(dept),
    ]
    ref_block = _manager_reference_block(dept)
    if ref_block:
        parts.extend(["", "=== REFERENCE FILES ===", ref_block])
    parts.extend([
        "",
        "=== YOUR DEPARTMENT MEMORY (manager-memory.md preview) ===",
        _manager_memory_preview(dept),
    ])
    # Peer directory — inject when we have the full org context
    if all_departments:
        peer_block = _build_peer_directory_block(company, all_departments, dept.name)
        if peer_block:
            parts.extend(["", peer_block])
    parts.extend([
        "",
        "=== HOW YOU WORK ===",
        "1. If the brief is a fresh session and you have a scout specialist (`*-scout`), "
        "dispatch the scout FIRST with: 'Produce a current-intelligence briefing relevant "
        "to this brief: {brief}.' Wait for the scout output before dispatching other "
        "specialists.",
        "2. Select the specialist(s) whose attribute matches the work. Call them via the "
        "Agent tool with a clear, self-contained brief — they do not see the session.",
        "3. For novel needs that don't fit any named specialist, call `flex-specialist` "
        "with a detailed role-description in the brief.",
        "4. If specialist outputs conflict, flag the conflict rather than quietly resolving.",
        "5. Before making cross-department decisions, Read the relevant peer manager's "
        "manager-memory.md (see COWORKER DIRECTORY above) to understand their current state.",
        "6. Append lessons / decisions to manager-memory.md at the end of the task.",
        "",
        "=== OUTPUT CONTRACT ===",
        "Your final response to the Orchestrator should be a structured summary of:",
        "  - what specialists you dispatched",
        "  - what they produced (short summaries with file paths to full outputs)",
        "  - any conflicts or open questions requiring cross-department coordination",
        "  - any peer managers whose context you read and what you learned",
        "  - any items flagged for approval (content for `output/pending-approval/`, etc.)",
        "",
        f"=== TRACE PROTOCOL ===",
        "At the end of the task, append to manager-memory.md a dated entry summarizing "
        "the brief received, specialists called, and any lessons learned. Keep it under "
        "150 words per session.",
    ])
    return "\n".join(parts)


def _build_specialist_prompt(
    company: CompanyConfig,
    dept: DepartmentConfig,
    spec: SpecialistConfig,
    all_departments: list[DepartmentConfig] | None = None,
) -> str:
    """Full system prompt for one specialist — preamble + dept context +
    specialist.md body + runtime reference-file list + memory preview."""
    parts = [
        _company_preamble(company),
        "",
        f"=== YOUR DEPARTMENT: {dept.display_name} ===",
        dept.prompt_body,
        "",
        "=== YOUR SPECIALIST PROMPT ===",
        spec.prompt_body,
    ]

    # Reference files the manager/Riley may have dropped
    ref_files = spec.reference_files()
    if ref_files:
        lines = ["", "=== YOUR REFERENCE FILES ===", "Riley has left these files in your reference/ folder (Read them as needed):"]
        for p in ref_files:
            lines.append(f"  - {p.relative_to(company.company_dir)}")
        parts.append("\n".join(lines))

    # Memory preview
    if spec.memory_path.exists():
        mem = spec.memory_path.read_text(encoding="utf-8").strip()
        if mem:
            if len(mem) > 4000:
                mem = mem[:4000] + "\n\n[...truncated; Read memory.md directly for more.]"
            parts.extend(["", "=== YOUR MEMORY (memory.md preview) ===", mem])
        else:
            parts.extend(["", "=== YOUR MEMORY ===", "(memory.md is empty — fresh start.)"])
    else:
        parts.extend(["", "=== YOUR MEMORY ===", "(memory.md does not exist yet — create it on first write.)"])

    # Coworker directory — sibling specialists + peer managers
    sibling_block = _build_specialist_sibling_block(company, dept, spec.name)
    peer_mgr_block = (
        _build_peer_directory_block(company, all_departments, dept.name)
        if all_departments
        else ""
    )
    if sibling_block or peer_mgr_block:
        parts.extend([
            "",
            "=== COWORKER DIRECTORY — REACHING PEERS ===",
            "You may Read any file listed here to get a coworker's current context.",
            "Use this BEFORE making a decision that another specialist or department",
            "would normally weigh in on. This is how you simulate asking a coworker.",
            "",
        ])
        if sibling_block:
            parts.append(sibling_block)
        if peer_mgr_block:
            # Extract just the manager list from the full peer block
            parts.extend(["", "Department managers (cross-department context):"])
            for dept_item in (all_departments or []):
                if dept_item.name == dept.name:
                    continue
                try:
                    mem_rel = dept_item.manager_memory_path.relative_to(company.company_dir)
                except ValueError:
                    mem_rel = dept_item.manager_memory_path
                parts.append(f"  - {dept_item.display_name} Manager  →  Read '{mem_rel}'")

    if config.is_dept_on_skill_agents(dept.name):
        skill_ids = default_registry.ids()
        helpers_line = (
            "skill-agents: " + ", ".join(skill_ids) if skill_ids
            else "(no skill-agents registered — ask the manager for resolution)"
        )
        parts.extend(
            [
                "",
                "=== DELEGATION TO SKILL-AGENTS ===",
                "You have the Agent tool and may invoke any of these "
                f"{helpers_line}. Each skill-agent has a declared rubric; "
                "do not instruct them to operate outside it. You are NOT "
                "permitted to CALL other specialists via Agent — that would",
                "bypass the manager layer. However, you MAY Read a sibling's memory.md",
                "or another manager's manager-memory.md (see COWORKER DIRECTORY above)",
                "before acting. If you need active collaboration, note it in your output",
                "so the manager can coordinate.",
            ]
        )
    else:
        parts.extend(
            [
                "",
                "=== DELEGATION TO WORKERS ===",
                "You have the Agent tool and access to: research-worker, writer-worker, "
                "analyst-worker, data-collector. Delegate narrow sub-tasks to them. "
                "You are NOT permitted to CALL other specialists via Agent — that would",
                "bypass the manager layer. However, you MAY Read a sibling's memory.md",
                "or another manager's manager-memory.md (see COWORKER DIRECTORY above)",
                "before acting. If you need active collaboration, note it in your output",
                "so the manager can coordinate.",
            ]
        )
    return "\n".join(parts)


def _build_flex_specialist_prompt(company: CompanyConfig) -> str:
    """Flex specialist prompt — used for novel needs not covered by a named role."""
    return "\n".join(
        [
            _company_preamble(company),
            "",
            "=== YOUR ROLE: flex-specialist (on-demand) ===",
            "You are the generic on-demand specialist. Your manager has called you because",
            "the task didn't fit any named specialist. The brief will describe the role",
            "you should play for this one task. Treat the brief as your temporary job",
            "description.",
            "",
            "You have no persistent memory and no dedicated reference folder. Use workers",
            "(research-worker, writer-worker, analyst-worker, data-collector) via the Agent",
            "tool as needed.",
            "",
            "Produce a clear, focused output. If the task you're being asked to do really",
            "ought to be its own named specialist, say so in your response — Riley may want",
            "to add a permanent role.",
        ]
    )


# ---------------------------------------------------------------------------
# Agent-definition builders
# ---------------------------------------------------------------------------
def build_flex_specialist(company: CompanyConfig) -> AgentDefinition:
    """Returns the FLEX_SPECIALIST AgentDefinition, bound to this company."""
    return AgentDefinition(
        description=(
            "Generic on-demand specialist for novel needs that don't fit any named role. "
            "Treat the brief as a temporary job description. Has worker access. No memory."
        ),
        prompt=_build_flex_specialist_prompt(company),
        tools=["Read", "Glob", "WebSearch", "WebFetch", "Write", "Agent"],
    )


def _build_specialist_agents(
    company: CompanyConfig,
    dept: DepartmentConfig,
    all_departments: list[DepartmentConfig] | None = None,
) -> dict[str, AgentDefinition]:
    """Convert every loaded SpecialistConfig in this dept into an
    AgentDefinition keyed by specialist name."""
    agents: dict[str, AgentDefinition] = {}
    for spec in dept.specialists:
        # Specialists always need the Agent tool so they can call workers.
        # The loader supplies tools from specialist.md frontmatter; merge
        # with Agent if missing.
        tools = list(spec.tools) if spec.tools else []
        if "Agent" not in tools:
            tools.append("Agent")
        agents[spec.name] = AgentDefinition(
            description=spec.description,
            prompt=_build_specialist_prompt(company, dept, spec, all_departments),
            tools=tools,
        )
    return agents


# ---------------------------------------------------------------------------
# Manager class
# ---------------------------------------------------------------------------
class Manager:
    """Bound to one department. Builds the agent pool (specialists + workers
    + flex) and runs the SDK query loop on execute()."""

    def __init__(
        self,
        company: CompanyConfig,
        dept: DepartmentConfig,
        all_departments: list[DepartmentConfig] | None = None,
    ):
        self.company = company
        self.dept = dept
        self.name = dept.name
        self.display_name = dept.display_name
        self._system_prompt = _build_manager_prompt(company, dept, all_departments)

        # Build the agent pool. Specialists + flex + (workers OR skill-agents)
        # are all AgentDefinitions in the same dict — specialists use the Agent
        # tool to dispatch to them.
        #
        # Phase 9.5 migration: if this dept is on the skill-agent path
        # (config.is_dept_on_skill_agents), workers are replaced by per-skill
        # AgentDefinitions built from the registry. Otherwise the legacy
        # worker roster is preserved so un-migrated depts keep running.
        specialists = _build_specialist_agents(company, dept, all_departments)
        flex = {FLEX_SPECIALIST_NAME: build_flex_specialist(company)}
        if config.is_dept_on_skill_agents(dept.name):
            helpers = build_skill_agents(default_registry.ids(), registry=default_registry)
        else:
            helpers = build_workers(company)
        self._agents: dict[str, AgentDefinition] = {**specialists, **flex, **helpers}

    # -- Public entry ------------------------------------------------------
    async def execute(self, brief: str) -> ManagerResult:
        """Run the manager on one brief. Returns ManagerResult with the
        synthesized text + metadata."""
        options = ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            model=self.dept.manager_model,
            cwd=str(self.company.company_dir),
            allowed_tools=self.dept.manager_tools,
            agents=self._agents,
            max_turns=MANAGER_MAX_TURNS,
            permission_mode=config.get_permission_mode(),
        )

        result = ManagerResult(
            manager_name=self.name,
            brief=brief,
            final_text="",
        )

        final_text_parts: list[str] = []

        async for message in query(prompt=brief, options=options):
            result.raw_messages_count += 1

            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        final_text_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        result.tool_calls.append(
                            {
                                "name": block.name,
                                "input": block.input,
                            }
                        )
                        # Detect specialist calls via the Agent tool.
                        # Chunk 1b.4 — typed SpecialistResult replaces the
                        # raw string heuristic. See _extract_specialist_attribution
                        # and the SpecialistResult dataclass above.
                        if block.name == "Agent":
                            result.specialists_called.append(
                                _extract_specialist_attribution(block)
                            )
                    elif isinstance(block, (ThinkingBlock, ToolResultBlock)):
                        # Not captured in final text; available for future
                        # tracing if needed.
                        pass

            elif isinstance(message, ResultMessage):
                if getattr(message, "usage", None):
                    result.usage = dict(message.usage)

        # Keep only the LAST assistant text block as the final synthesis —
        # earlier text blocks are intermediate reasoning.
        if final_text_parts:
            result.final_text = final_text_parts[-1].strip()
        return result


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Public re-exports — used by meeting.py and onboarding.py
# Signatures:
#   build_manager_prompt(company, dept, all_departments=None)
#   build_specialist_prompt(company, dept, spec, all_departments=None)
# ---------------------------------------------------------------------------
build_manager_prompt = _build_manager_prompt
build_specialist_prompt = _build_specialist_prompt


def dispatch_manager(
    manager_name: str,
    brief: str,
    company: CompanyConfig,
    departments: list[DepartmentConfig] | None = None,
    pre_hook: Callable[[str], None] | None = None,
    post_hook: Callable[[ManagerResult], None] | None = None,
) -> ManagerResult:
    """Synchronous wrapper around Manager.execute().

    Parameters
    ----------
    manager_name : str
        Department key, e.g. "marketing", "finance", "operations".
    brief : str
        The task to dispatch.
    company : CompanyConfig
        Loaded company.
    departments : optional pre-loaded list
        Pass the list you already loaded to avoid re-scanning the folder.
        If None, the loader runs.
    pre_hook : optional callable
        If provided, called with `brief` as its only argument immediately
        before the SDK dispatch starts. Chunk 1a.8 pinned this signature
        for Phase 7 (handshake_runner, evaluator). Default `None` means
        no-op, so all existing call sites are unaffected.
    post_hook : optional callable
        If provided, called with the returned `ManagerResult` as its only
        argument after dispatch completes. Same Phase 7 contract as
        `pre_hook`. Receives the full result (including usage + specialists
        list) so downstream consumers like the cost dashboard (1a.9) and
        memory updater (Phase 7) can intercept without editing this body.
    """
    depts = departments if departments is not None else load_departments(company)
    matched = next((d for d in depts if d.name == manager_name), None)
    if matched is None:
        raise ValueError(
            f"Manager '{manager_name}' not found. "
            f"Available: {[d.name for d in depts]}"
        )
    manager = Manager(company, matched, all_departments=depts)

    # Phase 14 — consolidated-2026-04-18 §10.2: ambient awareness
    # preamble. Opt-in via COMPANY_OS_AMBIENT_AWARENESS=1 so the rollout
    # can be toggled per deploy and existing tests remain green.
    effective_brief = brief
    if _ambient_awareness_enabled():
        try:
            from core.primitives.awareness import preamble_for_dispatch

            preamble = preamble_for_dispatch(brief, company.company_dir)
            if preamble:
                effective_brief = preamble + "\n" + brief
        except Exception:
            # Awareness is opportunistic — never block a dispatch.
            pass

    if pre_hook is not None:
        pre_hook(effective_brief)

    result = anyio.run(manager.execute, effective_brief)

    if post_hook is not None:
        post_hook(result)

    return result


def _ambient_awareness_enabled() -> bool:
    """Opt-in gate for the ambient awareness preamble injector. Reads
    `COMPANY_OS_AMBIENT_AWARENESS` env var (any truthy value enables).
    Kept as a local helper so tests can monkey-patch trivially."""
    import os
    val = os.environ.get("COMPANY_OS_AMBIENT_AWARENESS", "").strip().lower()
    return val in {"1", "true", "yes", "on"}
