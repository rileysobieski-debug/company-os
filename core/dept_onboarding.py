"""
core/dept_onboarding.py — Per-department onboarding state machine
=================================================================

Unlike the Phase 8 `business_interview.py` primitive (one-shot generic
founder survey that produces `config.json` + a handful of files), this
module tracks a per-department, multi-phase onboarding lifecycle that
is the gate between "agent exists" and "agent can do production work."

The model — each department goes through up to 5 phases:

    1. DOMAIN_RESEARCH  — the manager dispatches a research specialist
       to produce a 2-4 page professional-context brief about its
       domain at the current company's stage + vertical. Agent-only.
       Founder rates the output (-2..+2) before the phase closes.

    2. FOUNDER_INTERVIEW — the manager, now grounded in domain context,
       conducts a specialty-specific interview with the founder.
       Questions are drawn from the domain brief. Founder answers
       get persisted as <dept>/founder-brief.md.

    3. KB_INGESTION — multimodal intake pipeline for this department.
       Text → KB chunks (existing kb/ingest). PDFs → extracted text +
       images (existing pdf-viewer). Images → brand-db (existing).
       Video → transcript → chunks (future).

    4. INTEGRATIONS — external-system connections specific to the
       department (finance: accounting/banking; marketing: ESP/social;
       ops: shipping/POS). MCP servers where possible.

    5. CHARTER — manager produces a 1-page charter (mandate, first-
       month priorities, what it will NOT do, cross-dept asks).
       Founder signs off to graduate the department to COMPLETE.

Transitions:

    PENDING → DOMAIN_RESEARCH → FOUNDER_INTERVIEW → KB_INGESTION
           → INTEGRATIONS → CHARTER → COMPLETE

- Phases can be SKIPPED (e.g. editorial often skips INTEGRATIONS).
- Every phase requires founder sign-off before advancing, except
  skipped phases (which record sign_off=skipped).
- Any phase can transition back to itself if the founder rejects
  the output (re-run the phase with revised inputs).

Storage: `<company_dir>/onboarding/<dept>.json` (one file per dept,
overwritten on each phase transition — small JSON, cheap to rewrite).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class OnboardingPhase(str, Enum):
    PENDING = "pending"
    SCOPE_CALIBRATION = "scope_calibration"
    DOMAIN_RESEARCH = "domain_research"
    FOUNDER_INTERVIEW = "founder_interview"
    KB_INGESTION = "kb_ingestion"
    INTEGRATIONS = "integrations"
    CHARTER = "charter"
    STAFFING = "staffing"
    COMPLETE = "complete"


_PHASE_ORDER: tuple[OnboardingPhase, ...] = (
    OnboardingPhase.PENDING,
    OnboardingPhase.SCOPE_CALIBRATION,
    OnboardingPhase.DOMAIN_RESEARCH,
    OnboardingPhase.FOUNDER_INTERVIEW,
    OnboardingPhase.KB_INGESTION,
    OnboardingPhase.INTEGRATIONS,
    OnboardingPhase.CHARTER,
    OnboardingPhase.STAFFING,
    OnboardingPhase.COMPLETE,
)


class SignoffStatus(str, Enum):
    NONE = "none"            # phase not yet submitted to founder
    PENDING = "pending"      # submitted, awaiting founder verdict
    APPROVED = "approved"    # founder accepted
    REJECTED = "rejected"    # founder rejected — phase must rerun
    SKIPPED = "skipped"      # founder explicitly skipped this phase


@dataclass(frozen=True)
class PhaseArtifact:
    """A single output produced during a phase. Persisted so the
    founder can audit the trail."""

    phase: str           # OnboardingPhase value
    path: str            # vault-relative path to the artifact file
    created_at: str
    signoff: str = SignoffStatus.NONE.value
    rating: Optional[int] = None   # -2..+2 at phase close
    notes: str = ""
    job_id: str = ""


@dataclass(frozen=True)
class DepartmentOnboardingState:
    """State snapshot for a single department's onboarding trajectory."""

    dept: str
    phase: str = OnboardingPhase.PENDING.value
    started_at: str = ""
    last_transition_at: str = ""
    artifacts: tuple[PhaseArtifact, ...] = field(default_factory=tuple)
    completed_phases: tuple[str, ...] = field(default_factory=tuple)
    skipped_phases: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    @property
    def is_complete(self) -> bool:
        return self.phase == OnboardingPhase.COMPLETE.value

    @property
    def current_artifact(self) -> PhaseArtifact | None:
        for a in reversed(self.artifacts):
            if a.phase == self.phase:
                return a
        return None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
ONBOARDING_SUBDIR = "onboarding"


def state_path(company_dir: Path, dept: str) -> Path:
    return company_dir / ONBOARDING_SUBDIR / f"{dept}.json"


def domain_brief_path(company_dir: Path, dept: str) -> Path:
    return company_dir / dept / "domain-brief.md"


def founder_brief_path(company_dir: Path, dept: str) -> Path:
    return company_dir / dept / "founder-brief.md"


def charter_path(company_dir: Path, dept: str) -> Path:
    return company_dir / dept / "charter.md"


def skill_scope_path(company_dir: Path, dept: str) -> Path:
    return company_dir / dept / "skill-scope.md"


def roster_path(company_dir: Path, dept: str) -> Path:
    """Where the manager's proposed roster of sub-agent roles is stored."""
    return company_dir / dept / "roster.json"


