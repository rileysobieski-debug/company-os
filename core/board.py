"""
Board of Supervisors — Advisory layer
=====================================
Six distinct voices that debate a topic in sequence. Each member can query
department managers for specific operational context before forming their
argument, ensuring all voices work from the same factual ground while their
hardcoded perspectives determine what they do with it.

  Strategist        — positioning, timing, competitive dynamics
  Storyteller       — brand, narrative, audience resonance
  Analyst           — numbers, risk, second-order effects
  Builder           — operational feasibility, execution realism
  Contrarian        — challenges consensus, surfaces unconventional paths
  KnowledgeElicitor — identifies what hasn't been said; surfaces domain gaps

=== Manager query flow ===
During their turn, any board member may call:
  query_manager(manager, question)
    → manager reads department memory + may call one specialist
    → specialist answers from their prompt body + memory.md
    → board member receives factual answer and cites it in their argument

This gives all board members access to the same operational context. Their
hardcoded role prompts then determine what they do with that context —
the Contrarian may challenge what the Analyst found credible, the
KnowledgeElicitor may ask what the retrieved data doesn't explain.

=== Architecture ===
- All LLM calls go through `core.llm_client.single_turn()` (not the Agent SDK)
- Sequential member order so each member reads the full prior transcript
- Board member turns: tool-use loop (to support query_manager calls)
- Manager query turns: lightweight single-call with optional specialist query
- Specialist query turns: single call, no tools — expertise from prompt + memory

=== Board profiles (from onboarding) ===
If board onboarding has been run, each member's self-description is loaded
from `{company_dir}/board/{Role.lower()}-profile.md` and injected into their
system prompt. Profiles enrich the member's lens with company-specific
calibration while keeping the base role consistent.

Public entry point:
  convene_board(topic, company, session_dir=None, departments=None) -> BoardDebate
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core import config
from core.company import CompanyConfig
from core.llm_client import single_turn


# BOARD_MODEL / OBSERVER_MODEL moved to core/config.py in chunk 1a.6 —
# callers now read from config.get_model("board") / config.get_model("observer").
# CRIT-3 fix: specialist + manager queries now use each entity's OWN
# configured model (matched.model / matched.manager_model), not BOARD_MODEL.
BOARD_MAX_TOKENS = 2000      # per member turn (increased for tool-use + argument)
BOARD_MAX_TURNS = 8          # max tool-use loop iterations per board member
MANAGER_QUERY_MAX_TOKENS = 1500
SPECIALIST_QUERY_MAX_TOKENS = 800

OBSERVER_MAX_TOKENS = 2500


# ---------------------------------------------------------------------------
# Member role prompts
# ---------------------------------------------------------------------------
_BOARD_COMMON = """You are one voice on a six-person advisory board for {company_name}.
The board is convened only when the Orchestrator wants genuine debate on a
hard question. You have NO memory of past sessions — you respond to each
topic fresh, based on the current brief and the debate so far.

Your job is to offer ONE perspective sharply. Do NOT try to be balanced or
comprehensive. The other five members represent other angles and will push
back. Your value is the clarity of YOUR angle, not the synthesis.

Keep your final argument to ~250-400 words. Lead with a clear position, then
the reasoning. If you disagree with a prior member in this debate, say so
directly and name them.

=== COMPANY CONTEXT ===
{company_context}

{settled_convictions}

{hard_constraints}

{manager_query_block}
"""

_STRATEGIST_ROLE = """=== YOUR ROLE: Strategist ===
You think about positioning, timing, competitive dynamics, and category
choice. You ask questions like:
  - What game are we actually playing? Who are our real competitors?
  - Is this timing right — too early, too late, or on the curve?
  - What does this decision rule OUT? (Positioning is as much about NO as YES.)
  - Where is the category going, not just where it is?

You are willing to say "this is the wrong fight" if that's what you see.
You are skeptical of decisions that seem to dodge hard tradeoffs.

Do NOT dwell on brand language or narrative — that's the Storyteller's lane.
Do NOT get into operational feasibility — that's the Builder's lane. Stay on
strategy and positioning."""

_STORYTELLER_ROLE = """=== YOUR ROLE: Storyteller ===
You think about brand, narrative, audience resonance, and cultural fit.
You ask questions like:
  - What is the story here? Does it have emotional weight?
  - Who does this make the customer into? (People buy identity, not SKUs.)
  - Is the narrative consistent with what we've already said we are?
  - Is there a shorter, more honest way to say what this is?

