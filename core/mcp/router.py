"""Model-string router: picks an adapter from a `provider/model` prefix.

Caller pattern:

    adapter = router.create("anthropic/claude-sonnet-4-6")
    response = adapter.complete(
        messages=(LLMMessage("user", "..."),),
        model=router.strip_prefix("anthropic/claude-sonnet-4-6"),
        max_tokens=1024,
    )

Or in one call via `router.complete(...)`:

    response = router.complete(
        model="anthropic/claude-sonnet-4-6",
        messages=(LLMMessage("user", "..."),),
    )

Swapping the full engine to a different provider is a single string
edit: `anthropic/...` -> `openai/...` -> `ollama/...`. The cost
ledger, evaluator, and caller code do not change because every
adapter returns the same normalized `LLMResponse`.
"""
from __future__ import annotations

from typing import Callable

from core.mcp.anthropic_adapter import AnthropicAdapter
from core.mcp.base import (
    LLMAdapter,
    LLMMessage,
    LLMMisconfigured,
    LLMResponse,
    LLMTool,
)
from core.mcp.ollama_adapter import OllamaAdapter
from core.mcp.openai_adapter import OpenAIAdapter


AdapterFactory = Callable[[], LLMAdapter]


_FACTORIES: dict[str, AdapterFactory] = {
    "anthropic": lambda: AnthropicAdapter(),
    "openai": lambda: OpenAIAdapter(),
    "ollama": lambda: OllamaAdapter(),
}


def register_provider(prefix: str, factory: AdapterFactory) -> None:
    """Register a new provider prefix at runtime. Tests use this to
    swap in mock adapters; production deployments can add a new
    provider without editing the router."""
    _FACTORIES[prefix] = factory


def unregister_provider(prefix: str) -> None:
    _FACTORIES.pop(prefix, None)


def known_providers() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES.keys()))


def _split(model: str) -> tuple[str, str]:
    if "/" not in model:
        raise LLMMisconfigured(
            f"model string {model!r} lacks a provider prefix; "
            f"expected one of {known_providers()} followed by '/<model>'",
        )
    provider, _, tail = model.partition("/")
    return provider, tail


def create(model: str) -> LLMAdapter:
    provider, _ = _split(model)
    factory = _FACTORIES.get(provider)
    if factory is None:
        raise LLMMisconfigured(
            f"unknown provider prefix {provider!r} in model {model!r}; "
            f"registered providers: {known_providers()}",
        )
    return factory()


def strip_prefix(model: str) -> str:
    _, tail = _split(model)
    return tail


def complete(
    *,
    model: str,
    messages: tuple[LLMMessage, ...],
    max_tokens: int = 1024,
    system: str | None = None,
    tools: tuple[LLMTool, ...] = (),
    temperature: float = 0.0,
    extra: dict | None = None,
) -> LLMResponse:
    """Convenience single-call shape. Picks an adapter, strips the
    prefix, and issues the call."""
    adapter = create(model)
    bare_model = strip_prefix(model)
    return adapter.complete(
        messages=messages,
        model=bare_model,
        max_tokens=max_tokens,
        system=system,
        tools=tools,
        temperature=temperature,
        extra=extra,
    )


__all__ = [
    "AdapterFactory",
    "complete",
    "create",
    "known_providers",
    "register_provider",
    "strip_prefix",
    "unregister_provider",
]
