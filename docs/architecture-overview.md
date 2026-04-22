# Company OS: Sovereign Governance Chassis Architecture Overview

**Version:** v6 (2026-04-22)
**Scope:** one-page orientation for reviewers, auditors, and incoming contributors. Every layer's boundaries and failure modes labeled.

## The four layers

The chassis has exactly four layers. Order is dependency order: each layer consumes only the layers above it. No back-edges.

```
  Agent (LLM or human) proposes an action
            |
            v
  +-----------------------------+
  | BRAIN                       |    Deterministic decision engine.
  |                             |    No LLM calls inside.
  |  Semantic Gateway           |
  |    Pydantic ActionRequest   |
  |    small classifiers        |
  |    intent validation        |
  |                             |
  |  Pure-Python Evaluator      |
  |    ordered gates            |
  |    EscalationManifest       |
  |    KMS-signed overrides     |
  +--------------+--------------+
                 |
                 v
  +-----------------------------+
  | MEMORY                      |    Canonicalized state provenance.
  |                             |
  |  Citation hash (SHA-256)    |
  |    semantic canonicalizer   |
  |    versioned rules          |
  |                             |
  |  Hardened / Shadow split    |
  |    14-day decay worker      |
  |  Inherited Context          |
  |    transition-mode imports  |
  |    hardened=False default   |
  |                             |
  |  DLQ (lossless writes)      |
  +--------------+--------------+
                 |
                 v
  +-----------------------------+
  | WALLS                       |    Resource-bounded tenant isolation.
  |                             |
  |  Schema-per-tenant Postgres |
  |    connection pool          |
  |    PgBouncer session mode   |
  |                             |
  |  SafePath chroot            |
  |  Per-request rlimits        |
  |    RLIMIT_AS, RLIMIT_CPU    |
  |    statement_timeout        |
  |                             |
  |  Wasm / Firecracker         |
  |    DEFERRED to Phase 3      |
  +--------------+--------------+
                 |
                 v
  +-----------------------------+
  | LENS                        |    Presentation + audit surface.
  |                             |
  |  Headless API (FastAPI)     |
  |  Fractal Zoom UI            |
  |  Tension HUD                |
  |  Atomic Citation drawer     |
  +-----------------------------+
```

## Layer responsibilities

### Brain (Weeks 4-5)

Deterministic PVE plus escalation protocol. The Semantic Gateway converts fuzzy LLM output into a validated `ActionRequest` or rejects it. The Evaluator runs ordered gates (autonomy, rate, dormancy, budget, hard constraints, explicit config approvals, trust-weighted risk tiers) and returns either approve, auto-deny, or an `EscalationManifest` naming who may override and what evidence they must cite. Override signatures come from AWS KMS or HashiCorp Vault; the evaluator process never holds the private key.

**Failure modes:**
- Gateway lets malformed intent through: Pydantic schema test suite catches this in CI.
- Evaluator bypassed by direct storage write: retrolog decorator on every founder-initiated route forces writes through the evaluator path.
- KMS outage: evaluator falls back to queue-and-retry for override requests; never forges a signature locally.

### Memory (Weeks 4-5)

Every state change carries a SHA-256 hash of its source, computed over a canonicalized form (whitespace-normalized, comment-stripped, JSON-key-sorted, Python-AST-normalized). Citations are hash-verified at execution time; drift triggers auto-deny. Ambient observations start as Shadow Context. After 14 days with no contradiction plus at least one successful citation, they harden into ground-truth facts.

Transition-mode tenants bring in existing state via Import Adapters. Imports land as `InheritedContext` rows with `hardened=False`. They do not participate in PVE hard-constraint evaluation until the founder explicitly hardens them. Conflicts surface as UI Tension events, not silent blocks.

The Dead-Letter Queue captures governance writes that fail the primary DB. Background worker drains on recovery. App refuses to serve until DLQ backlog is zero.

