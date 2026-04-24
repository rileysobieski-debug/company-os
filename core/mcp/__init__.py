"""Provider-neutral LLM adapter layer.

Reviewer flag (Grok + Gemini rounds 1 through 3): the chassis was
anthropic-locked. This package is the v6 resolution: every LLM call
goes through `LLMAdapter.complete()` with a normalized
request/response shape. Swapping provider becomes a model-string edit
(e.g. `"anthropic/claude-sonnet-4-6"` -> `"openai/gpt-4o"`) with no
caller changes.

Public surface:

    from core.mcp import (
        AnthropicAdapter, OpenAIAdapter, OllamaAdapter,
        LLMAdapter, LLMMessage, LLMResponse, LLMTool, LLMUsage,
        LLMError, LLMRateLimit, LLMProviderOutage, LLMMisconfigured,
    )
    from core.mcp.router import complete, create, known_providers

The existing `core.llm_client.single_turn()` wrapper is untouched in
this PR; a follow-up lands the migration to route every existing
call site through the adapter. That migration is scoped alongside the
first actual multi-provider tenant onboarding (Weeks 6-7 live-swap
test in the plan).
"""
from __future__ import annotations

from core.mcp.anthropic_adapter import AnthropicAdapter
from core.mcp.base import (
    LLMAdapter,
    LLMError,
    LLMMessage,
    LLMMisconfigured,
    LLMProviderOutage,
    LLMRateLimit,
    LLMResponse,
    LLMTool,
    LLMToolCall,
    LLMUsage,
    Role,
)
from core.mcp.ollama_adapter import OllamaAdapter
from core.mcp.openai_adapter import OpenAIAdapter

__all__ = [
    "AnthropicAdapter",
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
    "OllamaAdapter",
    "OpenAIAdapter",
    "Role",
]
