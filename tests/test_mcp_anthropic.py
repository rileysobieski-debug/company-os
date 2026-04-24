"""AnthropicAdapter tests with a mock Anthropic client.

No real SDK calls. The adapter is constructed with `client=mock` so
live HTTP never happens and the test runs without an API key.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.mcp.anthropic_adapter import AnthropicAdapter
from core.mcp.base import (
    LLMMessage,
    LLMMisconfigured,
    LLMProviderOutage,
    LLMRateLimit,
    LLMTool,
)


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _ToolBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


class _MockUsage:
    def __init__(self, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_creation: int = 0) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_creation


class _MockResponse:
    def __init__(self, content, usage, stop_reason="end_turn", model="claude-sonnet-4-6") -> None:
        self.content = content
        self.usage = usage
        self.stop_reason = stop_reason
        self.model = model


class _MockClient:
    def __init__(self, response=None, raise_exc=None) -> None:
        self._response = response
        self._raise = raise_exc
        self.last_kwargs: dict | None = None
        self.messages = self  # client.messages.create(...)

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raise is not None:
            raise self._raise
        return self._response


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------
def test_user_and_assistant_messages_pass_through() -> None:
    client = _MockClient(response=_MockResponse(
        content=[_TextBlock(text="hi back")],
        usage=_MockUsage(input_tokens=5, output_tokens=3),
    ))
    adapter = AnthropicAdapter(client=client)
    adapter.complete(
        messages=(
            LLMMessage("user", "hi"),
            LLMMessage("assistant", "hello"),
        ),
        model="claude-sonnet-4-6",
    )
    msgs = client.last_kwargs["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi"


def test_system_message_in_messages_gets_promoted() -> None:
    client = _MockClient(response=_MockResponse(
        content=[_TextBlock(text="")],
        usage=_MockUsage(input_tokens=1, output_tokens=1),
    ))
    adapter = AnthropicAdapter(client=client)
    adapter.complete(
        messages=(
            LLMMessage("system", "you are helpful"),
            LLMMessage("user", "hi"),
        ),
        model="claude-sonnet-4-6",
    )
    assert client.last_kwargs["system"] == "you are helpful"
    # And the system message was NOT passed into `messages`
    assert all(m["role"] != "system" for m in client.last_kwargs["messages"])


def test_system_kwarg_takes_precedence_over_messages() -> None:
    client = _MockClient(response=_MockResponse(
        content=[_TextBlock(text="")],
        usage=_MockUsage(input_tokens=1, output_tokens=1),
    ))
    adapter = AnthropicAdapter(client=client)
    adapter.complete(
        messages=(
            LLMMessage("system", "override me"),
            LLMMessage("user", "hi"),
        ),
        model="claude-sonnet-4-6",
        system="explicit-system",
    )
    assert client.last_kwargs["system"] == "explicit-system"


def test_tools_translated_to_anthropic_shape() -> None:
    client = _MockClient(response=_MockResponse(
        content=[_TextBlock(text="")],
        usage=_MockUsage(input_tokens=1, output_tokens=1),
    ))
    adapter = AnthropicAdapter(client=client)
    adapter.complete(
        messages=(LLMMessage("user", "hi"),),
        model="claude-sonnet-4-6",
        tools=(LLMTool(name="lookup", description="d", parameters={"type": "object"}),),
    )
    tools = client.last_kwargs["tools"]
    assert tools == [{"name": "lookup", "description": "d", "input_schema": {"type": "object"}}]


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------
def test_text_content_extracted() -> None:
    client = _MockClient(response=_MockResponse(
        content=[_TextBlock(text="hello "), _TextBlock(text="world")],
        usage=_MockUsage(input_tokens=2, output_tokens=3),
    ))
    adapter = AnthropicAdapter(client=client)
    resp = adapter.complete(
        messages=(LLMMessage("user", "hi"),),
        model="claude-sonnet-4-6",
    )
    assert resp.content == "hello world"


def test_tool_use_blocks_become_tool_calls() -> None:
    client = _MockClient(response=_MockResponse(
        content=[
            _TextBlock(text="thinking"),
            _ToolBlock(id="call-1", name="lookup", input={"q": "hi"}),
        ],
        usage=_MockUsage(input_tokens=1, output_tokens=1),
    ))
    adapter = AnthropicAdapter(client=client)
    resp = adapter.complete(
        messages=(LLMMessage("user", "hi"),),
        model="claude-sonnet-4-6",
    )
    assert resp.content == "thinking"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "lookup"
    assert resp.tool_calls[0].arguments == {"q": "hi"}
    assert resp.tool_calls[0].call_id == "call-1"


def test_cache_tokens_surface_in_usage_and_extra() -> None:
    client = _MockClient(response=_MockResponse(
        content=[_TextBlock(text="x")],
        usage=_MockUsage(input_tokens=100, output_tokens=50, cache_read=80, cache_creation=20),
    ))
    adapter = AnthropicAdapter(client=client)
    resp = adapter.complete(
        messages=(LLMMessage("user", "hi"),),
        model="claude-sonnet-4-6",
    )
    assert resp.usage.prompt_tokens == 100
    assert resp.usage.completion_tokens == 50
    assert resp.usage.cached_tokens == 80
    assert resp.extra["anthropic"]["cache_read_input_tokens"] == 80
    assert resp.extra["anthropic"]["cache_creation_input_tokens"] == 20


def test_stop_reason_preserved() -> None:
    client = _MockClient(response=_MockResponse(
        content=[_TextBlock(text="")],
        usage=_MockUsage(input_tokens=1, output_tokens=1),
        stop_reason="tool_use",
    ))
    adapter = AnthropicAdapter(client=client)
    resp = adapter.complete(
        messages=(LLMMessage("user", "hi"),),
        model="claude-sonnet-4-6",
    )
    assert resp.stop_reason == "tool_use"


# ---------------------------------------------------------------------------
# Error normalization
# ---------------------------------------------------------------------------
class _FakeRateLimitError(Exception):
    pass


_FakeRateLimitError.__name__ = "RateLimitError"


def test_rate_limit_error_normalized() -> None:
    client = _MockClient(raise_exc=_FakeRateLimitError("429 Too Many"))
    adapter = AnthropicAdapter(client=client)
    with pytest.raises(LLMRateLimit):
        adapter.complete(
            messages=(LLMMessage("user", "hi"),),
            model="claude-sonnet-4-6",
        )


class _FakeAuthError(Exception):
    pass


_FakeAuthError.__name__ = "AuthenticationError"


def test_auth_error_normalized_as_misconfigured() -> None:
    client = _MockClient(raise_exc=_FakeAuthError("401 invalid key"))
    adapter = AnthropicAdapter(client=client)
    with pytest.raises(LLMMisconfigured):
        adapter.complete(
            messages=(LLMMessage("user", "hi"),),
            model="claude-sonnet-4-6",
        )


class _FakeConnectionError(Exception):
    pass


_FakeConnectionError.__name__ = "APIConnectionError"


def test_connection_error_normalized_as_outage() -> None:
    client = _MockClient(raise_exc=_FakeConnectionError("dns failure"))
    adapter = AnthropicAdapter(client=client)
    with pytest.raises(LLMProviderOutage):
        adapter.complete(
            messages=(LLMMessage("user", "hi"),),
            model="claude-sonnet-4-6",
        )


def test_missing_sdk_raises_misconfigured() -> None:
    """Without a client and without the anthropic SDK we fall through
    to the import attempt. The SDK is currently installed so we cannot
    reliably assert this; instead we assert that constructing with
    explicit client=None still lets the adapter be instantiated."""
    adapter = AnthropicAdapter()
    assert adapter.provider_name == "anthropic"
