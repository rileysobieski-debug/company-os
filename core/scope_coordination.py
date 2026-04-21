"""
core/scope_coordination.py

Company-level scope coordination round. Fires once every active
department has completed its founder interview. Produces a scope map
plus one scope-of-work.md per department. Founder ratifies the whole
set before any department is allowed to advance to Charter.

v1 is single-pass: a coordinating agent reads every manager's
skill-scope, domain-brief, and founder-brief, plus the orchestrator
charter and company config, and drafts the full set in one shot. A
future v2 can decompose into per-manager drafting plus pairwise
negotiation, but v1 already enforces the contract.

Invariants surfaced in the output:
  1. No unnecessary overlap between departments.
  2. Full coverage of the implied business scope.
  3. Each cross-departmental handoff is explicit.

Founder role: ratify or reject the whole map. Not author.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


COORDINATION_SUBDIR = "coordination"
SCOPE_OF_WORK_FILENAME = "scope-of-work.md"


# ---------------------------------------------------------------------------
# Status model
# ---------------------------------------------------------------------------

STATUS_NOT_READY = "not_ready"
STATUS_READY = "ready"
STATUS_RUNNING = "running"
STATUS_AWAITING_SIGNOFF = "awaiting_signoff"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

VALID_STATUSES = frozenset({
    STATUS_NOT_READY, STATUS_READY, STATUS_RUNNING,
    STATUS_AWAITING_SIGNOFF, STATUS_APPROVED, STATUS_REJECTED,
})


@dataclass(frozen=True)
class CoordinationState:
    """Company-level scope coordination record."""

    status: str = STATUS_NOT_READY
    started_at: str = ""
    completed_at: str = ""
    approved_at: str = ""
    job_id: str = ""
    scope_map_path: str = ""           # vault-relative, markdown
    scope_map_json_path: str = ""      # vault-relative, JSON structured
    error: str = ""
    notes: str = ""                    # founder notes on approve/reject


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def coordination_dir(company_dir: Path) -> Path:
    return company_dir / COORDINATION_SUBDIR


def coordination_state_path(company_dir: Path) -> Path:
    return coordination_dir(company_dir) / "state.json"


def scope_map_md_path(company_dir: Path) -> Path:
    return coordination_dir(company_dir) / "scope-map.md"


def scope_map_json_path(company_dir: Path) -> Path:
    return coordination_dir(company_dir) / "scope-map.json"


def scope_of_work_path(company_dir: Path, dept: str) -> Path:
    return company_dir / dept / SCOPE_OF_WORK_FILENAME


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_state(company_dir: Path) -> CoordinationState:
    p = coordination_state_path(company_dir)
    if not p.exists():
        return CoordinationState()
    raw = json.loads(p.read_text(encoding="utf-8"))
    return CoordinationState(
        status=raw.get("status", STATUS_NOT_READY),
        started_at=raw.get("started_at", ""),
        completed_at=raw.get("completed_at", ""),
        approved_at=raw.get("approved_at", ""),
        job_id=raw.get("job_id", ""),
        scope_map_path=raw.get("scope_map_path", ""),
        scope_map_json_path=raw.get("scope_map_json_path", ""),
        error=raw.get("error", ""),
        notes=raw.get("notes", ""),
    )


def persist_state(company_dir: Path, state: CoordinationState) -> Path:
    p = coordination_state_path(company_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": state.status,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "approved_at": state.approved_at,
        "job_id": state.job_id,
        "scope_map_path": state.scope_map_path,
        "scope_map_json_path": state.scope_map_json_path,
        "error": state.error,
        "notes": state.notes,
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def department_ready_for_coordination(state_obj) -> bool:
    """A department is ready to participate in the coordination round
    once founder_interview is in completed_phases. We don't require
    KB / integrations / charter to be done because scope coordination
    is what unblocks charter."""
    return "founder_interview" in getattr(state_obj, "completed_phases", ())


def all_departments_ready(dept_states: list) -> bool:
    if not dept_states:
        return False
    return all(department_ready_for_coordination(s) for s in dept_states)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

COORDINATION_PROMPT_TEMPLATE = """You are the Company OS coordinating agent for {company_name}, a {industry} company.

Your job now: produce the full departmental scope map for this company. Every department has just finished its founder interview. Their managers have skill-scopes, domain briefs, and founder briefs ready for you. You will synthesize all of that into one coherent scope map that:

1. Gives each department a `scope-of-work.md`: what the department owns, what it does NOT own, and which adjacent work it hands off to which other department.
2. Identifies and resolves any overlaps where two departments would otherwise duplicate effort.
3. Identifies and closes any gaps where the combined scopes would leave part of the business unowned.
4. Makes every cross-departmental handoff explicit.

## Framing rules

