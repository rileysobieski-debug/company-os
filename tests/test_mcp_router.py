"""MCP router tests: provider-prefix dispatch + register/unregister."""
from __future__ import annotations

import pytest

from core.mcp import AnthropicAdapter, OllamaAdapter, OpenAIAdapter
from core.mcp.base import (
    LLMAdapter,
    LLMMessage,
    LLMMisconfigured,
    LLMResponse,
    LLMUsage,
)
from core.mcp.router import (
    complete,
    create,
    known_providers,
    register_provider,
    strip_prefix,
    unregister_provider,
)


class _StubAdapter(LLMAdapter):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def provider_name(self) -> str:
        return "stub"

    def complete(
        self,
        *,
        messages: tuple[LLMMessage, ...],
        model: str,
        max_tokens: int = 1024,
        system: str | None = None,
        tools=(),
        temperature: float = 0.0,
        extra=None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "model": model, "system": system})
        return LLMResponse(
            content="stub-ok",
            tool_calls=(),
            stop_reason="end",
            model=model,
            usage=LLMUsage(prompt_tokens=1, completion_tokens=2),
            extra={},
        )


@pytest.fixture
def stub_registered():
    stub = _StubAdapter()
    register_provider("stub", lambda: stub)
    try:
        yield stub
    finally:
        unregister_provider("stub")


def test_default_providers_registered() -> None:
    assert "anthropic" in known_providers()
    assert "openai" in known_providers()
    assert "ollama" in known_providers()


def test_create_anthropic_adapter() -> None:
    adapter = create("anthropic/claude-sonnet-4-6")
    assert isinstance(adapter, AnthropicAdapter)
    assert adapter.provider_name == "anthropic"


def test_create_openai_adapter() -> None:
    adapter = create("openai/gpt-4o")
    assert isinstance(adapter, OpenAIAdapter)
    assert adapter.provider_name == "openai"


def test_create_ollama_adapter() -> None:
    adapter = create("ollama/llama4")
    assert isinstance(adapter, OllamaAdapter)
    assert adapter.provider_name == "ollama"


def test_unknown_provider_raises_misconfigured() -> None:
    with pytest.raises(LLMMisconfigured):
        create("martian/model-v1")


def test_model_without_prefix_raises() -> None:
    with pytest.raises(LLMMisconfigured):
        create("claude-sonnet-4-6")


def test_strip_prefix_returns_tail() -> None:
    assert strip_prefix("anthropic/claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert strip_prefix("openai/gpt-4o") == "gpt-4o"
    assert strip_prefix("ollama/llama4:70b") == "llama4:70b"


def test_register_provider_adds_new_prefix(stub_registered) -> None:
    assert "stub" in known_providers()
    adapter = create("stub/model-x")
    assert adapter.provider_name == "stub"


def test_unregister_provider_removes_prefix() -> None:
    register_provider("temporary", lambda: _StubAdapter())
    assert "temporary" in known_providers()
    unregister_provider("temporary")
    assert "temporary" not in known_providers()


def test_complete_dispatches_to_registered_adapter(stub_registered) -> None:
    resp = complete(
        model="stub/model-x",
        messages=(LLMMessage(role="user", content="hi"),),
        max_tokens=64,
        system="act as an agent",
    )
    assert resp.content == "stub-ok"
    assert len(stub_registered.calls) == 1
    # The adapter sees the stripped model string, not the prefixed one.
    assert stub_registered.calls[0]["model"] == "model-x"
    assert stub_registered.calls[0]["system"] == "act as an agent"


def test_swap_provider_by_string_edit(stub_registered) -> None:
    """Rubric criterion #5 dress rehearsal. The caller does not change;
    only the model string flips between provider prefixes."""
    register_provider("stub2", lambda: _StubAdapter())
    try:
        r1 = complete(
            model="stub/x",
            messages=(LLMMessage("user", "q"),),
            max_tokens=16,
        )
        r2 = complete(
            model="stub2/x",
            messages=(LLMMessage("user", "q"),),
            max_tokens=16,
        )
        assert r1.content == "stub-ok"
        assert r2.content == "stub-ok"
    finally:
        unregister_provider("stub2")
