# Company OS

A multi-agent operating system for a solo-founder company. Each department has a manager agent with a locked primary expertise and a serendipitous secondary background the agent chooses for itself. Managers hire sub-agents through a candidate-slate flow. A company-level scope-coordination round settles department boundaries. A governance layer observes the signal the founder is leaving through ratings and audit-logs every dispatch.

## What this is

Company OS is an engine. You point it at a vault folder on your disk that contains your company config and founder context, and the web app gives you a workflow for hiring, coordinating, and running the department agents that run your company.

The design goal is to let a solo founder scale their intent through AI agents without losing the plot. Specifically:

- Every agent carries a locked primary expertise (role x industry) so their work is grounded in the vertical.
- Every agent also carries a **serendipitous secondary** they declare themselves, not the founder. The secondary is an insight-only lens, never a second job. The point is unusual problem-solving frames the primary alone would miss.
- Agents get hired through candidate slates (three at a time, founder picks one). Each candidate draws independent personality seeds so the three are measurably different from each other.
- Managers stand up their own departments. The founder does not scope each manager one by one.
- A company-level coordination round settles scope-of-work across departments, replacing per-department founder interviews about scope. Managers cross-reference each other to avoid overlap and ensure full coverage.
- Governance Phase 1 observes and retro-logs founder-initiated dispatches. Agent-initiated actions are a separate future phase that depends on a wake-trigger mechanism not yet built.

## Repo layout

```
core/               engine primitives (config, department onboarding, coordination, roster, candidates, governance, cost, LLM client, etc.)
webapp/             Flask app, templates, services wrapper
cli/                command-line entry points
plugin/             Claude Code plugin shim
skills/             task-specific agent skills
verticals/          vertical-specific packs (scenario portfolios, etc.)
docs/               runbooks and reference material
tests/              pytest suite
```

## Running it

### Prerequisites

- Python 3.12+ (developed on 3.14)
- An Obsidian-style vault directory that holds your company folders. Each company folder contains a `config.json` and department subfolders.

### Environment

Set `COMPANY_OS_VAULT_DIR` to the absolute path of your vault. Secrets (Anthropic API key, Telegram, SMTP) live in `~/.company-os/.env` which is deliberately outside this tree and is never committed.

### Quick start

```bash
pip install -r requirements.txt
COMPANY_OS_VAULT_DIR="/path/to/your/vault" python webapp/app.py --host 127.0.0.1 --port 5050 --prod
```

Then open `http://localhost:5050/` and pick your company from the index.

## Current phase (2026-04-21)

Agent Governance Phase 1 shipped today:

- New `core/governance/` module with SQLite storage (WAL, foreign keys, busy_timeout).
- Trust scores per agent derived from three existing rating sources (onboarding signoff, scenario ledger, training ranks).
- Decisions audit table retro-logs every founder-initiated dispatch via a `@retrolog_dispatch` decorator.
- New `Governance` nav item with a trust-and-decisions page.

Phase 2+ of the governance design (agent-facing request tool, evaluator, budget holds, inbox, etc.) is deferred until a wake-trigger mechanism for autonomous agents exists. See the plan document in `~/.claude/plans/how-would-an-agent-quiet-origami.md` on the author's local machine for the full roadmap.

## License

Apache License 2.0. See LICENSE.

## Status

Pre-release, under active development. Not yet stable; interfaces change between commits. Production use at your own risk.
