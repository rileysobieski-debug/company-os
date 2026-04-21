"""Tests for core/skill_runner.py — chunk 1b.1 agentic mode.

No real SDK calls. `_collect_messages` is monkeypatched to return crafted
message lists so we can exercise the envelope + cap + escalation branches
deterministically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Minimal SDK-shaped fakes (mirror claude_agent_sdk block types)
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
class _AssistantMsg:
    content: list[Any]


def _make_messages(n_tool_calls: int, final_text: str = "done") -> list[_AssistantMsg]:
    """Build an assistant message trace with `n_tool_calls` ToolUse blocks."""
    blocks: list[Any] = []
    for i in range(n_tool_calls):
        blocks.append(_ToolUse(name="Read", input={"source_path": f"file{i}.md"}))
    blocks.append(_Text(text=final_text))
    return [_AssistantMsg(content=blocks)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_pure_mode_returns_result_without_entering_agentic_loop(monkeypatch) -> None:
    """Regression guard — 1a.4 pure-mode semantics must still work."""
    from core import skill_runner

    called = {"sdk": False}

    def _explode(*_a: Any, **_kw: Any) -> list[Any]:
        called["sdk"] = True
        raise AssertionError("pure mode must not call the SDK")

    monkeypatch.setattr(skill_runner, "_collect_messages", _explode)

    result = skill_runner.run("any-skill", inputs={"x": 1}, mode="pure")
    assert result.mode == "pure"
    assert result.status in ("stub-ok", "registered-stub")
    assert called["sdk"] is False


def test_agentic_mode_returns_skill_result_with_required_envelope_fields(
    monkeypatch,
) -> None:
    from core import skill_runner

    monkeypatch.setattr(
        skill_runner,
        "_collect_messages",
        lambda prompt, model, tools: _make_messages(n_tool_calls=1, final_text="ok"),
    )

    result = skill_runner.run("file-fetcher", inputs={"prompt": "fetch"}, mode="agentic")

    # All five documented envelope fields must be present and populated.
    assert result.mode == "agentic"
    assert isinstance(result.result, str) and result.result == "ok"
    assert isinstance(result.tools_used, list) and len(result.tools_used) == 1
    assert isinstance(result.sources, list)
    assert isinstance(result.max_iterations_hit, bool)
    assert isinstance(result.model_used, str) and result.model_used


def test_max_tool_iterations_cap_trips_when_sdk_exceeds_cap(monkeypatch) -> None:
    """With cap=2 and SDK doing 3 tool calls, max_iterations_hit must be True."""
    from core import skill_runner

    monkeypatch.setattr(
        skill_runner,
        "_collect_messages",
        lambda prompt, model, tools: _make_messages(n_tool_calls=3, final_text="x"),
    )

    result = skill_runner.run(
        "file-fetcher",
        inputs={"prompt": "p"},
        mode="agentic",
        max_tool_iterations=2,
    )
    assert result.max_iterations_hit is True
    assert result.status == "capped"
    assert len(result.tools_used) == 3


def test_default_agentic_invocation_uses_haiku(monkeypatch) -> None:
    from core import skill_runner

    monkeypatch.setattr(
        skill_runner,
        "_collect_messages",
        lambda prompt, model, tools: _make_messages(n_tool_calls=0),
    )
    result = skill_runner.run("any", inputs={}, mode="agentic")
    assert result.model_used == "claude-haiku-4-5-20251001"


def test_synthesis_difficulty_high_escalates_to_sonnet(monkeypatch) -> None:
    from core import skill_runner

    monkeypatch.setattr(
        skill_runner,
        "_collect_messages",
        lambda prompt, model, tools: _make_messages(n_tool_calls=0),
    )
    result = skill_runner.run(
        "any",
        inputs={"synthesis_difficulty": "high"},
        mode="agentic",
    )
    assert result.model_used == "claude-sonnet-4-6"


def test_tools_used_list_populated_from_sdk_iteration_trace(monkeypatch) -> None:
    from core import skill_runner

    msgs = [
        _AssistantMsg(content=[_ToolUse(name="Read", input={"source_path": "a.md"})]),
        _AssistantMsg(content=[_ToolUse(name="Grep", input={"pattern": "foo"})]),
        _AssistantMsg(content=[_Text(text="final")]),
    ]
    monkeypatch.setattr(skill_runner, "_collect_messages", lambda *_a, **_kw: msgs)

    result = skill_runner.run("any", inputs={}, mode="agentic")
    names = [t["name"] for t in result.tools_used]
    assert names == ["Read", "Grep"]
    # The one with a source_path populates sources.
    assert "a.md" in result.sources
