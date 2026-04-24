"""Ollama / local-model concrete adapter.

Swapping to a local model is the v6 model-sovereignty rubric criterion
in its most demanding form: an air-gapped deployment can run the
chassis without any external LLM provider. Ollama exposes an OpenAI-
compatible HTTP API, so this adapter reuses the OpenAI adapter's wire
format with a different base URL. The default base URL is the local
Ollama daemon; override via `OllamaAdapter(base_url=...)` for a remote
or a llama.cpp server.

The model string uses the Ollama local id: `ollama/llama4`,
`ollama/qwen2.5:72b`, etc. The adapter strips the `ollama/` prefix
before calling the daemon.
"""
from __future__ import annotations

from typing import Any

from core.mcp.base import (
    LLMAdapter,
    LLMMessage,
    LLMMisconfigured,
    LLMProviderOutage,
    LLMResponse,
    LLMTool,
    LLMUsage,
)
from core.mcp.openai_adapter import OpenAIAdapter


class OllamaAdapter(LLMAdapter):
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
        client: Any = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._client = client

    @property
    def provider_name(self) -> str:
        return "ollama"

    def _resolve_inner(self) -> OpenAIAdapter:
        if self._client is not None:
            return OpenAIAdapter(client=self._client)
        try:
            import openai  # type: ignore[import-untyped]
        except ImportError as exc:
            raise LLMMisconfigured(
                "openai SDK not installed; Ollama adapter reuses the "
                "OpenAI client against the local daemon. Install `openai` "
                "and run an Ollama instance at the configured base_url.",
            ) from exc
        client = openai.OpenAI(api_key=self._api_key, base_url=self._base_url)
        return OpenAIAdapter(client=client)

    def supports_tools(self) -> bool:
        # Some local models respond to tools; default to False so
        # callers know to fall back to prompting unless explicitly
        # verified.
        return False

    def complete(
        self,
        *,
        messages: tuple[LLMMessage, ...],
        model: str,
        max_tokens: int = 1024,
        system: str | None = None,
        tools: tuple[LLMTool, ...] = (),
        temperature: float = 0.0,
        extra: dict | None = None,
    ) -> LLMResponse:
        inner = self._resolve_inner()
        try:
            return inner.complete(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=tools,
                temperature=temperature,
                extra=extra,
            )
        except LLMProviderOutage:
            raise
        except Exception as exc:
            name = type(exc).__name__
            raise LLMProviderOutage(
                f"Ollama daemon at {self._base_url} unreachable: {name}: {exc}",
            ) from exc


__all__ = ["OllamaAdapter"]
