"""
core/training.py — Phase 10 — Training program data layer
=========================================================
Plan references:
  * §5.6 (line 366): "Full specialist onboarding is deferred until
    training session (Phase 10)."
  * §5.8 (line 380): "depth comes once your knowledge base finishes
    ingesting and you've had a training session with each specialist."
  * §9 (line 585): "Any skill explicitly tagged `reasoning_required:
    true` by the founder during training" earns Opus escalation.
  * §18 (line 761): "Eval ground truth? Authored during training (§10
    of plan); validated by founder ranking."

A training session is a structured interview between the founder and
one specialist (or one skill). The founder ranks the specialist's
example outputs on concrete briefs; top-ranked examples become positive
benchmarks, bottom-ranked become anti-examples. The founder can also
flag specific skills as requiring enhanced reasoning.

This module is the pure data layer — no LLM calls, no interactive I/O.
The actual interview driver (CLI prompts, session orchestration) is a
later layer that consumes these primitives.

Public surface (this chunk, 10.1):
  * `TrainingExample(input_brief, agent_output, founder_rank, notes)`
  * `TrainingQuestion(prompt, response)`
  * `TrainingSession(specialist_id, started_at, ended_at, examples,
                     questions, founder_notes)`
  * `render_transcript(session) → str` — markdown
  * `parse_transcript(md) → TrainingSession` — roundtrip
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrainingExample:
    """One example output the specialist produced, ranked by the founder.

    `founder_rank` uses a signed 5-point scale: +2 = exemplar (gold),
    +1 = good, 0 = neutral, -1 = weak, -2 = anti-exemplar (do not
    replicate). This gives the benchmark authoring step (Chunk 10.2)
    enough signal to partition positives from negatives.
    """

    input_brief: str
    agent_output: str
    founder_rank: int  # -2 .. +2
    notes: str = ""

    def __post_init__(self) -> None:
        if not -2 <= self.founder_rank <= 2:
            raise ValueError(
                f"founder_rank must be in [-2, 2], got {self.founder_rank}"
            )


@dataclass(frozen=True)
class TrainingQuestion:
    """A question the founder asked and the specialist's free-form answer."""

    prompt: str
    response: str


@dataclass(frozen=True)
class TrainingSession:
    """Full record of one training session."""

    specialist_id: str
    started_at: str  # ISO timestamp
    ended_at: str    # ISO timestamp
    examples: tuple[TrainingExample, ...] = ()
    questions: tuple[TrainingQuestion, ...] = ()
    founder_notes: str = ""

    def positive_examples(self) -> tuple[TrainingExample, ...]:
        return tuple(e for e in self.examples if e.founder_rank > 0)

    def negative_examples(self) -> tuple[TrainingExample, ...]:
        return tuple(e for e in self.examples if e.founder_rank < 0)


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Transcript render
# ---------------------------------------------------------------------------
_MARKER = "<!-- training-session -->"


def render_transcript(session: TrainingSession) -> str:
    """Render a TrainingSession as a markdown transcript.

    The marker at the top lets `parse_transcript()` detect the file
    shape. Each example and question is written as a discrete block
    with HTML-comment metadata so parsing is unambiguous even when
    authors hand-edit.
    """
    lines: list[str] = [
        _MARKER,
        f"# Training session — {session.specialist_id}",
        "",
        f"**Specialist:** `{session.specialist_id}`",
        f"**Started:** {session.started_at}",
        f"**Ended:** {session.ended_at}",
        "",
    ]
    if session.founder_notes.strip():
        lines.extend(["## Founder notes", "", session.founder_notes.strip(), ""])

    if session.questions:
        lines.append("## Questions")
        lines.append("")
        for i, q in enumerate(session.questions, start=1):
            lines.append(f"### Q{i}")
            lines.append("")
            lines.append(f"**Prompt:** {q.prompt}")
            lines.append("")
            lines.append(q.response.strip() or "_(no response recorded)_")
            lines.append("")

    if session.examples:
        lines.append("## Examples (ranked)")
        lines.append("")
        for i, ex in enumerate(session.examples, start=1):
            lines.append(f"### Example {i} (rank: {_rank_label(ex.founder_rank)})")
            lines.append("")
            lines.append(f"<!-- rank: {ex.founder_rank} -->")
            lines.append("")
            lines.append("**Input brief:**")
            lines.append("")
            lines.append(ex.input_brief.strip())
            lines.append("")
            lines.append("**Agent output:**")
            lines.append("")
            lines.append(ex.agent_output.strip())
            lines.append("")
            if ex.notes.strip():
                lines.append("**Founder notes:**")
                lines.append("")
                lines.append(ex.notes.strip())
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _rank_label(rank: int) -> str:
    return {
        2: "+2 exemplar",
        1: "+1 good",
        0: "0 neutral",
        -1: "-1 weak",
        -2: "-2 anti-exemplar",
    }.get(rank, str(rank))


