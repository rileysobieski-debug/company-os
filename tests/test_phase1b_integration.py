"""Chunk 1b.5 — Phase 1b integration tests.

Cross-chunk verification that 1b.1 / 1b.2 / 1b.3 / 1b.4 interact correctly:
  - max_iterations_hit propagates from SkillResult to the synthesis
    envelope's needs_founder_review flag (Phase 9 auto-demote gate).
  - All six employee YAMLs are invocable through skill_runner agentic
    mode with a mock SDK.
  - synthesis_difficulty routing works on web-researcher round-trip.
  - SpecialistResult attribution survives a mock dispatch_manager call.

No real SDK calls. No real vault access.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# SDK fakes (mirror structure of claude_agent_sdk blocks)
# ---------------------------------------------------------------------------
@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _ToolUse:
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _Msg:
    content: list[Any]


def _trace(n_tools: int, final: str = "ok") -> list[_Msg]:
    blocks: list[Any] = [
        _ToolUse(name="Read", input={"source_path": f"f{i}.md"}) for i in range(n_tools)
    ]
    blocks.append(_Text(text=final))
    return [_Msg(content=blocks)]


@pytest.fixture(scope="module")
def loaded_registry():
    """Fresh registry with all six employee YAMLs loaded."""
    from core.skill_registry import SkillRegistry
    r = SkillRegistry()
    root = Path(__file__).resolve().parent.parent / "skills" / "employees"
    n = r.load(root=root)
    assert n >= 6, f"expected ≥6 employee YAMLs for integration, got {n}"
    return r


# ---------------------------------------------------------------------------
# Test 1 — max_iterations_hit → needs_founder_review
# ---------------------------------------------------------------------------
def test_max_iterations_hit_propagates_to_needs_founder_review(monkeypatch) -> None:
    from core import skill_runner

    monkeypatch.setattr(
        skill_runner,
        "_collect_messages",
        lambda prompt, model, tools: _trace(n_tools=10),
    )
    result = skill_runner.run(
        "file-fetcher", inputs={"prompt": "p"}, mode="agentic", max_tool_iterations=2
    )
    synth = skill_runner.to_synthesis(result)
    assert synth["needs_founder_review"] is True
    assert synth["max_iterations_hit"] is True

    # Conversely — a clean run must NOT set the flag.
    monkeypatch.setattr(
        skill_runner, "_collect_messages", lambda prompt, model, tools: _trace(n_tools=1)
    )
    clean = skill_runner.run("file-fetcher", inputs={}, mode="agentic")
    assert skill_runner.to_synthesis(clean)["needs_founder_review"] is False


# ---------------------------------------------------------------------------
# Test 2 — all six YAMLs invocable through skill_runner agentic mode
# ---------------------------------------------------------------------------
def test_all_six_employee_yamls_invocable_in_agentic_mode(
    monkeypatch, loaded_registry
) -> None:
    from core import skill_runner
    from core.skill_runner import SkillRunner

    monkeypatch.setattr(
        skill_runner,
        "_collect_messages",
        lambda prompt, model, tools: _trace(n_tools=1, final="done"),
    )
    runner = SkillRunner(registry=loaded_registry)

    for skill_id in (
        "file-fetcher",
        "doc-writer",
        "schema-validator",
        "web-researcher",
        "kb-retriever",
        "calc",
    ):
        result = runner.run(skill_id, inputs={"prompt": "x"}, mode="agentic")
        # Every envelope field populated and of the right type.
        assert result.mode == "agentic"
        assert result.result == "done"
        assert isinstance(result.tools_used, list)
        assert isinstance(result.max_iterations_hit, bool)
        assert result.model_used  # non-empty


# ---------------------------------------------------------------------------
# Test 3 — synthesis_difficulty routing on web-researcher
# ---------------------------------------------------------------------------
def test_web_researcher_high_difficulty_selects_sonnet(monkeypatch, loaded_registry) -> None:
    from core import skill_runner
    from core.skill_runner import SkillRunner

    monkeypatch.setattr(
        skill_runner, "_collect_messages", lambda prompt, model, tools: _trace(0)
    )
    runner = SkillRunner(registry=loaded_registry)
    result = runner.run(
        "web-researcher",
        inputs={"query": "x", "synthesis_difficulty": "high"},
        mode="agentic",
    )
    assert result.model_used == "claude-sonnet-4-6"


def test_web_researcher_default_difficulty_uses_haiku(monkeypatch, loaded_registry) -> None:
    from core import skill_runner
    from core.skill_runner import SkillRunner

    monkeypatch.setattr(
        skill_runner, "_collect_messages", lambda prompt, model, tools: _trace(0)
    )
    runner = SkillRunner(registry=loaded_registry)
    # web-researcher's spec declares synthesis_difficulty: low — so no input
    # override means the spec's value is used, which should route to haiku.
    result = runner.run("web-researcher", inputs={"query": "x"}, mode="agentic")
    assert "haiku" in result.model_used


# ---------------------------------------------------------------------------
# Test 5 — SpecialistResult attribution round-trip through dispatch_manager
# ---------------------------------------------------------------------------
def test_specialist_result_round_trip_through_mock_dispatch_manager(monkeypatch) -> None:
    from core.managers import base as base_mod
    from core.managers.base import ManagerResult, SpecialistResult

    # Construct a ManagerResult containing SpecialistResult objects as if
    # the SDK dispatch had populated them — then thread it through
    # dispatch_manager's post_hook to verify attribution survives.
    canned = ManagerResult(
        manager_name="mktg",
        brief="brief",
        final_text="synthesized",
    )
    canned.specialists_called.append(
        SpecialistResult(name="copywriter", attribution_source="subagent_type")
    )
    canned.specialists_called.append(
        SpecialistResult(name="<unknown>", attribution_source="unknown")
    )

    fake_dept = MagicMock()
    fake_dept.name = "mktg"
    monkeypatch.setattr(base_mod, "load_departments", lambda _c: [fake_dept])
    monkeypatch.setattr(base_mod, "Manager", MagicMock())
    monkeypatch.setattr(base_mod.anyio, "run", lambda _fn, _brief: canned)

    captured: list[ManagerResult] = []
    base_mod.dispatch_manager(
        "mktg",
        "brief",
        company=MagicMock(),
        post_hook=captured.append,
    )
    assert len(captured) == 1
    specs = captured[0].specialists_called
    assert len(specs) == 2
    # Typed attribution preserved.
    assert specs[0].attribution_source == "subagent_type"
    assert specs[0].name == "copywriter"
    assert specs[1].attribution_source == "unknown"
