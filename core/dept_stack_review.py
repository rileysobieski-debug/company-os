"""
core/dept_stack_review.py — Post-onboarding departmental review
================================================================

A Board-led synthesis that runs after all active departments complete
onboarding. The Board reads the full corpus (every dept's skill-scope,
founder-brief, and charter; the orchestrator charter; config.json
priorities + convictions) and produces a review document proposing:

  - New departments worth adding (with scope/NEVER lines)
  - Possible consolidations (two depts whose scopes overlap enough to merge)
  - Possible terminations (depts whose mandate no longer serves priorities)
  - Orchestrator charter amendments (routing that needs rewiring given the
    current or proposed stack)

Propose-only. Riley approves specific proposals; implementation creates
dormant dept stubs and amends the orchestrator charter.

Storage:
  <company>/decisions/stack-reviews/<YYYY-MM-DD>-review.md   (human-readable)
  <company>/decisions/stack-reviews/<YYYY-MM-DD>-review.json (structured)
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable

from core.dept_onboarding import (
    OnboardingPhase,
    charter_path,
    ensure_state,
    founder_brief_path,
    list_all_states,
    skill_scope_path,
)


STACK_REVIEWS_SUBDIR = "decisions/stack-reviews"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
class ProposalKind(str, Enum):
    NEW_DEPARTMENT = "new_department"
    CONSOLIDATION = "consolidation"
    TERMINATION = "termination"
    ORCHESTRATOR_AMENDMENT = "orchestrator_amendment"


class ProposalStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class StackReviewProposal:
    """A single proposal from the Board. Propose-only by design — the
    founder decides whether to implement, and implementation for new
    departments + orchestrator amendments is a separate UI action."""

    id: str
    kind: str  # ProposalKind value
    title: str
    rationale: str
    status: str = ProposalStatus.PROPOSED.value
    # Kind-specific fields (only populated where relevant):
    proposed_dept_name: str = ""     # NEW_DEPARTMENT
    proposed_dept_owns: tuple[str, ...] = field(default_factory=tuple)
    proposed_dept_never: tuple[str, ...] = field(default_factory=tuple)
    consolidation_depts: tuple[str, ...] = field(default_factory=tuple)  # CONSOLIDATION
    termination_dept: str = ""       # TERMINATION
    orchestrator_delta: str = ""     # ORCHESTRATOR_AMENDMENT — the proposed change
    # Audit
    implemented_at: str = ""
    implementation_notes: str = ""


@dataclass(frozen=True)
class StackReviewCorpus:
    """The inputs that go into the dossier. Kept separate from the
    review output so the dossier can be rebuilt without re-running
    the Board."""

    company_name: str
    industry: str
    active_departments: tuple[str, ...]
    priorities: tuple[str, ...]
    settled_convictions: tuple[str, ...]
    hard_constraints: tuple[str, ...]
    orchestrator_charter: str
    # One entry per department currently in the stack
    dept_artifacts: tuple["DeptArtifactBundle", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DeptArtifactBundle:
    dept: str
    phase: str
    skill_scope: str        # full text of skill-scope.md if present
    founder_brief: str      # full text of founder-brief.md if present
    charter: str            # full text of charter.md if present


@dataclass(frozen=True)
class StackReview:
    """The complete review document. Persisted as md + json sidecar."""

    id: str                          # YYYY-MM-DD-review
    created_at: str                  # ISO-8601
    corpus_summary: dict             # stats about the corpus reviewed
    gaps: tuple[str, ...] = field(default_factory=tuple)
    proposals: tuple[StackReviewProposal, ...] = field(default_factory=tuple)
    board_transcript_path: str = ""  # vault-relative path to full transcript
    notes: str = ""


# ---------------------------------------------------------------------------
# Paths / IO
# ---------------------------------------------------------------------------
def reviews_dir(company_dir: Path) -> Path:
    return company_dir / STACK_REVIEWS_SUBDIR


def review_md_path(company_dir: Path, review_id: str) -> Path:
    return reviews_dir(company_dir) / f"{review_id}.md"


def review_json_path(company_dir: Path, review_id: str) -> Path:
    return reviews_dir(company_dir) / f"{review_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _safe_read(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Corpus assembly
# ---------------------------------------------------------------------------
def load_review_corpus(
    company_dir: Path,
    *,
    company_name: str,
    industry: str,
    active_departments: Iterable[str],
    priorities: Iterable[str],
    settled_convictions: Iterable[str],
    hard_constraints: Iterable[str],
) -> StackReviewCorpus:
    """Gather every artifact the Board needs to review the stack.

    Missing artifacts render as empty strings — the dossier will note
    which departments have incomplete onboarding so the Board knows
    what signal is weak."""
    dept_list = tuple(active_departments)
    bundles: list[DeptArtifactBundle] = []
    for dept in dept_list:
        state = ensure_state(company_dir, dept)
        bundles.append(DeptArtifactBundle(
            dept=dept,
            phase=state.phase,
            skill_scope=_safe_read(skill_scope_path(company_dir, dept)),
            founder_brief=_safe_read(founder_brief_path(company_dir, dept)),
            charter=_safe_read(charter_path(company_dir, dept)),
        ))
    orch_charter = _safe_read(company_dir / "orchestrator-charter.md")
    return StackReviewCorpus(
        company_name=company_name,
        industry=industry,
        active_departments=dept_list,
        priorities=tuple(priorities),
        settled_convictions=tuple(settled_convictions),
        hard_constraints=tuple(hard_constraints),
        orchestrator_charter=orch_charter,
        dept_artifacts=tuple(bundles),
    )


def all_departments_complete(
    company_dir: Path,
    active_departments: Iterable[str],
) -> bool:
    """True iff every listed dept's onboarding state is COMPLETE.
    Used as the auto-trigger gate after onboarding_signoff."""
    states = list_all_states(company_dir, list(active_departments))
    return bool(states) and all(
        s.phase == OnboardingPhase.COMPLETE.value for s in states
    )


# ---------------------------------------------------------------------------
# Dossier rendering — this is what the Board actually reads
# ---------------------------------------------------------------------------
def render_dossier(corpus: StackReviewCorpus) -> str:
    """Render the stack-review dossier as a single markdown blob the
    Board will ingest as its meeting context.

    Structure (intentionally explicit so the Board voices cite it):
      1. Company frame — name, industry, priorities, convictions, hard_constraints
      2. Current stack — one block per department with its full onboarding artifacts
      3. Orchestrator charter — full text if present
      4. The question — what the Board is asked to answer
    """
    lines: list[str] = []
    lines.append(f"# Stack review dossier — {corpus.company_name}")
    lines.append("")
    lines.append(f"**Industry (primary anchor):** {corpus.industry}")
    lines.append("")
    lines.append("## Stated priorities")
    for p in corpus.priorities:
        lines.append(f"- {p}")
    lines.append("")
    lines.append("## Settled convictions")
    for c in corpus.settled_convictions:
        lines.append(f"- {c}")
    lines.append("")
    lines.append("## Hard constraints")
    for c in corpus.hard_constraints:
        lines.append(f"- {c}")
    lines.append("")

    lines.append("## Current departmental stack")
    lines.append("")
    if not corpus.dept_artifacts:
        lines.append("_No departments onboarded._")
    else:
        for b in corpus.dept_artifacts:
            lines.append(f"### Dept: {b.dept}")
            lines.append(f"_Phase: {b.phase}_")
            lines.append("")
            if b.skill_scope:
                lines.append("#### skill-scope.md")
                lines.append(b.skill_scope.strip())
                lines.append("")
            else:
                lines.append("_(no skill-scope on record)_")
                lines.append("")
            if b.founder_brief:
                lines.append("#### founder-brief.md")
                lines.append(b.founder_brief.strip())
                lines.append("")
            else:
                lines.append("_(no founder-brief on record)_")
                lines.append("")
            if b.charter:
                lines.append("#### charter.md")
                lines.append(b.charter.strip())
                lines.append("")
            else:
                lines.append("_(no charter on record — phase did not complete)_")
                lines.append("")

    lines.append("## Orchestrator charter")
    lines.append("")
    if corpus.orchestrator_charter:
        lines.append(corpus.orchestrator_charter.strip())
    else:
        lines.append("_(no orchestrator charter on record)_")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## The review question")
    lines.append("")
    lines.append(
        "Given the stated priorities + convictions above and the current "
        "departmental stack, produce a **stack review**: what is missing, "
        "what overlaps, what should be retired, and how should the "
        "orchestrator be rewired? Each Board voice speaks from its own lens. "
        "The review closes with a synthesizer who consolidates into "
        "structured proposals."
    )
    lines.append("")
    lines.append(
        "**Important:** This is PROPOSE-ONLY. Do not invent new departments "
        "that don't serve a stated priority. Do not propose terminations "
        "unless you can name a specific priority they fail to advance. "
        "Cite artifact content (e.g., 'editorial's skill-scope names X "
        "secondary — this creates overlap with marketing's Y') rather than "
        "inventing motivations."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Synthesizer prompt — the closing participant follows this to produce
# the machine-parseable section of the review
# ---------------------------------------------------------------------------
SYNTHESIZER_CLOSING_PROMPT = """You are the synthesizer closing this Board meeting. Consolidate the preceding voices into a structured review.

