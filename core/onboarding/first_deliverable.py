"""
core/onboarding/first_deliverable.py — Phase 8.4 — §5.8 first deliverable
=========================================================================
Plan §5.8:

  "Orchestrator proposes one concrete deliverable producible from the
   interview alone, without requiring KB ingest to be complete.
   Examples: a 1-page positioning statement drawn from the founder's
   own answers, a priority-risk matrix from the stated constraints, a
   draft settled-convictions summary for the founder's approval. The
   deliverable is intentionally in the 'synthesize what you already
   told me' space."

This primitive is deterministic — no LLM calls. It scores the interview
answers against three candidate kinds and chooses the richest one. The
orchestrator renders a handshake and dispatches to the preferred
department (fallback: first active dept if preferred is dormant).

Deliverable kinds (all KB-independent):

  * `positioning_statement` — 1-page brand/positioning draft from vision
    answers. Preferred dept: marketing.
  * `priority_risk_matrix`  — risk matrix from priority_stack +
    hard_rules + regulatory + budget. Preferred dept: operations.
  * `convictions_summary`   — founder-register restatement of
    settled_convictions. Preferred dept: editorial.

If signal is too thin (all scores zero), defaults to
`priority_risk_matrix` — the one that degrades gracefully since a
priority stack is always required by the interview validator.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

POSITIONING_STATEMENT = "positioning_statement"
PRIORITY_RISK_MATRIX = "priority_risk_matrix"
CONVICTIONS_SUMMARY = "convictions_summary"

_PREFERRED_DEPT: dict[str, str] = {
    POSITIONING_STATEMENT: "marketing",
    PRIORITY_RISK_MATRIX: "operations",
    CONVICTIONS_SUMMARY: "editorial",
}

# Tiebreak order when scores are equal — convictions first (most founder-
# specific), positioning next (vision-backed), risk-matrix last (generic).
_KIND_PRIORITY: tuple[str, ...] = (
    CONVICTIONS_SUMMARY,
    POSITIONING_STATEMENT,
    PRIORITY_RISK_MATRIX,
)


@dataclass(frozen=True)
class DeliverableProposal:
    kind: str
    title: str
    rationale: str
    assigned_dept: str
    brief: str
    score: int = 0


# ---------------------------------------------------------------------------
# Signal scoring
# ---------------------------------------------------------------------------
def _score_signals(answers: Mapping[str, Any]) -> dict[str, int]:
    """Return {kind: raw signal score}. Higher = richer input material."""
    convictions = answers.get("settled_convictions") or []
    vision_12 = str(answers.get("twelve_month_vision") or "").strip()
    vision_5 = str(answers.get("five_year_vision") or "").strip()
    priorities = answers.get("priority_stack") or []
    hard_rules = answers.get("hard_rules") or []
    regulatory = str(answers.get("regulatory") or "").strip()
    budget = str(answers.get("budget") or "").strip()
    return {
        CONVICTIONS_SUMMARY: 2 * len(convictions),
        POSITIONING_STATEMENT: (1 if vision_12 else 0) + (1 if vision_5 else 0),
        PRIORITY_RISK_MATRIX: (
            len(priorities)
            + len(hard_rules)
            + (1 if regulatory else 0)
            + (1 if budget else 0)
        ),
    }


def _choose_kind(scores: Mapping[str, int]) -> str:
    """Pick highest-scoring kind; fall back to priority_risk_matrix on ties."""
    ordered = sorted(
        _KIND_PRIORITY,
        key=lambda k: (-scores.get(k, 0), _KIND_PRIORITY.index(k)),
    )
    best = ordered[0]
    if scores.get(best, 0) == 0:
        return PRIORITY_RISK_MATRIX
    return best


# ---------------------------------------------------------------------------
# Brief rendering
# ---------------------------------------------------------------------------
def _render_positioning(
    answers: Mapping[str, Any], dept_note: str
) -> tuple[str, str, str]:
    vision_12 = str(answers.get("twelve_month_vision") or "").strip()
    vision_5 = str(answers.get("five_year_vision") or "").strip()
    title = "Draft 1-page positioning statement"
    rationale = (
        "Founder supplied vision material — a positioning draft is "
        "producible from the interview alone. " + dept_note
    )
    brief_lines = [
        "Draft a 1-page positioning statement drawn from the founder's "
        "own vision answers.",
        "",
        "12-month vision:",
        f"> {vision_12 or '(not supplied)'}",
        "",
        "5-year vision:",
        f"> {vision_5 or '(not supplied)'}",
        "",
        "Output: founder-ready copy, not analysis. 1 page max.",
    ]
    return title, rationale, "\n".join(brief_lines)


def _render_priority_risk(
    answers: Mapping[str, Any], dept_note: str
) -> tuple[str, str, str]:
    priorities = answers.get("priority_stack") or []
    hard_rules = answers.get("hard_rules") or []
    regulatory = str(answers.get("regulatory") or "").strip()
    budget = str(answers.get("budget") or "").strip()
    title = "Build priority-risk matrix"
    rationale = (
        "Founder's stated constraints (priority stack, hard rules, "
        "regulatory, budget) provide enough input to synthesize a risk "
        "matrix without KB ingest. " + dept_note
    )
    lines = [
        "Build a priority-risk matrix from the founder's own constraints.",
        "",
        "Priorities:",
    ]
    if priorities:
        lines.extend(f"- {p}" for p in priorities)
    else:
        lines.append("- (none recorded)")
    lines.append("")
    lines.append("Hard rules:")
    if hard_rules:
        lines.extend(f"- {r}" for r in hard_rules)
    else:
        lines.append("- (none listed)")
    if regulatory:
        lines.extend(["", "Regulatory scope:", regulatory])
    if budget:
        lines.extend(["", f"Budget: {budget}"])
    lines.extend([
        "",
        "Output: 2-column matrix (priority, top-2 risks) with one-line "
        "mitigation per risk.",
    ])
    return title, rationale, "\n".join(lines)


def _render_convictions(
    answers: Mapping[str, Any], dept_note: str
) -> tuple[str, str, str]:
    convictions = answers.get("settled_convictions") or []
    title = "Draft settled-convictions summary"
    rationale = (
        "Founder supplied multiple settled convictions — the summary is "
        "synthesizable from the interview alone. " + dept_note
    )
    lines = [
        "Draft a settled-convictions summary for the founder's approval.",
        "",
        "Source convictions (DO NOT RE-EXAMINE per §0.5):",
    ]
    lines.extend(f"- {c}" for c in convictions)
    lines.extend([
        "",
        "Output: crisp restatement of each conviction in the founder's "
        "own register, grouped if thematic patterns emerge.",
    ])
    return title, rationale, "\n".join(lines)


_RENDERERS = {
    POSITIONING_STATEMENT: _render_positioning,
    PRIORITY_RISK_MATRIX: _render_priority_risk,
    CONVICTIONS_SUMMARY: _render_convictions,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def propose_first_deliverable(
    answers: Mapping[str, Any],
    *,
    active_departments: Sequence[str],
) -> DeliverableProposal:
    """Pick a KB-independent first deliverable for this founder.

    Routes to the preferred dept when it is active; otherwise falls
    back to the first active dept (with the fallback noted in the
    rationale).
    """
    active = [d for d in active_departments]
    if not active:
        raise ValueError(
            "at least one active department required to assign the "
            "first deliverable"
        )

    scores = _score_signals(answers)
    chosen = _choose_kind(scores)
    preferred = _PREFERRED_DEPT[chosen]
    if preferred in active:
        assigned = preferred
        dept_note = f"Assigned to preferred dept '{preferred}'."
    else:
        assigned = active[0]
        dept_note = (
            f"Preferred dept '{preferred}' not active — assigning to "
            f"first active dept '{assigned}'."
        )

    title, rationale, brief = _RENDERERS[chosen](answers, dept_note)
    return DeliverableProposal(
        kind=chosen,
        title=title,
        rationale=rationale,
        assigned_dept=assigned,
        brief=brief,
        score=scores.get(chosen, 0),
    )
