# Phase 14 — Session handoff (2026-04-18)

**For Riley to read on wakeup.** 3-minute briefing on what shipped overnight.

## What you can do right now

Open **http://127.0.0.1:5050** (already running, ambient awareness enabled).

New in the nav:

| Route | What it is |
|---|---|
| **/office** | Radial org chart. Click = inspect. Double-click manager or specialist = inline dispatch form. |
| **/awareness** | Browse the 3 seeded ambient notes. Write your own via the "+ Write a founder note" form. |
| **/scenario** | **Primary experimental UI.** 9 seeded department briefs, click one, dispatch. Sidebar previews ambient context that'll be injected. |
| **/ledger** | Every scenario run. Rate -2..+2 with notes. Per-dept averages. This is what the newsletter pipeline reads. |
| **/ledger/export.md** | Newsletter-ready markdown digest of rated runs. Add `?include_unrated=1` for everything. |
| **/ledger/export.json** | Machine-readable export for the newsletter agent or analytics. |

Run a scenario. Rate it. That's the iteration-data loop you asked for.

## What was built (Grok + Gemini consolidated action items)

1. ✅ **Hash-backed provenance binding** (§10.1) — Vulnerability #1 both reviewers convergent on. SHA-256 over body + canonical provenance, baked into every KB chunk at ingest, verified in `resolve_conflict_with_integrity` and in `drift_guard.evaluate_dispatch` by default.
2. ✅ **Timestamp sanity check** (§10.3) — rejects `updated_at` > now + 5min. Blocks Gemini year-2099 attack.
3. ✅ **Founder-signature guard** (§10.3) — Priority 2 Decisions can't supersede Priority 1 Founder claims without `founder_signature: true` or `updated_by` in `FOUNDER_PRINCIPALS`.
4. ✅ **Watchdog substring-bypass fix** (§10.6) — `MIN_CLAIM_LENGTH=40`, word-boundary-aware match, opt-in `require_coverage` coverage ratio.
5. ✅ **Ambient awareness layer v1** (§10.2) — evidence-required + 14d TTL + TF-IDF relevance. Quality gate rejects hyper-generic noise at write-time. Preamble injected into dispatches when `COMPANY_OS_AMBIENT_AWARENESS=1`.
6. ✅ **Adversary rating trend detection** (Grok §6) — flags gradual poisoning without auto-resetting.
7. ✅ **Out-of-band verifiability principle** (§6.10 new) — the synthesized architectural claim. Added to EVALUATION_PACKAGE.md.

Deferred (per unanimous recommendation):
- ❌ Relationship layer (§9) — rejected at solo-operator scale
- Deferred: Prompt versioning, Claims 6.3/6.4/6.8 prior-art verification, evaluator hardening

## Tests

```
Baseline:  704 passing, 35 skipped
Now:       866 passing, 35 skipped  (+162 new)
```

Full run: 3.7 seconds. No flakiness. All 17 webapp routes return 200.

## Files to read first (if you want to verify)

- `company-os/lab-notebook.md` — 8 entries documenting the build. This is a newsletter-pillar candidate as-is.
- `company-os/eval-responses/consolidated-2026-04-18.md` — Grok + Gemini synthesis that drove the work.
- `company-os/EVALUATION_PACKAGE.md` — §6.7 + §6.9 revised, §6.10 added.
- `company-os/core/primitives/awareness.py` — the biggest new primitive. ~380 LOC.
- `company-os/core/primitives/integrity.py` — hash-backed provenance binding.
- `company-os/core/scenario_ledger.py` — iteration-data capture.

## Open threads (in order of leverage)

1. **Run 5–10 scenarios + rate them.** First real data into the ledger.
2. **BRAND store integrity hashing** — kb/ingest hashes; brand-db doesn't yet. Parallel the pattern.
3. **Specialist-level dispatch route** — `/office` double-click goes through the manager; direct-to-specialist needs a new endpoint.
4. **Flip `COMPANY_OS_AMBIENT_AWARENESS` default to on** after 14 days of dogfood noise-rate data.
5. **Verify contested novelty claims** (§10.5) — Linda tuple spaces, Hearsay-II, MemGPT, AgentDrift arXiv ID authenticity.
6. **Agent-side `write_awareness_note()` tool** — primitive works, UI surfaces it, but no tool yet for agents to write. Next iteration.

## Newsletter angle (per your directive)

The lab notebook format (built / observable result / open questions per entry) is already newsletter-shaped. Eight entries cover five attack surfaces and two new primitives. Each entry ends in a question the 60-day dogfood will answer. That's a natural pillar.

---

*Webapp runs until you `kill` it. Tests all green. Lab notebook is the paper trail. Sleep well.*
