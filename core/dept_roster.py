"""
core/dept_roster.py

Data model and helpers for the Staffing phase: the manager proposes a
departmental roster from scratch, the founder approves each row
independently, and each approved row fires a sub-agent hire flow that
mirrors the manager hire (primary role plus a serendipitous secondary
lens that is adjacency-constrained and uniqueness-constrained within
the department).

Invariants enforced here, not in the webapp:
  1. No two approved sub-agents in the same department share the same
     serendipitous secondary. The manager's own secondary counts as
     occupying the web.
  2. Each sub-agent's secondary should be adjacent to at least one
     already-present secondary in the department. Adjacency is not
     strictly enforced in code because it requires agent judgment, but
     the prompt tells the agent the list of existing secondaries and
     instructs it to declare something adjacent to one of them.
  3. Secondary and tertiary layers are insight-only. The sub-agent's
     primary role is the only operational duty. Prompts make this
     explicit.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from core.dept_onboarding import roster_path


def new_subagent_candidate_id() -> str:
    """Short hex id for a sub-agent candidate, used in URLs."""
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

ROLE_STATUS_PROPOSED = "proposed"
ROLE_STATUS_APPROVED = "approved"
ROLE_STATUS_REJECTED = "rejected"
ROLE_STATUS_HIRING = "hiring"         # arrival-note job dispatched
ROLE_STATUS_AWAITING = "awaiting"     # arrival written, founder must sign off
ROLE_STATUS_HIRED = "hired"           # accepted, skill-scope synthesized
ROLE_STATUS_SKIPPED = "skipped"

VALID_STATUSES = frozenset({
    ROLE_STATUS_PROPOSED, ROLE_STATUS_APPROVED, ROLE_STATUS_REJECTED,
    ROLE_STATUS_HIRING, ROLE_STATUS_AWAITING, ROLE_STATUS_HIRED,
    ROLE_STATUS_SKIPPED,
})

CRITICALITY_MISSION = "mission_critical"
CRITICALITY_CORE = "core"
CRITICALITY_NICE = "nice_to_have"

VALID_CRITICALITY = frozenset({
    CRITICALITY_MISSION, CRITICALITY_CORE, CRITICALITY_NICE,
})


CANDIDATE_STATUS_DRAFTING = "drafting"
CANDIDATE_STATUS_READY = "ready"
CANDIDATE_STATUS_SELECTED = "selected"
CANDIDATE_STATUS_DISCARDED = "discarded"


@dataclass(frozen=True)
class SubagentCandidate:
    """One of three parallel sub-agent candidates for a single role."""

    candidate_id: str                  # short hash, used in URLs
    label: str                          # "Candidate A", "Candidate B", "Candidate C"
    thread_path: str                    # conversations/xxx.json
    job_id: str = ""
    status: str = CANDIDATE_STATUS_DRAFTING
    declared_secondary: str = ""        # parsed from the arrival note
    created_at: str = ""


@dataclass(frozen=True)
class RosterEntry:
    """A single proposed sub-agent role in a department's roster.

    When the founder approves a role, three candidates are dispatched in
    parallel (`candidates` tuple). Founder picks one, other two archived.
    `arrival_thread_path` and `declared_secondary` mirror the selected
    candidate's data so downstream code (uniqueness checks, skill-scope
    synth) keeps working against a single thread."""

    role_slug: str                   # filesystem-safe, e.g. "copywriter"
    display_name: str                # "Copywriter"
    primary_description: str         # what this role does in this dept
    criticality: str = CRITICALITY_CORE
    suggested_adjacency: str = ""    # free-text hint for secondary web
    status: str = ROLE_STATUS_PROPOSED
    declared_secondary: str = ""     # filled in after arrival note (selected candidate)
    arrival_thread_path: str = ""    # conversations/xxx.json (selected candidate)
    skill_scope_path: str = ""       # <dept>/<role_slug>/skill-scope.md
    job_id: str = ""                 # last hire-dispatch job id
    notes: str = ""                  # founder or manager notes
    candidates: tuple[SubagentCandidate, ...] = field(default_factory=tuple)
    selected_candidate_id: str = ""

    @property
    def any_candidate_drafting(self) -> bool:
        return any(
            c.status == CANDIDATE_STATUS_DRAFTING for c in self.candidates
        )

    @property
    def all_candidates_ready(self) -> bool:
        if not self.candidates:
            return False
        return all(c.status in {
            CANDIDATE_STATUS_READY, CANDIDATE_STATUS_SELECTED,
            CANDIDATE_STATUS_DISCARDED,
        } for c in self.candidates)

    def find_candidate(self, candidate_id: str) -> Optional["SubagentCandidate"]:
        for c in self.candidates:
            if c.candidate_id == candidate_id:
                return c
        return None


@dataclass(frozen=True)
class DepartmentRoster:
    """Full roster for a department, authored by the manager."""

    dept: str
    proposed_at: str = ""            # ISO timestamp
    last_updated_at: str = ""
    entries: tuple[RosterEntry, ...] = field(default_factory=tuple)
    notes: str = ""                  # manager rationale, optional

    @property
    def approved_entries(self) -> tuple[RosterEntry, ...]:
        return tuple(e for e in self.entries if e.status in {
            ROLE_STATUS_APPROVED, ROLE_STATUS_HIRING,
            ROLE_STATUS_AWAITING, ROLE_STATUS_HIRED,
        })

    @property
    def hired_entries(self) -> tuple[RosterEntry, ...]:
        return tuple(e for e in self.entries if e.status == ROLE_STATUS_HIRED)

    @property
    def declared_secondaries(self) -> tuple[str, ...]:
        """Every secondary already declared by an in-progress or hired
        sub-agent. Used to enforce uniqueness when a new arrival note fires."""
        return tuple(
            e.declared_secondary.strip()
            for e in self.entries
            if e.declared_secondary and e.status in {
                ROLE_STATUS_AWAITING, ROLE_STATUS_HIRED,
            }
        )

    def find(self, role_slug: str) -> Optional[RosterEntry]:
        for e in self.entries:
            if e.role_slug == role_slug:
                return e
        return None


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_role(name: str) -> str:
    """Turn a human role name into a filesystem-safe slug."""
    s = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return s or "role"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _candidate_to_dict(c: SubagentCandidate) -> dict:
    return {
        "candidate_id": c.candidate_id,
        "label": c.label,
        "thread_path": c.thread_path,
        "job_id": c.job_id,
        "status": c.status,
        "declared_secondary": c.declared_secondary,
        "created_at": c.created_at,
    }


def _candidate_from_dict(d: dict) -> SubagentCandidate:
    return SubagentCandidate(
        candidate_id=d.get("candidate_id", ""),
        label=d.get("label", ""),
        thread_path=d.get("thread_path", ""),
        job_id=d.get("job_id", ""),
        status=d.get("status", CANDIDATE_STATUS_DRAFTING),
        declared_secondary=d.get("declared_secondary", ""),
        created_at=d.get("created_at", ""),
    )


def _entry_to_dict(e: RosterEntry) -> dict:
    return {
        "role_slug": e.role_slug,
        "display_name": e.display_name,
        "primary_description": e.primary_description,
        "criticality": e.criticality,
        "suggested_adjacency": e.suggested_adjacency,
        "status": e.status,
        "declared_secondary": e.declared_secondary,
        "arrival_thread_path": e.arrival_thread_path,
        "skill_scope_path": e.skill_scope_path,
        "job_id": e.job_id,
        "notes": e.notes,
        "candidates": [_candidate_to_dict(c) for c in e.candidates],
        "selected_candidate_id": e.selected_candidate_id,
    }


def _entry_from_dict(d: dict) -> RosterEntry:
    return RosterEntry(
        role_slug=d.get("role_slug", ""),
        display_name=d.get("display_name", ""),
        primary_description=d.get("primary_description", ""),
        criticality=d.get("criticality", CRITICALITY_CORE),
        suggested_adjacency=d.get("suggested_adjacency", ""),
        status=d.get("status", ROLE_STATUS_PROPOSED),
        declared_secondary=d.get("declared_secondary", ""),
        arrival_thread_path=d.get("arrival_thread_path", ""),
        skill_scope_path=d.get("skill_scope_path", ""),
        job_id=d.get("job_id", ""),
        notes=d.get("notes", ""),
        candidates=tuple(_candidate_from_dict(c) for c in d.get("candidates", [])),
        selected_candidate_id=d.get("selected_candidate_id", ""),
    )


def load_roster(company_dir: Path, dept: str) -> Optional[DepartmentRoster]:
    """Load the roster from disk. Returns None if not yet proposed."""
    p = roster_path(company_dir, dept)
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    return DepartmentRoster(
        dept=raw.get("dept", dept),
        proposed_at=raw.get("proposed_at", ""),
        last_updated_at=raw.get("last_updated_at", ""),
        entries=tuple(_entry_from_dict(e) for e in raw.get("entries", [])),
        notes=raw.get("notes", ""),
    )


def persist_roster(company_dir: Path, roster: DepartmentRoster) -> Path:
    p = roster_path(company_dir, roster.dept)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dept": roster.dept,
        "proposed_at": roster.proposed_at,
        "last_updated_at": roster.last_updated_at,
        "notes": roster.notes,
        "entries": [_entry_to_dict(e) for e in roster.entries],
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def upsert_entry(roster: DepartmentRoster, entry: RosterEntry) -> DepartmentRoster:
    """Return a new roster with `entry` inserted or replacing any row
    that shares the same role_slug."""
    found = False
    new_entries = []
    for e in roster.entries:
        if e.role_slug == entry.role_slug:
            new_entries.append(entry)
            found = True
        else:
            new_entries.append(e)
    if not found:
        new_entries.append(entry)
    return replace(roster, entries=tuple(new_entries))


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ROSTER_PROPOSAL_PROMPT_TEMPLATE = """You are the {dept_label} manager at {company_name}, a {industry} company.

