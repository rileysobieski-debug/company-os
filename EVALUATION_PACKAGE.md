# Company OS — External Evaluation Package

**Prepared for:** AI-powered evaluation teams (Grok, Gemini, other frontier-model agents)
**Prepared on:** 2026-04-18
**Build status:** 741 tests passing, 35 skipped. Phases 0.5–13 complete. Phase 14 (60-day operational dogfood) ready to begin.
**Author:** Riley Sobieski (solo operator, 5-10 hrs/wk, Maine)
**Framework code:** `C:\Users\riley_edejtwi\Obsidian Vault\company-os\` (~15k lines Python, 58 test files, 12 plugin skills, 9-dept wine-beverage vertical pack)

---

## What this document is and what it isn't

**It is:** a self-contained evaluation package for a multi-agent orchestration framework built around an integrated governance stack. Everything you need to evaluate the engineering, the architectural choices, the proposed research directions, and the empirical methodology is in this file.

**It isn't:** a marketing document, a finished commercial product, or a research paper. No ML novelty is claimed. The code is competent application engineering; the integration pattern and the empirical methodology are the contribution.

**What feedback is most valuable:**

1. Where is the architectural thinking wrong or weak?
2. What primitives am I missing that would be cheap to add?
3. Where is the governance stack susceptible to adversarial prompt crafting?
4. Are any of the "novel/uncommon" claims actually prior art I'm unaware of? Please cite.
5. Of the proposed ambient-awareness and relationship layers, which mechanisms would fail first under real load?
6. Which research directions (§9) are actually worth pursuing versus intellectually seductive distractions?

**What feedback isn't useful:** marketing suggestions, "build X framework instead," or "this is impressive" pleasantries. The project is committed; the author needs critique, not validation.

---

## Table of contents

1. [One-sentence pitch](#1-one-sentence-pitch)
2. [Why this exists — the problem space](#2-why-this-exists)
3. [Architecture at a glance](#3-architecture)
4. [Complete source manifest](#4-source-manifest)
5. [Key primitives (full source)](#5-key-primitives-full-source)
6. [Novel / uncommon patterns — the honest highlight reel](#6-novel-patterns)
7. [What's derivative / standard](#7-derivative)
8. [Proposed ambient awareness layer (spec, not built)](#8-ambient-awareness)
9. [Proposed relationship layer (spec, not built)](#9-relationship-layer)
10. [Research-worthy directions](#10-research-directions)
11. [Empirical methodology — the 60-day dogfood](#11-empirical-methodology)
12. [Specific questions for evaluators](#12-questions)

---

## 1. One-sentence pitch

Company OS is a **multi-agent orchestration framework built around an integrated governance stack** (citation contract + scope matrix + state-authority graph + adversary with drift-detection benchmark + kill-switch retros + freshness clock + training loop), wrapped around a deliberately simple 3-tier agent hierarchy (Orchestrator → Manager → Specialist), tested by **dogfooding on a real pre-revenue operation** (Maine-based winery + companion editorial publication) for 60 days instead of benchmark suites.

---

## 2. Why this exists — the problem space {#2-why-this-exists}

The agent-framework landscape in 2026 splits cleanly:

**Orchestration-first frameworks** — LangGraph, AutoGen, CrewAI, OpenAI Swarm. Strong around agent topology and dispatch. Weak around persistence, governance, conflict resolution. Treat agents as ephemeral or semi-ephemeral.

**Memory-first frameworks** — MemGPT, Letta, Mem0. Strong around long-term per-agent memory. Weak around multi-agent coordination and structural governance. Typically single-agent deep memory rather than multi-agent relational memory.

**What neither camp addresses coherently:**
- What happens when agents produce load-bearing claims without provenance?
- How do you resolve deterministic conflicts between two sources (KB vs. founder decision vs. memory)?
- How do you catch a long-running specialist that's silently drifting from its original role?
- How do you fire an agent and not accidentally reuse its contaminated memory?
- How do you enable autonomous cross-agent coordination without compound hallucination cascades?

Company OS is an attempt to sketch an integrated answer — not by building another orchestration framework, but by implementing a **governance layer** on top of a deliberately simple dispatch topology. The specific contribution is the **integration** of roughly 30 primitives across provenance, conflict resolution, scope enforcement, drift detection, training-signal isolation, and autonomous-loop safety.

---

## 3. Architecture at a glance {#3-architecture}

### Three agent tiers

```
        ┌─────────────────┐
        │   Orchestrator  │    single instance; talks to founder directly
        │  (top-level)    │    can dispatch managers + convene the Board
        └────────┬────────┘
                 │
        ┌────────┼────────┬────────┐
        │        │        │        │
    ┌───▼──┐ ┌──▼──┐ ┌───▼──┐  ...N managers (one per active department)
    │Mgr A │ │Mgr B│ │Mgr C │      use claude_agent_sdk
    └───┬──┘ └──┬──┘ └───┬──┘
        │       │        │
      [N specialists per manager — declared in YAML]
           │
           └──> invoke agentic skills (employees) via Agent tool
                employees = skill_runner.run() with bounded tool loop
```

**Orthogonal to the tiers:**
- **Board** (6 voices: Strategist, Storyteller, Analyst, Builder, Contrarian, KnowledgeElicitor) — convenes on request or milestone escalation, separate deliberation layer with its own prompts and calibration.
- **Adversary** — Path C first-class agent whose only job is to stress-test founder direction. Independent from the Board Contrarian (which is a debate voice in a group deliberation; the Adversary is solitary and loyal only to the thesis being challenged).

### Governance stack (integrated around dispatch)

```
Dispatch flow: Founder → Orchestrator → Manager → Specialist → output

At each stage:
  1. Citation contract      — load-bearing claims require structured Reference objects
  2. State authority        — 8-tier priority for conflict resolution
  3. Scope matrix           — per-dept OWNS/NEVER enforced at dispatch-time
  4. Turn cap               — max_inter_agent_turns=3, escalates to founder
  5. Watchdog               — verifies claim text appears verbatim in cited source
  6. Freshness clock        — assumptions TTL'd: 5 uses or 14 days → review
  7. Evaluator              — rubric-based PASS/NEEDS_REVIEW/FAIL, fail-any-hard-gate
  8. Memory updater         — idempotent append gated by evaluator
  9. Routing                — output → approved/pending-approval/rejected per verdict
  10. Cost envelope         — per-call and per-session spend caps

Periodic:
  * Adversary drift benchmark — every 30d or 10 activations, founder rates last 3;
    2 consecutive sub-threshold → adversary memory + prompt auto-reset
  * Autoresearch proposals   — evaluator triggers on 3+ fails; 7-day TTL; budget-
    deferred escalates to founder

Kill-switch:
  * /kill <specialist> → forced 3-question retro (expected/saw/fix) → prompt
    reset to last-known-good → retro surfaces in next adversary activation