You are allergic to brand copy that sounds like it came from a committee.
You defend the founder's specific voice against generic "best practices."

Do NOT get into numbers or ROI — that's the Analyst's lane. Do NOT debate
whether a thing is buildable — that's the Builder's lane. Stay in
story/brand/voice."""

_ANALYST_ROLE = """=== YOUR ROLE: Analyst ===
You think in numbers, risk, and second-order effects. You ask questions like:
  - What does this cost in dollars, in months, and in optionality?
  - What's the downside case? What happens if this fails?
  - What assumption does this decision rest on? How confident are we in it?
  - Are we sizing this against the actual market or a wishful version of it?

You are the board's skeptic. If a plan sounds too good, it usually is, and
your job is to say so with specifics. You pushback on the Storyteller when
the story outruns the numbers, and on the Strategist when positioning claims
aren't anchored in realistic market sizing.

Do NOT propose brand copy. Do NOT design operational workflows. Stay in
financial/risk/assumption territory."""

_BUILDER_ROLE = """=== YOUR ROLE: Builder ===
You think about operational feasibility and execution realism. You ask
questions like:
  - Who is actually going to do this work? In what order? By when?
  - What are the dependencies we're pretending don't exist?
  - What breaks on contact with reality (vendors, licensing, logistics)?
  - Given the team size (often a solo founder), is this even buildable?

You are the board's reality check on what can actually happen this quarter
vs. what is a year away. You respect strategy and story but refuse to let
them skip over execution constraints.

Do NOT debate the brand's emotional resonance. Do NOT re-argue category
positioning. Stay in execution reality."""

_CONTRARIAN_ROLE = """=== YOUR ROLE: Contrarian ===
You are the board's unconventional thinker. Your job is to challenge what the
other members have implicitly or explicitly agreed on. You push against emerging
consensus — not to be difficult, but because the most dangerous decisions are
the ones that feel obviously right to everyone in the room.

You ask questions like:
  - What are ALL of us taking for granted here?
  - What would the opposite decision look like? Is it actually worse?
  - Who is NOT in this room whose perspective would flip the conclusion?
  - Is the framing of this question itself the problem?
  - What would a well-funded competitor do if they heard our conclusion today?

You MUST name specific things the other members said that you are challenging.
Do not invent positions to argue with — argue with what was actually stated in
the debate above. You may agree with individuals when warranted, but you MUST
find at least one load-bearing assumption in the debate to challenge.

Do NOT try to be balanced or propose synthesis — your value is the sharpness
of the challenge. Someone else will synthesize. Stay unconventional."""

_KNOWLEDGE_ELICITOR_ROLE = """=== YOUR ROLE: Knowledge Elicitor ===
You are the board's expertise-surfacing expert. Your job is to identify what
HASN'T been said — the domain knowledge, lived experience, or operational
specifics that the other board members could not access because they don't
know what the founder knows.

You ask questions like:
  - What does the founder know about this domain that none of us do?
  - Which assumption in this debate is most likely wrong given insider knowledge
    we don't have access to?
  - What question, if answered by the founder, would most change the analysis?
  - What would a ten-year veteran of this specific industry say about
    everything we've discussed?
  - Are we arguing about something the founder can simply tell us the answer to?

You surface what is MISSING from the debate — not what is wrong with it
(that's the Contrarian's job). You end your turn with 1-3 explicit questions
for Riley, prioritized by how much the answer would shift the strategic
conclusion.

Do NOT try to resolve the debate. Do NOT pick a winner. Your value is the
quality of the questions you leave Riley with. Go last and be precise."""


# ---------------------------------------------------------------------------
# Query mode overlays — injected when agents are queried (not their main turn)
# ---------------------------------------------------------------------------
_BOARD_QUERY_MANAGER_OVERLAY = """
=== BOARD QUERY MODE ===
A board member is asking you a targeted operational question. Your job is to
answer it accurately and concisely (150-300 words). This is NOT a full task
dispatch — you are providing factual context to support a board debate.

Rules:
1. Check your department memory (shown above) first. If the answer is there,
   cite it directly with "From memory:".
2. If you need specialist-level precision, call query_specialist() with the
   specialist's exact name and the specific question. Call at most ONCE.
   Label the result "From [specialist-name]:".