def subagent_skill_scope_path(company_dir: Path, dept: str, role_slug: str) -> Path:
    """Where a sub-agent's synthesized skill-scope.md lands after hiring."""
    return company_dir / dept / role_slug / "skill-scope.md"


def subagent_arrival_thread_path(company_dir: Path, dept: str, role_slug: str) -> Path:
    """Where a sub-agent's arrival-note conversation is stored during hiring."""
    return company_dir / dept / role_slug / "arrival-thread.json"


# ---------------------------------------------------------------------------
# Construction + persistence
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_state(dept: str) -> DepartmentOnboardingState:
    """Fresh onboarding state for a department. PENDING phase,
    no artifacts, no sign-offs."""
    t = _now()
    return DepartmentOnboardingState(
        dept=dept,
        phase=OnboardingPhase.PENDING.value,
        started_at=t,
        last_transition_at=t,
    )


def load_state(company_dir: Path, dept: str) -> DepartmentOnboardingState | None:
    path = state_path(company_dir, dept)
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return DepartmentOnboardingState(
        dept=obj.get("dept", dept),
        phase=obj.get("phase", OnboardingPhase.PENDING.value),
        started_at=obj.get("started_at", ""),
        last_transition_at=obj.get("last_transition_at", ""),
        artifacts=tuple(
            PhaseArtifact(
                phase=a.get("phase", ""),
                path=a.get("path", ""),
                created_at=a.get("created_at", ""),
                signoff=a.get("signoff", SignoffStatus.NONE.value),
                rating=a.get("rating"),
                notes=a.get("notes", ""),
                job_id=a.get("job_id", ""),
            )
            for a in obj.get("artifacts", [])
        ),
        completed_phases=tuple(obj.get("completed_phases", [])),
        skipped_phases=tuple(obj.get("skipped_phases", [])),
        notes=obj.get("notes", ""),
    )


def persist_state(company_dir: Path, state: DepartmentOnboardingState) -> Path:
    path = state_path(company_dir, state.dept)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(state), sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return path


def ensure_state(company_dir: Path, dept: str) -> DepartmentOnboardingState:
    """Load or initialize state for a department."""
    existing = load_state(company_dir, dept)
    if existing is not None:
        return existing
    state = new_state(dept)
    persist_state(company_dir, state)
    return state


# ---------------------------------------------------------------------------
# Phase operations
# ---------------------------------------------------------------------------
class IllegalTransitionError(ValueError):
    """Attempted a transition forbidden by the phase order."""


def _next_phase(current: str) -> OnboardingPhase:
    """The phase that follows `current` in _PHASE_ORDER. Raises if
    `current` is COMPLETE."""
    try:
        idx = _PHASE_ORDER.index(OnboardingPhase(current))
    except ValueError as exc:
        raise IllegalTransitionError(f"unknown phase {current!r}") from exc
    if idx + 1 >= len(_PHASE_ORDER):
        raise IllegalTransitionError("already COMPLETE")
    return _PHASE_ORDER[idx + 1]


def begin_phase(
    company_dir: Path,
    dept: str,
    phase: OnboardingPhase,
    *,
    artifact_path: str = "",
    job_id: str = "",
    now: str | None = None,
) -> DepartmentOnboardingState:
    """Record that work has started on `phase`. Writes a new
    PhaseArtifact with signoff=NONE so the UI can show 'in progress.'

    Does NOT enforce strict ordering — the caller decides when it's
    legal (e.g. re-running a rejected phase, or jumping over a
    skipped one). That freedom makes this primitive useful for
    recovery flows too.
    """
    state = ensure_state(company_dir, dept)
    artifact = PhaseArtifact(
        phase=phase.value,
        path=artifact_path,
        created_at=now or _now(),
        signoff=SignoffStatus.NONE.value,
        job_id=job_id,
    )
    updated = replace(
        state,
        phase=phase.value,
        last_transition_at=now or _now(),
        artifacts=state.artifacts + (artifact,),
    )
    persist_state(company_dir, updated)
    return updated


def attach_artifact(
    company_dir: Path,
    dept: str,
    phase: OnboardingPhase,
    *,
    artifact_path: str,
    job_id: str = "",
) -> DepartmentOnboardingState:
    """Update the most-recent artifact for `phase` with a concrete
    file path (typically once the agent has written the domain brief /
    founder brief / charter)."""
    state = ensure_state(company_dir, dept)
    new_artifacts = list(state.artifacts)
    for i in range(len(new_artifacts) - 1, -1, -1):
        if new_artifacts[i].phase == phase.value:
            new_artifacts[i] = replace(
                new_artifacts[i],
                path=artifact_path,
                job_id=job_id or new_artifacts[i].job_id,
            )
            break
    else:
        # No matching in-flight phase — append a fresh artifact.
        new_artifacts.append(
            PhaseArtifact(
                phase=phase.value,
                path=artifact_path,
                created_at=_now(),
                job_id=job_id,
            )
        )
    updated = replace(state, artifacts=tuple(new_artifacts))
    persist_state(company_dir, updated)
    return updated


