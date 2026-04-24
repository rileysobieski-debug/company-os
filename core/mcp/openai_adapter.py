"""OpenAI concrete adapter for the MCP-shaped LLMAdapter surface.

Shipped alongside Anthropic so the "swap provider in under 5 minutes"
rubric criterion #5 is testable. The `openai` SDK is NOT added to
requirements.lock yet (founder decision: add real provider creds
before a live swap). Until the dep lands, `complete()` raises
`LLMMisconfigured` with a clear message; the router contract still
works and the Anthropic path is unaffected.

Schema translation notes:

    - OpenAI uses `system` as a message role, not a top-level field.
      This adapter prepends a system message when the caller provides
      `system`.
    - OpenAI tools use `{"type": "function", "function": {...}}`
      wrappers; the caller-provided `LLMTool` is provider-neutral
      and gets wrapped here.
    - Response usage is `prompt_tokens` + `completion_tokens`; no
      native cache-token field, so `cached_tokens=0`. When OpenAI
      ships prompt caching the extra dict grows a new key without
      a breaking change.
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


class OpenAIAdapter(LLMAdapter):
    def __init__(self, api_key: str | None = None, *, client: Any = None) -> None:
        self._api_key = api_key
        self._client = client

    @property
    def provider_name(self) -> str:
        return "openai"

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import openai  # type: ignore[import-untyped]
        except ImportError as exc:
            raise LLMMisconfigured(
                "openai SDK not installed; add `openai` to requirements.lock "
                "or pass an explicit client via OpenAIAdapter(client=...)",
            ) from exc
        kwargs: dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        self._client = openai.OpenAI(**kwargs)
        return self._client

    @staticmethod
    def _to_openai_tools(tools: tuple[LLMTool, ...]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def _build_messages(
        self,
        messages: tuple[LLMMessage, ...],
        system: str | None,
    ) -> list[dict]:
        out: list[dict] = []
        if system is not None:
            out.append({"role": "system", "content": system})
        for m in messages:
            out.append({"role": m.role, "content": m.content})
        return out

    @staticmethod
    def _extract_content_and_tools(choice: Any) -> tuple[str, tuple[LLMToolCall, ...]]:
        message = getattr(choice, "message", None)
        content = getattr(message, "content", "") or ""
        tool_calls: list[LLMToolCall] = []
        for call in getattr(message, "tool_calls", []) or []:
            fn = getattr(call, "function", None)
            name = getattr(fn, "name", "") if fn else ""
            raw_args = getattr(fn, "arguments", "") if fn else ""
            import json
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except json.JSONDecodeError:
                args = {"_raw_arguments": raw_args}
            tool_calls.append(
                LLMToolCall(
                    tool_name=name,
                    arguments=args,
                    call_id=getattr(call, "id", "") or "",
                ),
            )
        return (content, tuple(tool_calls))

    @staticmethod
    def _extract_usage(response: Any) -> tuple[LLMUsage, dict]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return LLMUsage(prompt_tokens=0, completion_tokens=0), {}
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        fingerprint = getattr(response, "system_fingerprint", None)
        extra = {"openai": {"system_fingerprint": fingerprint}} if fingerprint else {}
        return LLMUsage(prompt_tokens=prompt, completion_tokens=completion), extra

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
        call_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._build_messages(messages, system),
            "temperature": temperature,
        }
        if tools:
            call_kwargs["tools"] = self._to_openai_tools(tools)
        if extra:
            call_kwargs.update(extra)

        try:
            response = client.chat.completions.create(**call_kwargs)
        except Exception as exc:
            message = str(exc)
            name = type(exc).__name__
            if "RateLimit" in name or "429" in message:
                raise LLMRateLimit(message) from exc
            if "Authentication" in name or "401" in message:
                raise LLMMisconfigured(message) from exc
            if "Connection" in name or "Timeout" in name or "APIError" in name:
                raise LLMProviderOutage(message) from exc
            raise LLMProviderOutage(f"{name}: {message}") from exc

        choices = getattr(response, "choices", [])
        if not choices:
            return LLMResponse(
                content="",
                tool_calls=(),
                stop_reason="",
                model=getattr(response, "model", model) or model,
                usage=LLMUsage(prompt_tokens=0, completion_tokens=0),
                extra={},
            )
        content, tool_calls = self._extract_content_and_tools(choices[0])
        usage, extra_meta = self._extract_usage(response)
        stop_reason = getattr(choices[0], "finish_reason", "") or ""
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model=getattr(response, "model", model) or model,
            usage=usage,
            extra=extra_meta,
        )


__all__ = ["OpenAIAdapter"]