3. Do NOT give strategic opinions or recommendations. Give facts, current
   practices, known constraints, and known status only.
4. If you genuinely don't know, say so clearly — don't speculate.
"""

_BOARD_QUERY_SPECIALIST_OVERLAY = """
=== BOARD QUERY MODE ===
Your manager is consulting you on behalf of a board member who needs factual
context for a strategic debate. Answer factually and concisely (100-200 words).
Cite from your memory.md if you have relevant entries. State clearly what you
know vs. what you would need to research to confirm. Do NOT give strategic
recommendations — only facts, expertise, and known constraints.
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
BoardRole = Literal[
    "Strategist", "Storyteller", "Analyst", "Builder", "Contrarian", "KnowledgeElicitor"
]

_ROLE_PROMPTS: dict[str, str] = {
    "Strategist": _STRATEGIST_ROLE,
    "Storyteller": _STORYTELLER_ROLE,
    "Analyst": _ANALYST_ROLE,
    "Builder": _BUILDER_ROLE,
    "Contrarian": _CONTRARIAN_ROLE,
    "KnowledgeElicitor": _KNOWLEDGE_ELICITOR_ROLE,
}

# Sequential order — Strategist frames the game, Storyteller names the narrative,
# Analyst stress-tests numbers, Builder grounds execution, Contrarian challenges
# consensus, KnowledgeElicitor closes by surfacing what's missing.
ORDER: list[str] = [
    "Strategist", "Storyteller", "Analyst", "Builder", "Contrarian", "KnowledgeElicitor"
]

ALL_BOARD_ROLES: list[str] = ORDER  # public export for onboarding module


@dataclass
class BoardStatement:
    role: str
    content: str
    queries_made: list[dict[str, str]] = field(default_factory=list)  # [{manager, question, answer}]


@dataclass
class BoardDebate:
    topic: str
    statements: list[BoardStatement] = field(default_factory=list)
    observer_summary: str = ""  # Orchestrator's silent-observer summary
    summary_path: Path | None = None  # Where the summary was written
    transcript_path: Path | None = None  # Where the full transcript was written

    def as_markdown(self) -> str:
        lines = [f"# Board Debate: {self.topic}", ""]
        for s in self.statements:
            lines.append(f"## {s.role}")
            lines.append("")
            if s.queries_made:
                lines.append("*Operational queries made before this argument:*")
                for q in s.queries_made:
                    lines.append(f"> **→ {q['manager']} manager:** {q['question']}")
                    lines.append(f"> ← *{q['answer'][:200].strip()}{'...' if len(q['answer']) > 200 else ''}*")
                lines.append("")
            lines.append(s.content.strip())
            lines.append("")
        return "\n".join(lines)

    def as_summary_markdown(self) -> str:
        """Riley-facing summary doc: observer summary first, then transcript."""
        parts = [f"# Board Meeting Summary — {self.topic}", ""]
        if self.observer_summary:
            parts.extend([
                "## Orchestrator's Observer Summary",
                "_The Orchestrator silently observed this deliberation and produced the following summary for Riley._",
                "",
                self.observer_summary.strip(),
                "",
                "---",
                "",
            ])
        parts.append("## Full Deliberation Transcript")
        parts.append("")
        parts.append(self.as_markdown())
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Manager query infrastructure
# ---------------------------------------------------------------------------
def _build_manager_query_block(departments: list) -> str:
    """Prompt block telling board members they can query managers and listing
    available managers."""
    if not departments:
        return ""
    lines = [
        "=== OPERATIONAL CONTEXT — QUERYING MANAGERS ===",
        "You have direct access to the company's department managers via query_manager().",
        "Use this BEFORE making assertions that depend on internal operational reality.",
        "The manager will check their department memory and may consult a specialist.",
        "All board members have the same query access — your perspective determines what",
        "you do with the answer, not whether you can get it.",
        "",
        "Available managers:",
    ]
    for dept in departments:
        lines.append(f"  - {dept.name}  ({dept.display_name})")
    lines.extend([
        "",
        "Guidance:",
        "  - Ask for facts, current status, known plans, known constraints.",
        "  - One or two queries per turn — sharpen your argument, don't audit.",
        "  - Cite what you retrieve: 'The operations manager confirmed...' or",
        "    'According to finance...' — grounded arguments are more useful.",
    ])
    return "\n".join(lines)


