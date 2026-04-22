# Week 1 Rubric Audit: Phase 1 vs Sovereign Governance Standard

**Status:** SCAFFOLD (2026-04-22). The line-by-line walk is in progress; this file holds the structure plus known violations already surfaced during planning. Completed walk expected by end of Week 1.

**Scope:** audit every file under `core/`, `webapp/`, `plugin/`, `verticals/`, `skills/` against the 8 Sovereign Governance Standard rubric criteria. Each violation gets a `file:line` citation and a proposed remediation week.

## Rubric criteria (fixed)

| # | Criterion | Remediation owner week |
|---|---|---|
| 1 | Deterministic PVE: LLM cannot bypass any logic gate | Weeks 4-5 |
| 2 | Physical tenant isolation: cross-tenant leak impossible at DB level | Weeks 2-3 |
| 3 | State Provenance: every state change carries a SHA-256 citation hash | Weeks 4-5 |
| 4 | Lossless Auditing: decision writes survive DB locks / corruption | Weeks 4-5 |
| 5 | Model Sovereignty: swap Anthropic for Llama 4 in under 5 minutes | Weeks 6-7 |
| 6 | Atomic Financial Settlement: SLA-Escrow-Citation loop | Weeks 6-7 |
| 7 | Ambient Awareness with 14-day decay / hardened-fact split | Weeks 4-5 |
| 8 | Fractal Zoom UI with Tension HUD | Week 8 |

## Audit progress

| Area | Walk status |
|---|---|
| `core/governance/` | Pending |
| `core/primitives/` | Pending |
| `core/` (root modules) | Pending |
| `webapp/app.py` | 3 known bugs surfaced (see below) |
| `webapp/templates/` | Flagged for deprecation in Week 8 |
| `plugin/` | Pending |
| `verticals/` | Pending |
| `skills/` | Pending |

## Immediate-breakage findings (Week 1 PR)

These fixes land before Week 2 so we are not migrating polluted data.

### Finding 1: Path-traversal bypass on company route

- **Location:** `webapp/app.py:194` (`_company_or_404` helper).
- **Rubric criterion:** #2 (physical tenant isolation).
- **Severity:** high. Current containment is a string-prefix check, not a resolved-path check; `..` segments and Windows short-name variants may escape the vault root.
- **Remediation:** use `Path(candidate).resolve().relative_to(get_vault_dir().resolve())`; raise 404 on `ValueError`.
- **Owner week:** Week 1 (this week).

### Finding 2: `trust_snapshots` row bloat on every page load

- **Location:** `core/governance/trust.py::aggregate_trust`, `trust_snapshots` PK includes `computed_at`.
- **Rubric criterion:** #4 (lossless auditing, lightly; #7 adjacent via Ambient Awareness perf).
- **Severity:** medium. Every `/governance` hit writes a new row. A fresh sample vault accumulates thousands of rows inside a day of manual testing.
- **Remediation:** 60-second staleness guard. Only insert a new snapshot if the latest row for `(tenant, agent)` is older than 60s; otherwise return the latest.
- **Owner week:** Week 1.

### Finding 3: `_extract_job_id_from_response` helper missing

- **Location:** referenced in the Phase 1 plan snippet for `core/governance/retrolog.py`; not in the shipped file.
- **Rubric criterion:** #3 (state provenance; each retrolog row should carry the job id it is tied to).
- **Severity:** low but propagating: downstream decision rows cannot join cleanly to jobs.
- **Remediation:** ship the helper. Extract `job_id` from Flask redirect `Location` headers of the form `/c/<slug>/j/<job_id>`; fall back to `None`.
- **Owner week:** Week 1.

## Violation log (growing)

This table fills in as the walk proceeds. Format:

| File:line | Rubric # | Finding | Remediation | Owner week |
|---|---|---|---|---|
| `webapp/app.py:194` | 2 | Path-traversal guard missing | `.resolve().relative_to()` containment | 1 |
| `core/governance/trust.py` (aggregate_trust) | 4 | Snapshot row bloat | 60s staleness guard | 1 |
| `core/governance/retrolog.py` | 3 | `_extract_job_id_from_response` missing | Ship helper | 1 |
| `core/governance/evaluator.py` | 1 | File does not yet exist; Phase 1 has no deterministic PVE | Build pure-Python evaluator | 4-5 |
| `webapp/templates/*.html` | 8 | Flask templates inconsistent with Fractal Zoom model | Deprecate; replace with Next.js canvas | 8 |
| (pending) | | Wine-specific strings embedded in core code paths | Move to TenantConfig.vertical_config | 1 (extraction map) |
| (pending) | | SQLite-per-company under `<company>/governance/governance.sqlite` | Schema-per-tenant Postgres via DB adapter | 2-3 |
| (pending) | | Anthropic SDK calls directly wired in caller modules | Route through `core.llm_client.single_turn()` then MCP adapter | 6-7 |

## Wine-specific extraction inventory (separate file)

A dedicated inventory of every `wine`, `alcohol`, `vineyard`, `TTB`, `PLCB`, `winery` token inside `core/` lives in `docs/wine-extraction-inventory.md` (to be generated as part of the Week 1 walk). Each hit gets a proposed replacement: either a `TenantConfig.vertical_config` field reference or industry-agnostic phrasing.

## Completion criteria for this document

Week 1 Friday sign-off:
1. Every file under `core/`, `webapp/`, `plugin/`, `verticals/`, `skills/` appears in the Walk Status table with status `Complete`.
2. Every rubric violation has a `file:line` row in the Violation Log.
3. The three immediate-breakage fixes are merged to main.
4. `TenantConfig` Pydantic model exists in `core/tenant_config.py` and covers every field named in the Phase 2 plan.
5. Wine-extraction inventory is generated and each entry carries a replacement.
6. Risk register, rollback plan, Legacy Bridge spec, Brain/Memory/Walls/Lens overview, and pre-kernel test harnesses all exist in the repo.

## Notes for the walk

- Read-before-Edit discipline. Do not edit files before a full Read pass.
- No em dashes in any finding text or remediation note.
- Every violation gets a week owner even if the fix is a single line, so the sprint plan absorbs it.
- Findings with severity `low` can be grouped into a single cleanup PR at the end of the week.
