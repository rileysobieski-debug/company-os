---
description: Invoke Company OS's adversary agent to stress-test a founder-supplied thesis. Records an AdversaryReview stub under decisions/adversary-reviews/. Use when the user types something like "challenge this thesis" or invokes /company-os:adversary with a claim.
---

# Company OS — Adversary review

The user wants to stress-test a thesis with the adversary agent (plan §0.5 —
Path C). The adversary's only loyalty is to the thesis being challenged: it
does not synthesize, does not defer, and records its objections to disk.

## What to do

User arguments: `$ARGUMENTS`

1. Read `$ARGUMENTS` as the thesis to be challenged. If empty, ask the user
   to supply one.
2. Run the CLI to record an adversary review scaffold:
   ```bash
   python -m cli adversary --on "$ARGUMENTS" \
     --company-dir "<resolved company path>"
   ```
   Resolve `<resolved company path>` by reading the user's
   `COMPANY_OS_VAULT_DIR` env var + the company folder they specified
   (or the default active company if only one company folder exists).
3. After the scaffold lands, draft the actual adversary objections: a
   first-principles list of ways the thesis could be wrong. Cite KB
   passages where possible; otherwise mark claims as `assumption`.
4. Append your objections to the review file the CLI just created.

## What NOT to do

* Do not synthesize. Do not defer. Do not soften to stay agreeable.
* Do not invent concrete facts (competitor names, market sizes) without
  a KB-citable basis or an explicit `assumption` label.
* Do not override the founder's decision — log your objection, leave the
  call to them.
