# Sample Vault for System-Behavior Review

This directory is a scrubbed, synthetic vault for reviewers who want to evaluate Company OS beyond the code itself. It contains a representative "company" named **Quarry Ridge Wine Co. LLC** with pre-populated artifacts across three departments, a company-level scope coordination output, rated scenario runs, and a sample arrival-note conversation.

**None of this content is real.** It is hand-written to plausibly resemble what the engine produces when run against a real vault. Reviewers should treat it as reference material for what the system generates, not as ground-truth training data.

## What's included

```
sample-vault/
  Quarry Ridge Wine Co. LLC/
    config.json                           # structured company config
    context.md, domain.md, priorities.md  # founder-facing narrative context
    founder_profile.md                    # persona the agents see
    orchestrator-charter.md               # orchestrator's scope rules
    priorities.md                         # long-form priority narrative
    marketing/
      skill-scope.md                      # manager arrival synthesis (personality seed + secondary)
      domain-brief.md                     # Phase 2 research output
      founder-brief.md                    # Phase 3 interview synthesis
      charter.md                          # Phase 6 final charter
      scope-of-work.md                    # Phase 7 coordination output
      roster.json                         # Phase 7 staffing proposal + hires
      copywriter/skill-scope.md           # sample sub-agent
      brand-strategist/skill-scope.md     # sample sub-agent
    finance/
      skill-scope.md
      scope-of-work.md
    operations/
      skill-scope.md
      scope-of-work.md
    coordination/
      scope-map.md                        # human-readable coordination output
      scope-map.json                      # structured coordination output
      state.json                          # coordination state machine
    scenarios/
      scenarios.jsonl                     # rated scenario runs (feeds trust aggregation)
    onboarding/
      marketing.json, finance.json, operations.json  # per-dept state machines
    conversations/
      arrival-marketing-a.json            # sample arrival-note thread, Candidate A
```

## What reviewers should look for

**Personality variance across candidates.** The 3-candidate hire flow claims to produce measurably different people by sampling independent personality seeds per candidate. Compare `marketing/skill-scope.md` (the selected hire) against the sample arrival conversation under `conversations/` to see the seed-anchored voice show through.

**Secondary expertise framing.** Every `skill-scope.md` should (a) name the primary expertise industry-locked to wine, (b) name a concrete prior-work secondary (NOT an abstract characteristic or philosophy), (c) explicitly state that the secondary is insight-only and not a second job. The marketing skill-scope uses "decommissioned textile-mill restoration" as its secondary. The finance skill-scope uses "community land-trust bookkeeping". The operations skill-scope uses "community radio station engineering". These are supposed to be adjacent but distinct, forming a connected lattice.

**Scope coordination output.** `coordination/scope-map.md` shows what the coordinating agent produces given three managers with their domain briefs and founder briefs. Look for: overlaps resolved (who owns each contested area), gaps flagged (what falls through the cracks), handoffs made explicit. Each department's `scope-of-work.md` should read as a direct derivative of the scope map.

**Trust aggregation on real data.** `scenarios/scenarios.jsonl` has 6 rated runs across the three managers. Run `aggregate_trust()` on this vault and you should see three distinct scores. The marketing manager has mixed signal (two +1s, one -1), finance has consistently positive (two +2s), operations has one negative (a -1 ratings flip). These produce scores in the roughly +0.2 / +0.7 / -0.1 range depending on timestamps and the half-life decay.

**Onboarding state integrity.** The three `onboarding/*.json` files show departments at different lifecycle stages: marketing is at `staffing` with an approved charter and an active roster, finance is at `charter` awaiting signoff, operations is at `founder_interview` with a running job. This lets reviewers verify the state transitions at a glance without needing to trigger live dispatches.

## How to spin it up (optional)

If you want to run the webapp against this fixture vault and actually click through the UI:

```bash
cd /path/to/cloned/company-os
pip install -r requirements.txt
COMPANY_OS_VAULT_DIR="$(pwd)/fixtures/sample-vault" \
    python webapp/app.py --host 127.0.0.1 --port 5050 --prod
```

Then open `http://localhost:5050` and pick Quarry Ridge Wine Co. from the index. You should see the three departments at their listed phases, be able to navigate to the Governance page and see computed trust scores, and be able to read the coordination output.

Clicking dispatch buttons (Run the hire, Start domain research, etc.) will require a valid `ANTHROPIC_API_KEY` in `~/.company-os/.env` because those routes actually call the Anthropic API. For pure code review you do not need to trigger LLM calls; the static fixtures are enough to understand the system's shape.

## What's deliberately absent

- **Real conversation history at volume.** Only one sample arrival-note conversation is included. Reviewers considering whether long threads stay coherent should inspect `core/conversation.py` for the mechanics.
- **Real rating data.** The six scenarios.jsonl entries are synthetic. Real vaults accumulate dozens to hundreds over time.
- **The `governance/governance.sqlite` database.** It is created on first access to the governance page. If you spin up the webapp, the SQLite file will be created under `sample-vault/Quarry Ridge Wine Co. LLC/governance/` on demand.
- **Demo artifacts, session logs, and other peripheral files** that accumulate in real use. The fixtures focus on the reviewable surface.