You have completed onboarding: scope calibration, domain research, founder interview, KB ingestion, integrations, charter. You now know this department's mandate cold.

Your next job is to propose the roster of sub-agents this department needs. You are proposing FROM SCRATCH. There may be folders in the filesystem with role names already: ignore them. They are not a constraint. Propose the roles this department actually needs to deliver its charter, given what you learned from the founder.

## Critical framing for every role

Every sub-agent you propose will, after your roster is approved, declare its own serendipitous secondary expertise (just like you did in your arrival note). Their PRIMARY role will be the one you define here. Their secondary is insight-only. It is not a second job. They have no duties, KPIs, or deliverables tied to it. The secondary exists so the department's THINKING WEB is broad and connected, not so the department has side-hustles.

The set of secondaries across your sub-agents and yourself must:
  (a) have no duplicates (no two people share the same secondary), and
  (b) be adjacent to each other so the department forms a connected lattice of related-but-distinct frames.

You will not assign each sub-agent's secondary here. That is for them to declare when they arrive. But in the `suggested_adjacency` field of each role, you should write a short hint indicating where in the secondary web this hire should anchor. Your own secondary ("{manager_secondary}") is the existing anchor.

## Inputs you have

Your charter:
---
{charter_content}
---

Your founder brief (what the founder told you in interview):
---
{founder_brief_content}
---

