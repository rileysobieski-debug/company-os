"""
core/primitives/turn_cap.py — Inter-agent turn cap (§7.1)
==========================================================
Protocol-layer drift defense. Every capability (a slice of work — e.g.
"marketing-growth-plan", "finance-q2-projection") can involve multiple
inter-agent turns (manager→specialist→manager→…). When that count hits
`max_inter_agent_turns`, the next attempt must escalate to a human
rather than continue.

This module owns the bookkeeping. It is pure + deterministic — no
LLM calls, no network. A caller increments for each inter-agent turn
inside a capability; `check_turn_cap()` returns either OK or an
escalation event with the context needed to build a notify() payload.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

DEFAULT_MAX_INTER_AGENT_TURNS = 3


class TurnCapStatus(Enum):
    OK = "ok"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class TurnCapAssessment:
    """Result of a `check_turn_cap()` call."""

    status: TurnCapStatus
    capability: str
    turns_used: int
    cap: int
    reason: str = ""


@dataclass
class TurnCapLedger:
    """Per-capability turn counter. Not thread-safe; the orchestrator
    is single-threaded per capability by design (§7.1)."""

    cap: int = DEFAULT_MAX_INTER_AGENT_TURNS
    _counts: dict[str, int] = field(default_factory=dict)

    def record_turn(self, capability: str) -> int:
        """Record one inter-agent turn under `capability`. Returns the
        post-increment count."""
        if not capability:
            raise ValueError("capability must be a non-empty string")
        self._counts[capability] = self._counts.get(capability, 0) + 1
        return self._counts[capability]

    def count(self, capability: str) -> int:
        return self._counts.get(capability, 0)

    def reset(self, capability: str) -> None:
        self._counts.pop(capability, None)

    def check(self, capability: str) -> TurnCapAssessment:
        """Return the assessment without mutating state."""
        used = self._counts.get(capability, 0)
        if used >= self.cap:
            return TurnCapAssessment(
                status=TurnCapStatus.ESCALATE,
                capability=capability,
                turns_used=used,
                cap=self.cap,
                reason=(
                    f"capability {capability!r} has used {used} inter-agent "
                    f"turns, cap is {self.cap} — escalate to human"
                ),
            )
        return TurnCapAssessment(
            status=TurnCapStatus.OK,
            capability=capability,
            turns_used=used,
            cap=self.cap,
        )


def check_turn_cap(
    ledger: TurnCapLedger, capability: str
) -> TurnCapAssessment:
    """Stateless shim around `TurnCapLedger.check()` for callers that
    prefer function style."""
    return ledger.check(capability)