Output THREE sections in EXACTLY this order and format. The machine-readable part MUST be a valid JSON code block.

## Gaps
A short markdown paragraph naming the 2-5 most load-bearing gaps the Board surfaced. Concrete — name the priority each gap fails to advance.

## Executive summary
A 3-5 sentence summary written for the founder. Plain English, no buzzwords. Start with the single most important insight from the discussion.

## Proposals (JSON)

```json
{
  "proposals": [
    {
      "kind": "new_department" | "consolidation" | "termination" | "orchestrator_amendment",
      "title": "Short imperative phrase naming the proposal",
      "rationale": "2-4 sentences grounded in dossier content and voices. Cite which voice raised it.",
      "proposed_dept_name": "lowercase-slug-name",
      "proposed_dept_owns": ["topic 1", "topic 2", "..."],
      "proposed_dept_never": ["topic 1", "topic 2", "..."],
      "consolidation_depts": ["dept_a", "dept_b"],
      "termination_dept": "dept_name",
      "orchestrator_delta": "A specific diff statement describing what should change in the orchestrator-charter.md — e.g., 'route queries about X to Y instead of Z'"
    }
  ]
}
```

Rules:
- Only populate fields that match the kind. For `new_department`, fill `proposed_dept_name`, `proposed_dept_owns`, `proposed_dept_never`; leave the others empty.
- For `orchestrator_amendment`, fill only `orchestrator_delta` with a specific diff — not "update the charter."
- 0-6 proposals total. Do not force the count; if the stack is healthy, return an empty proposals array.
- Every rationale must cite specific content from the dossier — manager skill-scopes, priorities, convictions, etc. No vague claims.
- The JSON must be syntactically valid. Use empty strings/arrays for irrelevant fields, NEVER null.