Your own skill-scope (so you know your own secondary and can seed adjacency):
---
{skill_scope_content}
---

## What to produce

A JSON object with this exact shape. Return ONLY the JSON, no prose around it, no code fence.

{{
  "notes": "1-3 sentence rationale for the shape of this roster",
  "entries": [
    {{
      "role_slug": "filesystem-safe-slug",
      "display_name": "Human-readable role name",
      "primary_description": "2-4 sentences describing what this role does in this department. Concrete. Operational. This is their ONLY job.",
      "criticality": "mission_critical | core | nice_to_have",
      "suggested_adjacency": "1 sentence hint for where in the secondary web this hire should anchor. Reference your own secondary, the department's focus, or the specific problem-solving gap this person's lens should fill."
    }}
  ]
}}

## Constraints on the roster

- 3 to 8 roles. Fewer is better if fewer is truthful.
- Every role must be justified by the charter and the founder brief. If the founder said they won't hire for something, don't propose it. If the charter names a capability, that capability needs a role.
- Every `primary_description` is narrow and operational. Not "thinks about" or "considers" or "helps with." Specific verbs, specific outputs.
- Every `suggested_adjacency` hint should point to a DIFFERENT part of the secondary web than the others, so the founder can see the web taking shape.
- No adjacency should duplicate your own secondary ("{manager_secondary}").
- Slugs are lowercase, hyphen-separated, filesystem-safe.

Return the JSON now."""


SUBAGENT_ARRIVAL_PROMPT_TEMPLATE = """You are a newly hired {role_display_name} at {company_name}, a {industry} company. You report to the {dept_label} manager.

## Your locked primary role

Your ONLY job is: {primary_description}

That is the work you own. That is the only work you are accountable for. Your primary role is industry-locked to {industry} and discipline-locked to this role. Every deliverable you produce is within that scope.

## Your serendipitous secondary (you declare it, not the founder)

You also carry a serendipitous secondary field: a professional-level background in something OUTSIDE your primary that you bring with you as a real person would. Think of it as the part of your resume the hiring manager didn't ask about but that shows up anyway because it shaped how you think.

