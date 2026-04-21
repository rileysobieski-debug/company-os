# Consolidated evaluation analysis prompt

**Purpose:** After you've received responses from Grok and Gemini to `EVALUATION_PROMPT.md`, use this prompt to have Claude Code (or any other capable model) produce a consolidated analysis that identifies agreements, disagreements, convergent attack surfaces, and prioritized action items.

## How to use

1. Save Grok's response to a file: `eval-responses/grok-[DATE].md`
2. Save Gemini's response to a file: `eval-responses/gemini-[DATE].md`
3. Open this file
4. In the two sections below marked `PASTE GROK RESPONSE HERE` and `PASTE GEMINI RESPONSE HERE`, paste the full contents of each response (replacing the placeholder text entirely)
5. Copy the resulting document from the `---` line below through to the end
6. Paste into a fresh Claude Code chat (or Claude.ai / another capable model)
7. Send. Expect a 1,500-3,000 word consolidated analysis back.

The output goes naturally into your vault at `eval-responses/consolidated-[DATE].md` for long-term reference.

---

You are a senior technical reviewer synthesizing two independent evaluations of a multi-agent orchestration framework called **Company OS**. Two expert reviewers (Grok-4 and Gemini-2.5-Pro) have each produced a structured evaluation response covering prior-art classification, attack surface analysis, assessment of two proposed primitive layers, research direction ranking, and missing-primitive suggestions.

Your job is to consolidate their responses into a single actionable analysis. The framework's author (solo operator, 5-10 hrs/wk capacity) needs to know where the reviewers agreed (strong signal), where they disagreed (investigate further), and what concrete action items drop out.

## Your task

Produce a structured consolidated analysis with exactly the ten sections below. No preamble, no pleasantries, no "overall the reviewers thought this is interesting." Go directly to substance.

### Section 1 — Agreement Map (§6 novelty claims)

For each of the 9 novelty claims (6.1 through 6.9 from the original eval package), build a comparison table:

| Claim | Grok | Gemini | Agree? |
|---|---|---|---|
| 6.1 Integrated governance stack | [classification + 1-sentence summary] | [classification + 1-sentence summary] | YES/NO/PARTIAL |
| ... | ... | ... | ... |

If both classified it the same way (both NOVEL, both DERIVATIVE, both UNCOMMON) → YES. If one said NOVEL and the other DERIVATIVE → NO (flag this — needs investigation). If they used different classifications but their reasoning was compatible → PARTIAL.

### Section 2 — High-confidence derivatives

Claims BOTH reviewers marked as DERIVATIVE. Deduplicate their citations. These are items the author should stop claiming as novel. Include the overlap set of cited frameworks/papers so the author can read them.

Format:
- **Claim 6.X — [title]**
  - Both reviewers cite: [deduplicated list]
  - Action: stop claiming novelty; acknowledge prior art in any future writing.

### Section 3 — Genuinely novel (per both reviewers)

Claims BOTH reviewers marked as GENUINELY NOVEL (or UNCOMMON with no strong prior art). Strongest signal that the author has something worth developing. For each, note what specifically makes it novel per the reviewers (paraphrase their reasoning).

Format:
- **Claim 6.X — [title]**
  - Why novel (per Grok): [paraphrase]
  - Why novel (per Gemini): [paraphrase]
  - Action: protect and develop; this is a candidate for the empirical focus.

### Section 4 — Contested novelty

Claims where reviewers DISAGREED on classification. Lay out both positions fairly, then identify what evidence would resolve the disagreement.

Format:
- **Claim 6.X — [title]**
  - Grok's position: [summary + citations if any]
  - Gemini's position: [summary + citations if any]
  - Resolving evidence needed: [what would the author check to decide?]

### Section 5 — Convergent attack surfaces

Vulnerabilities BOTH reviewers flagged as exploitable. Merge their attack sequences if compatible. Rank by combined exploitability (higher if both ranked it top-3).

Format:
- **Vulnerability #1: [name]**
  - Grok's attack: [summary]
  - Gemini's attack: [summary]
  - Combined severity: HIGH / MEDIUM / LOW
  - Immediate mitigation suggested by reviewers: [if any]

