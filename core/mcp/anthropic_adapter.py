"""Anthropic concrete adapter for the MCP-shaped LLMAdapter surface.

Translates the provider-neutral request into `anthropic.Anthropic`'s
`messages.create` call and normalizes the response back. Handles the
Anthropic-specific cache-token math; stores the provider's cache
fields under `LLMResponse.extra["anthropic"]` so downstream cost
tracking can still access them without leaking back into caller code.

The SDK is imported lazily inside `complete()` so an environment
without the `anthropic` package can still import the adapter surface
and run the router's contract tests. `complete()` raises
`LLMMisconfigured` when the SDK is missing.
"""
from __future__ import annotations

from typing import Any

from core.mcp.base import (
    LLMAdapter,
    LLMMessage,
    LLMMisconfigured,
    LLMProviderOutage,
    LLMRateLimit,
    LLMResponse,
    LLMTool,
    LLMToolCall,
    LLMUsage,
)


class AnthropicAdapter(LLMAdapter):
    def __init__(self, api_key: str | None = None, *, client: Any = None) -> None:
        self._api_key = api_key
        self._client = client

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic  # type: ignore[import-untyped]
        except ImportError as exc:
            raise LLMMisconfigured(
                "anthropic SDK not installed; add `anthropic` to "
                "requirements.lock or pass an explicit client via "
                "AnthropicAdapter(client=...)",
            ) from exc
        kwargs: dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        self._client = anthropic.Anthropic(**kwargs)
        return self._client

    @staticmethod
    def _to_anthropic_tools(tools: tuple[LLMTool, ...]) -> list[dict]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    @staticmethod
    def _extract_content_and_tools(response: Any) -> tuple[str, tuple[LLMToolCall, ...]]:
        parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        for block in getattr(response, "content", []):
            block_type = getattr(block, "type", None)
            if block_type == "text":
                parts.append(getattr(block, "text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    LLMToolCall(
                        tool_name=getattr(block, "name", ""),
                        arguments=dict(getattr(block, "input", {}) or {}),
                        call_id=getattr(block, "id", ""),
                    ),
                )
        return ("".join(parts), tuple(tool_calls))

    @staticmethod
    def _extract_usage(response: Any) -> tuple[LLMUsage, dict]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return LLMUsage(prompt_tokens=0, completion_tokens=0), {}
        prompt = int(getattr(usage, "input_tokens", 0) or 0)
        completion = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        extra = {
            "anthropic": {
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
        }
        return (
            LLMUsage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                cached_tokens=cache_read,
            ),
            extra,
        )

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
        client = self._resolve_client()
        anthropic_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        # Anthropic accepts a top-level `system` parameter; if a caller
        # provided a system role in the messages list, promote it.
        resolved_system = system
        if resolved_system is None:
            for m in messages:
                if m.role == "system":
                    resolved_system = m.content
                    break

        call_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
            "temperature": temperature,
        }
        if resolved_system is not None:
            call_kwargs["system"] = resolved_system
        if tools:
            call_kwargs["tools"] = self._to_anthropic_tools(tools)
        if extra:
            # Extra kwargs let callers pass through features like
            # `extended_thinking` or custom headers without the adapter
            # needing a schema change per release.
            call_kwargs.update(extra)

        try:
            response = client.messages.create(**call_kwargs)
        except Exception as exc:
            message = str(exc)
            # Provider SDKs expose distinct exception classes, but we
            # intentionally avoid importing them to keep the adapter
            # importable without the SDK. String-shape matching on
            # the class name is the compromise.
            name = type(exc).__name__
            if "RateLimit" in name or "429" in message:
                raise LLMRateLimit(message) from exc
            if "Unauthor" in name or "Authentication" in name or "401" in message:
                raise LLMMisconfigured(message) from exc
            if "Connection" in name or "Timeout" in name or "APIStatusError" in name:
                raise LLMProviderOutage(message) from exc
            raise LLMProviderOutage(f"{name}: {message}") from exc

        content, tool_calls = self._extract_content_and_tools(response)
        usage, extra_meta = self._extract_usage(response)
        stop_reason = getattr(response, "stop_reason", "") or ""
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model=getattr(response, "model", model) or model,
            usage=usage,
            extra=extra_meta,
        )


__all__ = ["AnthropicAdapter"]