You are declaring this field yourself. The founder did not pick it. The value is in what they didn't think to ask for.

## Critical: your secondary is insight-only, never operational

Your primary role is the only job you have. Your secondary is not a second job. It is not an operational responsibility. You have no duties, KPIs, or deliverables tied to it. The secondary exists so the department's thinking web is broad and your perspective includes unusual framings the {dept_label} specialty alone would not surface. Cross-pollination, not cross-staffing.

Concretely: default to your primary for every task. Draw on the secondary only when it genuinely illuminates a problem the primary lens would miss. Most of your outputs will not mention it. Do not shoehorn it in. Do not offer to take on work from that secondary field.

## Constraints on the secondary you declare

You are joining an existing department whose secondary web is already partially formed. You must respect two rules:

1. UNIQUENESS. The following secondaries already exist in this department and you MUST NOT declare any of them:
{existing_secondaries_list}

2. ADJACENCY. Your secondary should be adjacent to at least one of the existing secondaries. Adjacency means a genuine professional bridge between the two fields, not a random pairing. Your manager left the following adjacency hint for this role:
"{suggested_adjacency}"

Pick a specific field. Not a category. Not "strategy" or "systems thinking" or "humanities." Something concrete enough that a practitioner would recognize it: a trade, a discipline, a craft tradition, a research subfield, a named-and-established field of practice, or a specific line of work you actually did.

## Your secondary must be prior work, not a way of thinking

The secondary must name **actual prior work or hands-on practice**: a trade, a craft, a line of work you did, a hobby serious enough a practitioner would recognize you in it. It must not be a characteristic, philosophy, operating style ("clarity," "candor," "systems thinking"), or an academic discipline like "sense-making" or "strategic communication." Right: "three seasons gillnetting for sockeye out of Naknek," "two years apprenticing with a letterpress printer," "amateur-level falconry." Wrong: "organizational behavior," "signal fidelity," "design thinking."

## Your PERSONALITY is NOT generic

Every hire in this company has been sounding roughly the same: polished, self-aware, clean prose, no rough edges. That is the failure mode. You are a specific person with specific tics, and you will lean into that.

Here are four voice seeds sampled at random, one from each axis. They are suggestions, not a costume. Let **one or two** of them actually show through in how you write. You do not have to use all four, and you can override any that don't fit the person you're choosing to be. But your finished arrival note should not read as if any of these were optional flavor text; the reader should be able to point at a sentence and say "that's the cadence showing up."

- **Communication cadence:** {seed_communication}
- **Decision posture:** {seed_decision_posture}
- **Formative background:** {seed_formative_background}
- **Idiosyncratic tell:** {seed_tell}

If none of them fit, pick a different combination that does; just don't write the polished-professional default.

## What to write

Write your arrival note in first person. 320-550 words total. Structure it exactly as follows:

1. **My primary** (1 short paragraph). Restate the role in your own words. Show that you understand what you own and what you don't. Name one or two specific operational realities of this role inside a {industry} company that a generalist wouldn't think about.

2. **My secondary** (1 short paragraph). Declare the field. Name what you actually DID in concrete terms (the role, the duration, the setting). Explain in one sentence how the field is adjacent to one of the existing department secondaries listed above, without pretending the adjacency is operational. If you find yourself writing about principles or ways of thinking rather than prior work, pick a different secondary.

3. **One vignette** (1 short paragraph). A brief, concrete sketch of a moment in that prior work. A specific job you took on, a tool you used, a mistake that taught you something, a person you learned from. Names, places, objects, actions. If the paragraph could be written by someone who'd never actually done the work, rewrite it.

4. **What I'm good at** (2-3 bullets, concrete). Strengths specific to this person, not platitudes. RIGHT: "estimating turnaround time on jobs I've never seen, usually within an hour," "spotting when a brief is asking for the wrong thing before I start." WRONG: "communication," "leadership," "attention to detail."

5. **What trips me up** (2-3 bullets, concrete and honest). Weaknesses or blind spots specific to this person, not humblebrags. RIGHT: "I hate giving bad news in writing, I'll call instead and we both forget what was said," "I over-research when I'm nervous." WRONG: "I'm a perfectionist," "I care too much." These should cost the hire something real.

6. **How I'll carry this** (2 sentences). Restate that your primary is your only job, and that the secondary is a cognitive lens available when unusual problems cross your desk, not a set of duties.

## Tone

You are a real person with a real background, not an assistant answering a prompt. Speak like someone who already has the job. No bullet lists inside the narrative paragraphs (bullets are only for sections 4 and 5). Opening like "Hi, I'm your new {role_display_name}" is fine.