### Section 6 — Divergent attack surfaces

Vulnerabilities only ONE reviewer flagged. Worth investigating but lower urgency. Note which reviewer raised it and why the other may have missed it (different threat model? different attack-surface familiarity?).

### Section 7 — Ambient + Relationship layer synthesis

Both reviewers assessed §8 (ambient awareness) and §9 (relationship layer). Synthesize:

a) **Ambient awareness (§8):**
   - Both agree on soundness? [Y/N]
   - Common concerns: [deduplicated list]
   - Prior art citations they each gave: [deduplicated]
   - Consolidated recommendation: BUILD / BUILD WITH MODIFICATION (specify) / DON'T BUILD
   - Reasoning: [1-2 sentences]

b) **Relationship layer (§9):**
   - Both agree on soundness? [Y/N]
   - Common concerns: [deduplicated list]
   - Prior art citations they each gave: [deduplicated]
   - Consolidated recommendation: BUILD / DEFER / DON'T BUILD
   - Reasoning: [1-2 sentences]

c) **Build priority:** if both said to build at least one, which first? If they disagreed, note which each preferred and why.

### Section 8 — Research direction consolidated ranking

Both reviewers re-ranked the 8 research directions from §10. Produce a consolidated ranking:

| Rank | Direction | Grok's rank | Gemini's rank | Consolidated | Confidence |
|---|---|---|---|---|---|
| 1 | [title] | 1 | 2 | 1-2 | HIGH |
| ... | ... | ... | ... | ... | ... |

- Consolidated rank = average, rounded to tier.
- Confidence:
  - HIGH = reviewers differed by ≤1 position
  - MEDIUM = differed by 2-3 positions
  - LOW = differed by 4+ positions (flag as contested; investigate why)

Call out any direction that ONE reviewer rated highly but the OTHER rated low — that's the most interesting signal.

### Section 9 — Missing-primitive synthesis

Both reviewers suggested primitives/patterns the author missed. Deduplicate. For each suggestion:

- **Suggested primitive: [name]**
  - Proposed by: Grok / Gemini / both
  - Summary: [1 sentence]
  - Estimated build cost: LOW (<5hrs) / MEDIUM (5-20hrs) / HIGH (>20hrs)
  - Recommendation: ADOPT NOW / ADOPT POST-PHASE-14 / SKIP
  - Reasoning: [brief]

### Section 10 — Prioritized action items

Synthesize the above into an ordered action list. Each item should be:
- Specific (not "improve governance" — actual action)
- Actionable within the 5-10 hr/wk constraint
- Either a BUILD action, a STOP-CLAIMING action, a READ-AND-INVESTIGATE action, or a DEFER-WITH-CRITERIA action

Format:
1. **[ACTION TYPE] [specific action]** — [1-sentence justification from the synthesis above]
2. ...

Aim for 8-15 action items, ranked by leverage (highest-ROI first).

## Style requirements

- No pleasantries. No "both reviewers provided thoughtful feedback."
- Direct. If one reviewer was clearly wrong on a point, say so.
- If the reviewers missed something you notice in comparing them, add it — note that it's your observation, not theirs.
- Every citation claim in your output should trace back to either Grok's response or Gemini's response (or both). Do not introduce new citations.
- Use tables where comparison is direct. Use bullets where listing.
- Target length: 2,000-3,500 words.

## What NOT to do

- Do not re-summarize the original Company OS package — the author wrote it, the reviewers read it, you don't need to.
- Do not add new evaluations — synthesize only.
- Do not soften disagreements. Contested items are useful data; don't paper over them.
- Do not be diplomatic. If both reviewers thought something was a bad idea, say "both rejected this" — don't write "the reviewers expressed concerns."

---

# GROK'S RESPONSE

[PASTE GROK RESPONSE HERE — replace this entire placeholder including the brackets with Grok's full response text, preserving its section headers]

---

# GEMINI'S RESPONSE

[PASTE GEMINI RESPONSE HERE — replace this entire placeholder including the brackets with Gemini's full response text, preserving its section headers]

---

Now produce the consolidated 10-section analysis described above. Start directly with "## Section 1 — Agreement Map" — no preamble.
