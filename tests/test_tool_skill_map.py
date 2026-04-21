"""Tool → skill translation primitive (Phase 9.4a — §13 Phase 9)."""
from __future__ import annotations

import pytest

from core.primitives.tool_skill_map import (
    DEFAULT_RETAINED_TOOLS,
    ToolSkillTranslation,
    translate_tools_to_skills,
)
from core.skill_registry import SkillSpec


def _spec(skill_id: str, *tools: str) -> SkillSpec:
    return SkillSpec(skill_id=skill_id, description="", mode="agentic", tools=tools)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------
def test_single_skill_covers_all_declared_tools() -> None:
    specs = [_spec("web-researcher", "WebSearch", "WebFetch")]
    t = translate_tools_to_skills(
        ["WebSearch", "WebFetch", "Agent"], specs,
    )
    assert t.granted_skills == ("web-researcher",)
    assert set(t.dropped_tools) == {"WebSearch", "WebFetch"}
    assert t.retained_tools == ("Agent",)
    assert t.is_clean


def test_skill_requires_subset_not_covered_is_dropped() -> None:
    """A skill whose tool requirements are NOT a subset of declared tools is
    skipped (not granted)."""
    specs = [_spec("web-researcher", "WebSearch", "WebFetch")]
    t = translate_tools_to_skills(["WebSearch", "Agent"], specs)
    # WebFetch is missing — web-researcher not grantable.
    assert t.granted_skills == ()
    assert "WebSearch" in t.coverage_gaps


def test_multiple_skills_contribute_to_coverage() -> None:
    specs = [
        _spec("file-fetcher", "Read", "Glob", "Grep"),
        _spec("doc-writer", "Read", "Write", "Edit"),
    ]
    t = translate_tools_to_skills(
        ["Read", "Glob", "Grep", "Write", "Edit", "Agent"], specs,
    )
    assert set(t.granted_skills) == {"file-fetcher", "doc-writer"}
    assert set(t.dropped_tools) == {"Read", "Glob", "Grep", "Write", "Edit"}
    assert t.is_clean


def test_uncovered_tool_flagged_as_gap() -> None:
    specs = [_spec("file-fetcher", "Read", "Glob", "Grep")]
    t = translate_tools_to_skills(
        ["Read", "Glob", "Grep", "Bash", "Agent"], specs,
    )
    assert "Bash" in t.coverage_gaps
    assert not t.is_clean


def test_agent_retained_by_default() -> None:
    specs = [_spec("web-researcher", "WebSearch")]
    t = translate_tools_to_skills(["WebSearch", "Agent"], specs)
    assert "Agent" in t.retained_tools
    assert "Agent" not in t.dropped_tools


def test_custom_always_retain_list() -> None:
    specs = [_spec("web-researcher", "WebSearch")]
    t = translate_tools_to_skills(
        ["WebSearch", "Agent", "Bash"], specs,
        always_retain=["Agent", "Bash"],
    )
    assert set(t.retained_tools) == {"Agent", "Bash"}
    # Bash is retained → not a gap.
    assert "Bash" not in t.coverage_gaps


def test_skill_with_no_tool_requirements_is_always_granted() -> None:
    """Pure skills (no tools) should be grantable regardless of declared tools."""
    specs = [_spec("pure-skill")]  # empty tools
    t = translate_tools_to_skills(["Agent"], specs)
    assert "pure-skill" in t.granted_skills


def test_empty_declared_tools_yields_no_grants() -> None:
    specs = [_spec("web-researcher", "WebSearch")]
    t = translate_tools_to_skills([], specs)
    assert t.granted_skills == ()
    assert t.retained_tools == ()
    assert t.dropped_tools == ()
    assert t.coverage_gaps == ()


def test_duplicate_declared_tools_deduplicated() -> None:
    specs = [_spec("file-fetcher", "Read")]
    t = translate_tools_to_skills(
        ["Read", "Read", "Agent", "Agent"], specs,
    )
    # Retained list has Agent once; dropped has Read once.
    assert t.retained_tools == ("Agent",)
    assert t.dropped_tools == ("Read",)


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def test_report_mentions_granted_retained_dropped() -> None:
    specs = [_spec("web-researcher", "WebSearch", "WebFetch")]
    t = translate_tools_to_skills(
        ["WebSearch", "WebFetch", "Agent"], specs,
    )
    report = t.as_report()
    assert "web-researcher" in report
    assert "Agent" in report
    assert "WebSearch" in report
    assert "WebFetch" in report
    assert "clean" in report.lower()


def test_report_highlights_gaps() -> None:
    specs = [_spec("web-researcher", "WebSearch")]
    t = translate_tools_to_skills(
        ["WebSearch", "Bash"], specs,
    )
    report = t.as_report()
    assert "GAPS" in report
    assert "Bash" in report


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
def test_default_retained_tools_constant() -> None:
    assert "Agent" in DEFAULT_RETAINED_TOOLS


# ---------------------------------------------------------------------------
# Real migration — Old Press copywriter
# ---------------------------------------------------------------------------
def test_copywriter_declared_tools_translate_cleanly() -> None:
    """Copywriter has [Read, Glob, Write, Agent]. file-fetcher covers
    Read+Glob+Grep; doc-writer covers Read+Write+Edit. Copywriter should
    get doc-writer granted. file-fetcher is NOT grantable because copywriter
    lacks Grep. Read must be reported as gap OR covered by doc-writer alone."""
    specs = [
        _spec("file-fetcher", "Read", "Glob", "Grep"),
        _spec("doc-writer", "Read", "Write", "Edit"),
    ]
    t = translate_tools_to_skills(
        ["Read", "Glob", "Write", "Agent"], specs,
    )
    # file-fetcher not grantable (needs Grep); doc-writer not grantable
    # either (needs Edit). No skills cover this specialist.
    assert t.granted_skills == ()
    assert set(t.coverage_gaps) == {"Read", "Glob", "Write"}


def test_market_researcher_declared_tools_translate_cleanly() -> None:
    """market-researcher declares [Read, Glob, WebSearch, WebFetch, Write, Agent].
    web-researcher covers WebSearch+WebFetch. Read/Glob/Write fall through
    to gaps unless we add skills covering them — which we would in a real
    migration. This test documents the expected behavior."""
    specs = [
        _spec("web-researcher", "WebSearch", "WebFetch"),
    ]
    t = translate_tools_to_skills(
        ["Read", "Glob", "WebSearch", "WebFetch", "Write", "Agent"], specs,
    )
    assert "web-researcher" in t.granted_skills
    assert {"WebSearch", "WebFetch"}.issubset(set(t.dropped_tools))
    assert set(t.coverage_gaps) >= {"Read", "Glob", "Write"}