# ---------------------------------------------------------------------------
# Transcript parse
# ---------------------------------------------------------------------------
_META_RE = re.compile(r"\*\*(\w[\w\s]*):\*\*\s*(.*)")
_RANK_RE = re.compile(r"<!--\s*rank:\s*(-?\d+)\s*-->")


def parse_transcript(md: str) -> TrainingSession:
    """Parse a transcript back into a TrainingSession.

    Roundtrip-stable against `render_transcript()`. Raises ValueError
    if the marker is missing or required metadata (specialist_id,
    started_at, ended_at) cannot be extracted.
    """
    if _MARKER not in md:
        raise ValueError("transcript marker missing — not a training transcript")

    meta = _extract_top_meta(md)
    specialist_id = meta.get("specialist", "").strip().strip("`")
    started_at = meta.get("started", "").strip()
    ended_at = meta.get("ended", "").strip()
    if not specialist_id:
        raise ValueError("training transcript missing Specialist metadata")
    if not started_at or not ended_at:
        raise ValueError("training transcript missing Started/Ended metadata")

    founder_notes = _extract_section(md, "Founder notes")
    questions = _extract_questions(md)
    examples = _extract_examples(md)

    return TrainingSession(
        specialist_id=specialist_id,
        started_at=started_at,
        ended_at=ended_at,
        examples=examples,
        questions=questions,
        founder_notes=founder_notes,
    )


def _extract_top_meta(md: str) -> dict[str, str]:
    """Grab `**Key:** value` pairs from the top meta block."""
    meta: dict[str, str] = {}
    # Stop at first `##` heading (section break).
    head = md.split("\n##", 1)[0]
    for line in head.splitlines():
        m = _META_RE.match(line.strip())
        if m:
            meta[m.group(1).lower().strip()] = m.group(2).strip()
    return meta


def _extract_section(md: str, heading: str) -> str:
    """Pull the body of a `## {heading}` section; empty if missing."""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(md)
    if not m:
        return ""
    return m.group(1).strip()


def _extract_questions(md: str) -> tuple[TrainingQuestion, ...]:
    section = _extract_section(md, "Questions")
    if not section:
        return ()
    out: list[TrainingQuestion] = []
    for block in re.split(r"^###\s+Q\d+\s*$", section, flags=re.MULTILINE):
        if not block.strip():
            continue
        prompt_match = re.search(r"\*\*Prompt:\*\*\s*(.*)", block)
        if not prompt_match:
            continue
        prompt = prompt_match.group(1).strip()
        # Response is everything after the prompt line.
        body = block[prompt_match.end():].strip()
        if body.startswith("_(no response recorded)_"):
            body = ""
        out.append(TrainingQuestion(prompt=prompt, response=body))
    return tuple(out)


def _extract_examples(md: str) -> tuple[TrainingExample, ...]:
    section = _extract_section(md, "Examples (ranked)")
    if not section:
        return ()
    out: list[TrainingExample] = []
    blocks = re.split(r"^###\s+Example\s+\d+\s.*?$", section, flags=re.MULTILINE)
    for block in blocks:
        if not block.strip():
            continue
        rank_match = _RANK_RE.search(block)
        if not rank_match:
            continue
        rank = int(rank_match.group(1))
        brief = _extract_labeled_body(block, "Input brief")
        output = _extract_labeled_body(block, "Agent output")
        notes = _extract_labeled_body(block, "Founder notes")
        if not brief or not output:
            continue
        out.append(TrainingExample(
            input_brief=brief,
            agent_output=output,
            founder_rank=rank,
            notes=notes,
        ))
    return tuple(out)


