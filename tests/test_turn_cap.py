"""Turn cap primitive (Phase 5.1 — §7.1)."""
from __future__ import annotations

import pytest

from core.primitives.turn_cap import (
    DEFAULT_MAX_INTER_AGENT_TURNS,
    TurnCapLedger,
    TurnCapStatus,
    check_turn_cap,
)


def test_default_cap_is_three() -> None:
    """§7.5: default max_inter_agent_turns: 3."""
    assert DEFAULT_MAX_INTER_AGENT_TURNS == 3


def test_fresh_capability_is_ok() -> None:
    ledger = TurnCapLedger()
    result = ledger.check("marketing-growth-plan")
    assert result.status is TurnCapStatus.OK
    assert result.turns_used == 0


def test_under_cap_stays_ok() -> None:
    ledger = TurnCapLedger()
    ledger.record_turn("x")
    ledger.record_turn("x")
    assert ledger.check("x").status is TurnCapStatus.OK


def test_at_cap_escalates() -> None:
    ledger = TurnCapLedger()
    for _ in range(3):
        ledger.record_turn("x")
    result = ledger.check("x")
    assert result.status is TurnCapStatus.ESCALATE
    assert "cap is 3" in result.reason
    assert "escalate to human" in result.reason


def test_capabilities_are_independent() -> None:
    ledger = TurnCapLedger()
    for _ in range(3):
        ledger.record_turn("a")
    ledger.record_turn("b")
    assert ledger.check("a").status is TurnCapStatus.ESCALATE
    assert ledger.check("b").status is TurnCapStatus.OK


def test_reset_clears_count() -> None:
    ledger = TurnCapLedger()
    for _ in range(3):
        ledger.record_turn("x")
    ledger.reset("x")
    assert ledger.check("x").status is TurnCapStatus.OK
    assert ledger.count("x") == 0


def test_custom_cap() -> None:
    ledger = TurnCapLedger(cap=1)
    ledger.record_turn("x")
    assert ledger.check("x").status is TurnCapStatus.ESCALATE


def test_record_turn_rejects_empty_capability() -> None:
    ledger = TurnCapLedger()
    with pytest.raises(ValueError):
        ledger.record_turn("")


def test_check_turn_cap_function_shim() -> None:
    ledger = TurnCapLedger()
    for _ in range(3):
        ledger.record_turn("x")
    result = check_turn_cap(ledger, "x")
    assert result.status is TurnCapStatus.ESCALATE
    assert result.capability == "x"