```

### State authority graph (the conflict-resolution primitive)

The 8 priorities, top wins. Full source in §5.1 below.

| Priority | Store | Authoritative for |
|---|---|---|
| 1 | Founder authority | Identity, convictions, non-negotiables |
| 2 | Decisions | Chronological tiebreaker; can supersede 1 iff explicit |
| 3 | Knowledge Base | External-world facts (as-of-date required) |
| 4 | Brand DB | Voice and aesthetic reference (not facts) |
| 5 | Handshakes | Audit trail only — never a source of new claims |
| 6 | Memory files | Derived summary — never authoritative on its own |
| 7 | Taste profile | Preference signal only |
| 8 | Assumption log | Provisional; has TTL |

Deterministic resolver; single exception rule (Decision may supersede higher-priority iff it explicitly lists the superseded ref AND is chronologically newer). Structural tiebreak by lexicographic ref when timestamps tie.

---

## 4. Complete source manifest {#4-source-manifest}

### `core/` — agent engine

| File | Lines | Purpose |
|---|---|---|
| `core/config.py` | 130 | Central accessor for model-by-role, cost envelope, vault dir, output subdirs, permission mode. Per-dept skill-agent migration env flag. |
| `core/env.py` | 90 | `load_env()` + pure `read_env_file()` |
| `core/llm_client.py` | 200 | `single_turn()` wrapper + `TokenLedger` with jsonl persistence |
| `core/company.py` | 160 | `CompanyConfig` dataclass, `load_company()` |
| `core/orchestrator.py` | 700 | Top-level coordinator; dispatches managers, convenes board, writes decisions, logs sessions |
| `core/board.py` | 650 | 6-voice advisory layer with calibrated profiles + `convene_board()` |
| `core/meeting.py` | 350 | `run_department_meeting()` + `run_cross_agent_meeting()` with board-role and manager participant specs |
| `core/employees.py` | 210 | Legacy workers (research/writer/analyst/data-collector) as AgentDefinitions — the pre-v2 shim, still default for un-migrated depts |
| `core/skill_registry.py` | 180 | YAML skill loader + `SkillSpec` schema, `reasoning_required` flag for Opus opt-in |
| `core/skill_runner.py` | 290 | Pure + agentic skill execution with bounded tool loop and `SkillResult` envelope |
| `core/notify.py` | 200 | O(1) append + amortized trim notification log |
| `core/training.py` | 461 | Phase 10 — training transcripts, benchmark authoring, `mark_reasoning_required` YAML toggle |
| `core/autoresearch.py` | 357 | Phase 11 — AutoresearchProposal lifecycle (pending→running→completed/expired/escalated) with 7-day TTL |
| `core/adversary.py` | 526 | Phase 12 — AdversaryReview, KillSwitchRetro, DriftWindow, `consider_reset_trigger()` with 2-consecutive-failure auto-reset |
| `core/vertical_pack.py` | 214 | Phase 13.1 — `DeptBriefTemplate`, `VerticalPack`, `load_vertical_pack()`, `render_dept_brief()` with placeholder substitution |

### `core/primitives/` — dependency-light shared building blocks

| File | Lines | Purpose |
|---|---|---|
| `state.py` | 280 | **KEY** — Provenance check + `AuthorityPriority` 8-tier enum + `Claim`/`ResolvedClaim` + `resolve_conflict()` with Decision-supersession exception + `render_state_authority_doc()` (full source in §5.1) |
| `scope_matrix.py` | 306 | **KEY** — `DepartmentScope`, `ScopeMatrix`, YAML parse/load, `validate_output_in_scope()`, `find_overlaps()` returning `OverlapReport` (full source in §5.3) |
| `cost.py` | 150 | `BudgetSession`, `check_budget()`, band thresholds |
| `turn_cap.py` | 140 | `TurnCapLedger`, `check_turn_cap()`, `DEFAULT_MAX_INTER_AGENT_TURNS=3` |
| `citation.py` | 195 | `Reference`, `ReferencedClaim`, `OriginalCitation`, `parse_references()`, `requires_references()` |
| `drift.py` | 131 | `watchdog_check()` — verifies referenced messages exist + claim text appears verbatim + blocks path traversal |
| `freshness.py` | 167 | `Assumption`, `FreshnessStatus` (fresh/needs_review/grace/demoted), lifecycle mutations |
| `voice.py` | 200 | Pure `diff_from_brand(draft, entries) → VoiceDiff` with token+bigram alignment, stopword-filtered, deterministic |
| `taste.py` | 220 | `TasteProfile`, `fit_preference_vector()` (cosine × confidence) |
| `ab.py` | 220 | Taste Inbox math — incremental mean per axis, `discover_axis()` first-principles axis scoring |
| `tool_skill_map.py` | 130 | Phase 9.4a migration — `translate_tools_to_skills()` → grants skills whose tool-set is a subset of declared |

### `core/dispatch/` — handshake + evaluator + memory updater + drift guard

| File | Lines | Purpose |
|---|---|---|
| `handshake_runner.py` | 260 | `Handshake`, `record_handshake`, `handshake_to_claim` → Priority 5 HANDSHAKE Claim |
| `evaluator.py` | 380 | `Verdict` with `VerdictStatus`{PASS, NEEDS_FOUNDER_REVIEW, FAIL}, `evaluate_output(brief, output, rubric, judge)`, `consider_autoresearch_trigger()` |
| `memory_updater.py` | 250 | `record_dispatch_outcome()` — idempotent append via `derived_from_ts` marker; routes output PASS→approved/ NEEDS_REVIEW→pending-approval/ FAIL→rejected/ |
| `drift_guard.py` | 180 | `evaluate_dispatch()` composes watchdog + turn-cap + provenance → unified `DriftGuardReport` |
| `hooks.py` | 150 | `make_handshake_pre_hook` + `make_evaluate_post_hook` factories matching Chunk 1a.8 pre/post signatures |

### `core/onboarding/` — first-time initialization + setup-wizard primitives

| File | Lines | Purpose |
|---|---|---|
| `shared.py` | 90 | `OnboardingResult`, `needs_onboarding()`, `ONBOARDING_MAX_TURNS` |
| `department.py` | 340 | JIT manager onboarding — manager prompt body, specialist proposals, founder-approved roster |
| `board.py` | 280 | Board profile calibration on first convening |
| `orchestrator.py` | 500 | Interactive founder Q&A for Orchestrator setup |
| `runner.py` | 100 | `check_and_run_all_onboarding()` top-level loop |
| `business_interview.py` | 400 | §5.2 — 18-question interview across 7 phases (Basics/Vision/Constraints/Founder/Convictions/Pre-mortem/Priorities) |
| `dept_selection.py` | 190 | `VERTICAL_DEPARTMENTS` 9-tuple + deterministic keyword-scored `suggest_top_n_departments()` |
| `premortem.py` | 180 | `PremortemContext`, `load_premortem_from_profile()`, `inject_premortem_context(body, ctx, kind)` with idempotent marker |
| `first_deliverable.py` | 230 | `propose_first_deliverable(answers, active_departments)` — scores KB-independent deliverable candidates |
| `pre_warm.py` | 220 | `PrewarmMode`{SYNCHRONOUS, PREWARM, DORMANT}, `schedule_manager_onboardings()`, `PrewarmLedger` with save/load |
| `dept_creation.py` | 296 | **NEW** — operator-initiated dept creation with slug validation, config+scope-matrix update |

### `core/managers/`, `core/kb/`, `core/brand_db/`

| File | Lines | Purpose |
|---|---|---|
| `managers/base.py` | 630 | Manager class; `dispatch_manager()` with pre/post hooks + `SpecialistResult` typed envelope; env-gated skill-agent vs. legacy-worker path |
| `managers/loader.py` | 340 | File-driven auto-discovery of departments + specialists (any folder with `department.md` / `specialist.md`) |
| `managers/skill_agents.py` | 110 | `build_skill_agent(spec)` → AgentDefinition wrapping skill YAML (prompt bakes rubric + iteration budget) |
| `kb/ingest.py` | 180 | Source → chunks with provenance frontmatter |
| `kb/retrieve.py` | 150 | `kb_query()` — keyword backend, sqlite-vec deferred |
| `kb/claim.py` | 60 | `chunk_to_claim` → Priority 3 KB Claim |
| `brand_db/store.py` | 240 | `VoiceEntry`/`ImageEntry`, `load_*` helpers, verdicts={gold, acceptable, reference, anti-exemplar} |
| `brand_db/claim.py` | 60 | `brand_entry_to_claim` → Priority 4 BRAND Claim |

### `verticals/wine-beverage/` — the only vertical pack currently shipped

| File | Purpose |
|---|---|
| `scope_matrix.yaml` | Canonical 9-dept OWNS/NEVER matrix, proven overlap-free |
| `dept_briefs.yaml` | 9-dept generic demo templates with placeholders (`{{ company.name }}`, `{{ settled_convictions }}`, etc.) |

### `cli/` — operator command-line interface

`cli/main.py` with 8 subcommands: `run`, `talk-to`, `demo`, `adversary`, `kill`, `meeting`, `add-dept`, `costs`, `assumptions`. `python -m cli <cmd>`.

### `plugin/` — Claude Code plugin (live-installed via local marketplace)

`.claude-plugin/plugin.json` manifest + `skills/<name>/SKILL.md` × 8 (adversary, kill, costs, assumptions, run-dept, talk-to, demo, meeting). Slash namespace `/company-os:<command>`.

### `webapp/` — Flask GUI (exists but lightly used)

Routes: dashboard, departments, board, sessions, decisions, artifacts, run, jobs, costs. Planned additions (not built): radial org-chart `/office` route + double-click agent chat handoff.

### Test suite

58 test files, 741 passing + 35 skipped. Tests exercise primitive integration at unit level; no real API calls in tests (SDK seam monkeypatched). Full suite runs in ~10s.

---

## 5. Key primitives — full source {#5-key-primitives-full-source}

### 5.1 State authority graph — `core/primitives/state.py`

This is the clearest example of what the governance stack actually does. Deterministic conflict resolution between 8 authority tiers with one exception rule for Decisions that explicitly supersede.

```python
"""
core/primitives/state.py — Provenance + conflict resolution
===========================================================
Plan §1.5 specifies four required provenance fields on every persisted
entry so downstream code can trace where a fact came from and when it
was last refreshed:

  updated_at    — ISO timestamp of the last write
  updated_by    — principal (agent or human) that authored the write
  source_path   — filesystem path the fact was derived from
  ingested_at   — ISO timestamp of the original ingestion

An entry missing any of these fields fails `check_provenance()` and
must not be surfaced to a downstream consumer.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


_REQUIRED_FIELDS = ("updated_at", "updated_by", "source_path", "ingested_at")


class ProvenanceStatus(Enum):
    VALID = "valid"
    INVALID = "invalid"


def check_provenance(entry: Mapping[str, Any]) -> ProvenanceStatus:
    """Validate that `entry` carries all required provenance fields."""
    if not isinstance(entry, Mapping):
        return ProvenanceStatus.INVALID
    for key in _REQUIRED_FIELDS:
        value = entry.get(key)
        if value is None or value == "":
            return ProvenanceStatus.INVALID
    return ProvenanceStatus.VALID


class AuthorityPriority(Enum):
    """8-tier authority ranking. Lower value = higher authority."""
    FOUNDER = 1       # context.md, founder_profile.md, priorities.md
    DECISION = 2      # decisions/<date>-<slug>.md — can supersede FOUNDER iff explicit
    KB = 3            # knowledge-base/chunks/*.md
    BRAND = 4         # brand-db/voice, brand-db/images
    HANDSHAKE = 5     # handshakes/<session>/*.json — audit only
    MEMORY = 6        # departments/**/manager-memory.md, specialist memory
    TASTE = 7         # taste/profile.yaml — preference signal only
    ASSUMPTION = 8    # assumptions-log.jsonl — provisional, has TTL


@dataclass(frozen=True)
class Claim:
    priority: AuthorityPriority
    content: Any
    ref: str
    provenance: Mapping[str, Any]
    supersedes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ResolvedClaim:
    winner: Claim
    loser: Claim
    reason: str


def _decision_override(a: Claim, b: Claim) -> ResolvedClaim | None:
    """A Decision may override a higher-priority claim only if (1) it lists
    the other claim's ref in `supersedes` AND (2) it is chronologically newer."""
    for decision, other in ((a, b), (b, a)):
        if decision.priority is not AuthorityPriority.DECISION:
            continue
        if other.priority.value >= decision.priority.value:
            continue
        if other.ref not in decision.supersedes:
            continue
        if decision.provenance["updated_at"] <= other.provenance["updated_at"]:
            continue
        return ResolvedClaim(
            winner=decision, loser=other,
            reason=(
                f"Decision {decision.ref} supersedes "
                f"priority_{other.priority.value}_{other.priority.name.lower()} "
                f"ref {other.ref} per explicit supersedes list"
            ),
        )
    return None


