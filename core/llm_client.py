"""
core/llm_client.py — Thin wrapper over the Anthropic client + TokenLedger
=========================================================================
Chunk 1a.2 introduces a single call site, `single_turn()`, that every
direct `client.messages.create()` caller in `core/` (onboarding, board,
meeting, orchestrator) migrates onto in chunks 1a.5–1a.7.

Benefits the wrapper buys us:
  - One place to add cost tracking, retries, or timeouts.
  - Consistent `LLMResponse` shape for all callers (content + usage + error).
  - Tests can monkeypatch `_get_client()` without touching call sites.

Non-goals for 1a.2:
  - No streaming.
  - No multi-turn tool loops. Callers that already run their own tool-
    dispatch loop (e.g. onboarding.py) keep doing that; `single_turn()`
    runs exactly one round-trip.
  - No cost-log persistence. TokenLedger holds counts in memory only;
    chunk 1a.9 wires the jsonl ledger on top.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------
@dataclass
class LLMResponse:
    """Structured return from one `single_turn()` call.

    `content` is the SDK's list of content blocks (TextBlock / ToolUseBlock /
    etc.) exactly as the Anthropic client returned them — callers that need
    tool-use parsing keep using it verbatim. `text` is a convenience field
    joining all text blocks for callers that only need the prose reply.
    `error` is populated (and `content` is empty) when the underlying client
    raised — no exception propagates out of `single_turn()`.
    """

    content: list[Any] = field(default_factory=list)
    text: str = ""
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    cost_tag: str = ""
    stop_reason: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Client factory (monkeypatched in tests)
# ---------------------------------------------------------------------------
def _get_client() -> Any:
    """Return an `anthropic.Anthropic()` instance.

    Tests monkeypatch this function to inject a mock; production callers
    use the real SDK. Imported lazily so that importing this module does
    not require `anthropic` to be installed (useful for CI jobs that only
    run the structural subset of tests).
    """
    import anthropic  # noqa: PLC0415 — intentional lazy import
    return anthropic.Anthropic()


def _extract_text(content: list[Any]) -> str:
    """Join all text blocks in the SDK response content list."""
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts)


def _extract_usage(response: Any) -> dict[str, int]:
    """Flatten the SDK `usage` object into a plain dict.

    The SDK returns a Usage object with attributes like `input_tokens`,
    `output_tokens`, and cache metrics. We coerce to int and default
    missing fields to 0 so downstream code can sum without guarding.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        ),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Global cost log (opt-in): set a path at startup and every successful
# single_turn() call appends one JSONL line. The webapp uses this to
# power the dashboard real-time spend clock.
# ---------------------------------------------------------------------------
_COST_LOG_PATH: Path | None = None


def set_cost_log_path(path: Path | None) -> None:
    """Configure the global cost-log target. Pass None to disable."""
    global _COST_LOG_PATH
    _COST_LOG_PATH = Path(path) if path else None


def get_cost_log_path() -> Path | None:
    return _COST_LOG_PATH


def _append_cost_log(response: "LLMResponse") -> None:
    """Append one JSONL line describing this call. Swallowed on error."""
    if _COST_LOG_PATH is None:
        return
    u = response.usage or {}
    line = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cost_tag": response.cost_tag or "",
        "model": response.model or "",
        "input_tokens": int(u.get("input_tokens", 0) or 0),
        "output_tokens": int(u.get("output_tokens", 0) or 0),
        "cache_read_input_tokens": int(u.get("cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens", 0) or 0),
        "error": response.error or "",
    }
    try:
        _COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _COST_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[cost-log] append failed: {exc}")