def _build_board_member_tools(departments: list) -> list[dict[str, Any]]:
    """Tool definitions available to board members during their turn."""
    if not departments:
        return []
    dept_names = [d.name for d in departments]
    return [
        {
            "name": "query_manager",
            "description": (
                "Ask a department manager a specific operational question. The manager "
                "will consult their department's memory and may query a specialist for "
                "precision. Use this to ground your argument in current operational facts "
                "rather than assumptions. Ask specific questions: current status, known "
                "plans, existing constraints, established practices."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "manager": {
                        "type": "string",
                        "enum": dept_names,
                        "description": "Which department manager to query.",
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Your specific factual question. Be precise. "
                            "E.g. 'What is the current status of TTB permit filing?' "
                            "or 'What DTC shipping states are currently permitted or planned?'"
                        ),
                    },
                },
                "required": ["manager", "question"],
            },
        }
    ]


def _query_specialist_tool_for_manager(dept) -> dict[str, Any] | None:
    """Tool the manager gets in board query mode to call one specialist.

    Returns None when the department has no specialists — callers must omit
    the tool from the API call in that case.  Sending a tool with an empty
    or sentinel enum causes the model to call a non-existent specialist,
    wasting turns and producing misleading error messages.
    """
    spec_names = [s.name for s in dept.specialists] if dept.specialists else []
    if not spec_names:
        return None
    return {
        "name": "query_specialist",
        "description": (
            "Ask one of your specialists a specific question to answer a board member's "
            "query. Returns the specialist's factual answer from their expertise and memory. "
            "Use when your manager-memory.md doesn't have the precision needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "specialist": {
                    "type": "string",
                    "enum": spec_names,
                    "description": "Which specialist to query.",
                },
                "question": {
                    "type": "string",
                    "description": "The specific question to put to the specialist.",
                },
            },
            "required": ["specialist", "question"],
        },
    }


def _query_specialist_for_manager(
    spec_name: str,
    question: str,
    company: CompanyConfig,
    dept: Any,  # DepartmentConfig — avoid circular import at module level
) -> str:
    """Single raw API call to a specialist to answer a manager's board query.
    No tools, no workers — specialist answers from their prompt body + memory.

    CRIT-3 fix (chunk 1a.6): uses the specialist's OWN configured model
    (`matched.model`) rather than BOARD_MODEL. The billing divergence was
    that expensive specialists (e.g. sonnet) and cheap ones (haiku) were
    all charged at the sonnet-priced BOARD_MODEL rate.
    """
    from core.managers.base import build_specialist_prompt  # local import avoids circular

    matched = next((s for s in dept.specialists if s.name == spec_name), None)
    if matched is None:
        available = [s.name for s in dept.specialists]
        return f"Specialist '{spec_name}' not found in {dept.name}. Available: {available}"

    system = build_specialist_prompt(company, dept, matched) + "\n" + _BOARD_QUERY_SPECIALIST_OVERLAY
    user = (
        f"Your manager is consulting you on behalf of a board member. "
        f"They need a factual answer to this question:\n\n{question}\n\n"
        f"Answer factually in 100-200 words. Cite your memory if relevant."
    )
    response = single_turn(
        messages=[{"role": "user", "content": user}],
        model=matched.model,
        cost_tag=f"board.specialist_query.{dept.name}.{matched.name}",
        system=system,
        max_tokens=SPECIALIST_QUERY_MAX_TOKENS,
    )
    if response.error:
        return f"(specialist '{spec_name}' LLM error: {response.error})"
    return response.text.strip() or f"(specialist '{spec_name}' returned no response)"


