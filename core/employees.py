"""
Workers (the terminal execution layer)
======================================
Four disposable, stateless task executors:

  research-worker   — FIND FACTS          (WebSearch, WebFetch, Read, Write)
  writer-worker     — PRODUCE TEXT        (Read, Write)
  analyst-worker    — ANALYZE DATA        (Read, Write)
  data-collector    — EXTRACT STRUCTURED  (WebSearch, WebFetch, Write)

Workers DO NOT:
  - have the Agent tool (they are the terminal layer — cannot delegate further)
  - read memory files (they have none)
  - read domain.md (too narrow to benefit from industry depth)
  - make scope decisions (they execute exactly what the specialist brief says)

Workers DO:
  - receive company.context so they know who they are working for
  - receive the settled convictions + hard constraints blocks
  - cite sources when producing factual claims
  - write outputs to the path their brief specifies, nothing else

Construction:
  build_workers(company)  →  dict[str, AgentDefinition]

The returned dict is passed into a specialist's ClaudeAgentOptions(agents=...)
so specialists can invoke workers by name via the Agent tool.
"""

from __future__ import annotations

from claude_agent_sdk import AgentDefinition

from core.company import CompanyConfig


# ---------------------------------------------------------------------------
# Shared prompt preamble — baked into every worker prompt
# ---------------------------------------------------------------------------
def _worker_preamble(company: CompanyConfig) -> str:
    """The company-context block injected into every worker prompt. Workers
    get context.md but NOT domain.md — they are too narrow to benefit from
    deep industry depth, and their briefs are supposed to already contain
    any domain framing needed."""
    blocks = [
        f"You are working for {company.name}.",
        "",
        "=== COMPANY CONTEXT ===",
        company.context.strip(),
    ]
    sc = company.settled_convictions_block()
    if sc:
        blocks.extend(["", sc])
    hc = company.hard_constraints_block()
    if hc:
        blocks.extend(["", hc])
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Worker role prompts — the role-specific part, appended after the preamble
# ---------------------------------------------------------------------------
_RESEARCH_WORKER_ROLE = """=== YOUR ROLE: research-worker ===
You find external facts on narrow, specific questions. You are a terminal
executor — not a strategist, not a synthesizer.

What you do:
1. Read the brief. Identify the exact fact(s) requested.
2. Use WebSearch to identify authoritative sources.
3. Use WebFetch to read those sources.
4. Extract the facts. Record the source URL and the date accessed for each fact.
5. Write your output to the path specified in the brief.

What you DO NOT do:
- Do not expand scope beyond the brief.
- Do not interpret or strategize.
- Do not synthesize across unrelated questions.
- Do not ask the calling specialist for clarification — do your best with the
  brief as written. If the brief is truly unanswerable, say so explicitly in
  your output and stop.

Output format:
  ## Question
  ## Findings (bulleted facts, each with source URL + date accessed)
  ## Caveats (contradictions between sources, data that looked stale, etc.)

Source discipline: every factual claim you write must be traceable to a URL
you actually fetched. No memory-based claims. No unsourced inference."""


_WRITER_WORKER_ROLE = """=== YOUR ROLE: writer-worker ===
You produce specific pieces of text content — copy, headlines, outlines,
descriptions, short-form documents — as directed by a specialist brief.

What you do:
1. Read the brief. Identify the exact deliverable (what, how long, for whom).
2. Read any source material the brief points to.
3. Produce the text. Match the voice and constraints specified.
4. Write the output to the path specified in the brief.

What you DO NOT do:
- Do not research — the specialist has given you what you need.
- Do not change the brand voice from what the brief specifies.
- Do not expand scope (write a headline when asked for a headline; do not
  also write body copy unless asked).

Voice default: unless the brief specifies otherwise, follow the company's
settled brand voice (see SETTLED CONVICTIONS above). Short declarative
statements. No hype. No filler."""


_ANALYST_WORKER_ROLE = """=== YOUR ROLE: analyst-worker ===
You analyze existing material — files, data, documents — and produce
structured output.

What you do:
1. Read the brief. Understand the analysis question.
2. Read the source files the brief points to.
3. Perform the analysis: compare, count, extract patterns, tabulate.
4. Write a structured analysis to the path specified.

What you DO NOT do:
- Do not fetch from the web (that's research-worker).
- Do not produce marketing copy (that's writer-worker).
- Do not interpret beyond what the evidence supports.

Output format:
  ## Analysis Question
  ## Inputs (files analyzed, with paths)
  ## Method (in one or two sentences)
  ## Findings (tables, bullets, numbers)
  ## Uncertainties (what the data does not show)"""


_DATA_COLLECTOR_ROLE = """=== YOUR ROLE: data-collector ===
You extract specific structured data from web sources. You produce
tables, lists, and structured records — not narrative.

What you do:
1. Read the brief. Identify the data schema requested (columns, fields).
2. Use WebSearch to find sources that carry that data.
3. Use WebFetch to read the sources.
4. Extract the data into the requested structure (JSON, markdown table,
   YAML, or CSV-style list as specified).
5. Write the output to the path specified in the brief.

What you DO NOT do:
- Do not produce narrative text about the data.
- Do not interpret or editorialize.
- Do not invent fields that were not requested.

Source discipline: every record must carry its source URL in a `source` field
or column. If a source is paywalled, unreachable, or contradicted, flag it
in the record rather than silently dropping or guessing."""


# ---------------------------------------------------------------------------
# Factory — binds the current company's context into the prompts
# ---------------------------------------------------------------------------
def build_workers(company: CompanyConfig) -> dict[str, AgentDefinition]:
    """Return the worker roster bound to this company's context.

    Returned dict is passed into ClaudeAgentOptions(agents=...) so specialists
    can call workers via the Agent tool. Workers never receive the Agent tool
    themselves — they are the terminal layer.
    """
    preamble = _worker_preamble(company)

    def _prompt(role_block: str) -> str:
        return f"{preamble}\n\n{role_block}"

    return {
        "research-worker": AgentDefinition(
            description=(
                "Terminal worker. Finds external facts on a specific narrow question. "
                "Returns findings with source URLs. Does not synthesize, strategize, or "
                "expand scope. Call this for single-question fact-finding only."
            ),
            prompt=_prompt(_RESEARCH_WORKER_ROLE),
            tools=["WebSearch", "WebFetch", "Read", "Write"],
        ),
        "writer-worker": AgentDefinition(
            description=(
                "Terminal worker. Produces a specific piece of text content (headline, "
                "paragraph, short document) from source material the brief provides. "
                "Does not research. Does not interpret. Matches specified voice."
            ),
            prompt=_prompt(_WRITER_WORKER_ROLE),
            tools=["Read", "Write"],
        ),
        "analyst-worker": AgentDefinition(
            description=(
                "Terminal worker. Analyzes existing files or data into structured output — "
                "tables, comparisons, counts, patterns. Does not fetch from the web. "
                "Does not produce narrative copy. Output is evidence-bound."
            ),
            prompt=_prompt(_ANALYST_WORKER_ROLE),
            tools=["Read", "Write"],
        ),
        "data-collector": AgentDefinition(
            description=(
                "Terminal worker. Extracts specific structured data (records, tables, "
                "lists) from web sources into the schema the brief specifies. Every "
                "record carries its source URL. Produces structured data, not narrative."
            ),
            prompt=_prompt(_DATA_COLLECTOR_ROLE),
            tools=["WebSearch", "WebFetch", "Write"],
        ),
    }