**Failure modes:**
- Canonicalizer version skew: each citation records the canonicalizer version active at write time; re-verification uses that version, not the current one.
- Shadow row quietly promoted without citation: decay worker verifies at-least-one-citation invariant before hardening.
- DLQ loss during OS crash: journal is append-only fsync; startup check replays and refuses to serve with backlog.

### Walls (Weeks 2-3)

One Postgres schema per tenant. Cross-tenant queries are physically impossible at the DB level (not just policy enforced). `SafePath(tenant_id).resolve(path)` enforces chroot containment on every filesystem read. Per-request `RLIMIT_AS`, `RLIMIT_CPU`, and `statement_timeout` bound a single tenant's compute so a runaway loop cannot starve peers.

**Known deferred:** Wasm / Firecracker microVM isolation is the physical-compute endgame. Shared Python interpreter with GIL contention under heavy concurrent load is the v6 gap, documented here so auditors see it explicitly. Phase 3 work.

**Failure modes:**
- Schema name collision: tenant IDs are UUIDs, schema names derive from them via deterministic hash.
- Path-traversal bypass: every resolved path is asserted `.is_relative_to(tenant_root.resolve())`; anything else raises `SovereignBreach`.
- rlimit evasion via subprocess: we forbid subprocess spawn from inside tenant handlers. Where needed (PDF rendering, image processing) it goes through a worker pool with its own rlimits.

### Lens (Week 8, UI track parallelizes from Week 3)

Headless FastAPI plus Next.js canvas UI. Four zoom levels: Portfolio, Company, Specialist, Atomic Citation. Tension HUD heatmap overlays the canvas. Vibe Stream side-panel visualizes Shadow Context decay and hardening events. Citation Drawer is the deepest zoom: click any decision, see the hash, the source content, the full provenance chain back to founder intent.

**Failure modes:**
- API shape drift vs UI: OpenAPI 3.1 spec lives in `core/api/openapi.yaml`; CI verifies UI's generated client matches current spec.
- Stale HUD: SSE stream from `/events` pushes Tension events to the canvas live; no polling.
- Citation chain gap: UI refuses to render a drawer if any link in the chain fails hash verification; surfaces as red Tension event instead.

## Tenancy modes

Every tenant carries a `tenancy_mode` field: `native`, `transition`, or `hybrid`. All modes run through the same Brain, Memory, Walls, and Lens. Only the ingress path differs.

- **Native:** greenfield. No import adapters. All state originates inside the chassis.
- **Transition:** existing business. Import Adapters read Notion, QuickBooks, Slack, and other legacy stacks. Imports land as Shadow Context; founder hardens selectively.
- **Hybrid:** transition tenant that has finished the import wave and now operates mostly native, but retains a few read-only Legacy Bridge connections for historical reporting.

## What this diagram does not show

- Settlement (Coinbase x402, mocked in Weeks 6-7): attaches to Brain via escrow primitive. SLA-Escrow-Citation atomic loop.
- MCP model adapter (Weeks 6-7): attaches between the agent and the Semantic Gateway. Backend-swappable in under 5 minutes (Anthropic, OpenAI, Llama 4).
- Legacy Bridge (Week 1): read-only SQLite fallback for Phase 1 historical vaults. Returned rows marked `legacy=true`; never confused with hardened rows.

## Phase 1 to Phase 2 boundary

Phase 1 (shipped 2026-04-21) stays on disk, read-only. The Legacy Bridge surfaces it. Nothing in the rewrite modifies Phase 1 SQLite files. Rollback at any point in Weeks 2-12 is a `COMPANY_OS_VAULT_DIR` env var change plus a `webapp/app.py` revert.

## Reviewer orientation (30-second read)

If you are auditing this system in under a minute: the claim is that no LLM can bypass any logic gate (Brain), no state change lacks a verifiable hash to source (Memory), no tenant can read or starve another (Walls), and every decision has an atomic citation trail visible four zoom levels deep (Lens). Each claim has a Week-numbered deliverable and a verification test. Start at `docs/rubric-audit-2026-w1.md` for the line-by-line walk.