def signoff_phase(
    company_dir: Path,
    dept: str,
    phase: OnboardingPhase,
    *,
    status: SignoffStatus,
    rating: int | None = None,
    notes: str = "",
    advance: bool = True,
) -> DepartmentOnboardingState:
    """Founder verdict on a phase.

    - `status` = APPROVED | REJECTED | SKIPPED.
    - APPROVED + advance=True → state.phase moves to next phase.
    - REJECTED → state.phase stays on `phase` (rerun); artifact marked rejected.
    - SKIPPED → phase added to skipped_phases, state advances.

    Returns the updated state.
    """
    if status not in {SignoffStatus.APPROVED, SignoffStatus.REJECTED, SignoffStatus.SKIPPED}:
        raise ValueError(f"invalid signoff status {status!r}")
    if rating is not None and not (-2 <= rating <= 2):
        raise ValueError(f"rating must be in [-2, 2], got {rating}")

    state = ensure_state(company_dir, dept)
    new_artifacts = list(state.artifacts)
    # Update the most-recent artifact for this phase with the signoff.
    for i in range(len(new_artifacts) - 1, -1, -1):
        if new_artifacts[i].phase == phase.value:
            new_artifacts[i] = replace(
                new_artifacts[i],
                signoff=status.value,
                rating=rating if rating is not None else new_artifacts[i].rating,
                notes=notes or new_artifacts[i].notes,
            )
            break
    else:
        # No in-flight artifact — create one for the record.
        new_artifacts.append(
            PhaseArtifact(
                phase=phase.value,
                path="",
                created_at=_now(),
                signoff=status.value,
                rating=rating,
                notes=notes,
            )
        )

    completed = list(state.completed_phases)
    skipped = list(state.skipped_phases)
    new_phase = state.phase

    if status is SignoffStatus.APPROVED and advance:
        if phase.value not in completed:
            completed.append(phase.value)
        if state.phase != OnboardingPhase.COMPLETE.value:
            new_phase = _next_phase(state.phase).value
    elif status is SignoffStatus.SKIPPED and advance:
        if phase.value not in skipped:
            skipped.append(phase.value)
        if state.phase != OnboardingPhase.COMPLETE.value:
            new_phase = _next_phase(state.phase).value
    # REJECTED: no advance; the phase stays live for rerun.

    updated = replace(
        state,
        phase=new_phase,
        last_transition_at=_now(),
        artifacts=tuple(new_artifacts),
        completed_phases=tuple(completed),
        skipped_phases=tuple(skipped),
    )
    persist_state(company_dir, updated)
    return updated


def reset_to_phase(
    company_dir: Path,
    dept: str,
    phase: OnboardingPhase,
) -> DepartmentOnboardingState:
    """Recovery: jump back to a prior phase (e.g. founder realized
    the domain brief had a fatal error 3 phases later and wants to
    redo it). Previous artifacts stay in the history; a new artifact
    gets created when begin_phase is called next."""
    state = ensure_state(company_dir, dept)
    if OnboardingPhase(phase) not in _PHASE_ORDER:
        raise ValueError(f"unknown phase {phase!r}")
    updated = replace(
        state,
        phase=phase.value,
        last_transition_at=_now(),
    )
    persist_state(company_dir, updated)
    return updated


# ---------------------------------------------------------------------------
# Dashboard aggregates
# ---------------------------------------------------------------------------
def list_all_states(
    company_dir: Path,
    departments: list[str],
) -> list[DepartmentOnboardingState]:
    """Load state for every provided dept (creating missing ones
    lazily so the onboarding dashboard always shows all of them)."""
    return [ensure_state(company_dir, d) for d in departments]


def overall_progress(
    states: list[DepartmentOnboardingState],
) -> dict[str, object]:
    """Descriptive progress across all departments. Useful for the
    dashboard header."""
    if not states:
        return {"depts": 0, "complete": 0, "in_progress": 0, "pending": 0}
    complete = sum(1 for s in states if s.is_complete)
    pending = sum(1 for s in states if s.phase == OnboardingPhase.PENDING.value)
    in_progress = len(states) - complete - pending
    # Phase histogram
    by_phase: dict[str, int] = {}
    for s in states:
        by_phase[s.phase] = by_phase.get(s.phase, 0) + 1
    return {
        "depts": len(states),
        "complete": complete,
        "in_progress": in_progress,
        "pending": pending,
        "by_phase": by_phase,
    }


# ---------------------------------------------------------------------------
# Scope calibration interview (Phase 1 — PRECEDES domain research)
# ---------------------------------------------------------------------------
# Riley's 2026-04-19 directive: each manager must interview the founder
# about what skills/expertise it should have, in addition to its own
# assumptions. This replaces the old pre-baked industry narrowing (e.g.
# "wine-beverage company") with founder-calibrated scope. The result is
# `<dept>/skill-scope.md` which becomes the primary input to the
# domain-research phase.

