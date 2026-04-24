"""Provider-neutral LLM adapter contract.

The chassis does not let call sites import provider SDKs directly.
Every LLM call goes through `LLMAdapter.complete()` and receives a
normalized `LLMResponse`. This is the v6 answer to both reviewers'
"Anthropic lock-in" concern: a new provider is a new adapter class,
not a rewrite of caller code.

Contract design principles:

    1. Normalized request shape. `LLMMessage` carries only role +
       content; provider-specific extras go through the adapter's
       kwargs and never leak into caller code.

    2. Normalized response shape. `LLMResponse.content` is a plain
       string, `tool_calls` a tuple of `LLMToolCall`, usage fields are
       standard (prompt_tokens / completion_tokens / cached_tokens).
       Provider-specific signals (Anthropic `cache_creation_tokens`,
       OpenAI `system_fingerprint`, Gemini grounding metadata) land
       in `LLMResponse.extra` under stable keys.

    3. Model string carries provider. `"anthropic/claude-sonnet-4-6"`,
       `"openai/gpt-4o-2025-05"`, `"gemini/gemini-2.5-pro"`,
       `"ollama/llama4"`. The `router.create()` helper picks the
       adapter by prefix and strips it before calling the SDK.

    4. Tool-calling is unified. Adapters accept a provider-neutral
       `LLMTool` list and translate to the provider's native shape at
       call time (Anthropic `tools`, OpenAI `functions`, Gemini
       `function_declarations`).

    5. Errors are normalized. `LLMError` subclasses cover rate limit,
       provider outage, and misconfiguration without leaking the
       provider SDK's exception hierarchy.

Signing, caching, and budget enforcement live above this layer in
`core.governance.evaluator`; the adapter itself is transport-only.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Role = Literal["system", "user", "assistant"]


class LLMError(RuntimeError):
    """Base class for every LLM call failure. Subclasses normalize the
    specific error category across providers."""


class LLMRateLimit(LLMError):
    """The provider refused with a 429 / rate-limit signal. Caller
    should back off and retry."""


class LLMProviderOutage(LLMError):
    """The provider returned 5xx or the network was unreachable.
    Caller should fail the request but keep the circuit breaker data
    so subsequent requests can short-circuit without hammering the
    provider."""


class LLMMisconfigured(LLMError):
    """Credentials missing, invalid model id, unsupported feature, etc.
    Caller should surface to the operator; retry does not help."""


@dataclass(frozen=True)
class LLMMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class LLMTool:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass(frozen=True)
class LLMToolCall:
    tool_name: str
    arguments: dict
    call_id: str = ""


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: tuple[LLMToolCall, ...]
    stop_reason: str
    model: str
    usage: LLMUsage
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tool_calls"] = [asdict(tc) for tc in self.tool_calls]
        d["usage"] = asdict(self.usage)
        return d


class LLMAdapter(ABC):
    """Provider-neutral LLM call surface. Every concrete adapter
    (Anthropic, OpenAI, Gemini, Ollama) implements `complete`."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable slug used for logging and cost attribution."""

    @abstractmethod
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
        """Issue a single completion call and return a normalized
        response. Raise an `LLMError` subclass on failure."""

    def supports_tools(self) -> bool:
        return True

    def supports_system_role(self) -> bool:
        return True


__all__ = [
    "LLMAdapter",
    "LLMError",
    "LLMMessage",
    "LLMMisconfigured",
    "LLMProviderOutage",
    "LLMRateLimit",
    "LLMResponse",
    "LLMTool",
    "LLMToolCall",
    "LLMUsage",
    "Role",
]