def _extract_labeled_body(block: str, label: str) -> str:
    """Return the text block following `**{label}:**` up to the next
    `**X:**` label or end of block."""
    pattern = re.compile(
        rf"\*\*{re.escape(label)}:\*\*\s*(.*?)(?=^\*\*\w[\w\s]*:\*\*|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(block)
    if not m:
        return ""
    return m.group(1).strip()


# ---------------------------------------------------------------------------
# Benchmark authoring (Phase 10.2)
# ---------------------------------------------------------------------------
import json
from pathlib import Path


@dataclass(frozen=True)
class Benchmark:
    """One input/expected-output benchmark pair.

    `kind` distinguishes positive ("should produce something like this")
    from negative ("should NOT produce something like this") — the
    evaluator uses kind to pick pass/fail polarity. `source_session`
    names the training transcript this benchmark was derived from so
    the evaluator can trace back when a benchmark stops matching founder
    taste.
    """

    skill_id: str
    kind: str           # "positive" | "negative"
    input_brief: str
    expected_output: str
    rank: int           # original founder rank (+2/+1 or -1/-2)
    notes: str = ""
    source_session: str = ""

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "kind": self.kind,
            "input_brief": self.input_brief,
            "expected_output": self.expected_output,
            "rank": self.rank,
            "notes": self.notes,
            "source_session": self.source_session,
        }


def author_benchmarks(
    session: TrainingSession,
    *,
    skill_id: str | None = None,
    source_session: str = "",
) -> list[Benchmark]:
    """Turn a TrainingSession's ranked examples into Benchmark records.

    * Rank > 0 → kind="positive" (specialist should emulate)
    * Rank < 0 → kind="negative" (specialist should NOT replicate)
    * Rank == 0 is skipped (no training signal).

    `skill_id` defaults to the session's specialist_id, which is the
    natural fit when training a specialist as a whole. For per-skill
    training, pass the skill_id explicitly.
    """
    sid = skill_id or session.specialist_id
    benchmarks: list[Benchmark] = []
    for ex in session.examples:
        if ex.founder_rank == 0:
            continue
        kind = "positive" if ex.founder_rank > 0 else "negative"
        benchmarks.append(Benchmark(
            skill_id=sid,
            kind=kind,
            input_brief=ex.input_brief,
            expected_output=ex.agent_output,
            rank=ex.founder_rank,
            notes=ex.notes,
            source_session=source_session,
        ))
    return benchmarks


def write_benchmarks(
    benchmarks: Sequence[Benchmark],
    path: Path,
    *,
    append: bool = True,
) -> int:
    """Persist benchmarks as JSONL (one JSON object per line).

    JSONL was chosen over YAML to keep appends O(1) and parsing robust
    when a session stops mid-write. Returns the number of records
    written. When `append=False`, truncates the file first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append and path.exists() else "w"
    written = 0
    with path.open(mode, encoding="utf-8") as fh:
        for b in benchmarks:
            fh.write(json.dumps(b.to_dict(), sort_keys=True))
            fh.write("\n")
            written += 1
    return written


# ---------------------------------------------------------------------------
# Reasoning-required toggle (Phase 10.3 — §9 Opus opt-in)
# ---------------------------------------------------------------------------
import yaml


def mark_reasoning_required(
    skill_id: str,
    skills_dir: Path,
    *,
    required: bool = True,
) -> bool:
    """Toggle `reasoning_required: <required>` on `skills_dir/<skill_id>.yaml`.

    Returns True when the file was modified; False when the target flag
    value was already set (no-op write skipped). Raises FileNotFoundError
    when no such skill YAML exists.

    The skill-runner reads this flag via `SkillSpec.reasoning_required`
    (§9): true → Opus is selected for the next invocation.
    """
    path = skills_dir / f"{skill_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no skill YAML at {path}")

    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"skill YAML at {path} is not a mapping")

    current = bool(data.get("reasoning_required", False))
    if current == required:
        return False

    data["reasoning_required"] = required
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return True


def load_benchmarks(path: Path) -> list[Benchmark]:
    """Read a benchmarks JSONL file back into Benchmark objects.

    Missing file → empty list. Malformed lines (blank or unparseable
    JSON) are skipped silently so a partially-written file still yields
    the records that are complete.
    """
    if not path.exists():
        return []
    out: list[Benchmark] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        try:
            out.append(Benchmark(
                skill_id=str(data["skill_id"]),
                kind=str(data["kind"]),
                input_brief=str(data["input_brief"]),
                expected_output=str(data["expected_output"]),
                rank=int(data["rank"]),
                notes=str(data.get("notes", "")),
                source_session=str(data.get("source_session", "")),
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return out
