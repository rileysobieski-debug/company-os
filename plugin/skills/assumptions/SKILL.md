---
description: Print Company OS assumption freshness status from assumptions-log.jsonl — which assumptions are fresh, which need founder review, which have been auto-demoted. Use when the user asks "what assumptions are we holding" or invokes /company-os:assumptions.
---

# Company OS — Assumption freshness status

The user wants to see the state of every tracked assumption — the
freshness-clock primitive (§7.4) promotes, demotes, or flags assumptions
based on use count and age.

## What to do

1. Resolve the company dir.
2. Run:
   ```bash
   python -m cli assumptions --company-dir "<resolved company path>"
   ```
3. Present the output, highlighting any `needs_review` or `demoted`
   entries — those require founder attention.

## Notes

* Assumption log path: `<company>/assumptions-log.jsonl`.
* Statuses: `fresh`, `needs_review`, `grace` (7-day window after
  needs_review), `demoted` (auto-demoted after grace elapses).
* If an entry is `needs_review`, suggest the founder review and
  either `promote` it (keep using) or `extend` it (defer).