End your message after the JSON code block. Do not add a sign-off or further prose."""


# ---------------------------------------------------------------------------
# Parsing the synthesizer's output
# ---------------------------------------------------------------------------
_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


def _extract_gaps(synthesizer_text: str) -> tuple[str, ...]:
    """Pull the 'Gaps' section — one paragraph of markdown — out of
    the synthesizer's response. Returns a tuple of one-sentence lines."""
    m = re.search(
        r"##\s*Gaps\s*\n(.+?)(?:\n##\s|$)",
        synthesizer_text, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return ()
    body = m.group(1).strip()
    # Split into bullets or sentences
    if body.startswith("-"):
        out = [ln.lstrip("- ").strip() for ln in body.splitlines() if ln.strip()]
    else:
        out = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    return tuple(out)


def _extract_executive_summary(synthesizer_text: str) -> str:
    m = re.search(
        r"##\s*Executive\s*summary\s*\n(.+?)(?:\n##\s|```|$)",
        synthesizer_text, re.DOTALL | re.IGNORECASE,
    )
    return m.group(1).strip() if m else ""


def _extract_proposals(synthesizer_text: str) -> list[dict]:
    """Find the fenced JSON block; parse; return the proposals list.
    Returns [] on any failure — the review still renders, just with
    no structured proposals for Riley to action."""
    m = _JSON_FENCE_RE.search(synthesizer_text)
    if not m:
        return []
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    props = obj.get("proposals", [])
    if not isinstance(props, list):
        return []
    return [p for p in props if isinstance(p, dict)]


def _make_proposal_id(idx: int, kind: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:32]
    return f"p{idx:02d}-{kind}-{slug or 'untitled'}"


def parse_review(synthesizer_text: str) -> tuple[tuple[str, ...], str, tuple[StackReviewProposal, ...]]:
    """Parse the synthesizer's output into (gaps, executive_summary, proposals).
    Used both by the run-path (to build the StackReview) and by tests."""
    gaps = _extract_gaps(synthesizer_text)
    summary = _extract_executive_summary(synthesizer_text)
    raw_proposals = _extract_proposals(synthesizer_text)
    built: list[StackReviewProposal] = []
    allowed_kinds = {k.value for k in ProposalKind}
    for i, raw in enumerate(raw_proposals, start=1):
        kind = str(raw.get("kind", "")).strip()
        if kind not in allowed_kinds:
            continue
        title = str(raw.get("title", "")).strip() or "Untitled proposal"
        rationale = str(raw.get("rationale", "")).strip()
        built.append(StackReviewProposal(
            id=_make_proposal_id(i, kind, title),
            kind=kind,
            title=title,
            rationale=rationale,
            proposed_dept_name=str(raw.get("proposed_dept_name", "")).strip(),
            proposed_dept_owns=tuple(str(x).strip() for x in raw.get("proposed_dept_owns", []) if str(x).strip()),
            proposed_dept_never=tuple(str(x).strip() for x in raw.get("proposed_dept_never", []) if str(x).strip()),
            consolidation_depts=tuple(str(x).strip() for x in raw.get("consolidation_depts", []) if str(x).strip()),
            termination_dept=str(raw.get("termination_dept", "")).strip(),
            orchestrator_delta=str(raw.get("orchestrator_delta", "")).strip(),
        ))
    return gaps, summary, tuple(built)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def render_review_markdown(review: StackReview, synthesizer_text: str) -> str:
    """Render the human-readable review.md. Includes the full
    synthesizer section + our parsed structured proposals alongside
    it so Riley can audit how we interpreted the JSON."""
    lines: list[str] = []
    lines.append(f"# Departmental stack review — {review.id[:10]}")
    lines.append("")
    lines.append(f"**Created:** {review.created_at}")
    lines.append(f"**Departments reviewed:** {review.corpus_summary.get('dept_count', 0)}")
    if review.board_transcript_path:
        lines.append(f"**Full Board transcript:** `{review.board_transcript_path}`")
    lines.append("")

    if review.gaps:
        lines.append("## Gaps identified")
        for g in review.gaps:
            lines.append(f"- {g}")
        lines.append("")

    lines.append("## Proposals")
    lines.append("")
    if not review.proposals:
        lines.append("_No proposals surfaced. Stack is coherent as-is._")
        lines.append("")
    else:
        for p in review.proposals:
            lines.append(f"### [{p.kind}] {p.title}")
            lines.append(f"_Proposal id:_ `{p.id}`")
            lines.append("")
            lines.append(p.rationale or "_(no rationale captured)_")
            lines.append("")
            if p.kind == ProposalKind.NEW_DEPARTMENT.value and p.proposed_dept_name:
                lines.append(f"**Proposed dept name:** `{p.proposed_dept_name}`")
                if p.proposed_dept_owns:
                    lines.append("**OWNS:**")
                    for t in p.proposed_dept_owns:
                        lines.append(f"- {t}")
                if p.proposed_dept_never:
                    lines.append("**NEVER:**")
                    for t in p.proposed_dept_never:
                        lines.append(f"- {t}")
                lines.append("")
            if p.kind == ProposalKind.CONSOLIDATION.value and p.consolidation_depts:
                lines.append(f"**Consolidate:** {' + '.join(p.consolidation_depts)}")
                lines.append("")
            if p.kind == ProposalKind.TERMINATION.value and p.termination_dept:
                lines.append(f"**Terminate:** `{p.termination_dept}`")
                lines.append("")
            if p.kind == ProposalKind.ORCHESTRATOR_AMENDMENT.value and p.orchestrator_delta:
                lines.append(f"**Orchestrator delta:** {p.orchestrator_delta}")
                lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Full synthesizer output")
    lines.append("")
    lines.append(synthesizer_text.strip())
    lines.append("")
    return "\n".join(lines) + "\n"


def persist_review(
    company_dir: Path,
    review: StackReview,
    synthesizer_text: str,
) -> tuple[Path, Path]:
    """Write both the .md and .json. Returns (md_path, json_path)."""
    reviews_dir(company_dir).mkdir(parents=True, exist_ok=True)
    md_path = review_md_path(company_dir, review.id)
    json_path = review_json_path(company_dir, review.id)
    md_path.write_text(
        render_review_markdown(review, synthesizer_text),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(asdict(review), sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return md_path, json_path


def load_review(company_dir: Path, review_id: str) -> StackReview | None:
    path = review_json_path(company_dir, review_id)
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return StackReview(
        id=obj.get("id", review_id),
        created_at=obj.get("created_at", ""),
        corpus_summary=dict(obj.get("corpus_summary", {})),
        gaps=tuple(obj.get("gaps", [])),
        proposals=tuple(
            StackReviewProposal(
                id=p.get("id", ""),
                kind=p.get("kind", ""),
                title=p.get("title", ""),
                rationale=p.get("rationale", ""),
                status=p.get("status", ProposalStatus.PROPOSED.value),
                proposed_dept_name=p.get("proposed_dept_name", ""),
                proposed_dept_owns=tuple(p.get("proposed_dept_owns", [])),
                proposed_dept_never=tuple(p.get("proposed_dept_never", [])),
                consolidation_depts=tuple(p.get("consolidation_depts", [])),
                termination_dept=p.get("termination_dept", ""),
                orchestrator_delta=p.get("orchestrator_delta", ""),
                implemented_at=p.get("implemented_at", ""),
                implementation_notes=p.get("implementation_notes", ""),
            )
            for p in obj.get("proposals", [])
        ),
        board_transcript_path=obj.get("board_transcript_path", ""),
        notes=obj.get("notes", ""),
    )


def list_reviews(company_dir: Path) -> list[StackReview]:
    """Reverse-chronological list of all persisted reviews."""
    d = reviews_dir(company_dir)
    if not d.exists():
        return []
    out: list[StackReview] = []
    for p in sorted(d.glob("*.json"), reverse=True):
        r = load_review(company_dir, p.stem)
        if r is not None:
            out.append(r)
    return out


def mark_proposal_status(
    company_dir: Path,
    review_id: str,
    proposal_id: str,
    *,
    status: ProposalStatus,
    notes: str = "",
) -> StackReview | None:
    """Update a proposal's status (accepted | rejected | deferred).
    Returns the updated StackReview, or None if review/proposal not found."""
    review = load_review(company_dir, review_id)
    if review is None:
        return None
    new_props: list[StackReviewProposal] = []
    found = False
    for p in review.proposals:
        if p.id == proposal_id:
            found = True
            new_props.append(StackReviewProposal(
                id=p.id, kind=p.kind, title=p.title, rationale=p.rationale,
                status=status.value,
                proposed_dept_name=p.proposed_dept_name,
                proposed_dept_owns=p.proposed_dept_owns,
                proposed_dept_never=p.proposed_dept_never,
                consolidation_depts=p.consolidation_depts,
                termination_dept=p.termination_dept,
                orchestrator_delta=p.orchestrator_delta,
                implemented_at=(_now_iso() if status is ProposalStatus.ACCEPTED else p.implemented_at),
                implementation_notes=notes or p.implementation_notes,
            ))
        else:
            new_props.append(p)
    if not found:
        return None
    updated = StackReview(
        id=review.id,
        created_at=review.created_at,
        corpus_summary=review.corpus_summary,
        gaps=review.gaps,
        proposals=tuple(new_props),
        board_transcript_path=review.board_transcript_path,
        notes=review.notes,
    )
    # Persist only the json; the md is a snapshot of the synthesis moment
    # and doesn't need to reflect status changes. Reviewers read md for
    # the original content, the /stack-review/<id> UI renders from json
    # for live status.
    review_json_path(company_dir, review.id).write_text(
        json.dumps(asdict(updated), sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return updated


# ---------------------------------------------------------------------------
# Orchestration — run the Board meeting
# ---------------------------------------------------------------------------
# The Board voices in order. Synthesizer is intentionally last and
# gets a special closing prompt (SYNTHESIZER_CLOSING_PROMPT) so the
# output is machine-parseable.
STACK_REVIEW_PARTICIPANTS: tuple[str, ...] = (
    "board:Strategist",
    "board:Storyteller",
    "board:Analyst",
    "board:Builder",
    "board:Contrarian",
    "board:KnowledgeElicitor",
    "board:Analyst",  # closes as synthesizer — the Analyst voice is best-suited
)


def build_review_id(*, now: datetime | None = None) -> str:
    d = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return f"{d}-review"