def resolve_conflict(a: Claim, b: Claim) -> ResolvedClaim:
    """Deterministic winner between two conflicting claims.

    Rules (ordered):
      1. Both claims must carry valid provenance (ValueError otherwise).
      2. A Priority 2 Decision may supersede a higher-priority claim iff
         explicit supersedes + newer timestamp. §1.5 escape hatch.
      3. Otherwise: lower priority number wins.
      4. Same priority: newer `updated_at` wins.
      5. Same priority AND timestamp: lexicographic `ref` tiebreak (pure).
    """
    if check_provenance(a.provenance) is not ProvenanceStatus.VALID:
        raise ValueError(f"Claim a has invalid provenance (ref={a.ref!r})")
    if check_provenance(b.provenance) is not ProvenanceStatus.VALID:
        raise ValueError(f"Claim b has invalid provenance (ref={b.ref!r})")

    override = _decision_override(a, b)
    if override is not None:
        return override

    if a.priority.value < b.priority.value:
        return ResolvedClaim(winner=a, loser=b, reason=...)
    if b.priority.value < a.priority.value:
        return ResolvedClaim(winner=b, loser=a, reason=...)

    ts_a, ts_b = a.provenance["updated_at"], b.provenance["updated_at"]
    if ts_a > ts_b:
        return ResolvedClaim(winner=a, loser=b, reason=...)
    if ts_b > ts_a:
        return ResolvedClaim(winner=b, loser=a, reason=...)

    # Lexicographic tiebreak — keeps the function pure.
    if a.ref <= b.ref:
        return ResolvedClaim(winner=a, loser=b, reason="lexicographic tiebreak")
    return ResolvedClaim(winner=b, loser=a, reason="lexicographic tiebreak")
