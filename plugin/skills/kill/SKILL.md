---
description: Kill-switch for a Company OS specialist. Pauses the specialist and records a 3-question retro (what did you expect / see / would fix it). Use when the user types "this specialist isn't working" or invokes /company-os:kill with a specialist name.
---

# Company OS — Kill-switch retro

The user wants to pause a specialist and record a 3-question retro
(plan §0.5 Component 3). The retro will be surfaced as an input signal
to the next adversary activation.

## What to do

User arguments: `$ARGUMENTS` — expected to be a specialist name.

1. Parse the specialist name from `$ARGUMENTS`. If missing, ask.
2. Ask the user the three required questions in order:
   * What did you expect?
   * What did you see?
   * What would fix it?
3. Run the CLI to record the retro, passing the answers:
   ```bash
   python -m cli kill $ARGUMENTS \
     --expected "<answer 1>" \
     --saw "<answer 2>" \
     --fix "<answer 3>" \
     --prompt-ref "<last-known-good prompt ref if known>" \
     --company-dir "<resolved company path>"
   ```
4. Confirm the retro was recorded and surface the file path to the user.

## Notes

* The retro file goes to `<company>/decisions/retros/<date>-<specialist>.md`
  with a JSON sidecar for programmatic access.
* Do not restart the specialist automatically — that is a deliberate
  founder action after the retro is reviewed.
