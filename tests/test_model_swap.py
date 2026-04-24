"""Model-sovereignty swap harness.

Graduated from the Week 1 xfail-strict harness now that the MCP
adapter layer lands. Runs the same synthetic prompt through two
provider adapters and confirms:

    1. The router resolves both prefixes to concrete adapters.
    2. Both adapters produce a response of the same `LLMResponse`
       shape (rubric criterion #5: output shape parity across
       providers).
    3. Swapping provider is a single-string edit at the call site;
       no caller code changes.

The adapters here are mocked so the test runs in CI without any LLM
credentials. The real live-swap test against Anthropic + a local
Ollama daemon is an operator runbook (docs/runbook-model-swap.md,
shipping alongside Weeks 6-7 live-swap work).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.mcp.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    LLMUsage,
)
from core.mcp.router import (
    complete,
    known_providers,
    register_provider,
    unregister_provider,
)


class _FixedResponseAdapter(LLMAdapter):
    def __init__(self, provider: str, content: str) -> None:
        self._provider = provider
        self._content = content

    @property
    def provider_name(self) -> str:
        return self._provider

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
        return LLMResponse(
            content=self._content,
            tool_calls=(),
            stop_reason="end",
            model=model,
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
            extra={self._provider: {"mocked": True}},
        )


@pytest.fixture
def two_providers():
    register_provider("anthro_mock", lambda: _FixedResponseAdapter("anthro_mock", "hello from anthropic"))
    register_provider("local_mock", lambda: _FixedResponseAdapter("local_mock", "hello from llama"))
    try:
        yield
    finally:
        unregister_provider("anthro_mock")
        unregister_provider("local_mock")


def test_default_providers_cover_the_required_vendors() -> None:
    # Rubric criterion #5 requires at minimum one paid provider + one
    # local. Anthropic + Ollama satisfy that.
    assert "anthropic" in known_providers()
    assert "ollama" in known_providers()
    assert "openai" in known_providers()


def test_swap_by_string_edit(two_providers) -> None:
    """The entire swap contract: change the `model=` string, nothing
    else, and the call routes to a different provider."""
    messages = (LLMMessage("user", "ping"),)

    from_a = complete(model="anthro_mock/test-model", messages=messages)
    from_b = complete(model="local_mock/test-model", messages=messages)

    assert from_a.content == "hello from anthropic"
    assert from_b.content == "hello from llama"


def test_output_shape_is_identical_across_providers(two_providers) -> None:
    """Rubric criterion #5 `Output shape parity`: the LLMResponse shape
    is the same regardless of provider. Downstream cost tracking,
    citation handling, and tool-call routing all key off this."""
    messages = (LLMMessage("user", "ping"),)
    a = complete(model="anthro_mock/x", messages=messages)
    b = complete(model="local_mock/x", messages=messages)

    # Same fields.
    assert set(a.to_dict().keys()) == set(b.to_dict().keys())
    # Same types on every field.
    for key in a.to_dict():
        assert type(getattr(a, key)) is type(getattr(b, key)), (
            f"field {key}: {type(getattr(a, key))} vs {type(getattr(b, key))}"
        )


def test_usage_is_normalized_across_providers(two_providers) -> None:
    messages = (LLMMessage("user", "ping"),)
    a = complete(model="anthro_mock/x", messages=messages)
    b = complete(model="local_mock/x", messages=messages)
    # Both expose the same three usage fields.
    for resp in (a, b):
        assert hasattr(resp.usage, "prompt_tokens")
        assert hasattr(resp.usage, "completion_tokens")
        assert hasattr(resp.usage, "cached_tokens")


def test_provider_specific_fields_live_in_extra_not_top_level(two_providers) -> None:
    """Provider-specific signals must not leak into the top-level
    LLMResponse shape; they live in `extra[<provider>]` under stable
    keys so downstream code can read them when it wants to and ignore
    them otherwise."""
    messages = (LLMMessage("user", "ping"),)
    a = complete(model="anthro_mock/x", messages=messages)
    b = complete(model="local_mock/x", messages=messages)
    # Each adapter's extra dict is keyed by its provider_name so two
    # responses can co-exist in a cost log without colliding.
    assert "anthro_mock" in a.extra
    assert "local_mock" in b.extra
    assert "local_mock" not in a.extra
