---
description: Print Company OS cost summary from the cost-log.jsonl. Shows total calls, input/output tokens, and per-tag breakdown. Optionally filter to a YYYY-MM month. Use when the user asks "what has the OS spent this month" or invokes /company-os:costs.
---

# Company OS — Cost dashboard (CLI)

The user wants a readout of LLM spend.

## What to do

User arguments: `$ARGUMENTS` — if it matches `YYYY-MM`, pass as the month filter.

1. Resolve the company dir from the user's vault + the active company.
2. Run:
   ```bash
   python -m cli costs --company-dir "<resolved company path>" \
     [--month $ARGUMENTS]
   ```
3. Surface the output. If there's no `cost-log.jsonl` file, tell the
   user the system hasn't logged any calls yet.

## Notes

* The CLI reads `<company>/cost-log.jsonl` which every dispatch path
  appends to via the `TokenLedger` primitive.
* The per-tag breakdown is useful for identifying where budget is
  going — `demo.*`, `adversary.*`, `training.*`, etc.