```

**Why this matters to an evaluator:** deterministic conflict resolution is rare in LLM-centric systems. Most let the LLM decide which source to trust, which is unreliable and unauditable. The 8-tier ranking with one explicit exception rule gives you a cache-safe, pure function you can test exhaustively. Feedback wanted on (a) whether the priority ordering is correct, (b) whether one exception rule is enough.

### 5.2 Adversary drift benchmark — `core/adversary.py` (excerpt)

The adversary is a first-class agent; the interesting part is the *drift-detection mechanism* that keeps it from being absorbed into the house voice over time.

```python
DRIFT_WINDOW_DAYS = 30
DRIFT_WINDOW_ACTIVATIONS = 10
DRIFT_SAMPLE_SIZE = 3
MIN_MEDIAN = 3.0
CONSECUTIVE_FAILURES_TO_RESET = 2


@dataclass(frozen=True)
class AdversaryRating:
    """One founder-rating of an adversary review. 0-5 integer scale
    (lower = performative, higher = stress-tested)."""
    review_key: str
    score: int
    notes: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 5:
            raise ValueError(f"score must be in [0, 5]")


@dataclass(frozen=True)
class DriftWindow:
    """A completed drift-benchmark period. Closes when EITHER 30 days
    elapsed OR 10 activations rated, whichever first (§0.5)."""
    started_at: str
    ended_at: str
    activations: int
    rating_median: float | None
    passed: bool
    ratings: tuple[AdversaryRating, ...] = ()


class ResetAction(str, Enum):
    HOLD = "hold"
    RESET = "reset"  # 2 consecutive sub-threshold → reset memory+prompt


def build_window(started_at, ended_at, ratings, activations, *, min_median=MIN_MEDIAN):
    """`passed` = True iff ≥1 rating AND median ≥ min_median.
    Zero-rating windows pass by default (no signal → no alarm)."""
    scored = [r.score for r in ratings]
    if scored:
        med = float(median(scored))
        passed = med >= min_median
    else:
        med = None
        passed = True
    return DriftWindow(started_at=started_at, ended_at=ended_at,
                       activations=activations, rating_median=med,
                       passed=passed, ratings=tuple(ratings))


def consider_reset_trigger(windows, *, consecutive_needed=CONSECUTIVE_FAILURES_TO_RESET):
    """RESET iff the last `consecutive_needed` windows all failed.
    Only the tail is inspected — a pass window clears earlier failures."""
    if len(windows) < consecutive_needed:
        return ResetDecision(action=ResetAction.HOLD, reason=...)
    tail = windows[-consecutive_needed:]
    failures = sum(1 for w in tail if not w.passed)
    if failures >= consecutive_needed:
        return ResetDecision(
            action=ResetAction.RESET,
            reason=f"{failures} consecutive sub-threshold windows — "
                   "adversary memory + prompt reset per §0.5",
            consecutive_failures=failures,
        )
    return ResetDecision(action=ResetAction.HOLD, reason=...)
```

**Why this matters:** most "adversary" or "critic" agents in the literature are prompts. They drift toward agreeableness over time because LLMs are trained on pleasant-sounding text. Making drift-defense a structural primitive with an explicit reset mechanism is uncommon. Feedback wanted on (a) is median-of-3 the right sample statistic, (b) is 2 consecutive failures the right reset threshold, (c) what happens when founder ratings are themselves noisy.

### 5.3 Scope matrix with clean OWNS/NEVER semantics — `core/primitives/scope_matrix.py` (excerpt)

```python
@dataclass(frozen=True)
class DepartmentScope:
    dept: str
    owns: tuple[str, ...] = ()
    never: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeOverlap:
    """Same topic (case-normalised) claimed by 2+ depts' OWNS lists."""
    topic: str
    departments: tuple[str, ...]


@dataclass(frozen=True)
class ScopeContradiction:
    """SAME dept lists the same topic in BOTH its OWNS and its NEVER.
    Cross-dept OWNS/NEVER pairs are NOT contradictions: a dept's NEVER
    is a self-disclaimer, and it is expected that the disclaimed topic
    is owned by another dept."""
    dept: str
    topic: str


def find_overlaps(matrix: ScopeMatrix) -> OverlapReport:
    """Identify matrix-level coherence issues.
      * `overlaps` — same topic (case-normalised) in 2+ depts' OWNS
      * `contradictions` — SAME dept lists topic in BOTH its OWNS+NEVER
    Cross-dept OWNS/NEVER is EXPECTED and NOT flagged."""
    ...
```

**Why this matters:** most scope systems (RBAC, ACLs) treat all conflicts as errors. The insight here is that in multi-agent systems, a NEVER entry is a **self-disclaimer that presumes another agent owns the topic**. This avoids the brittleness of "I can't have Finance NEVER 'brand voice' because Marketing OWNS it — that's a contradiction" — it ISN'T a contradiction, it's *exactly the intended coordination mechanism*.

### 5.4 Pre-mortem injection — `core/onboarding/premortem.py`

A small primitive that does something mechanically simple but philosophically sharp: the founder's named failure mode becomes **load-bearing context** for every cross-dept synthesis and every adversary activation.

```python
PREMORTEM_MARKER = "<!-- premortem-injected -->"

SYNTHESIS_GUARD_SENTENCE = (
    "Before synthesizing, check: does this plan accelerate the founder's "
    "named failure mode? If yes, surface that explicitly in the output."
)

ADVERSARY_GUARD_SENTENCE = (
    "When stress-testing the founder's direction, reference this failure "
    "mode explicitly — the founder already accepts it as plausible and "
    "expects the adversary to flag plans that drift toward it."
)


def inject_premortem_context(body, premortem, *, kind="synthesis"):
    """Prepend a pre-mortem context block to `body`. Idempotent —
    repeat calls are a no-op when the marker is already present."""
    if premortem is None or is_premortem_injected(body):
        return body
    guard = SYNTHESIS_GUARD_SENTENCE if kind == "synthesis" else ADVERSARY_GUARD_SENTENCE
    block = (
        f"{PREMORTEM_MARKER}\n"
        f"## Founder pre-mortem (load-bearing per §0.5)\n"
        f"The founder named this as the most likely cause of failure "
        f"12 months out:\n\n> {premortem.cause}\n\n{guard}\n"
        f"{PREMORTEM_END_MARKER}\n"
    )
    return block + "\n\n" + body
