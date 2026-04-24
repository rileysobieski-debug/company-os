"""MCP adapter base contract tests.

Covers the shared shapes (LLMMessage, LLMResponse, LLMUsage, LLMTool)
and confirms the abstract class cannot be instantiated.
"""
from __future__ import annotations

import pytest

from core.mcp.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    LLMTool,
    LLMToolCall,
    LLMUsage,
)


def test_abstract_adapter_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        LLMAdapter()  # type: ignore[abstract]


def test_message_is_frozen() -> None:
    m = LLMMessage(role="user", content="hi")
    with pytest.raises(Exception):
        m.content = "mutated"  # type: ignore[misc]


def test_tool_is_frozen() -> None:
    tool = LLMTool(name="lookup", description="x", parameters={})
    with pytest.raises(Exception):
        tool.name = "x"  # type: ignore[misc]


def test_usage_total_tokens_sums() -> None:
    usage = LLMUsage(prompt_tokens=10, completion_tokens=20, cached_tokens=5)
    assert usage.total_tokens == 30


def test_response_to_dict_is_serializable() -> None:
    resp = LLMResponse(
        content="ok",
        tool_calls=(LLMToolCall(tool_name="t", arguments={"k": 1}),),
        stop_reason="end_turn",
        model="test/model",
        usage=LLMUsage(prompt_tokens=1, completion_tokens=2),
        extra={"provider": "x"},
    )
    d = resp.to_dict()
    assert d["content"] == "ok"
    assert d["model"] == "test/model"
    assert d["usage"] == {"prompt_tokens": 1, "completion_tokens": 2, "cached_tokens": 0}
    assert isinstance(d["tool_calls"], list)
    assert d["tool_calls"][0]["tool_name"] == "t"
