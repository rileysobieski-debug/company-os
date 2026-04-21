"""
core/onboarding/business_interview.py — Phase 8.1 — §5.2 business interview
============================================================================
Drives the 10-12 minute business interview described in plan §5.2. Pure
data layer — no LLM calls, no I/O at parse time. The UI (CLI or web)
pipes answers in, this module validates them, builds the six onboarding
files, and writes them to the company dir.

Interview phases (§5.2):
  1. Basics       — company_name, industry, geography, team_size, revenue_status
  2. Vision       — twelve_month_vision, five_year_vision, refusals
  3. Constraints  — budget, timeline, regulatory, hard_rules
  4. Founder      — role, background, bandwidth, decision_style
  5. Convictions  — settled_convictions (list, may be empty)
  6. Pre-mortem   — premortem_cause  (§0.5, load-bearing — injected into
                    every cross-dept synthesis and adversary activation)
  7. Priorities   — priority_stack (ordered list, top 3 become starting
                    active departments)

Output files written to company_dir (§5.2 ending):
  * config.json         — structured, priorities + active_departments set
  * context.md          — human-readable company context
  * priorities.md       — ranked stack with rationales
  * founder_profile.md  — including pre-mortem
  * domain.md           — industry + geography + regulatory scope
  * state-authority.md  — §1.5 map (delegated to primitives.state)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from core.primitives.state import render_state_authority_doc


class InterviewPhase(Enum):
    BASICS = "basics"
    VISION = "vision"
    CONSTRAINTS = "constraints"
    FOUNDER = "founder"
    CONVICTIONS = "convictions"
    PREMORTEM = "premortem"
    PRIORITIES = "priorities"


@dataclass(frozen=True)
class InterviewQuestion:
    phase: InterviewPhase
    id: str
    prompt: str
    required: bool = True
    is_list: bool = False
    help_text: str = ""


# ---------------------------------------------------------------------------
# Canonical question set (§5.2)
# ---------------------------------------------------------------------------
INTERVIEW_QUESTIONS: tuple[InterviewQuestion, ...] = (
    # 1. Basics
    InterviewQuestion(InterviewPhase.BASICS, "company_name",
                      "What is the company's legal or working name?"),
    InterviewQuestion(InterviewPhase.BASICS, "industry",
                      "What industry or category does the company sit in?"),
    InterviewQuestion(InterviewPhase.BASICS, "geography",
                      "Where is the company based (operational geography)?"),
    InterviewQuestion(InterviewPhase.BASICS, "team_size",
                      "How many people are on the team today (including founder)?"),
    InterviewQuestion(InterviewPhase.BASICS, "revenue_status",
                      "Pre-revenue, in-revenue, or profitable?"),

    # 2. Vision
    InterviewQuestion(InterviewPhase.VISION, "twelve_month_vision",
                      "What does the company look like in 12 months if things go well?"),
    InterviewQuestion(InterviewPhase.VISION, "five_year_vision",
                      "What does the company look like in 5 years if things go well?"),
    InterviewQuestion(InterviewPhase.VISION, "refusals", required=False, is_list=True,
                      prompt="What will the company NEVER do? (List; may be empty.)"),

    # 3. Constraints
    InterviewQuestion(InterviewPhase.CONSTRAINTS, "budget",
                      "What is the monthly operating budget available?"),
    InterviewQuestion(InterviewPhase.CONSTRAINTS, "timeline",
                      "What are the hard deadlines in the next 12 months?"),
    InterviewQuestion(InterviewPhase.CONSTRAINTS, "regulatory", required=False,
                      prompt="What regulatory or compliance constraints apply?"),
    InterviewQuestion(InterviewPhase.CONSTRAINTS, "hard_rules", required=False, is_list=True,
                      prompt="Any hard rules the company operates under? (List.)"),

    # 4. Founder
    InterviewQuestion(InterviewPhase.FOUNDER, "founder_role",
                      "What is your role in the company (founder/CEO/other)?"),
    InterviewQuestion(InterviewPhase.FOUNDER, "founder_background",
                      "One-paragraph summary of your professional background."),
    InterviewQuestion(InterviewPhase.FOUNDER, "founder_bandwidth",
                      "Hours per week you can realistically spend on this?"),
    InterviewQuestion(InterviewPhase.FOUNDER, "decision_style",
                      "When two managers disagree, how do you want it resolved?"),

    # 5. Convictions
    InterviewQuestion(InterviewPhase.CONVICTIONS, "settled_convictions",
                      required=False, is_list=True,
                      prompt="What decisions are already settled and should NEVER be re-examined?"),

    # 6. Pre-mortem (§0.5, load-bearing)
    InterviewQuestion(InterviewPhase.PREMORTEM, "premortem_cause",
                      prompt="12 months from now this business failed. What is the most "
                             "likely cause?",
                      help_text="Used in every cross-dept synthesis and adversary activation."),

    # 7. Priorities
    InterviewQuestion(InterviewPhase.PRIORITIES, "priority_stack", is_list=True,
                      prompt="Rank your top 3–5 priorities for the next 90 days. "
                             "The top 3 become starting active departments."),
)

_QUESTIONS_BY_ID: dict[str, InterviewQuestion] = {q.id: q for q in INTERVIEW_QUESTIONS}

MIN_PRIORITY_STACK = 3
MAX_PRIORITY_STACK = 5


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ValidationIssue:
    question_id: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)


def _is_empty(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str):
        return val.strip() == ""
    if isinstance(val, (list, tuple, dict)):
        return len(val) == 0
    return False


def validate_answers(answers: Mapping[str, Any]) -> ValidationResult:
    """Check every required question has a non-empty answer."""
    issues: list[ValidationIssue] = []
    for q in INTERVIEW_QUESTIONS:
        val = answers.get(q.id)
        if q.required and _is_empty(val):
            issues.append(ValidationIssue(q.id, f"required answer missing: {q.id}"))

    # Priority stack specific — min 3, max 5, non-empty strings.
    stack = answers.get("priority_stack")
    if stack is not None:
        if not isinstance(stack, (list, tuple)):
            issues.append(ValidationIssue(
                "priority_stack", "priority_stack must be a list"
            ))
        else:
            items = [s for s in stack if isinstance(s, str) and s.strip()]
            if len(items) < MIN_PRIORITY_STACK:
                issues.append(ValidationIssue(
                    "priority_stack",
                    f"need at least {MIN_PRIORITY_STACK} priorities, got {len(items)}",
                ))
            elif len(items) > MAX_PRIORITY_STACK:
                issues.append(ValidationIssue(
                    "priority_stack",
                    f"at most {MAX_PRIORITY_STACK} priorities, got {len(items)}",
                ))

    return ValidationResult(ok=not issues, issues=tuple(issues))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get(answers: Mapping[str, Any], key: str, default: Any = "") -> Any:
    val = answers.get(key)
    if val is None:
        return default
    return val


def _list(answers: Mapping[str, Any], key: str) -> list[str]:
    val = answers.get(key) or []
    if isinstance(val, str):
        return [val] if val.strip() else []
    return [str(x).strip() for x in val if str(x).strip()]


def _slugify_priority(item: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in item.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "priority"


# ---------------------------------------------------------------------------
# config.json builder
# ---------------------------------------------------------------------------
def build_config_payload(
    answers: Mapping[str, Any],
    *,
    company_id: str | None = None,
    active_departments: list[str] | None = None,
) -> dict[str, Any]:
    """Build the structured `config.json` payload from interview answers.

    `active_departments` is supplied by the dept-selection step (Chunk
    8.2); left to the caller so this function stays pure.
    """
    priorities = _list(answers, "priority_stack")
    cid = company_id or _slugify_priority(_get(answers, "company_name", "company"))
    return {
        "company_id": cid,
        "company_name": _get(answers, "company_name"),
        "industry": _get(answers, "industry"),
        "geography": _get(answers, "geography"),
        "team_size": _get(answers, "team_size"),
        "revenue_status": _get(answers, "revenue_status"),
        "priorities": priorities,
        "active_departments": list(active_departments or []),
        "settled_convictions": _list(answers, "settled_convictions"),
        "hard_constraints": _list(answers, "hard_rules"),
        "delegation": {
            "decision_style": _get(answers, "decision_style"),
        },
        "premortem": _get(answers, "premortem_cause"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Markdown renderers
# ---------------------------------------------------------------------------
def render_context_md(answers: Mapping[str, Any]) -> str:
    lines = [
        f"# {_get(answers, 'company_name') or 'Company'} — Context",
        "",
        f"**Industry:** {_get(answers, 'industry')}",
        f"**Geography:** {_get(answers, 'geography')}",
        f"**Team size:** {_get(answers, 'team_size')}",
        f"**Revenue status:** {_get(answers, 'revenue_status')}",
        "",
        "## Vision",
        "",
        f"**12 months:** {_get(answers, 'twelve_month_vision')}",
        "",
        f"**5 years:** {_get(answers, 'five_year_vision')}",
        "",
    ]
    refusals = _list(answers, "refusals")
    if refusals:
        lines.append("**Refusals — what we'll never do:**")
        lines.extend(f"- {r}" for r in refusals)
        lines.append("")

    budget = _get(answers, "budget")
    timeline = _get(answers, "timeline")
    if budget or timeline:
        lines.append("## Operating envelope")
        lines.append("")
        if budget:
            lines.append(f"**Budget:** {budget}")
        if timeline:
            lines.append(f"**Timeline:** {timeline}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_priorities_md(answers: Mapping[str, Any]) -> str:
    items = _list(answers, "priority_stack")
    lines = [
        f"# {_get(answers, 'company_name') or 'Company'} — Priority Stack",
        "",
        "Ranked list of near-term priorities. **Top 3 become starting active "
        "departments.** The founder reviews this before the first deliverable.",
        "",
    ]
    if not items:
        lines.append("_(no priorities recorded)_")
    else:
        for i, item in enumerate(items, start=1):
            marker = " **[ACTIVE]**" if i <= 3 else ""
            lines.append(f"{i}. {item}{marker}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_founder_profile_md(answers: Mapping[str, Any]) -> str:
    lines = [
        f"# Founder Profile",
        "",
        f"**Role:** {_get(answers, 'founder_role')}",
        f"**Bandwidth:** {_get(answers, 'founder_bandwidth')}",
        "",
        "## Background",
        "",
        f"{_get(answers, 'founder_background')}",
        "",
        "## Decision style",
        "",
        f"{_get(answers, 'decision_style')}",
        "",
    ]
    convictions = _list(answers, "settled_convictions")
    if convictions:
        lines.append("## Settled convictions (DO NOT RE-EXAMINE)")
        lines.append("")
        lines.extend(f"- {c}" for c in convictions)
        lines.append("")

    premortem = _get(answers, "premortem_cause")
    if premortem:
        lines.extend([
            "## Pre-mortem",
            "",
            "_Load-bearing per §0.5: this text is injected into every "
            "cross-dept synthesis and adversary activation._",
            "",
            f"> {premortem}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def render_domain_md(answers: Mapping[str, Any]) -> str:
    lines = [
        f"# Domain",
        "",
        f"**Industry:** {_get(answers, 'industry')}",
        f"**Geography:** {_get(answers, 'geography')}",
        "",
    ]
    regulatory = _get(answers, "regulatory")
    if regulatory:
        lines.extend(["## Regulatory scope", "", str(regulatory), ""])
    hard_rules = _list(answers, "hard_rules")
    if hard_rules:
        lines.append("## Hard rules")
        lines.append("")
        lines.extend(f"- {r}" for r in hard_rules)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Composed writer
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InterviewWriteResult:
    config_path: Path
    context_path: Path
    priorities_path: Path
    founder_profile_path: Path
    domain_path: Path
    state_authority_path: Path


def write_interview_files(
    company_dir: Path,
    answers: Mapping[str, Any],
    *,
    active_departments: list[str] | None = None,
    company_id: str | None = None,
) -> InterviewWriteResult:
    """Write all six §5.2 files in one shot.

    Validation runs first — a failure raises ValueError with every issue
    listed, so the UI can surface the full punch list rather than whack-
    a-mole. `active_departments` is the top-3 selection result (Chunk 8.2)
    or whatever the caller chose.
    """
    result = validate_answers(answers)
    if not result.ok:
        summary = "; ".join(f"{i.question_id}: {i.message}" for i in result.issues)
        raise ValueError(f"interview validation failed: {summary}")

    company_dir.mkdir(parents=True, exist_ok=True)
    company_name = _get(answers, "company_name") or "Company"

    config_payload = build_config_payload(
        answers, company_id=company_id, active_departments=active_departments,
    )
    config_path = company_dir / "config.json"
    config_path.write_text(
        json.dumps(config_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    paths = {
        "context": company_dir / "context.md",
        "priorities": company_dir / "priorities.md",
        "founder_profile": company_dir / "founder_profile.md",
        "domain": company_dir / "domain.md",
    }
    paths["context"].write_text(render_context_md(answers), encoding="utf-8")
    paths["priorities"].write_text(render_priorities_md(answers), encoding="utf-8")
    paths["founder_profile"].write_text(render_founder_profile_md(answers), encoding="utf-8")
    paths["domain"].write_text(render_domain_md(answers), encoding="utf-8")

    state_path = company_dir / "state-authority.md"
    state_path.write_text(render_state_authority_doc(company_name), encoding="utf-8")

    return InterviewWriteResult(
        config_path=config_path,
        context_path=paths["context"],
        priorities_path=paths["priorities"],
        founder_profile_path=paths["founder_profile"],
        domain_path=paths["domain"],
        state_authority_path=state_path,
    )