```

**Why this matters:** structurally guarantees that whenever the system is about to do anything consequential, it's reminded of the specific way this business is most likely to fail. Not a nice-to-have documented practice — a mechanical guard inserted at every cross-dept synthesis.

### 5.5 Autoresearch proposal lifecycle — `core/autoresearch.py` (excerpt)

Evaluator-alone trigger (not manager — motivated-reasoning hazard). 7-day TTL. Budget defer → founder escalation.

```python
class ProposalStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    EXPIRED = "expired"
    ESCALATED = "escalated"


DEFAULT_TTL_DAYS = 7


@dataclass(frozen=True)
class AutoresearchProposal:
    proposal_id: str  # {specialist}--{skill}--{ts-safe}
    specialist_id: str
    skill_id: str
    trigger_reason: str
    failures_in_window: int
    skill_pattern_count: int
    budget_estimate: float
    created_at: str
    ttl_days: int = DEFAULT_TTL_DAYS
    status: ProposalStatus = ProposalStatus.PENDING
    started_at: str | None = None
    completed_at: str | None = None
    artifact_path: str | None = None
    notes: str = ""


_ALLOWED_TRANSITIONS = {
    ProposalStatus.PENDING: frozenset({RUNNING, EXPIRED, ESCALATED}),
    ProposalStatus.RUNNING: frozenset({COMPLETED, EXPIRED}),
    ProposalStatus.COMPLETED: frozenset(),  # terminal
    ProposalStatus.EXPIRED: frozenset(),    # terminal
    ProposalStatus.ESCALATED: frozenset({PENDING, EXPIRED}),  # founder approves → resume
}


def build_proposal(specialist_id, skill_id, decision, budget_estimate, ...):
    """Route: APPROVE → PENDING, DEFER → ESCALATED, DECLINE → raise."""
    if decision.action is TriggerAction.DECLINE:
        raise ValueError(...)
    status = ProposalStatus.PENDING if decision.action is TriggerAction.APPROVE \
             else ProposalStatus.ESCALATED
    ...
```

**Why this matters:** self-improvement loops are the most dangerous autonomy. The 7-day TTL forces decisions; budget-deferred escalation prevents silent runaway. The state machine is pure — transitions raise `IllegalTransitionError` if attempted out of order.

---

## 6. Novel / uncommon patterns — the honest highlight reel {#6-novel-patterns}

Ordered from most-to-least genuinely uncommon. Please challenge any of these claims with prior art citations if you know of them.

### 6.1 Integrated governance stack (the most important claim)

Individual governance primitives exist scattered across frameworks:
- Citation contracts exist (typically as RAG source-attribution).
- Scope systems exist (RBAC, ACLs, typed capabilities).
- Adversarial critics exist (as prompts).
- Freshness TTLs exist (cache invalidation literature).
- Conflict resolution exists (priority queues, CRDTs).

**Having them wired into the same dispatch loop, through typed envelopes (`Claim`, `Verdict`, `Handshake`, `RelationalEvent`), unit-testable in isolation AND composable end-to-end, is uncommon.** `drift_guard.py` is the smallest example — one function composes watchdog + turn-cap + provenance checks into a unified report. Tested independently; tested composed.

### 6.2 State-authority graph with deterministic supersession

8-tier priority + one exception rule (Decision supersedes higher-priority iff explicit supersedes list + newer timestamp) = pure function with provable properties. I haven't found a published treatment of this exact mechanism for LLM agent systems. Claims to investigate: is this prior art in policy engines? Cedar? OPA?

### 6.3 Adversary drift-detection with auto-reset

Periodic founder rating (median of 3 on 0-5 scale, every 30 days or 10 activations), 2 consecutive sub-threshold windows → adversary memory + prompt reset. Prevents the critic from being absorbed into the house voice. Claim: most LLM critics drift toward agreeableness; this is a structural defense, not a prompt trick.

### 6.4 Scope-matrix semantics — NEVER as self-disclaimer, not contradiction

Counterintuitive framing that avoids brittleness of most OWNS/NEVER systems. Cross-dept NEVER entries EXPECT another dept to own the disclaimed topic — that's the coordination mechanism, not a rule violation. Claim to investigate: prior art in multi-agent coordination literature?

### 6.5 Kill-switch forced retro as adversary input

`/kill <specialist>` → 3 mandatory questions (expected / saw / fix) → prompt reset to last-known-good → retro becomes input to next adversary activation. Firing an agent produces a signal, not just a deletion. I don't know of prior art here.

### 6.6 Training-signal isolation architectural rule

Self-improvement loops MUST draw training signal from OUTSIDE the agent being trained (founder ratings, evaluator verdicts, contribution acceptance rates, brand-DB voice-diff, external engagement metrics — NEVER the agent's own past output). This prevents the RLHF-style collapse mode where an agent training on its own outputs reinforces its current biases. This is an architectural constraint, not a policy. Claim: underappreciated principle for self-modifying agents.

### 6.7 Ratification threshold for autonomous outputs (designed, not built)

Autonomous-loop outputs land in `output/pending-ratification/`. Small ones auto-ratify after 24h (no durable state changes). Large ones (memory updates, benchmark changes, scope-crossing, prompt edits) queue for founder review. This is the **mechanical bound on autonomy** — not "founder approves each action" (attention-exhausting) but "small decisions ratify silently, large ones gate."

**Revised 2026-04-18 per consolidated eval (Grok-4 + Gemini-2.5-Pro):** prior versions of this document called this pattern "uncommon." Both reviewers independently classified it as DERIVATIVE with direct prior art: LangGraph human-in-the-loop checkpoints, CrewAI task approvals, AutoGen HIL patterns, OpenAI Swarm (Grok); RPA tools like UiPath, AutoGPT, BabyAGI (Gemini). The ratification threshold is a **standard workflow pattern across RPA, LangGraph HIL, and autonomous-agent HIL implementations.** No novelty claimed going forward.

### 6.8 Pre-mortem as structural cross-synthesis context

The founder's one-sentence answer to "12 months from now this business failed — what's the most likely cause?" becomes a mandatory-injected context block in every cross-dept synthesis and every adversary activation, via `inject_premortem_context()`. Simple mechanism, sharp philosophical claim: system must be reminded of its own most-plausible failure mode *every time it's about to do something consequential*.

### 6.9 Cost envelope + autoresearch TTL as forcing functions

Expensive queries surface to founder before execution (not after). Self-improvement proposals expire in 7 days, forcing real decisions rather than drift into pending purgatory.

**Revised 2026-04-18 per consolidated eval (Grok-4 + Gemini-2.5-Pro):** prior claim of "forcing-function property is rare" is wrong. Both reviewers independently classified this as DERIVATIVE with ubiquitous prior art: LangGraph/LangChain cost tracking, MemGPT/Letta memory freshness TTLs, CrewAI/AutoGen task timeouts, OpenAI Swarm cost envelopes (Grok); enterprise message brokers (Gemini). Per-call/session budgets and TTL-based expiration are **ubiquitous production primitives in almost all production-grade wrappers.** No novelty claimed going forward.

### 6.10 Out-of-band verifiability as an architectural principle (added 2026-04-18)

**Added after consolidated eval (Grok-4 + Gemini-2.5-Pro).** Both reviewers independently identified the same top-severity attack surface: `check_provenance` verifies only the *presence* of string fields, not their integrity — and the LLM controls the provenance metadata it writes. Grok's proposed mitigation was prompt-versioning; Gemini's was hash-backed provenance binding. The two proposals converge on a single architectural principle that is stronger than either primitive individually:

> **Any field an LLM writes must also be re-derivable or verifiable by the engine from out-of-band state.**

Provenance hashes (computed at ingest time by the engine, stored in frontmatter, verified at `resolve_conflict`) are the first instantiation. Prompt versioning under the state-authority graph is the second. Timestamp sanity checks (reject future-dated `updated_at`) are the third. Founder-signature requirements on Priority 1 overrides are the fourth. Each individually trivial; together they move the system's trust boundaries outside the agent's context window.

Implementation: `core/primitives/integrity.py` (hash compute + verify), `core/primitives/state.py::is_future_timestamp`, `_has_founder_signature`.

Claim: **moderate novelty**. The individual primitives are obvious in isolation; stating them as a single principle ("any LLM-writable field must be engine-verifiable") is what's worth naming. Cryptographic commitments in agent contexts appear scattered (e.g. content-addressed storage in some IPFS-backed setups, signed claims in Verifiable Credentials work), but a named principle bound to the provenance-spoofing failure mode is worth surfacing.

---

## 7. What's derivative / standard {#7-derivative}

To save evaluators time:

- **Three-tier hierarchy** — obvious shape. Chosen because it's the minimum sufficient topology. No novelty claimed.
- **Python + YAML + Claude API** — standard stack, no exotic dependencies.
- **File-system-backed persistence** — single-writer, not scalable to multi-tenant without rework.
- **Keyword retrieval** (no vector DB) — deferred sqlite-vec. Fine for solo-operator scale.
- **No fine-tuning, no RLHF** — all orchestration of foundation-model calls.
- **`Verdict` with PASS/FAIL/NEEDS_REVIEW** — standard pattern.
- **Handshake protocol with session_id + references** — straightforward correlation.
- **Evaluator as separate agent** — common pattern (LLM-as-judge).
- **Memory files per agent** — standard practice.
- **JSONL logs** — standard.
- **Env-var gated migration** — common deployment pattern.
- **Most CLI subcommands** — standard argparse.

**If you were hoping for an ML contribution, there isn't one.** This is application engineering around orchestration + governance patterns.

---

## 8. Proposed ambient awareness layer (spec, not built) {#8-ambient-awareness}

**Status:** designed, not implemented. ~3-5 hours of build estimated. Identified in recent design discussion as the highest-priority next primitive.

**Problem:** real organizations run on ~80% informal information flow (overheard context, weak-tie bridges, accumulated background observations, second-order relational awareness). Current multi-agent systems model only the ~20% formal dispatch. This gap causes information-flow failures that look like "the agents didn't know" when really "the information was available but had no channel."

**Wrong approach:** agents chatting informally with each other. LLMs will happily produce indefinite unstructured conversation — ceremonial noise, token burn, hallucination multiplier.

**Right approach:** agents observe the environment and leave **grounded observations** on a shared log that other agents opportunistically consume.

### Specification

**Data model:**
```python
@dataclass(frozen=True)
class AwarenessNote:
    observer: str          # agent who wrote the note
    subject: str           # agent or entity being observed
    observation: str       # one-line text (max ~200 chars)
    evidence: str          # required: dispatch ID, memory ref, URL w/ date, etc.
    created_at: str        # ISO timestamp
    # Computed: expires_at = created_at + 14d unless re-confirmed