def single_turn(
    messages: list[dict[str, Any]],
    model: str,
    cost_tag: str,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
) -> LLMResponse:
    """Run one Anthropic `messages.create()` round-trip.

    Parameters mirror the SDK signature so migration from direct calls is
    mechanical. `cost_tag` is stored on the returned `LLMResponse` and on
    any TokenLedger record, letting cost accounting attribute spend to the
    call site (e.g. `"onboarding.department"`, `"board.debate.round2"`).

    Exceptions raised by the client are caught and surfaced via
    `LLMResponse.error`; callers get a consistent shape either way.
    """
    try:
        client = _get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system
        if tools is not None:
            kwargs["tools"] = tools
        response = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 — intentional: flatten to error field
        return LLMResponse(
            model=model,
            cost_tag=cost_tag,
            error=f"{type(exc).__name__}: {exc}",
        )

    content = list(getattr(response, "content", []) or [])
    result = LLMResponse(
        content=content,
        text=_extract_text(content),
        model=model,
        usage=_extract_usage(response),
        cost_tag=cost_tag,
        stop_reason=getattr(response, "stop_reason", None),
        error=None,
    )
    _append_cost_log(result)
    return result


# ---------------------------------------------------------------------------
# TokenLedger
# ---------------------------------------------------------------------------
@dataclass
class LedgerEntry:
    """One recorded dispatch — enough to reconstruct spend by cost_tag."""

    cost_tag: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class TokenLedger:
    """Accumulates token counts across `single_turn()` calls.

    In-memory by default. Chunk 1a.9 added optional jsonl persistence:
    pass `log_path` to the constructor and every `record()` call appends a
    single JSON line to that file. `session_id` is written with each entry
    so the webapp cost dashboard can filter per session.

    The log path is intentionally opt-in — tests that instantiate a ledger
    with no path do zero disk I/O, and production callers that want
    accounting flip it on at construction time.
    """

    def __init__(
        self,
        log_path: Path | None = None,
        session_id: str = "",
    ) -> None:
        self._entries: list[LedgerEntry] = []
        self._log_path = log_path
        self._session_id = session_id

    def record(self, response: LLMResponse) -> LedgerEntry:
        """Append one entry derived from an LLMResponse. Returns the entry.

        If `log_path` was set at construction, the entry is also appended
        to that file as a single JSON line with an ISO-8601 UTC timestamp.
        Disk write failures are swallowed and logged via print — the
        in-memory record still completes. Budget accounting must not
        crash the call site over a filesystem issue.
        """
        u = response.usage
        entry = LedgerEntry(
            cost_tag=response.cost_tag,
            model=response.model,
            input_tokens=int(u.get("input_tokens", 0)),
            output_tokens=int(u.get("output_tokens", 0)),
            cache_read_input_tokens=int(u.get("cache_read_input_tokens", 0)),
            cache_creation_input_tokens=int(u.get("cache_creation_input_tokens", 0)),
        )
        self._entries.append(entry)
        if self._log_path is not None:
            self._append_to_log(entry)
        return entry

    def _append_to_log(self, entry: LedgerEntry) -> None:
        """Append one JSON line to `self._log_path`. Never raises."""
        line = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self._session_id,
            "cost_tag": entry.cost_tag,
            "model": entry.model,
            "input_tokens": entry.input_tokens,
            "output_tokens": entry.output_tokens,
            "cache_read_input_tokens": entry.cache_read_input_tokens,
            "cache_creation_input_tokens": entry.cache_creation_input_tokens,
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[TokenLedger] log append failed: {exc}")

    def entries(self) -> list[LedgerEntry]:
        """Return a copy of the recorded entries (for inspection / testing)."""
        return list(self._entries)

    def check_envelope(self) -> dict[str, Any]:
        """Return a summary dict of accumulated spend.

        The returned keys are stable (callers can rely on them):
          - total_input_tokens, total_output_tokens
          - total_cache_read_tokens, total_cache_creation_tokens
          - total_tokens (sum of input + output; cache counts are informational)
          - call_count
        """
        totals = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_read_tokens": 0,
            "total_cache_creation_tokens": 0,
        }
        for e in self._entries:
            totals["total_input_tokens"] += e.input_tokens
            totals["total_output_tokens"] += e.output_tokens
            totals["total_cache_read_tokens"] += e.cache_read_input_tokens
            totals["total_cache_creation_tokens"] += e.cache_creation_input_tokens
        totals["total_tokens"] = totals["total_input_tokens"] + totals["total_output_tokens"]
        totals["call_count"] = len(self._entries)
        return totals
