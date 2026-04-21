---
description: Single-turn chat with a Company OS specialist — no handshake, no evaluator, no memory loop. Faster than /company-os:run-dept for quick questions. Use when the user wants to ask one specialist one question directly.
---

# Company OS — Direct specialist chat

A single-turn escape hatch. The specialist's prompt body is loaded,
the user's message is sent, the response is returned. No dispatch
machinery fires.

## What to do

User arguments: `$ARGUMENTS` — expected format: `<specialist-name> <message>`.

1. Parse the first token of `$ARGUMENTS` as the specialist name; the
   rest is the message.
2. If either is missing, ask.
3. Resolve the company dir.
4. Run:
   ```bash
   python -m cli talk-to <specialist> --message "<message>" \
     --company-dir "<resolved company path>"
   ```
5. Print the response.

## When to prefer this over `/company-os:run-dept`

* One-question check-in (no artifact needed).
* You know the exact specialist and bypass the manager routing.
* You want to avoid the evaluator writing a verdict to disk.

## When NOT to use this

* The task needs coordination across specialists — use `run-dept`
  so the manager can route.
* The output should be tracked, evaluated, or land in
  `pending-approval/` — use `run-dept`.