```

**Storage:** `<company>/awareness.jsonl` — append-only, JSONL.

**Write path:** every agent, at the end of each dispatch, MAY optionally append one `AwarenessNote`. Required validation:
- `evidence` must be present AND verifiable (dispatch ID exists, memory ref resolves, URL was fetched in last 24h, etc.)
- Notes without verifiable evidence are REJECTED at write time (same watchdog primitive)
- One note per dispatch maximum (prevents note spam)

**Read path:** every agent, at the start of each dispatch, gets a preamble including:
- Notes where `subject` = any agent in the current dispatch target list
- Notes where `subject` = current dept
- Notes where `subject` = broad category matching current topic

Relevance filter applied. Typically 3-5 notes surfaced per dispatch preamble.

**Decay mechanism:** notes auto-expire after 14 days unless confirmed. Confirmation = another agent observes the same pattern independently (different `observer`, same or overlapping `subject`, within the window). Confirmed notes extend by another 14 days.

**CLI/GUI surface:**
```bash
companyos awareness [--since YYYY-MM-DD] [--subject <agent>]
```

Displays current active notes. Founder peripheral vision on ambient state of the org.

### Why this is potentially novel

1. **Grounded vs. ungrounded distinction.** Evidence requirement blocks hallucinated observations at write time. Not filtering after, not trusting the LLM — structural gate.
2. **Opportunistic vs. directed consumption.** No agent is formally notified. Notes are read whenever relevant. Matches human ambient-awareness dynamics (Slack, calendar visibility, overhearing) without the simulation cost of chat.
3. **Decay + confirmation.** Notes that aren't independently re-observed fade. Drift is bounded structurally, not by policy.
4. **Write-at-most-once-per-dispatch.** Caps note-spam at source. Economical.

### Expected failure modes

1. **False positives from observational inference.** "Agent B seems tired" is impossible to ground. "Agent B's last 3 outputs were 40% shorter than their baseline" is. The schema should prefer quantitative/specific observations.
2. **Over-reliance by receivers.** If Agent B reads Agent A's note and accepts it uncritically, errors propagate. Mitigation: when a note is consumed, its evidence citation gets included in the receiving agent's output. Provenance chain visible.
3. **Founder losing direct observation habit.** If the awareness log becomes where the founder "knows what's happening," direct memory-reading atrophies. Mitigation: layer supplements, doesn't replace.
4. **Adversarial prompt crafting.** An agent might fabricate an evidence citation that technically parses but doesn't exist. Watchdog must verify existence, not just format.

### Why this matters for evaluation

This is the primitive most likely to surface interesting empirical data during the 60-day dogfood. It's also the one I believe has the strongest claim to genuine novelty — the specific shape (evidence-required, one-per-dispatch, 14-day decay with confirmation extension, opportunistic preamble consumption) is new as far as I know.

**Evaluator questions:** (a) Is there prior art here I'm missing? (b) What evidence-verification schemes would close the fabrication hole without expensive LLM-judge calls? (c) What's the failure mode you'd try first?

---

## 9. Proposed relationship layer (spec, not built) {#9-relationship-layer}

**Status:** designed, not implemented. ~6-8 hours of build estimated (event ledger + aggregation primitives). Builds on the awareness layer (§8).

**Problem:** relationships between agents are currently implicit — they live in dispatch edges, citation references, handshake records, memory cross-mentions, meeting co-attendance. Nothing analyzes them. Specifically missing: second-order relational modeling — "Agent A knows something about Agent B that would improve Agent C's response to Agent B."

### Specification

**Event ledger:**
```python
@dataclass(frozen=True)
class RelationalEvent:
    source_agent: str           # who acted
    target_agent: str           # who was acted upon
    event_type: str             # see below
    ts: str                     # ISO timestamp
    session_id: str
    outcome_score: float | None # evaluator's score if available
    context: str                # 1-line summary (max ~200 chars)