- The founder did not hand-scope these departments. Managers together are designing the lattice. You are their coordinator.
- Every department's primary is already locked (industry x discipline). Do not renegotiate primaries.
- Secondaries are insight-only, never operational. Do not assign work to a department based on its secondary. Scopes of work are drawn from the primary only.
- If two primaries would naturally touch the same territory, draw a line: one owns, the other hands off. State the handoff precisely.
- If nothing in the roster covers some implied business territory, flag it. Name it. Propose an owner from the existing departments or flag it as a gap that needs a new department.

## Inputs

### Orchestrator charter
{orchestrator_charter}

### Company config priorities
{company_priorities}

### Per-department context
{dept_context_blocks}

## Output format

Return ONE JSON object with the exact shape below. Return ONLY the JSON, no prose, no code fence.

{{
  "coverage_summary": "2-4 sentences on how the full business scope is covered by the union of departments. Call out any area that is NOT covered and name it as a gap.",
  "overlaps_resolved": [
    {{
      "territory": "short name of the contested area",
      "owning_dept": "dept slug that now owns it",
      "other_depts": ["slugs of depts that touch this but hand off"],
      "resolution": "1 sentence explaining the draw"
    }}
  ],
  "gaps": [
    {{
      "territory": "short name",
      "proposed_owner": "existing dept slug OR 'needs_new_department'",
      "reason": "1 sentence"
    }}
  ],
  "handoffs": [
    {{
      "from_dept": "slug",
      "to_dept": "slug",
      "trigger": "when this handoff fires (short phrase)",
      "artifact": "what gets handed off, concretely"
    }}
  ],
  "departments": [
    {{
      "dept": "slug",
      "owns": ["bullet list of specific work territories this dept owns outright"],
      "does_not_own": ["bullet list of adjacent territories this dept explicitly does NOT own, with the owning dept in parens"],
      "receives_from": ["list of inbound handoffs"],
      "sends_to": ["list of outbound handoffs"],
      "summary": "3-5 sentence prose summary that will be the body of <dept>/scope-of-work.md. Written in third person about the department, not first person."
    }}
  ]
}}

## Constraints

- Every active department in the inputs must appear exactly once in `departments`.
- `owns` entries are concrete work territories, not abstract capabilities. Right: "paid acquisition experiments and attribution reporting." Wrong: "growth thinking."
- `does_not_own` entries are sharp. Name the neighboring territory and who owns it.
- No em dashes in any string (em dash is a written-copy dead giveaway).
- `handoffs` must be pairwise and concrete. Both depts must exist in `departments`.
- If the input is sparse or contradictory, call that out in `coverage_summary` and do your best with what's there.

Return the JSON now."""


def render_coordination_prompt(
    *,
    company_name: str,
    industry: str,
    orchestrator_charter: str,
    company_priorities: str,
    dept_context_blocks: str,
) -> str:
    return COORDINATION_PROMPT_TEMPLATE.format(
        company_name=company_name,
        industry=industry,
        orchestrator_charter=orchestrator_charter or "(no orchestrator charter on file)",
        company_priorities=company_priorities or "(no priorities declared)",
        dept_context_blocks=dept_context_blocks,
    )


# ---------------------------------------------------------------------------
# Scope-of-work rendering (map JSON into per-dept markdown files)
# ---------------------------------------------------------------------------

def render_scope_of_work_md(dept_block: dict, coverage_summary: str) -> str:
    """Turn one entry from the coordination JSON into the body of the
    per-department scope-of-work.md file."""
    owns = dept_block.get("owns", []) or []
    not_owns = dept_block.get("does_not_own", []) or []
    recv = dept_block.get("receives_from", []) or []
    sends = dept_block.get("sends_to", []) or []
    summary = (dept_block.get("summary", "") or "").strip()
    dept = dept_block.get("dept", "").strip() or "(unknown)"

    lines: list[str] = []
    lines.append(f"# Scope of work: {dept}")
    lines.append("")
    lines.append("_Produced by the company-wide scope coordination round. "
                 "Ratified by the founder. Update by re-running coordination._")
    lines.append("")
    if summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(summary)
        lines.append("")
    lines.append("## Owns")
    lines.append("")
    if owns:
        for o in owns:
            lines.append(f"- {o}")
    else:
        lines.append("_None recorded._")
    lines.append("")
    lines.append("## Does not own")
    lines.append("")
    if not_owns:
        for n in not_owns:
            lines.append(f"- {n}")
    else:
        lines.append("_None recorded._")
    lines.append("")
    lines.append("## Handoffs in")
    lines.append("")
    if recv:
        for h in recv:
            lines.append(f"- {h}")
    else:
        lines.append("_None recorded._")
    lines.append("")
    lines.append("## Handoffs out")
    lines.append("")
    if sends:
        for h in sends:
            lines.append(f"- {h}")
    else:
        lines.append("_None recorded._")
    lines.append("")
    if coverage_summary:
        lines.append("## Company coverage context")
        lines.append("")
        lines.append(coverage_summary)
        lines.append("")
    return "\n".join(lines) + "\n"