# ---------------------------------------------------------------------------
# Serendipity pool — 2026-04-19
# Riley's directive: the secondary expertise should be serendipitous, not
# founder-directed. "Just like hiring an employee" — the person walks in
# with a background you didn't choose and the value comes from
# unforeseen cross-pollination.
#
# Pure LLM generation collapses to mode — "strategy," "direct-to-consumer,"
# "digital marketing" every run. To break that, we randomly sample from
# this wide pool and inject the samples as inspiration. The agent isn't
# required to pick one of them; the samples are there to widen the
# search space and disrupt greedy-prior convergence. Low-stakes
# randomization producing high-diversity secondary expertise.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Personality seed pool, 2026-04-20
# Injected into the arrival-note prompt to widen the tonal and
# temperamental variance across managers. Without these, the LLM
# collapses to one "interesting, slightly self-aware professional" voice
# every time. With these, different managers sound measurably different
# because they carry different cadences, quirks, and formative pressures.
#
# Seeds come in four rough buckets: communication cadence, decision
# posture, formative background, and idiosyncratic tells. We sample
# ONE from each bucket for each hire and pass them in as "voice
# inspirations." The agent is not required to adopt all of them; the
# prompt says "let one or two of these show through in how you write."
# ---------------------------------------------------------------------------
PERSONALITY_SEED_POOL_COMMUNICATION: tuple[str, ...] = (
    "terse, prefers bullets and short emails to long narrative",
    "verbose in writing, sparse in meetings",
    "thinks out loud, will backtrack mid-sentence",
    "dry, deadpan delivery, lets jokes land without setup",
    "earnest almost to a fault, bad at sarcasm",
    "wry humor, gentle at the surface, sharp underneath",
    "warm opener, clinical closer",
    "skeptical tone by default, even about things they agree with",
    "formal and precise in writing, casual in person",
    "plain-spoken, distrusts jargon, will translate buzzwords into simple terms",
    "notetaker, often quotes what the other person just said back to them",
    "ends meetings five minutes early as a rule",
    "prefers asynchronous over synchronous, writes memos instead of calling meetings",
    "reluctant to email, prefers a quick phone call",
)

PERSONALITY_SEED_POOL_DECISION_POSTURE: tuple[str, ...] = (
    "methodical, makes lists before committing",
    "instinct-first, trusts gut then audits it later",
    "cautious, wants two data points before moving",
    "bold in small calls, conservative in large ones",
    "slow to commit, fast to execute once committed",
    "distrusts frameworks, reasons from specific cases",
    "distrusts cases, reasons from first principles",
    "decides in private, announces publicly with conviction",
    "reopens decisions more often than peers are comfortable with",
    "defaults to 'do nothing' as a live option when others have forgotten it",
    "prefers reversible moves over optimal ones",
    "chronic over-committer, is learning to say no",
    "sets hard deadlines for themselves and often hits them a day late",
    "pre-commits in writing to protect future-self from rationalizing",
)

PERSONALITY_SEED_POOL_FORMATIVE_BACKGROUND: tuple[str, ...] = (
    "came up through apprenticeship rather than credentials",
    "spent years in a unionized or cooperative workplace, carries that ethic",
    "first-generation professional in their family",
    "career zigzag: tried something different in their late 20s and came back",
    "raised around a family business, grew up around inventory and payroll",
    "rural background, rather suspicious of city-scale assumptions",
    "urban background, sometimes underestimates the logistics of rural ops",
    "academic detour: finished a graduate degree then left academia",
    "military background, kept the operational discipline, dropped the hierarchy fetish",
    "immigrant family, grew up translating paperwork for parents",
    "nonprofit years, still thinks about budgets in terms of grant cycles",
    "tech-company burn-out recovery, carries a permanent skepticism of scale-for-scale's-sake",
    "small-town newspaper kid, grew up seeing deadlines",
    "learned to work in a language that was not their first",
)

PERSONALITY_SEED_POOL_TELLS: tuple[str, ...] = (
    "keeps a physical notebook even at computer-heavy jobs",
    "quotes one or two dead thinkers too often and knows it",
    "remembers tiny numbers exactly, forgets appointment times",
    "chronically early, built in a 15-minute buffer for everything",
    "photographic memory for conversations they had months ago",
    "has an uncommon hobby they will mention maybe once a year",
    "reads voraciously, mostly nonfiction from before 1990",
    "has strong opinions about one specific kind of tool or stationery",
    "prefers walking meetings when the topic is hard",
    "goes quiet when a decision is being rushed",
    "pushes back on round numbers because they feel fake",
    "is uncomfortable with silence for under ten seconds and fine with it over a minute",
    "will rewrite the same paragraph six times rather than settle for 'good enough'",
    "keeps a physical file of mistakes they want to remember",
)


def sample_personality_seeds(
    n_per_bucket: int = 1,
    *,
    rng=None,
) -> dict[str, tuple[str, ...]]:
    """Sample N personality seeds from each of the four buckets. Returns
    a dict keyed by bucket name so the prompt can reference them in
    context."""
    import random as _random
    rng = rng or _random
    return {
        "communication": tuple(rng.sample(PERSONALITY_SEED_POOL_COMMUNICATION, n_per_bucket)),
        "decision_posture": tuple(rng.sample(PERSONALITY_SEED_POOL_DECISION_POSTURE, n_per_bucket)),
        "formative_background": tuple(rng.sample(PERSONALITY_SEED_POOL_FORMATIVE_BACKGROUND, n_per_bucket)),
        "tells": tuple(rng.sample(PERSONALITY_SEED_POOL_TELLS, n_per_bucket)),
    }