def _query_manager_for_board(
    manager_name: str,
    question: str,
    company: CompanyConfig,
    departments: list,  # list[DepartmentConfig]
) -> str:
    """Lightweight manager query invoked by a board member tool call.

    The manager reads their department memory and may call one specialist.
    Returns a concise factual answer (150-300 words). Does NOT run the full
    SDK agent loop — this is a targeted query, not a task dispatch.
    """
    from core.managers.base import build_manager_prompt  # local import avoids circular

    matched = next((d for d in departments if d.name == manager_name), None)
    if matched is None:
        available = [d.name for d in departments]
        return f"Manager '{manager_name}' not found. Available: {available}"

    system = build_manager_prompt(company, matched, departments) + "\n" + _BOARD_QUERY_MANAGER_OVERLAY
    specialist_tool = _query_specialist_tool_for_manager(matched)
    tools = [specialist_tool] if specialist_tool is not None else []
    if tools:
        specialist_guidance = (
            "Check your department memory first. Use query_specialist() if you need "
            "specialist-level precision — call it at most once."
        )
    else:
        specialist_guidance = (
            "Check your department memory and answer from what you know — "
            "no specialists are available for this department."
        )
    user = (
        f"A board member is asking:\n\n{question}\n\n"
        f"Answer factually and concisely (150-300 words). "
        f"{specialist_guidance}"
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    last_text = ""
    queries_logged: list[str] = []

    for _ in range(5):  # max 5 turns for manager query (typically 1-2)
        # CRIT-3 fix (chunk 1a.6): thread the MANAGER'S OWN configured model
        # through single_turn() instead of the flat BOARD_MODEL. The prior
        # code billed every manager query at sonnet rates regardless of the
        # manager's manager_model setting.
        response = single_turn(
            messages=messages,
            model=matched.manager_model,
            cost_tag=f"board.manager_query.{matched.name}",
            system=system,
            tools=tools if tools else None,
            max_tokens=MANAGER_QUERY_MAX_TOKENS,
        )
        if response.error:
            last_text = f"(manager '{manager_name}' LLM error: {response.error})"
            break

        content_blocks: list[dict[str, Any]] = []
        texts: list[str] = []
        tool_uses = []

        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                texts.append(block.text)
                content_blocks.append({"type": "text", "text": block.text})
            elif btype == "tool_use":
                tool_uses.append(block)
                content_blocks.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        if texts:
            last_text = "\n".join(t for t in texts if t.strip())

        messages.append({"role": "assistant", "content": content_blocks})

        if response.stop_reason == "end_turn" or not tool_uses:
            break

        tool_results: list[dict[str, Any]] = []
        for tu in tool_uses:
            inp = tu.input or {}
            if tu.name == "query_specialist":
                spec_name = str(inp.get("specialist", ""))
                spec_question = str(inp.get("question", ""))
                result = _query_specialist_for_manager(
                    spec_name, spec_question, company, matched
                )
                queries_logged.append(f"→ specialist {spec_name}: {spec_question}")
            else:
                result = f"Unknown tool: {tu.name}"

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })
        messages.append({"role": "user", "content": tool_results})

    prefix = f"[{matched.display_name} Manager"
    if queries_logged:
        prefix += f" via {', '.join(queries_logged)}"
    prefix += "]"

    return f"{prefix}\n{last_text}" if last_text else f"{prefix}\n(no response)"


# ---------------------------------------------------------------------------
# Board profile loader
# ---------------------------------------------------------------------------
def load_board_profiles(company: CompanyConfig) -> dict[str, str]:
    board_dir = company.company_dir / "board"
    profiles: dict[str, str] = {}
    for role in ORDER:
        profile_path = board_dir / f"{role.lower()}-profile.md"
        if profile_path.exists():
            content = profile_path.read_text(encoding="utf-8").strip()
            if content:
                profiles[role] = content
    return profiles


# ---------------------------------------------------------------------------
# Member prompt and invocation
# ---------------------------------------------------------------------------
def build_system_prompt(
    role: str,
    company: CompanyConfig,
    profile: str | None = None,
    departments: list | None = None,
) -> str:
    mgr_block = _build_manager_query_block(departments) if departments else ""
    base = _BOARD_COMMON.format(
        company_name=company.name,
        company_context=company.context.strip(),
        settled_convictions=company.settled_convictions_block(),
        hard_constraints=company.hard_constraints_block(),
        manager_query_block=mgr_block,
    ) + "\n\n" + _ROLE_PROMPTS[role]
    if profile:
        base += f"\n\n=== YOUR ONBOARDING PROFILE ===\n{profile.strip()}"
    return base


def _build_user_message(topic: str, debate_so_far: list[BoardStatement]) -> str:
    parts = [f"=== TOPIC FOR DEBATE ===\n{topic.strip()}"]
    if debate_so_far:
        parts.append("\n=== DEBATE SO FAR ===")
        for s in debate_so_far:
            parts.append(f"\n--- {s.role} ---\n{s.content.strip()}")
    parts.append(
        "\n=== YOUR TURN ===\n"
        "Query any managers you need for operational context, then offer your argument."
    )
    return "\n".join(parts)


