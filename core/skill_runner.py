"""
core/skill_runner.py — Skill execution surface
==============================================
Pure mode is the deterministic, no-LLM skill path: given the skill ID
and inputs, return a result dict.

Agentic mode (chunk 1b.1) runs a bounded `claude_agent_sdk.query()` loop.
Four guarantees hold regardless of the skill:
  1. `max_tool_iterations` bounds the tool-use count; exceeding it sets
     `max_iterations_hit=True` and stops the loop. This is the Phase 9
     entry gate — unbounded spend would otherwise be possible.
  2. The returned `SkillResult` carries `{result, tools_used, sources,
     max_iterations_hit, model_used}` verbatim from the SDK trace.
  3. Model selection is explicit: haiku by default; `synthesis_difficulty
     == "high"` (on the spec or in inputs) escalates to sonnet.
  4. SDK integration is behind a single monkeypatch seam (`_collect_messages`)
     so tests never hit the real API.

Interface:
  SkillRunner.run(skill_id, inputs, mode="pure" | "agentic",
                  max_tool_iterations=None, system=None, tools=None)
    -> SkillResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core import config
from core.skill_registry import SkillRegistry, SkillSpec, default_registry


# ---------------------------------------------------------------------------
# Defaults + model escalation
# ---------------------------------------------------------------------------
_DEFAULT_MAX_TOOL_ITERATIONS = 5
_SONNET_MODEL = "claude-sonnet-4-6"
_OPUS_MODEL = "claude-opus-4-6"


def _select_model(spec: SkillSpec | None, inputs: dict[str, Any]) -> str:
    """Return the Claude model ID for an agentic skill invocation.

    Precedence (highest first):
      1. `spec.reasoning_required == True` OR `inputs["reasoning_required"]`
         → Opus (§9 — founder-approved opt-in during training).
      2. explicit `inputs["synthesis_difficulty"] == "high"` → Sonnet
      3. `spec.synthesis_difficulty == "high"` (when a spec is registered)
      4. default → `config.get_model("default")` (Haiku)
    """
    reasoning_required = bool(inputs.get("reasoning_required"))
    if not reasoning_required and spec is not None:
        reasoning_required = bool(getattr(spec, "reasoning_required", False))
    if reasoning_required:
        return _OPUS_MODEL

    difficulty = inputs.get("synthesis_difficulty")
    if difficulty is None and spec is not None:
        difficulty = getattr(spec, "synthesis_difficulty", None)
    if difficulty == "high":
        return _SONNET_MODEL
    return config.get_model("default")


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------
@dataclass
class SkillResult:
    """Structured return from `SkillRunner.run()`.

    `outputs` is the legacy dict kept from the 1a.4 stub; the new top-level
    fields (`result`, `tools_used`, `sources`, `max_iterations_hit`,
    `model_used`) are the documented Phase 1b+ envelope that downstream
    callers in 1b.4 and Phase 7 read directly.
    """

    skill_id: str
    mode: str
    status: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    result: str = ""
    tools_used: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    max_iterations_hit: bool = False
    model_used: str = ""


# ---------------------------------------------------------------------------
# SDK seam (monkeypatched by tests — never hits the real API in unit tests)
# ---------------------------------------------------------------------------
def _collect_messages(prompt: str, model: str, tools: list[str] | None) -> list[Any]:
    """Run one agentic loop via `claude_agent_sdk.query()` and return the
    collected messages list. Isolated as a single function so tests can
    monkeypatch this seam and feed crafted message lists.

    Imports are lazy so unit tests that never trigger agentic mode don't
    pay the SDK import cost.
    """
    import anyio  # noqa: PLC0415
    from claude_agent_sdk import (  # noqa: PLC0415
        ClaudeAgentOptions,
        query,
    )

    options = ClaudeAgentOptions(
        system_prompt="",
        model=model,
        allowed_tools=tools or [],
        permission_mode=config.get_permission_mode(),
    )

    async def _drain() -> list[Any]:
        out: list[Any] = []
        async for msg in query(prompt=prompt, options=options):
            out.append(msg)
        return out

    return anyio.run(_drain)


# ---------------------------------------------------------------------------
# Message post-processing
# ---------------------------------------------------------------------------
def _process_messages(
    messages: list[Any], max_tool_iterations: int
) -> dict[str, Any]:
    """Walk the SDK message list and build the envelope fields.

    Tool-use blocks are counted; when the count exceeds the cap, the
    returned dict has `max_iterations_hit=True`. The final prose result
    is the concatenation of all `TextBlock` contents.
    """
    tools_used: list[dict[str, Any]] = []
    text_parts: list[str] = []
    sources: list[str] = []

    for msg in messages:
        content = getattr(msg, "content", None)
        if content is None:
            continue
        for block in content:
            btype = getattr(block, "type", None) or type(block).__name__.lower()
            name = getattr(block, "name", None)
            # TextBlock: either SDK type or any block exposing .text
            text = getattr(block, "text", None)
            if text is not None and "tool" not in btype:
                text_parts.append(str(text))
                continue
            if "tool" in btype and "use" in btype or name is not None and text is None:
                tools_used.append(
                    {
                        "name": str(name or "<unnamed>"),
                        "input": getattr(block, "input", None) or {},
                    }
                )
                src = (getattr(block, "input", None) or {}).get("source_path")
                if src:
                    sources.append(str(src))

    return {
        "result": "\n".join(t for t in text_parts if t.strip()),
        "tools_used": tools_used,
        "sources": sources,
        "max_iterations_hit": len(tools_used) > max_tool_iterations,
    }


# ---------------------------------------------------------------------------
# SkillRunner
# ---------------------------------------------------------------------------
class SkillRunner:
    """Dispatches skill calls against a SkillRegistry."""

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self._registry = registry or default_registry

    def run(
        self,
        skill_id: str,
        inputs: dict[str, Any] | None = None,
        mode: str = "pure",
        max_tool_iterations: int | None = None,
        system: str | None = None,  # noqa: ARG002 — reserved for Phase 7
        tools: list[str] | None = None,
    ) -> SkillResult:
        if mode not in ("pure", "agentic"):
            raise ValueError(f"unknown skill mode: {mode!r}")

        inputs = dict(inputs or {})

        # Registry lookup — optional in both modes. Agentic mode can proceed
        # with a default SkillSpec when the registry is empty; this is the
        # pre-YAML state (1b.1 ships before 1b.2 populates the catalogue).
        spec: SkillSpec | None
        try:
            spec = self._registry.get(skill_id)
        except KeyError:
            spec = None

        if mode == "pure":
            return self._run_pure(skill_id, inputs, spec)
        return self._run_agentic(
            skill_id, inputs, spec, max_tool_iterations, tools
        )

    # -- pure mode --------------------------------------------------------
    def _run_pure(
        self,
        skill_id: str,
        inputs: dict[str, Any],
        spec: SkillSpec | None,
    ) -> SkillResult:
        status = "stub-ok" if spec is None else "registered-stub"
        return SkillResult(
            skill_id=skill_id,
            mode="pure",
            status=status,
            inputs=inputs,
            outputs={},
        )

    # -- agentic mode -----------------------------------------------------
    def _run_agentic(
        self,
        skill_id: str,
        inputs: dict[str, Any],
        spec: SkillSpec | None,
        max_tool_iterations: int | None,
        tools: list[str] | None,
    ) -> SkillResult:
        model = _select_model(spec, inputs)
        cap = (
            max_tool_iterations
            if max_tool_iterations is not None
            else _DEFAULT_MAX_TOOL_ITERATIONS
        )
        resolved_tools = tools if tools is not None else list(
            getattr(spec, "inputs", ()) if spec else ()
        )

        prompt = str(inputs.get("prompt") or inputs.get("task") or skill_id)
        messages = _collect_messages(prompt, model, resolved_tools)
        processed = _process_messages(messages, cap)

        return SkillResult(
            skill_id=skill_id,
            mode="agentic",
            status="ok" if not processed["max_iterations_hit"] else "capped",
            inputs=inputs,
            outputs=processed,
            result=processed["result"],
            tools_used=processed["tools_used"],
            sources=processed["sources"],
            max_iterations_hit=processed["max_iterations_hit"],
            model_used=model,
        )


default_runner = SkillRunner()


def run(
    skill_id: str,
    inputs: dict[str, Any] | None = None,
    mode: str = "pure",
    **kwargs: Any,
) -> SkillResult:
    """Shim for function-style callers."""
    return default_runner.run(skill_id, inputs=inputs, mode=mode, **kwargs)


# ---------------------------------------------------------------------------
# Synthesis envelope (Phase 9 auto-demote gate — chunk 1b.5)
# ---------------------------------------------------------------------------
def to_synthesis(result: SkillResult) -> dict[str, Any]:
    """Wrap a SkillResult in the downstream-synthesis envelope.

    `needs_founder_review` is the Phase 9 auto-demote signal: a capped
    (max_iterations_hit=True) skill cannot auto-approve through to
    persistent state — the founder must review the partial output.
    """
    return {
        "skill_id": result.skill_id,
        "mode": result.mode,
        "status": result.status,
        "result": result.result,
        "model_used": result.model_used,
        "tools_used": result.tools_used,
        "sources": result.sources,
        "max_iterations_hit": result.max_iterations_hit,
        "needs_founder_review": result.max_iterations_hit,
    }