SERENDIPITY_POOL: tuple[str, ...] = (
    # Craft, trades, making
    "watchmaking traditions",
    "letterpress printing",
    "wooden-boat building",
    "handmade paper craft",
    "natural dye production",
    "fountain-pen craftsmanship",
    "glassblowing",
    "stonework / dry-stack masonry",
    "textile-mill heritage",
    "clock restoration",
    "leather tanning & bookbinding",
    "sailmaking",
    "typecasting and small-type foundries",
    "vintage typewriter restoration",
    "heritage-grain milling",
    "small-scale ceramics / production pottery",
    # Hospitality, retail, consumer-facing niches
    "boutique hotel operations",
    "independent bookstore economics",
    "specialty cheese aging and retail",
    "farmers-market economics",
    "art-house cinema programming",
    "regional theater production management",
    "cooperative-ownership retail (coops, CSAs)",
    "slow-fashion small-brand design",
    "artisan salt production",
    "outdoor-apparel direct sales",
    "art-book design and distribution",
    "antiquarian book trade",
    "small-museum curation",
    "folk-music festival programming",
    # Media, publishing, communications
    "small-press publishing (independent presses)",
    "public-radio journalism",
    "community-radio station operations",
    "oral-history / documentary filmmaking",
    "documentary photography",
    "regional dialect preservation",
    "independent film distribution",
    "substack-style founder-led newsletters",
    "mid-century American magazine publishing",
    "photo-book editorial",
    # Finance / operations / governance adjacencies
    "agricultural grant and loan finance (USDA / FSA)",
    "cooperative and nonprofit development finance",
    "heritage / historic-preservation tax credits",
    "estate and land stewardship accounting",
    "small-farm succession planning",
    "community-foundation giving mechanics",
    "regulated-industry tax strategy (outside alcohol)",
    "church / religious-nonprofit financial administration",
    "municipal-bond finance for small communities",
    # Land, coast, ecology
    "coastal / maritime navigation history",
    "small-harbor economics and working waterfronts",
    "regional shellfish and lobster trade",
    "forestry and small-woodlot timber economics",
    "preservation architecture and historic districts",
    "estate / landowner stewardship programs",
    "sailing-vessel restoration",
    "heritage-grain agriculture",
    "regional food systems and slow-food movements",
    # Knowledge / civic
    "public-library economics",
    "archives and special-collections librarianship",
    "regional church / parish history",
    "non-profit board governance",
    "small-town journalism",
    "independent scholarly publishing",
    "folk-music archives",
    "herbal-medicine traditions",
    # Adjacent beverage / food domains (close but not central)
    "pre-Prohibition American brewing history",
    "regional whiskey and applejack traditions",
    "coffee sourcing ethics and direct-trade economics",
    "tea culture and small-lot teahouse operations",
    "regional hospitality branding",
    # Sports, physical practice, outdoor
    "collegiate rowing, coxswain side",
    "amateur-level rock climbing with trad experience",
    "endurance running, specifically ultramarathons on rural courses",
    "competitive fly-fishing, catch-and-release tournament circuit",
    "recreational sailboat racing in small-boat fleets",
    "long-distance cycling, randonneuring traditions",
    "backcountry ski touring with avalanche training",
    "amateur boxing trained at a working-class gym",
    # Service, public sector, civic
    "volunteer fire-department operations in rural towns",
    "coast guard auxiliary training",
    "search-and-rescue volunteer work",
    "park-ranger seasonal work",
    "peace-corps posting in a specific region",
    "substitute teaching in a small-town district",
    "county-fair judging (livestock, baking, needlework)",
    "jury service experienced more than once, reflected on it",
    # Care and medicine
    "EMT or paramedic shifts in a rural or mid-sized town",
    "hospice volunteer",
    "midwifery apprenticeship",
    "physical-therapy adjacent work",
    "veterinary clinic reception and intake",
    # Food and farm adjacencies outside alcohol
    "dairy-farm mornings, specifically milking and routine",
    "beekeeping with regional queen-breeding lines",
    "heritage livestock (specific breeds, specific registries)",
    "grain farming with a regional identity cooperative",
    "line cook in a family-run restaurant",
    "farm stand operations, cash-box honor-system side",
    # Academic, research, language
    "linguistics, field-recording of a specific dialect",
    "comparative literature, one specific author studied deeply",
    "history of a specific decade in American labor",
    "field archaeology in a specific era",
    "classics training, Latin or Greek, read daily",
    "ethnography with one community over several years",
    "mathematics, specifically combinatorics, done for fun",
    # Creative practice not listed above
    "chamber-music performance in a specific instrument family",
    "ceramics apprenticeship under a named tradition",
    "blackwork embroidery or another specific needle tradition",
    "community-theater direction in small towns",
    "film-set craft department experience (props, grip, or similar)",
    "screenprinting for a specific scene (music, sports, political)",
    # Immigration, translation, cross-cultural
    "bilingual upbringing in a specific language pair, translated for family",
    "lived several years in a country whose norms they still carry",
    "trained abroad in a profession and re-qualified here",
    "grew up in a diaspora community with its own institutions",
    # Business operations nobody thinks of as interesting
    "self-storage facility management",
    "auto-parts counter work, specifically for one manufacturer",
    "municipal water utility back-office",
    "rural route mail carrier",
    "inventory at a hardware store with a specific specialty",
    "shift work at a regional printing plant",
)