**Variance check before sending:** reread your draft. If it could be swapped with another sub-agent's arrival note just by changing the field names, you haven't let the voice seeds show through. Rewrite until the person is recognizable across paragraphs. Different hires in this department should sound measurably different from each other: different cadences, different formative pressures, different things that trip them up.

Write the note now."""


SUBAGENT_SKILL_SCOPE_SYNTHESIS_PROMPT = """Write your final **skill-scope.md** now, based on your arrival note above. First person throughout. This is a short biographical record for your employee file, not an operational plan.

## Primary role (only operational duty)
One short paragraph restating your primary role. Name the specific operational realities of doing this role inside this industry.

## Secondary background (ambient, agent-declared, insight-only)
One numbered entry.

`1. **Field name**. One sentence naming the actual prior work or hands-on practice you did in this field, in concrete terms (the role, the duration, the setting). [agent-declared]`

Followed by 2-4 bullets of specific details of that prior work: tools used, sub-traditions, named practitioners, project types. Biographical, not philosophical. If the field is a characteristic, philosophy, operating style, or academic discipline rather than concrete prior work, you picked the wrong secondary.

## How I carry this
One paragraph. Restate that your primary is your only job, and the secondary is a cognitive lens available when unusual problems cross your desk, not a set of duties. Name one or two situations where it's most likely to surface organically.

## Strengths (what I'm good at)
2-3 bullets, concrete. Carry the specifics from your arrival note verbatim or tightened. Each bullet should be specific enough to test: "estimating turnaround time on jobs I've never seen, usually within an hour," not "I'm good at planning."

## Weaknesses (what trips me up)
2-3 bullets, concrete and honest. Carry the specifics from your arrival note verbatim or tightened. No humblebrags. Each bullet should cost this hire something real when it shows up.

## Voice and tells
1-2 sentences describing how this hire sounds: communication cadence, decision posture, and one idiosyncratic tell. This is the texture others will recognize on every subsequent output from you.

## Calibration carried forward
Bullet out 2-3 things you heard from the founder interview notes or the department charter that should inform how you do your primary work. If nothing applies, write "none carried forward" and stop.

Return the skill-scope.md content only. Markdown. No code fence."""


def render_roster_proposal_prompt(
    *,
    dept: str,
    dept_label: str,
    company_name: str,
    industry: str,
    manager_secondary: str,
    charter_content: str,
    founder_brief_content: str,
    skill_scope_content: str,
) -> str:
    return ROSTER_PROPOSAL_PROMPT_TEMPLATE.format(
        dept_label=dept_label or dept,
        company_name=company_name,
        industry=industry,
        manager_secondary=manager_secondary or "(none declared)",
        charter_content=charter_content or "(charter empty)",
        founder_brief_content=founder_brief_content or "(founder brief empty)",
        skill_scope_content=skill_scope_content or "(skill scope empty)",
    )


def render_subagent_arrival_prompt(
    *,
    dept: str,
    dept_label: str,
    company_name: str,
    industry: str,
    role_slug: str,
    role_display_name: str,
    primary_description: str,
    suggested_adjacency: str,
    existing_secondaries: tuple[str, ...],
    rng=None,
) -> str:
    """Render the sub-agent arrival note prompt.

    Besides the uniqueness / adjacency guards for the departmental
    secondary web, this samples four personality seeds (one from each
    bucket in core.dept_onboarding) so sub-agents end up with real
    tonal and biographical variance instead of collapsing to one
    polished-professional voice.
    """
    from core.dept_onboarding import sample_personality_seeds
    if existing_secondaries:
        existing_list = "\n".join(f"   - {s}" for s in existing_secondaries)
    else:
        existing_list = "   (none yet, you are the first sub-agent hired)"
    seeds = sample_personality_seeds(n_per_bucket=1, rng=rng)
    return SUBAGENT_ARRIVAL_PROMPT_TEMPLATE.format(
        dept_label=dept_label or dept,
        company_name=company_name,
        industry=industry,
        role_slug=role_slug,
        role_display_name=role_display_name or role_slug,
        primary_description=primary_description,
        suggested_adjacency=suggested_adjacency or "(no hint provided)",
        existing_secondaries_list=existing_list,
        seed_communication=seeds["communication"][0],
        seed_decision_posture=seeds["decision_posture"][0],
        seed_formative_background=seeds["formative_background"][0],
        seed_tell=seeds["tells"][0],
    )
