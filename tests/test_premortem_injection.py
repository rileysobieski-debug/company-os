"""Pre-mortem injection primitive (Phase 8.3 — §5.2 / §0.5)."""
from __future__ import annotations

from core.onboarding.premortem import (
    ADVERSARY_GUARD_SENTENCE,
    PREMORTEM_END_MARKER,
    PREMORTEM_MARKER,
    SYNTHESIS_GUARD_SENTENCE,
    PremortemContext,
    inject_premortem_context,
    is_premortem_injected,
    load_premortem_from_profile,
    strip_premortem_injection,
)


_PROFILE_MD = """# Founder Profile

**Role:** sole founder
**Bandwidth:** 5-10 hours per week

## Background

Wine industry veteran.

## Settled convictions (DO NOT RE-EXAMINE)

- Coastal Maine is the operational base

## Pre-mortem

_Load-bearing per §0.5: this text is injected into every cross-dept
synthesis and adversary activation._

> Ran out of cash before first vintage; W-2 fell through.

## Other section

Not relevant.
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_load_premortem_extracts_cause_from_blockquote() -> None:
    ctx = load_premortem_from_profile(_PROFILE_MD)
    assert ctx is not None
    assert ctx.cause.startswith("Ran out of cash")
    assert "W-2 fell through" in ctx.cause


def test_load_premortem_skips_italic_meta_annotation() -> None:
    ctx = load_premortem_from_profile(_PROFILE_MD)
    assert ctx is not None
    assert "Load-bearing" not in ctx.cause


def test_load_premortem_stops_at_next_h2_section() -> None:
    ctx = load_premortem_from_profile(_PROFILE_MD)
    assert ctx is not None
    assert "Not relevant" not in ctx.cause
    assert "Other section" not in ctx.cause


def test_load_premortem_returns_none_when_section_absent() -> None:
    md = "# Founder Profile\n\n## Background\n\nFounder has experience."
    assert load_premortem_from_profile(md) is None


def test_load_premortem_returns_none_when_section_empty() -> None:
    md = "# Founder Profile\n\n## Pre-mortem\n\n## Next\n"
    assert load_premortem_from_profile(md) is None


def test_load_premortem_records_source_path() -> None:
    ctx = load_premortem_from_profile(_PROFILE_MD, source_path="founder_profile.md")
    assert ctx is not None
    assert ctx.source_path == "founder_profile.md"


def test_load_premortem_handles_plain_paragraph_not_blockquoted() -> None:
    """Some edits may strip the '>' — the parser should still catch it."""
    md = (
        "# Founder Profile\n\n"
        "## Pre-mortem\n\n"
        "Ran out of runway in month 8.\n\n"
        "## Next\n"
    )
    ctx = load_premortem_from_profile(md)
    assert ctx is not None
    assert ctx.cause == "Ran out of runway in month 8."


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------
def _ctx() -> PremortemContext:
    return PremortemContext(cause="Ran out of cash before first vintage.")


def test_inject_synthesis_prepends_block_with_marker() -> None:
    body = "Synthesize a Q3 marketing plan."
    injected = inject_premortem_context(body, _ctx(), kind="synthesis")
    assert injected.startswith(PREMORTEM_MARKER)
    assert PREMORTEM_END_MARKER in injected
    assert SYNTHESIS_GUARD_SENTENCE in injected
    assert body in injected


def test_inject_adversary_uses_adversary_guard() -> None:
    body = "Review the founder's quarterly plan."
    injected = inject_premortem_context(body, _ctx(), kind="adversary")
    assert ADVERSARY_GUARD_SENTENCE in injected
    assert SYNTHESIS_GUARD_SENTENCE not in injected


def test_inject_includes_cause_text() -> None:
    body = "Some brief."
    injected = inject_premortem_context(body, _ctx())
    assert "Ran out of cash before first vintage." in injected


def test_inject_is_idempotent() -> None:
    body = "Brief."
    once = inject_premortem_context(body, _ctx())
    twice = inject_premortem_context(once, _ctx())
    assert once == twice
    # Only one marker, no doubling.
    assert twice.count(PREMORTEM_MARKER) == 1


def test_inject_with_none_premortem_is_noop() -> None:
    body = "Brief."
    assert inject_premortem_context(body, None) == body


def test_inject_preserves_body_ordering() -> None:
    body = "First line.\nSecond line."
    injected = inject_premortem_context(body, _ctx())
    # Body appears after the end marker.
    tail = injected.split(PREMORTEM_END_MARKER, 1)[1]
    assert "First line." in tail
    assert tail.index("First line.") < tail.index("Second line.")


def test_is_premortem_injected_detects_marker() -> None:
    body = "Brief."
    assert not is_premortem_injected(body)
    injected = inject_premortem_context(body, _ctx())
    assert is_premortem_injected(injected)


# ---------------------------------------------------------------------------
# Strip
# ---------------------------------------------------------------------------
def test_strip_removes_injected_block() -> None:
    body = "Original brief."
    injected = inject_premortem_context(body, _ctx())
    stripped = strip_premortem_injection(injected)
    assert stripped == "Original brief."


def test_strip_on_unmarked_body_is_noop() -> None:
    body = "Brief with no marker."
    assert strip_premortem_injection(body) == body


def test_strip_then_reinject_updates_cause() -> None:
    body = inject_premortem_context("Brief.", PremortemContext(cause="Old cause."))
    body = strip_premortem_injection(body)
    body = inject_premortem_context(body, PremortemContext(cause="New cause."))
    assert "New cause." in body
    assert "Old cause." not in body