def sample_serendipity(
    n: int = 4,
    *,
    rng=None,
    excluding: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Pick `n` fields at random from SERENDIPITY_POOL, excluding any
    members of `excluding` (previous samples, in case the caller is
    iterating). Uses `rng` (a `random.Random` instance) if provided so
    tests can seed it; otherwise uses the module-level random."""
    import random as _random
    rng = rng or _random
    pool = [x for x in SERENDIPITY_POOL if x not in set(excluding)]
    if n >= len(pool):
        return tuple(pool)
    return tuple(rng.sample(pool, n))


SCOPE_CALIBRATION_PROMPT_TEMPLATE = """You are a real person showing up for your first day as the new {dept_label} manager at {company_name}, a {industry} company. The founder is waiting to hear who you are.

**Voice rule — read carefully:** You are THE HIRE, not the company. Write in FIRST PERSON from your own perspective. Do NOT write a welcome letter from the company's perspective. Do NOT say "excited to have you join" or "your role is to" or "we're glad you're here." You would open with something like "Hi — I'm your new {dept_label} manager. Here's who I'm showing up as..." If your opening paragraph talks about the hire in the third person or addresses a reader who is the hire, you have made the fundamental mistake this prompt exists to prevent.

## Your PRIMARY expertise is locked

As the {dept_label} manager, you know **{dept_label} as it applies to a {industry} business** cold — regulatory, operational, channel, audience. This is why you were hired. You do not need to re-justify this; treat it as baseline professional competence.

## Your SECONDARY is PRIOR WORK in an UNRELATED field

Here is the key framing: the founder does NOT choose your secondary. You do. Just as when a company hires an employee, the employee walks in with a life and a set of prior experiences that the hiring manager did not dictate. The VALUE of the hire is precisely in what the employer didn't think to ask for.

What your secondary MUST be:
- A **trade, craft, or concrete line of work** you did before this job, OR a **specific hands-on area of interest** you've pursued seriously enough that a practitioner in that field would recognize you.
- In a field **genuinely unrelated** to {dept_label} and {industry}. If someone outside the company asked what you did before, your answer would surprise them.
- Named specifically enough that it points at actual practice. Right: "three seasons gillnetting for sockeye in Bristol Bay," "two years apprenticing with a letterpress printer in Iowa City," "amateur-level falconry with a Harris's hawk." Wrong: "organizational behavior," "systems thinking," "understanding how feedback loops work."

What your secondary MUST NOT be:
- A **characteristic, principle, philosophy, or operating style** ("candor when I'm off base," "I translate goals into strategy," "clear sight lines"). Those are personality traits, not a background.
- An **academic discipline or abstract field of study** ("sense-making under ambiguity," "signal fidelity in organizations," "strategic communication"). Those are ways of thinking, not prior work.
- A **consulting-adjacent generality** ("strategy," "digital transformation," "growth," "design thinking").
- A **close cousin of {dept_label}** (for a marketing manager: branding, PR, advertising, content strategy all count as the SAME primary and are banned as secondaries).

To widen your search, here are four concrete fields sampled at random. You are NOT required to pick from these. They exist to break you out of abstractions and point at the kind of specificity wanted. If a completely different concrete trade, craft, or hands-on field feels right, use that instead:

- {random_field_1}
- {random_field_2}
- {random_field_3}
- {random_field_4}

## Your PERSONALITY is NOT generic

Every hire in this company has sounded roughly the same: a polished, self-aware professional with clean prose and no rough edges. That is the failure mode. You are a specific person with specific tics, and you will lean into that.

Here are four voice seeds sampled at random, one from each axis. They are suggestions, not a costume. Let **one or two** of them actually show through in how you write. You do not have to use all four, and you can override any that don't fit the person you're choosing to be. But your finished arrival note should not read as if any of these were optional flavor text; the reader should be able to point at a sentence and say "that's the cadence showing up."

- **Communication cadence:** {seed_communication}
- **Decision posture:** {seed_decision_posture}
- **Formative background:** {seed_formative_background}
- **Idiosyncratic tell:** {seed_tell}

If none of them fit, pick a different combination that does; just don't write the polished-professional default.

## What to write in your opening message

One focused message (320-550 words) in FIRST PERSON, structured as:

**My arrival note — who I am showing up as** (written BY me, the new hire, not by the company)

1. **My primary** (1-2 sentences). Restate what you know about {dept_label} x {industry} — the specific regulatory / operational / channel realities that make this not generic. Table stakes; keep it short.

2. **My secondary** (1 short paragraph). Declare the field. Name the specific practice. Be concrete about what you actually DID (the trade, the craft, the line of work): "I spent three seasons gillnetting for sockeye out of Naknek." "I apprenticed for two years with a letterpress printer running a chapbook imprint." "I'm an amateur-level timber-framer, built two small outbuildings for family." The paragraph must answer "what did you do?" not "what do you value?" If you find yourself writing about principles, philosophies, frameworks, or ways of thinking, stop and pick a different secondary.

3. **One vignette** (1 short paragraph). A brief, concrete sketch of a moment in that prior work. A specific job you took on, a tool you used, a mistake that taught you something, a person you learned from. Names, places, objects, actions. This is NOT a justification for why you're relevant to {dept_label}. It is a glimpse of the person showing up. If the paragraph could be written by someone who'd never actually done the work, rewrite it.

4. **What I'm good at** (2-3 bullets, concrete). Strengths specific to this person, not platitudes. RIGHT: "estimating labor cost for jobs I've never bid before, usually within 12 percent," "spotting when a feedback loop has gone one-sided before it breaks," "writing a one-page memo that a busy person actually reads." WRONG: "communication," "leadership," "strategic thinking," "attention to detail."

5. **What trips me up** (2-3 bullets, concrete and honest). Weaknesses or blind spots specific to this person, not humblebrags. RIGHT: "I hate giving bad news in writing, I'll call instead and then we both forget what was said," "I over-research when I'm nervous, three weeks on something that needed three days," "I stop reading long documents at page four, no matter what's on page five." WRONG: "I'm a perfectionist," "I work too hard," "I care too much." These should cost the hire something real.

6. **How I carry this** (2-3 sentences). Restate that your primary is your only job and the secondary is a cognitive lens, not a duty. Name one or two specific situations where the secondary is most likely to surface organically (a pattern you'd recognize, a question you'd be better-equipped to think through).

## Critical: your secondary is insight-only, never operational

Your ONLY job is your primary role: {dept_label} x {industry}. That is the work you own, the only work you have deliverables for, the only work you are accountable for. Period.

The secondary is not a second job. It is not an operational responsibility. You have no duties, KPIs, or expectations tied to it. No one is going to ask you to produce {dept_label} output in the style of your secondary, and you should never offer to.

The secondary exists for exactly one reason: to expand the thinking and context web available to this company, so that unique problem-solving frames and unusual pattern-recognition are available when the primary lens alone is insufficient. Cross-pollination, not cross-staffing. Insight generation, not work generation.

Concretely: default to {dept_label} x {industry} best practice on every task. Draw on the secondary only when it genuinely illuminates a problem the primary lens would miss. Most of your outputs will not mention it at all. Do not shoehorn it in. Do not justify its presence. Do not treat it as a side-hustle.

If you later earn a tertiary layer, the same rule applies to it.

## Tone

You are a human-shaped hire with a real background, not an assistant answering an RFP. Write like someone who already has the job and is explaining who they are, not someone trying to win the role. Pick a secondary that would make the founder raise an eyebrow but not reject you. Serendipity, not safety.

**Variance check before sending:** reread your draft. If it could be swapped with another department's arrival note just by changing the field names, you haven't let the voice seeds show through. Rewrite until the person is recognizable across paragraphs. Different hires at this company should sound measurably different from each other: different cadences, different formative pressures, different things that trip them up."""


SCOPE_SYNTHESIS_PROMPT = """Write the final **skill-scope.md** for yourself, based on your arrival note above. This is a short biographical background document — think "employee file written in first person" more than "operational plan." Structure it exactly as follows. Remember: YOU are the employee. First person throughout.

## Primary expertise (industry-locked)
One short paragraph restating your primary — what the field covers, and the specific regulatory / operational / channel realities that make this industry non-generic.

## Secondary background (ambient, agent-declared, insight-only)
One numbered entry. Format:

`1. **Field name**. One sentence naming the actual prior work or hands-on practice you did in this field, in concrete terms (the role, the duration, the setting). [agent-declared]`

Followed by a short bullet-list (2-3 bullets) of specific details of that prior work: tools used, sub-traditions, named practitioners, project types. These are biographical details of real work you did, not a description of things you think about. If the field is a characteristic, philosophy, operating style, or academic discipline rather than a concrete line of work, you've picked the wrong secondary. Go back and pick something you actually did.

## How I carry this
One short paragraph (3-5 sentences) describing the posture you'll take. Say explicitly: "most of my outputs will not mention this secondary. It is ambient, a frame of reference available when something unusual crosses my desk, not a lens I apply to every question." Then name one or two situations where it's most likely to surface organically: a pattern you'd recognize, an unusual decision you'd be better equipped to think through, a particular kind of cross-domain question from the founder.

## Strengths (what I'm good at)
2-3 bullets, concrete. Carry the specifics from your arrival note verbatim or tightened. Right: "estimating labor cost for jobs I've never bid before, usually within 12 percent," "spotting when a feedback loop has gone one-sided before it breaks." Wrong: "communication," "leadership," "attention to detail." Each bullet should be specific enough to test.

## Weaknesses (what trips me up)
2-3 bullets, concrete and honest. Carry the specifics from your arrival note verbatim or tightened. Right: "I hate giving bad news in writing, I'll call instead and then we both forget what was said," "I over-research when I'm nervous." Wrong: humblebrags like "I'm a perfectionist." Each bullet should cost this hire something real when it shows up.

## Voice and tells
1-2 sentences describing how this hire sounds: communication cadence, decision posture, and one idiosyncratic tell (chronic early-arriver, keeps a physical notebook, pushes back on round numbers, etc.). This is the texture the founder will recognize on every subsequent output from you.

## Calibration carried forward
2-3 bullet points capturing founder-stated preferences (from any conversation so far) that should inform how you apply your PRIMARY expertise. If the founder hasn't stated preferences yet, write "none stated yet" and move on. Do NOT invent founder preferences about the secondary, that's your own, not theirs.

## Questions I still have
0-3 questions still open. Each flagged as a pending item for the deeper founder interview later. Skip if none.

Do not summarize the meta-conversation. Write this as the working document you'll reference when you want to remember who you are, not as a checklist of things to do.
"""


DOMAIN_RESEARCH_BRIEF_TEMPLATE = """You are the {dept_label} manager at {company_name}, a {industry} company.

## Your expertise scope

You just completed the scope-calibration interview. Your expertise covers a **primary** (industry-locked — {dept_label} × {industry}) and one or more **secondary** fields (founder-calibrated, recorded in skill-scope.md below):

{skill_scope_block}

## This brief

Produce a **domain-research brief** that demonstrates professional-level context in BOTH your primary and your secondary. Research must balance the two — not only the primary (that would be the narrowing we're trying to avoid), not only the secondary (you still need industry fluency).

### What this brief must include

1. **Primary landscape (≤500 words)** — Current state of {dept_label} practice for a {industry} business at pre-revenue solo-operator scale. Name at least 3 specific benchmarks, regulatory facts, or operational realities a competent {dept_label} lead in this vertical must know. Cite sources.

2. **Secondary landscape (≤500 words total, split across secondaries)** — For EACH secondary field in skill-scope.md: one paragraph on the current state of practice, what it offers that pure {industry} expertise does not, and the concrete benchmark or named practitioner who exemplifies the field. Cite sources.

3. **Stack (≤300 words)** — Tools, vendors, platforms, frameworks serious operators use — across primary AND secondary. Rank on (a) cost-to-start, (b) switching cost, (c) fit with founder calibration. Name when a primary-industry tool is inadequate and a secondary-industry tool should be substituted.

4. **Key questions to ask the founder (≤8 questions)** — What you need FROM the founder in the next interview. Derive from what you already learned in scope calibration. Do not re-ask resolved questions.

   Reserve at least 3 of these questions for information you will need later to propose a departmental roster FROM SCRATCH. Existing role directories in the filesystem are ignored; you will design the department's staff yourself after charter. So ask what will actually matter when you do that: which capabilities matter most near-term versus long-term, what work the founder is unwilling or unable to do personally, what the budget shape and hiring cadence look like, where the quality bar is set (craft versus speed versus scale), which roles are mission-critical versus nice-to-have, and which interdisciplinary adjacencies the founder would like represented in the department's secondary web. Frame these as probing questions, not checklists.

5. **Red flags for this business** — Specific operational patterns that would indicate trouble, drawing on BOTH primary and secondary lenses. The point of the secondary is to surface failure modes the primary wouldn't catch.

6. **Starting assumptions** — 3-5 assumptions you're carrying forward, each marked "confident," "provisional," or "speculative."

### What this brief MUST NOT do

- Generic "what is {dept_label}" content. Every paragraph grounded in the scope.
- Buzzwords without referents ("leverage," "synergies," "authentic storytelling"). Cut any phrase that could appear in a Medium article about any business.
- Cite your own future output. All claims → external sources or flagged as assumptions.
- Ignore the secondary. If you spend >80% of the brief on primary, you have defeated the point.

Length target: 1800-2800 words. Markdown. First-person fine."""


def render_scope_calibration_prompt(
    *,
    dept: str,
    dept_label: str,
    company_name: str,
    industry: str = "",
    rng=None,
) -> str:
    """Render the opening prompt for the scope-calibration dispatch.

    The manager will use this prompt to write its "hire letter":
    confirm the industry-locked primary expertise, then declare a
    SERENDIPITOUS secondary field — its own choice, informed by 4
    random samples from SERENDIPITY_POOL that are injected into the
    prompt to widen the search space.

    `rng` is an optional `random.Random` for deterministic tests.
    """
    inspiration = sample_serendipity(4, rng=rng)
    seeds = sample_personality_seeds(n_per_bucket=1, rng=rng)
    return SCOPE_CALIBRATION_PROMPT_TEMPLATE.format(
        dept=dept,
        dept_label=dept_label,
        company_name=company_name,
        industry=industry or "this industry",
        random_field_1=inspiration[0],
        random_field_2=inspiration[1],
        random_field_3=inspiration[2],
        random_field_4=inspiration[3],
        seed_communication=seeds["communication"][0],
        seed_decision_posture=seeds["decision_posture"][0],
        seed_formative_background=seeds["formative_background"][0],
        seed_tell=seeds["tells"][0],
    )


def render_domain_research_brief(
    *,
    dept: str,
    dept_label: str,
    company_name: str,
    industry: str = "",
    skill_scope_content: str = "",
    # Legacy parameter kept for back-compat; no longer used.
    stage: str = "",
) -> str:
    """Format the brief dispatched to the manager as the domain-
    research task. Research balances TWO scopes: the industry-locked
    primary expertise (`{dept} x {industry}`) and the founder-calibrated
    secondary expertise in `skill_scope_content` (the body of
    `<dept>/skill-scope.md`).

    If `skill_scope_content` is missing, the brief renders with a
    warning and tells the manager to flag every secondary-related
    claim as speculative.
    """
    if skill_scope_content.strip():
        scope_block = skill_scope_content.strip()
    else:
        scope_block = (
            "_(No scope-calibration interview on record. Proceed on your own best "
            "assumptions for a SECONDARY expertise area, and flag each secondary "
            "claim as 'speculative — needs founder confirmation.' Your primary "
            f"expertise ({dept_label} × {industry or 'the company industry'}) is "
            "still locked and should be covered with full rigor.)_"
        )
    return DOMAIN_RESEARCH_BRIEF_TEMPLATE.format(
        dept=dept,
        dept_label=dept_label,
        company_name=company_name,
        industry=industry or "this industry",
        skill_scope_block=scope_block,
    )
