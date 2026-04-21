---
description: Dispatch a brief to a Company OS department manager (full handshake + evaluator + memory loop). Use when the user says "run marketing with this brief" or invokes /company-os:run-dept. Expects the dept name followed by the brief text.
---

# Company OS — Department dispatch

Full hierarchical dispatch: manager receives the brief, selects
specialist(s), they produce work, evaluator scores it, memory logs it.

## What to do

User arguments: `$ARGUMENTS` — expected format: `<dept-name> <brief text>`.

1. Parse the first token of `$ARGUMENTS` as the dept name; the rest is
   the brief.
2. If either is missing, ask the user.
3. Resolve the company dir.
4. Run:
   ```bash
   python -m cli run <dept> --brief "<brief text>" \
     --company-dir "<resolved company path>"
   ```
5. Print the dept manager's synthesis response.

## Active departments (wine-beverage vertical)

`marketing`, `finance`, `operations`, `product-design`, `community`,
`editorial`, `data`, `ai-workflow`, `ai-architecture`.

## Notes

* This is the FULL dispatch path — handshakes, evaluator, memory
  updater all fire. Use `/company-os:talk-to` instead for a quick
  single-turn chat with one specialist (no machinery).
