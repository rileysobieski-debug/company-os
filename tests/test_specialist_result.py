"""Chunk 1b.4 — SpecialistResult typed attribution.

No SDK calls; we fabricate tool-use blocks that match the shapes the SDK
emits and pass them through `_extract_specialist_attribution`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _ToolUseBlock:
    """Minimal shape mirroring SDK `ToolUseBlock.input`."""
    name: str
    input: dict[str, Any]


def test_attribution_captures_subagent_type_key() -> None:
    from core.managers.base import SpecialistResult, _extract_specialist_attribution
    block = _ToolUseBlock(name="Agent", input={"subagent_type": "data-scientist"})
    result = _extract_specialist_attribution(block)
    assert isinstance(result, SpecialistResult)
    assert result.name == "data-scientist"
    assert result.attribution_source == "subagent_type"


def test_specialist_result_carries_tools_used_list_for_phase7() -> None:
    """Placeholder tools_used list — populated by Phase 7; default empty."""
    from core.managers.base import SpecialistResult
    r = SpecialistResult(name="x", attribution_source="name")
    assert isinstance(r.tools_used, list)
    assert r.tools_used == []
    r.tools_used.append({"name": "Read", "input": {"path": "a"}})
    assert len(r.tools_used) == 1


def test_attribution_falls_through_to_agent_key_when_subagent_type_absent() -> None:
    """If `subagent_type` is missing but `agent` is present, we still
    return a populated name — the old heuristic handled this but
    SpecialistResult must surface which key supplied the value."""
    from core.managers.base import _extract_specialist_attribution
    block = _ToolUseBlock(name="Agent", input={"agent": "marketing-analyst"})
    result = _extract_specialist_attribution(block)
    assert result.name == "marketing-analyst"
    assert result.attribution_source == "agent"


def test_attribution_unknown_when_all_three_keys_are_missing() -> None:
    """Empty / malformed payloads produce attribution_source='unknown',
    NOT a silent `None`. Phase 9 entry gate — the unknown signal is
    observable and can be treated as `needs_founder_review`."""
    from core.managers.base import _extract_specialist_attribution
    block = _ToolUseBlock(name="Agent", input={"some_other_field": "noise"})
    result = _extract_specialist_attribution(block)
    assert result.name == "<unknown>"
    assert result.attribution_source == "unknown"


def test_manager_result_specialists_called_is_iterable_list_of_specialist_result() -> None:
    from core.managers.base import ManagerResult, SpecialistResult
    mr = ManagerResult(manager_name="mktg", brief="b", final_text="t")
    mr.specialists_called.append(
        SpecialistResult(name="a", attribution_source="subagent_type")
    )
    mr.specialists_called.append(
        SpecialistResult(name="b", attribution_source="name")
    )
    names = [s.name for s in mr.specialists_called]
    assert names == ["a", "b"]
    # str() coercion at string boundaries still works via __str__.
    assert ", ".join(str(s) for s in mr.specialists_called) == "a, b"