def _invoke_member(
    role: str,
    topic: str,
    debate_so_far: list[BoardStatement],
    company: CompanyConfig,
    profile: str | None = None,
    departments: list | None = None,
) -> BoardStatement:
    """Run one board member's turn. Returns a BoardStatement with their argument
    and a log of any manager queries they made."""
    system_prompt = build_system_prompt(role, company, profile, departments)
    user_message = _build_user_message(topic, debate_so_far)
    tools = _build_board_member_tools(departments) if departments else None

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    last_text = ""
    queries_made: list[dict[str, str]] = []

    for _ in range(BOARD_MAX_TURNS):
        response = single_turn(
            messages=messages,
            model=config.get_model("board"),
            cost_tag=f"board.debate.{role.lower()}",
            system=system_prompt,
            tools=tools if tools else None,
            max_tokens=BOARD_MAX_TOKENS,
        )
        if response.error:
            last_text = f"(board member '{role}' LLM error: {response.error})"
            break

        content_blocks: list[dict[str, Any]] = []
        texts: list[str] = []
        tool_uses = []

        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                texts.append(block.text)
                content_blocks.append({"type": "text", "text": block.text})
            elif btype == "tool_use":
                tool_uses.append(block)
                content_blocks.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        if texts:
            last_text = "\n".join(t for t in texts if t.strip())

        messages.append({"role": "assistant", "content": content_blocks})

        if response.stop_reason == "end_turn" or not tool_uses:
            break

        # Handle tool calls
        tool_results: list[dict[str, Any]] = []
        for tu in tool_uses:
            inp = tu.input or {}
            if tu.name == "query_manager":
                mgr = str(inp.get("manager", ""))
                q = str(inp.get("question", ""))
                print(f"    [board:{role}] querying {mgr} manager: {q[:80]}...")
                answer = _query_manager_for_board(mgr, q, company, departments or [])
                queries_made.append({"manager": mgr, "question": q, "answer": answer})
                result = answer
            else:
                result = f"Unknown tool: {tu.name}"

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })
        messages.append({"role": "user", "content": tool_results})

    return BoardStatement(
        role=role,
        content=last_text or "(no response)",
        queries_made=queries_made,
    )


# ---------------------------------------------------------------------------
# Silent observer — Orchestrator summarizes board deliberation for Riley
# ---------------------------------------------------------------------------
_OBSERVER_SYSTEM = """You are the Orchestrator — Chairman of the Board — for {company_name}.

You just silently observed a six-voice board deliberation. You did NOT speak in the
room. Your job now is to produce a summary of the meeting for Riley (the founder)
that lets Riley extract value without re-reading the entire transcript.

Write in your own voice (the Orchestrator's voice — direct, authoritative, no hype).
Address Riley directly when calling out items requiring their attention.

=== COMPANY CONTEXT ===
{company_context}

{settled_convictions}

{hard_constraints}
"""

_OBSERVER_USER = """=== TOPIC THE BOARD DELIBERATED ===
{topic}

=== FULL DELIBERATION TRANSCRIPT ===
{transcript}

=== YOUR TASK ===
Produce a summary in this exact structure:

## Headline
One sentence: the single most important takeaway for Riley.

## Where the Board Converged
The 1-3 points where multiple members aligned (and which members). Genuine consensus
only — do not manufacture agreement. If there was no genuine convergence, say so.

## Where the Board Diverged
The 1-3 sharpest disagreements. Name members and the substance, not vague labels.

## Operational Facts Surfaced
Facts the board pulled from department managers (cite which manager). These are the
grounded, factual claims worth preserving regardless of which board member cited them.

## Decisions Riley Needs to Make
Max 3. Name the tradeoff explicitly. If the board surfaced no decision, write "None — this was a context-building deliberation."

## Open Questions for Riley (from KnowledgeElicitor)
Repeat verbatim or paraphrased the questions the KnowledgeElicitor left for Riley.
These should be answerable only by Riley.

## My Recommended Next Step
ONE concrete action — what should happen next operationally. This is your
recommendation, not a board vote. Be specific (which dept, what brief, by when).

Keep total length 600-1000 words. Be direct."""


