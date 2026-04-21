---
description: Run the Company OS comprehensive demo — dispatches all active departments, runs orchestrator synthesis, runs board deliberation, writes an index artifact. Expensive (many LLM calls). Use only when the user explicitly asks to "run the full demo" or invokes /company-os:demo.
---

# Company OS — Comprehensive demo

Runs every active department against its vertical-pack brief, produces
a cross-dept synthesis, then convenes the board for a strategic
deliberation. Writes artifacts to `<company>/demo-artifacts/` and
`<company>/board/meetings/`.

## What to do

User arguments: `$ARGUMENTS` — optional flags or dept filter.

1. Confirm the user wants the expensive full run (many LLM calls at
   once — costs add up).
2. Resolve the company dir.
3. Run:
   ```bash
   python -m cli demo --company-dir "<resolved company path>" \
     [--depts <dept-list>] [--force] [--skip-board] [--skip-synthesis]
   ```
4. Stream the progress output. The runner is idempotent: without
   `--force`, existing dept artifacts are preserved.

## Flags

* `--depts <names>` — limit to specific departments.
* `--force` — regenerate artifacts even if they exist.
* `--skip-board` — skip the board deliberation step.
* `--skip-synthesis` — skip the orchestrator synthesis step.
* `--vertical <name>` — override the default "wine-beverage" pack.

## Notes

* This is the classic end-to-end demo. Real dispatch workloads should
  use `/company-os:run-dept` with a specific brief rather than running
  the whole demo every time.
