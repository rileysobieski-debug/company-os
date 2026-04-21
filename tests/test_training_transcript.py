"""Training session data model + transcript render/parse (Phase 10.1)."""
from __future__ import annotations

import pytest

from core.training import (
    TrainingExample,
    TrainingQuestion,
    TrainingSession,
    now_iso,
    parse_transcript,
    render_transcript,
)


def _sample_session() -> TrainingSession:
    return TrainingSession(
        specialist_id="copywriter",
        started_at="2026-04-18T12:00:00+00:00",
        ended_at="2026-04-18T12:30:00+00:00",
        founder_notes=(
            "Copywriter must never fall back on 'craft, stewardship, integrity' "
            "without a specific scene."
        ),
        questions=(
            TrainingQuestion(
                prompt="Describe Old Press's voice in one sentence.",
                response="Quiet authority. Short declarative. Never hype.",
            ),
        ),
        examples=(
            TrainingExample(
                input_brief="Write a 30-word landing-page headline.",
                agent_output="Pioneering the future of American wine.",
                founder_rank=2,
                notes="Exactly the voice.",
            ),
            TrainingExample(
                input_brief="Write a 30-word landing-page headline.",
                agent_output="Handcrafted coastal Maine wines for discerning palates.",
                founder_rank=-2,
                notes="Generic luxury slop. Never do this.",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
def test_rank_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match=r"\[-2, 2\]"):
        TrainingExample(
            input_brief="x", agent_output="y", founder_rank=5,
        )


def test_positive_and_negative_examples_filter_correctly() -> None:
    session = _sample_session()
    positives = session.positive_examples()
    negatives = session.negative_examples()
    assert len(positives) == 1
    assert positives[0].founder_rank == 2
    assert len(negatives) == 1
    assert negatives[0].founder_rank == -2


def test_now_iso_is_utc_with_timezone_suffix() -> None:
    ts = now_iso()
    assert "T" in ts
    assert ts.endswith("+00:00")


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def test_render_opens_with_marker_and_headline() -> None:
    md = render_transcript(_sample_session())
    lines = md.splitlines()
    assert lines[0] == "<!-- training-session -->"
    assert "# Training session — copywriter" in lines[1]


def test_render_includes_metadata_block() -> None:
    md = render_transcript(_sample_session())
    assert "**Specialist:** `copywriter`" in md
    assert "**Started:** 2026-04-18T12:00:00+00:00" in md
    assert "**Ended:** 2026-04-18T12:30:00+00:00" in md


def test_render_includes_founder_notes() -> None:
    md = render_transcript(_sample_session())
    assert "## Founder notes" in md
    assert "must never fall back" in md


def test_render_examples_carry_rank_comment() -> None:
    md = render_transcript(_sample_session())
    assert "<!-- rank: 2 -->" in md
    assert "<!-- rank: -2 -->" in md


def test_render_omits_empty_sections() -> None:
    session = TrainingSession(
        specialist_id="copywriter",
        started_at="2026-04-18T12:00:00+00:00",
        ended_at="2026-04-18T12:30:00+00:00",
    )
    md = render_transcript(session)
    assert "## Questions" not in md
    assert "## Examples" not in md
    assert "## Founder notes" not in md


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------
def test_parse_requires_marker() -> None:
    with pytest.raises(ValueError, match="marker"):
        parse_transcript("# Just a heading, no marker.")


def test_parse_recovers_session_shape() -> None:
    original = _sample_session()
    md = render_transcript(original)
    parsed = parse_transcript(md)
    assert parsed.specialist_id == original.specialist_id
    assert parsed.started_at == original.started_at
    assert parsed.ended_at == original.ended_at


def test_parse_recovers_questions() -> None:
    md = render_transcript(_sample_session())
    parsed = parse_transcript(md)
    assert len(parsed.questions) == 1
    assert parsed.questions[0].prompt.startswith("Describe Old Press")
    assert "Quiet authority" in parsed.questions[0].response


def test_parse_recovers_examples_with_ranks() -> None:
    md = render_transcript(_sample_session())
    parsed = parse_transcript(md)
    assert len(parsed.examples) == 2
    ranks = sorted(e.founder_rank for e in parsed.examples)
    assert ranks == [-2, 2]


def test_parse_recovers_example_briefs_and_outputs() -> None:
    md = render_transcript(_sample_session())
    parsed = parse_transcript(md)
    exemplar = next(e for e in parsed.examples if e.founder_rank == 2)
    assert "30-word landing-page headline" in exemplar.input_brief
    assert "Pioneering the future of American wine" in exemplar.agent_output


def test_parse_recovers_example_notes() -> None:
    md = render_transcript(_sample_session())
    parsed = parse_transcript(md)
    anti = next(e for e in parsed.examples if e.founder_rank == -2)
    assert "Generic luxury slop" in anti.notes


def test_parse_recovers_founder_notes() -> None:
    md = render_transcript(_sample_session())
    parsed = parse_transcript(md)
    assert "must never fall back" in parsed.founder_notes


def test_parse_missing_metadata_raises() -> None:
    md = "<!-- training-session -->\n# Training session\n\n_(no metadata)_\n"
    with pytest.raises(ValueError, match="Specialist"):
        parse_transcript(md)


# ---------------------------------------------------------------------------
# Roundtrip stability
# ---------------------------------------------------------------------------
def test_roundtrip_is_stable() -> None:
    original = _sample_session()
    md = render_transcript(original)
    parsed = parse_transcript(md)
    assert parsed.specialist_id == original.specialist_id
    assert len(parsed.examples) == len(original.examples)
    assert len(parsed.questions) == len(original.questions)
    # Re-render should produce identical output.
    md2 = render_transcript(parsed)
    assert md == md2
