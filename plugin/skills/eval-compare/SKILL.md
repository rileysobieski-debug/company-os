---
description: Consolidate two independent AI-model evaluations (Grok + Gemini) of the Company OS framework into a structured 10-section analysis. Produces agreements, disagreements, convergent attack surfaces, and prioritized action items. Use when the user says "consolidate the evals", "compare the evaluation responses", "analyze the grok and gemini feedback", or invokes /company-os:eval-compare with two response file paths.
---

# Company OS — Evaluation response consolidation

After `EVALUATION_PROMPT.md` has been run through Grok and Gemini and the
responses have been saved to disk, this skill produces a single
consolidated analysis that identifies where the two reviewers agreed
(strong signal), where they disagreed (needs investigation), and what
concrete action items drop out.

## What to do

User arguments: `$ARGUMENTS` — expected format: `<grok-path> <gemini-path>`.

### Step 1 — Validate the inputs

Run the CLI pre-flight check:

```bash
python -m cli eval-compare "<grok-path>" "<gemini-path>"
```

This confirms both files exist, contain the five expected section
headers, and are non-trivial in length. If the check fails with exit
code 2, report the missing file(s) to the user and stop. If the check
reports missing sections, flag that as a warning but proceed.

### Step 2 — Read both response files in full

Use the Read tool to load both files. Preserve the structure (section
headers, bullets, tables). Do not summarize yet — keep the raw content
available for the analysis below.

### Step 3 — Produce the 10-section consolidated analysis

Write the output with exactly these ten sections. No preamble, no
pleasantries, no "overall the reviewers thought this is interesting."

**Section 1 — Agreement Map (§6 novelty claims)**

Comparison table of all 9 novelty claims (6.1 through 6.9 from the
original eval package):

| Claim | Grok | Gemini | Agree? |
|---|---|---|---|
| 6.1 Integrated governance stack | [classification + 1-sentence summary] | [classification + 1-sentence summary] | YES/NO/PARTIAL |
| ... | ... | ... | ... |

Classification legend: GENUINELY NOVEL / UNCOMMON BUT EXISTS / DERIVATIVE.
YES = both classified the same; NO = one NOVEL, other DERIVATIVE;
PARTIAL = different classifications, compatible reasoning.

**Section 2 — High-confidence derivatives**

Claims BOTH reviewers marked DERIVATIVE. Deduplicate citations. These
are items to stop claiming as novel.

**Section 3 — Genuinely novel (per both reviewers)**

Claims BOTH reviewers marked GENUINELY NOVEL or UNCOMMON with no strong
prior art. Strongest signal — candidates for empirical focus.

**Section 4 — Contested novelty**

Claims where reviewers DISAGREED. Lay out both positions; identify what
evidence would resolve the disagreement.

**Section 5 — Convergent attack surfaces**

Vulnerabilities BOTH reviewers flagged. Rank by combined severity.
Include the attack sequences each described (merged if compatible).

**Section 6 — Divergent attack surfaces**

Vulnerabilities only ONE reviewer flagged. Note which and why the other
may have missed it.

**Section 7 — Ambient + Relationship layer synthesis**

For §8 (ambient awareness) and §9 (relationship layer) separately:
- Both agree on soundness? [Y/N]
- Common concerns (deduplicated)
- Prior art citations (deduplicated)
- Consolidated recommendation: BUILD / BUILD WITH MODIFICATION / DEFER / DON'T BUILD
- Reasoning (1-2 sentences)

Then: if both said build, which first? If they disagreed on priority,
note which each preferred and why.

**Section 8 — Research direction consolidated ranking**

Both reviewers re-ranked the 8 research directions from §10. Produce a
consolidated table:

| Rank | Direction | Grok's rank | Gemini's rank | Consolidated | Confidence |

Confidence: HIGH (differed ≤1), MEDIUM (differed 2-3), LOW (differed 4+).

Call out any direction where one reviewer rated highly but the other
rated low — that's the most interesting signal.

**Section 9 — Missing-primitive synthesis**

Deduplicate suggestions from both reviewers. For each:
- Proposed by: Grok / Gemini / both
- Summary (1 sentence)
- Estimated build cost: LOW (<5hrs) / MEDIUM (5-20hrs) / HIGH (>20hrs)
- Recommendation: ADOPT NOW / ADOPT POST-PHASE-14 / SKIP

**Section 10 — Prioritized action items**

Ordered 8-15 items, each tagged:
- **[BUILD]** — build this primitive / feature
- **[STOP-CLAIMING]** — remove novelty claim from future writing
- **[READ-AND-INVESTIGATE]** — read cited prior art, decide later
- **[DEFER-WITH-CRITERIA]** — defer until [specific condition met]

Rank by leverage (highest-ROI first).

### Step 4 — Write the output to disk

Save the analysis to:
`<company-os-root>/eval-responses/consolidated-YYYY-MM-DD.md`

Where YYYY-MM-DD is today's date. Create the `eval-responses/` directory
if it doesn't exist.

### Step 5 — Report to the user

Surface:
- The output file path
- A one-paragraph summary (what were the top 3 action items)
- Any section where the reviewers strongly disagreed (this is where the
  user should focus their own judgment)

## Style requirements for the analysis

- No pleasantries. No "both reviewers provided thoughtful feedback."
- Direct. If one reviewer was clearly wrong, say so.
- If you (the synthesizing agent) notice something both reviewers missed
  while comparing them, add it in Section 10 — flag it as "synthesis
  observation, not from either reviewer."
- Every citation claim traces back to one or both raw responses — don't
  introduce new citations.
- Tables for comparisons, bullets for lists, prose sparingly.
- Target length: 2,000-3,500 words total.

## What NOT to do

- Do not re-summarize the original Company OS eval package — the author
  wrote it, reviewers read it, you don't need to.
- Do not soften disagreements. Contested items are useful data.
- Do not be diplomatic. If both reviewers rejected something, say so.
- Do not suggest using different frameworks (LangGraph etc.) — the
  author's already evaluated those.