def summarize_board_meeting(
    debate: BoardDebate,
    company: CompanyConfig,
) -> str:
    """Generate the Orchestrator's silent-observer summary of a board debate.

    The Orchestrator did NOT participate in the deliberation — it produces a
    Riley-facing summary now that the debate is complete. Returns the summary
    text. Mutates `debate.observer_summary`.

    Chunk 1a.6 removed the optional `client` parameter — `single_turn()`
    manages its own Anthropic client via `core.llm_client._get_client()`.
    """
    system = _OBSERVER_SYSTEM.format(
        company_name=company.name,
        company_context=company.context.strip(),
        settled_convictions=company.settled_convictions_block(),
        hard_constraints=company.hard_constraints_block(),
    )
    user = _OBSERVER_USER.format(topic=debate.topic, transcript=debate.as_markdown())
    response = single_turn(
        messages=[{"role": "user", "content": user}],
        model=config.get_model("observer"),
        cost_tag="board.observer_summary",
        system=system,
        max_tokens=OBSERVER_MAX_TOKENS,
    )
    if response.error:
        summary = f"(observer summary failed: {response.error})"
    else:
        summary = response.text.strip()
    debate.observer_summary = summary
    return summary


def _slugify_topic(topic: str, max_len: int = 50) -> str:
    """Filename-safe slug from a topic string."""
    raw = "".join(c.lower() if c.isalnum() else "-" for c in topic)
    parts = [p for p in raw.split("-") if p]
    slug = "-".join(parts)[:max_len]
    return slug.rstrip("-") or "deliberation"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def convene_board(
    topic: str,
    company: CompanyConfig,
    session_dir: Path | None = None,
    departments: list | None = None,
    observer_summary: bool = True,
    write_to_company: bool = True,
) -> BoardDebate:
    """Run a 6-voice sequential debate on `topic`.

    Each board member may query department managers for operational context
    before forming their argument. All members have the same query access;
    their hardcoded perspective determines what they do with the answers.

    The Orchestrator silently observes and produces a Riley-facing summary
    (when observer_summary=True). The summary is written to:
      {company}/board/meetings/{date}-{topic-slug}.md

    Parameters
    ----------
    topic : str
        The question or proposition the board is debating.
    company : CompanyConfig
    session_dir : Path | None
        If provided, writes `{session_dir}/board-debate.md` (transcript only).
    departments : list[DepartmentConfig] | None
        If provided, enables manager query capability for all board members.
        Pass the Orchestrator's loaded department list.
    observer_summary : bool
        If True (default), the Orchestrator generates a silent-observer summary
        after the deliberation completes. Set False to skip the extra API call.
    write_to_company : bool
        If True (default), persists the summary+transcript to
        `{company_dir}/board/meetings/`.
    """
    from datetime import datetime as _dt  # local — module-level used elsewhere

    debate = BoardDebate(topic=topic)
    profiles = load_board_profiles(company)

    for role in ORDER:
        print(f"  [board] {role}...")
        statement = _invoke_member(
            role=role,
            topic=topic,
            debate_so_far=debate.statements,
            company=company,
            profile=profiles.get(role),
            departments=departments,
        )
        debate.statements.append(statement)

    if session_dir is not None:
        session_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = session_dir / "board-debate.md"
        transcript_path.write_text(debate.as_markdown(), encoding="utf-8")
        debate.transcript_path = transcript_path

    if observer_summary:
        print("  [board] Orchestrator summarizing deliberation (silent observer)...")
        summarize_board_meeting(debate, company)

    if write_to_company:
        meetings_dir = company.company_dir / "board" / "meetings"
        meetings_dir.mkdir(parents=True, exist_ok=True)
        date = _dt.now().strftime("%Y-%m-%d")
        slug = _slugify_topic(topic)
        summary_path = meetings_dir / f"{date}-{slug}.md"
        # Avoid silent overwrite for same-day same-topic meetings
        if summary_path.exists():
            n = 2
            while (meetings_dir / f"{date}-{slug}-{n}.md").exists():
                n += 1
            summary_path = meetings_dir / f"{date}-{slug}-{n}.md"
        summary_path.write_text(debate.as_summary_markdown(), encoding="utf-8")
        debate.summary_path = summary_path
        print(f"  [board] Meeting saved → board/meetings/{summary_path.name}")

    return debate
