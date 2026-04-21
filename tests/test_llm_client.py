"""Mock-based tests for core/llm_client.py — no real API calls.

Tests pin the interface contract that chunks 1a.5–1a.7 migrate onto:
  - LLMResponse fields and shape.
  - Exception flattening to `error`.
  - TokenLedger accumulation across calls.
  - `cost_tag` propagation from call site → ledger entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Minimal SDK-shaped fakes
# ---------------------------------------------------------------------------
@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _FakeResponse:
    content: list[Any]
    usage: _FakeUsage


class _FakeMessages:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.last_kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.messages = _FakeMessages(response)


class _RaisingClient:
    class _RaisingMessages:
        def create(self, **kwargs: Any) -> None:
            raise RuntimeError("mock failure")

    def __init__(self) -> None:
        self.messages = self._RaisingMessages()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_single_turn_returns_llm_response_with_expected_fields(monkeypatch) -> None:
    from core import llm_client

    fake = _FakeClient(_FakeResponse(
        content=[_FakeTextBlock(text="hello world")],
        usage=_FakeUsage(input_tokens=11, output_tokens=4),
    ))
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake)

    resp = llm_client.single_turn(
        messages=[{"role": "user", "content": "hi"}],
        model="claude-test",
        cost_tag="unit.test",
    )

    assert isinstance(resp, llm_client.LLMResponse)
    assert resp.model == "claude-test"
    assert resp.text == "hello world"
    assert resp.usage["input_tokens"] == 11
    assert resp.usage["output_tokens"] == 4
    assert resp.error is None
    assert resp.cost_tag == "unit.test"


def test_token_ledger_accumulates_across_calls(monkeypatch) -> None:
    from core import llm_client

    # Two sequential calls with different token counts.
    calls = iter([
        _FakeResponse(
            content=[_FakeTextBlock(text="first")],
            usage=_FakeUsage(input_tokens=100, output_tokens=50),
        ),
        _FakeResponse(
            content=[_FakeTextBlock(text="second")],
            usage=_FakeUsage(input_tokens=200, output_tokens=75),
        ),
    ])
    client = _FakeClient(next(calls))
    # Swap the response between calls by rebinding messages.create.
    responses = [
        _FakeResponse(content=[_FakeTextBlock(text="first")],
                      usage=_FakeUsage(input_tokens=100, output_tokens=50)),
        _FakeResponse(content=[_FakeTextBlock(text="second")],
                      usage=_FakeUsage(input_tokens=200, output_tokens=75)),
    ]
    call_iter = iter(responses)

    def _create(**kwargs: Any) -> _FakeResponse:
        return next(call_iter)

    client.messages.create = _create  # type: ignore[method-assign]
    monkeypatch.setattr(llm_client, "_get_client", lambda: client)

    ledger = llm_client.TokenLedger()
    r1 = llm_client.single_turn(messages=[], model="m", cost_tag="a")
    r2 = llm_client.single_turn(messages=[], model="m", cost_tag="b")
    ledger.record(r1)
    ledger.record(r2)

    assert len(ledger.entries()) == 2
    assert ledger.entries()[0].input_tokens == 100
    assert ledger.entries()[1].output_tokens == 75


def test_check_envelope_exposes_total_tokens(monkeypatch) -> None:
    from core import llm_client

    fake = _FakeClient(_FakeResponse(
        content=[_FakeTextBlock(text="x")],
        usage=_FakeUsage(input_tokens=10, output_tokens=20,
                         cache_read_input_tokens=5, cache_creation_input_tokens=3),
    ))
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake)

    ledger = llm_client.TokenLedger()
    resp = llm_client.single_turn(messages=[], model="m", cost_tag="e")
    ledger.record(resp)
    env = ledger.check_envelope()

    assert "total_tokens" in env
    assert env["total_tokens"] == 30  # 10 input + 20 output
    assert env["total_cache_read_tokens"] == 5
    assert env["total_cache_creation_tokens"] == 3
    assert env["call_count"] == 1


def test_single_turn_flattens_exceptions_to_error_field(monkeypatch) -> None:
    from core import llm_client

    monkeypatch.setattr(llm_client, "_get_client", lambda: _RaisingClient())

    resp = llm_client.single_turn(
        messages=[{"role": "user", "content": "x"}],
        model="claude-fail",
        cost_tag="unit.fail",
    )

    assert resp.error is not None
    assert "mock failure" in resp.error
    assert resp.content == []
    assert resp.text == ""
    # model and cost_tag should still be populated for accounting.
    assert resp.model == "claude-fail"
    assert resp.cost_tag == "unit.fail"


def test_ledger_appends_jsonl_line_when_log_path_is_set(monkeypatch, tmp_path) -> None:
    """Chunk 1a.9 — TokenLedger(log_path=…) writes one JSON line per record()."""
    import json as _json
    from core import llm_client

    fake = _FakeClient(_FakeResponse(
        content=[_FakeTextBlock(text="logged")],
        usage=_FakeUsage(input_tokens=7, output_tokens=3),
    ))
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake)

    log_path = tmp_path / "cost-log.jsonl"
    ledger = llm_client.TokenLedger(log_path=log_path, session_id="sess-1")
    resp = llm_client.single_turn(messages=[], model="claude-x", cost_tag="tag.log")
    ledger.record(resp)

    assert log_path.exists()
    line = log_path.read_text(encoding="utf-8").splitlines()[0]
    parsed = _json.loads(line)
    assert parsed["session_id"] == "sess-1"
    assert parsed["cost_tag"] == "tag.log"
    assert parsed["input_tokens"] == 7
    assert parsed["output_tokens"] == 3
    assert "timestamp" in parsed


def test_cost_tag_round_trips_into_ledger_entry(monkeypatch) -> None:
    from core import llm_client

    fake = _FakeClient(_FakeResponse(
        content=[_FakeTextBlock(text="tagged")],
        usage=_FakeUsage(input_tokens=1, output_tokens=1),
    ))
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake)

    ledger = llm_client.TokenLedger()
    resp = llm_client.single_turn(messages=[], model="m", cost_tag="board.debate.round1")
    entry = ledger.record(resp)

    assert entry.cost_tag == "board.debate.round1"
    assert ledger.entries()[0].cost_tag == "board.debate.round1"