```

**Event types:**
- `dispatch` — manager dispatches to specialist
- `cite` — specialist's output cites another agent's prior output (detected via `@agent-name` scan or structured Reference)
- `defer` — meeting records explicit deference ("I agree with Agent B's take")
- `disagree` — state-authority resolves a conflict between two sources
- `co-attend` — agents co-attended a meeting

**Integration points (retrofit into existing primitives):**
- `handshake_runner.record_handshake()` → append `dispatch` event
- `evaluator.record_verdict()` → attach outcome_score retroactively to the dispatch event
- `memory_updater.append_specialist_memory()` → scan for `@<agent-name>` mentions, append `cite` events
- `meeting.run_*_meeting()` → append `co-attend` events for each pairing
- `state.resolve_conflict()` → append `disagree` event when two sources collide

**Storage:** `<company>/insights/relational-log.jsonl`.

### Aggregation primitives

Pure functions over event lists (statistical rigor kept low — data volume will be modest):

- `interactions_between(events, a, b, since=None)` → count + breakdown by type
- `deference_proxy(events, a, b)` → ratio of defer events to disagree events. **NOT trust** — honest name is "deference proxy." Avoids over-interpretation.
- `disagreement_rate(events, a, b)` → conflict events normalized by interaction count
- `dormant_edges(events, threshold_days=30)` → pairs that used to interact but haven't in N days

**No analytics beyond counting and averaging.** Statistics at data volume of ~300 events/60-days would be fake precision.

### The specific novel target: second-order relational modeling

Real organizations run on "Agent A observed something about Agent B that matters for Agent C's next interaction with B."

Mechanism:
1. Agent A writes an awareness note: "Noticed B's recent outputs have been shorter than baseline. [cites specific dispatch IDs]"
2. Awareness layer stores the note with evidence.
3. Two days later, Agent C (a different agent) is dispatching with B as a target.
4. Agent C's dispatch preamble includes A's note about B.
5. Agent C factors this into its interaction — either adjusts the request (smaller scope, longer deadline) or raises it in the output ("noting A's earlier observation, scoping this conservatively").

**The result:** lateral information flow that doesn't require direct dispatch between A and C. Matches the "hallway wisdom" dynamic in real orgs. Doesn't require simulated gossip — requires a grounded log + opportunistic consumption.

### Why this is potentially paradigm-shifting (with strong caveats)

Most agent research treats agents as isolated units with shared context. This design treats them as **observing peers** who accumulate models of each other that the system then surfaces. The agent-to-agent model is SECOND-ORDER — not "what Agent A knows" but "what Agent A knows about how Agent B behaves that might matter for Agent C."

Claim: this is underexplored. The closest work I'm aware of is in multi-agent RL (theory-of-mind modeling), but those systems don't have the operational artifacts (memory files, citation graphs, meeting transcripts) that could feed relational inference at scale.

**But caveats:**
1. Data volume will be low at solo-operator scale. Relational insights might be noise.
2. "Deference proxy" is a loaded concept even with careful naming — easy to over-interpret.
3. Most of the relational value might emerge only at team scale (10+ active agents), not at the 8-specialist scale of Old Press.

### Evaluator questions

(a) Is there prior art in multi-agent systems that does second-order relational modeling at the operational level (not just academic theory-of-mind)?
(b) What's the minimum data volume before relational aggregation produces signal vs. noise?
(c) Does the "deference proxy is not trust" naming discipline actually hold under operational pressure, or will readers collapse the distinction?
(d) What's the most dangerous misinterpretation a user could draw from the aggregation outputs?

---

## 10. Research-worthy directions {#10-research-directions}

Ordered by viability × distinctiveness. High-conviction picks first.

### 10.1 Ambient awareness as primary knowledge-propagation in multi-agent systems

**Claim:** evidence-required, decay-bounded, opportunistically-consumed observations are a better primary knowledge-transfer mechanism for multi-agent systems than either formal dispatch OR unstructured chat.

**Research questions:**
- What's the optimal decay half-life for awareness notes in different operational cadences (weekly, daily, hourly)?
- Does evidence-required gating actually prevent hallucinated observations empirically, or does it just force agents to cite plausible-looking sources?
- What's the relationship between ambient-note volume and downstream dispatch quality? (Too few = blindness; too many = noise; what's the optimum?)

**Viability:** HIGH. Primitive is cheap to build (~5 hrs). The 60-day dogfood produces data naturally. Publishable.

### 10.2 Second-order relational modeling for operational agent systems

**Claim:** modeling "what Agent A knows about Agent B that matters for Agent C" as a first-class primitive surfaces insights no single-agent-memory system can produce.

**Research questions:**
- At what data volume does second-order relational inference stabilize (produce consistent signals) vs. noise?
- Which event types (dispatch / cite / defer / disagree / co-attend) carry the most second-order information?
- Does the "deference proxy is not trust" naming discipline survive operational pressure, or do users collapse it?

**Viability:** MEDIUM. Build cost moderate (~8 hrs). Data volume at solo scale may be insufficient. Most valuable at team scale — potentially worth publishing even with negative results.

### 10.3 Levels 1-4 autonomy with mechanical drift bounds

**Claim:** autonomous cross-agent coordination can be bounded against compound hallucination drift via mechanical thresholds (goal lattice + budget envelope + ratification threshold + contribution scope-not-action + loop termination + quality drift benchmark), producing a safer autonomous substrate than either "ungated autonomy" (hallucination cascade) or "founder-approves-everything" (attention-exhausting).

**Research questions:**
- Under what conditions does the guardrail set fail? Specifically: what adversarial input sequences bypass the ratification threshold?
- Does auto-pause on quality drift actually recover quality, or does it just mask degraded performance as outages?
- What's the practical ceiling on how much autonomy founders tolerate before losing situational awareness?

**Viability:** MEDIUM-HIGH. Build cost significant (~15 hrs). Empirical test is the 60-day dogfood. Publishable whether it succeeds or fails — failure modes are as valuable as success.

### 10.4 State-authority graph as conflict-resolution primitive

**Claim:** deterministic 8-tier priority with single-exception override rule is a pure, cache-safe, unit-testable conflict resolution mechanism for LLM-centric multi-source systems. Most existing systems defer to the LLM.

**Research questions:**
- Is the 8-tier ranking correct? What ordering does an expert panel prefer?
- Is one exception rule enough? Can I construct realistic cases where the override rule fails?
- Does deterministic resolution actually produce better outcomes than LLM-based conflict resolution in production?

**Viability:** MEDIUM. Could publish as a short-paper comparison study. Requires constructing adversarial test cases.

### 10.5 Drift benchmarks as generalized quality-assurance primitive

**Claim:** periodic founder-rating + median-based reset is a general pattern applicable to many agent-quality problems, not just adversary drift. Could apply to: specialist voice drift, copywriter tone drift, contribution-layer quality drift, any agent whose quality might degrade with accumulating memory.

**Research questions:**
- Does median-of-3 sample suffice, or do we need larger samples under higher-noise conditions?
- Is 2 consecutive sub-threshold the right reset threshold?
- Does founder-rating introduce its own biases that make the drift detection noisy?

**Viability:** MEDIUM. Generalization from a single use case. Would require multi-agent dogfood data to establish.

### 10.6 Training-signal isolation as architectural rule

**Claim:** self-improving agents must draw training signal from OUTSIDE themselves (founder ratings, evaluator verdicts, engagement metrics, external reference) never from their own past output. Prevents RLHF-style bias-reinforcement collapse at the organizational level.

**Research questions:**
- Under what conditions does self-training on own-output actually collapse vs. produce benign stability?
- Is the rule absolute, or are there bounded cases (e.g., training on explicitly-dissimilar past outputs) that are safe?
- How do you enforce this architecturally in practice?

**Viability:** HIGH for stating the principle; MEDIUM for producing empirical support. Could be a short position paper with illustrative examples.

### 10.7 The empirical methodology itself

**Claim:** longitudinal operational dogfooding of integrated agent stacks on real pre-revenue small businesses is genuinely novel as a research methodology. Most agent research tests primitives on synthetic workloads or short-term tasks. Almost no published work on "someone deployed a full agent stack on a real business for 60 days and reported honestly."

**Research questions:**
- What's the right instrumentation for a longitudinal dogfood study? (Hours-in vs. hours-out; artifacts shipped vs. internal; drift interventions vs. false positives; etc.)
- How do you separate "system helps" from "founder adapted to compensate"?
- What's the minimum study duration before conclusions stabilize?

**Viability:** HIGH. The dogfood is happening regardless. Publishable as field notes / case study. Could establish a methodology template for others.

### 10.8 Integrated governance stack vs. ungated autonomy — empirical comparison

**Claim:** the hypothesis that governance primitives pay for themselves in production reliability is testable. Most agent deployments skip governance; this project bakes it in. Running both modes on similar workloads would surface the value-vs-cost tradeoff.

**Research questions:**
- Does the governance stack reduce hallucination-driven errors?
- Does it introduce enough friction to offset the reliability gains?
- Which primitives contribute most to which outcomes?

**Viability:** LOW for rigorous comparison (requires two parallel operations — not feasible solo). MEDIUM for illustrative contrast (this project's log vs. a published ungated-agent case study).

---

## 11. Empirical methodology — the 60-day dogfood {#11-empirical-methodology}

**Operation:** Old Press Wine Company LLC (pre-TTB, no product yet) + American Wine editorial publication (beehiiv, launching imminently).

**Operator:** solo, 5-10 hrs/wk capacity, real stakes (cash-constrained, $50-60k debt, needs W-2 income).

**Dispatch volume expected:** 100-300 operations over 60 days, roughly evenly split between newsletter production, TTB compliance prep, brand positioning work, and system self-maintenance.

**Tracked metrics (weekly):**
1. Hours spent operating the system vs. hours of output actually produced
2. Artifacts shipped externally (newsletter issues, customer communications) vs. artifacts generated internally (memos, synthesis reports)
3. Drift-defense interventions fired vs. false positives
4. Adversary insights that materially affected a decision (founder-rated after the fact)
5. Ambient awareness notes written + consumed + confirmed
6. Cost per PASS verdict, trended over time
7. Level-4 autonomy signals: times "awareness was there, action required founder intervention"

**Output artifacts:**
- Weekly field-notes posts in American Wine newsletter (the documentation IS the publication; double-duty)
- End-of-Phase-14 summary report: what worked, what failed, what surprised
- Code artifacts: primitive-level improvements learned from real load
- Potentially publishable: the empirical methodology + specific findings

**Success criteria (predeclared):**
- At day 30: ≥3 examples of "the system surfaced something the founder wouldn't have seen"
- At day 60: measurable time-compression on recurring tasks (newsletter drafting, founder-profile synthesis, compliance research)
- At day 60: zero major hallucination-driven errors in externally-shipped content
- At day 60: drift-defense fired at least once on a real concern (demonstrating it's not decorative)

**Failure criteria (predeclared):**
- Hours operating the system consistently > hours of output it produces for 3 consecutive weeks → archive primitives, simplify to Claude-project-level
- Hallucinated content reaches externally-shipped output → shut down autonomous layers, revert to founder-gated every action
- Attentional burden of maintaining the system prevents shipping newsletter on cadence → reduce scope

---

## 12. Specific questions for evaluators {#12-questions}

Please prioritize these:

### Highest priority
1. **Prior art check.** Any of the "novel/uncommon" claims in §6 that you recognize as already published? Specific citations please — I want to know if I'm reinventing.
2. **Weakest primitive in the stack.** If you were to break this system, which primitive would you attack first, and how?
3. **Ambient awareness layer (§8).** Evidence-required, one-per-dispatch, 14-day decay with confirmation — is this a good design? What fails first under load?
4. **Relationship layer (§9).** Is second-order relational modeling worth the primitive complexity, or is it theater at this scale?

### Medium priority
5. **State-authority priority ordering (§5.1).** Is the 8-tier ranking correct? What ordering would you use?
6. **Training-signal isolation (§6.6).** Is "never train on own output" too strict, or is it correct as a hard architectural constraint?
7. **Level-4 autonomy guardrails (§10.3).** Which guardrail fails first in practice?
8. **Pre-mortem injection (§5.4).** Is structural guard-sentence injection the right shape for load-bearing context, or should it be queryable (agent decides when to check) vs. pushed (always present)?

### Standing invitation
9. What primitives am I missing that would be cheap to add?
10. What patterns in the design suggest a misunderstanding of how LLMs actually fail?

---

## Appendix — how to evaluate this efficiently

**If you have 30 minutes:**
- Read §1 (pitch), §3 (architecture), §5.1 (state authority full source), §6 (novel patterns), §8-9 (ambient + relational).
- Answer questions 1-4 in §12.

**If you have 2 hours:**
- Also read §4 (full manifest), §10 (research directions).
- Run the test suite (`python -m pytest`) to verify the 741 pass — I'm not claiming this blind.
- Answer all of §12.

**If you have a week:**
- Clone the code. Run the comprehensive demo. Write a dept. Try to break the governance stack via adversarial prompting.
- Write a response document. I'll address every point in writing.

---

*End of evaluation package. ~10,500 words. Self-contained. Send honest critique — validation is worthless.*
