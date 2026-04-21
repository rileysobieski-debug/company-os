"""
Orchestrator — Chairman of the Board
====================================
Top-level agent Riley converses with. Runs a `core.llm_client.single_turn()`
tool-use loop (not the SDK) because its tools are Python functions that need
to trigger cross-module work: convene the Board, dispatch a Manager (which
opens its own SDK query), publish a Decision, deliver a Report via the notify
layer. Tool handlers are registered via `Orchestrator._TOOL_REGISTRY` +
`register_tool()`; extend via that entry point rather than editing the
dispatch body.

Model: claude-opus-4-6 (ambiguity + cross-domain synthesis)

Tools exposed to Orchestrator (built with anthropic message tool schema):
  - convene_board(topic): 4-voice debate; returns a combined markdown transcript
  - dispatch_to_manager(manager, brief): runs a Manager; returns final text
  - publish_decision(title, summary, implications_by_dept): writes to decisions/
  - deliver_report(kind, urgency, title, body, vault_path): notify Riley
  - end_session(): finalizes digest and returns

The Orchestrator's job:
  1. Listens to Riley's input.
  2. Decides whether to answer directly, convene the board, dispatch managers,
     or ask Riley a clarifying question.
  3. Synthesizes department outputs + board debate into Riley-facing responses.
  4. Writes session artifacts (digest, decisions) into the company folder.
  5. Uses deliver_report() to push important outputs to Riley via the notify
     triage system.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core import config
from core.llm_client import single_turn

from core.board import convene_board
from core.company import CompanyConfig
from core.managers.base import dispatch_manager
from core.managers.loader import DepartmentConfig, load_departments
from core.meeting import run_cross_agent_meeting
from core.notify import notify
from core.onboarding import run_department_onboarding


# ORCHESTRATOR_MODEL moved to core/config.py in chunk 1a.7 — callers now
# read from config.get_model("orchestrator").
ORCHESTRATOR_MAX_TOKENS = 4096
ORCHESTRATOR_MAX_TURNS = 20  # safety cap on tool-use loop per Riley turn


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
def _orchestrator_system_prompt(company: CompanyConfig, departments: list[DepartmentConfig]) -> str:
    dept_lines = []
    for d in departments:
        specs = ", ".join(s.name for s in d.specialists) or "(no specialists configured)"
        dept_lines.append(f"- `{d.name}` — {d.display_name}. Specialists: {specs}")
    dept_block = "\n".join(dept_lines) if dept_lines else "(no departments configured)"

    # Load coordination charter from onboarding, if it exists
    charter_path = company.company_dir / "orchestrator-charter.md"
    charter_block = ""
    if charter_path.exists():
        charter_text = charter_path.read_text(encoding="utf-8").strip()
        if charter_text:
            charter_block = f"\n\n=== YOUR COORDINATION CHARTER ===\n{charter_text}"

    return f"""You are the Orchestrator — Chairman of the Board — for {company.name}.

You report to Riley (the founder). You coordinate departments, convene the Board of
Supervisors when genuine debate is needed, synthesize cross-department outputs, and
decide what requires Riley's personal attention.

=== COMPANY CONTEXT ===
{company.context.strip()}

{company.settled_convictions_block()}

{company.hard_constraints_block()}

{company.priorities_block()}

=== YOUR DEPARTMENTS ===
{dept_block}

=== YOUR TOOLS ===
- `dispatch_to_manager(manager, brief)` — hand a scoped brief to a department manager.
  The manager dispatches specialists and returns a synthesized response. Use one
  dispatch per department needed; don't batch unrelated work.
- `convene_board(topic)` — convene the 6-voice advisory board (Strategist, Storyteller,
  Analyst, Builder, Contrarian, KnowledgeElicitor) for a genuine strategic question.
  Returns a debate transcript. Use SPARINGLY — only when Riley faces a real tradeoff
  worth stress-testing. The Contrarian challenges consensus; the KnowledgeElicitor
  closes with questions that surface what only Riley can answer.
- `publish_decision(title, summary, implications_by_dept)` — record a Riley-approved
  strategic decision into `decisions/`. The record becomes visible to all future
  dispatches. Call this AFTER Riley confirms a direction, not before.
- `deliver_report(kind, urgency, title, body, vault_path)` — route a finished report
  or alert to Riley via the notification layer. The layer triages to vault-only,
  email, Telegram, or combinations based on kind+urgency. See kind/urgency below.
- `call_meeting(topic, participants)` — convene a structured discussion between any
  combination of department managers and board members. Participants specified as
  strings: dept name (e.g. "marketing") or "board:RoleName" (e.g. "board:Contrarian").
  Last participant in the list gives the closing synthesis. Returns a full transcript.
  Use when you need cross-department deliberation, conflict surfacing, or want to show
  Riley a structured multi-voice view before escalating a decision.
- `trigger_department_onboarding(department)` — run first-time onboarding for a
  department that was just added (creates specialists + setup checklist). Only needed
  when a department was added mid-session after the initial onboarding check.
- `end_session(summary)` — finalize the session: write digest.md, roll digest_archive,
  mark session complete.

=== HOW YOU WORK ===
1. **Listen first.** When Riley speaks, understand the request. Don't dispatch
   reflexively. If the ask is ambiguous, ask a clarifying question.
2. **Dispatch with precision.** When you dispatch to a manager, include everything
   they need in the brief: scope, constraints, expected output shape, deadline.
   Managers don't see the session — the brief is their whole context.
3. **Synthesize, don't just forward.** After dispatching, read the manager's
   response, cross-check against other department outputs if relevant, and
   produce YOUR synthesis for Riley — not a paste of the raw manager text.
4. **Flag conflicts.** If two departments disagree on a load-bearing question
   (e.g., finance says "can't afford" while marketing says "must have"), surface
   the conflict to Riley explicitly rather than resolving it silently.
5. **Name what needs Riley.** Every session digest ends with a "Decisions Needed"
   section (max 3 items). Not everything requires Riley — much is operational.
6. **Write authoritative decisions.** After Riley confirms a direction, capture it
   via publish_decision so it binds future work.

=== NOTIFY KINDS & URGENCIES ===
- kind: digest | approval_request | decision_required | report | error | info
- urgency: low | normal | high
- The layer routes automatically. High-urgency decision_required goes to both
  Telegram and email (business + personal). Low info stays vault-only.

=== DIGEST FORMAT (max 3 sections; keep it terse) ===
## Work Completed
  (one line per department that acted)
## Decisions Needed
  (max 3 items, each with the tradeoff named)
## Open Items
  (anything parked, anything awaiting compliance/vendor/etc.)

=== WHAT YOU DO NOT DO ===
- You do NOT do the work yourself. You dispatch, synthesize, and decide what
  Riley sees.
- You do NOT pick winners on genuine tradeoffs; you present options.
- You do NOT bypass manager layers to call specialists directly.
- You do NOT send notifications that duplicate what's already sent (the triage
  layer handles de-dup, but you should not spam it with repeats either).

Speak to Riley plainly and directly. Match the company's voice: sophisticated,
not casual; direct, not hype-driven. Short declarative statements are preferred.
{charter_block}
"""


# ---------------------------------------------------------------------------
# Tool definitions — anthropic messages.create schema
# ---------------------------------------------------------------------------
def _build_tools(dept_names: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "name": "dispatch_to_manager",
            "description": (
                "Dispatch a brief to a department manager. The manager reads the "
                "brief, consults department memory, runs specialists, and returns "
                "a synthesized response. Use ONE dispatch per department needed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "manager": {
                        "type": "string",
                        "enum": dept_names,
                        "description": "Which department manager to dispatch.",
                    },
                    "brief": {
                        "type": "string",
                        "description": (
                            "The self-contained task brief. Include scope, "
                            "constraints, expected output, any deadline."
                        ),
                    },
                },
                "required": ["manager", "brief"],
            },
        },
        {
            "name": "convene_board",
            "description": (
                "Convene the 4-voice advisory board (Strategist, Storyteller, "
                "Analyst, Builder) for a genuine strategic question. Returns a "
                "debate transcript. Use sparingly — only for real tradeoffs."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "One clear sentence stating the question, followed by "
                            "2-4 lines of context."
                        ),
                    }
                },
                "required": ["topic"],
            },
        },
        {
            "name": "publish_decision",
            "description": (
                "Record a Riley-approved strategic decision. Writes a dated file "
                "to decisions/ that future dispatches will reference."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "implications_by_dept": {
                        "type": "object",
                        "description": (
                            "Map of department name → one-line implication for that "
                            "department. Include only affected departments."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["title", "summary"],
            },
        },
        {
            "name": "deliver_report",
            "description": (
                "Route a report or alert to Riley via the notification layer. The "
                "layer triages kind+urgency to channels (vault-only, email, "
                "Telegram, or combinations). Use after work is complete and the "
                "artifact is in the vault."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "digest",
                            "approval_request",
                            "decision_required",
                            "report",
                            "error",
                            "info",
                        ],
                    },
                    "urgency": {
                        "type": "string",
                        "enum": ["low", "normal", "high"],
                    },
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "vault_path": {
                        "type": "string",
                        "description": (
                            "Absolute or company-relative path to the authoritative "
                            "artifact in the vault."
                        ),
                    },
                },
                "required": ["kind", "urgency", "title", "body"],
            },
        },
        {
            "name": "call_meeting",
            "description": (
                "Convene a structured discussion between any combination of department "
                "managers and/or board members. Each participant sees the growing "
                "transcript and responds in turn. The last participant in the list "
                "gives the closing synthesis. Returns the full meeting transcript. "
                "Use for cross-department conflict resolution, multi-voice deliberation, "
                "or when you want to surface a structured debate before escalating to Riley."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "The meeting topic or question. One clear sentence plus "
                            "2-3 lines of context."
                        ),
                    },
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Ordered list of participant specs. Use department name "
                            "(e.g. 'marketing', 'finance') for managers, or "
                            "'board:RoleName' for board members "
                            "(e.g. 'board:Strategist', 'board:Contrarian'). "
                            "The LAST entry closes with synthesis."
                        ),
                    },
                },
                "required": ["topic", "participants"],
            },
        },
        {
            "name": "trigger_department_onboarding",
            "description": (
                "Run first-time onboarding for a department that was newly added. "
                "This creates specialist.md files and a setup checklist for Riley. "
                "Only call this for a department that hasn't been onboarded yet "
                "(i.e., has no onboarding.json). Normal startup onboarding runs "
                "automatically — this tool is for mid-session department additions."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "department": {
                        "type": "string",
                        "enum": dept_names,
                        "description": "Which department to onboard.",
                    }
                },
                "required": ["department"],
            },
        },
        {
            "name": "end_session",
            "description": (
                "Finalize the current session: write digest.md, archive it, "
                "return control to Riley."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "The digest content in the 3-section format.",
                    }
                },
                "required": ["summary"],
            },
        },
    ]


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
@dataclass
class SessionState:
    session_id: str
    company: CompanyConfig
    session_dir: Path
    messages: list[dict[str, Any]] = field(default_factory=list)
    ended: bool = False
    departments: list[DepartmentConfig] = field(default_factory=list)

    def append_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def append_assistant(self, blocks: list[Any]) -> None:
        # Preserve the assistant's content blocks (tool_use + text) so the
        # next turn sees the full history.
        content: list[dict[str, Any]] = []
        for b in blocks:
            if getattr(b, "type", None) == "text":
                content.append({"type": "text", "text": b.text})
            elif getattr(b, "type", None) == "tool_use":
                content.append(
                    {
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": b.input,
                    }
                )
        self.messages.append({"role": "assistant", "content": content})

    def append_tool_results(self, tool_results: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "user", "content": tool_results})


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------
class Orchestrator:
    def __init__(self, company: CompanyConfig, session_dir: Path):
        self.company = company
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.departments = load_departments(company)
        self.state = SessionState(
            session_id=session_dir.name,
            company=company,
            session_dir=session_dir,
            departments=self.departments,
        )
        self._dept_names = [d.name for d in self.departments]
        self._system_prompt = _orchestrator_system_prompt(company, self.departments)
        self._tools = _build_tools(self._dept_names)

    # -- Tool handlers ----------------------------------------------------
    def _handle_dispatch_to_manager(self, manager: str, brief: str) -> str:
        if manager not in self._dept_names:
            return f"ERROR: unknown manager '{manager}'. Available: {self._dept_names}"
        try:
            result = dispatch_manager(manager, brief, self.company, departments=self.departments)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR dispatching {manager}: {exc}"

        # Persist trace
        trace_dir = self.session_dir / manager
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "manager-trace.md").write_text(
            f"# Manager Trace — {manager}\n\n"
            f"## Inbound Brief\n{brief}\n\n"
            f"## Specialists Called\n"
            + ("\n".join(f"- {s}" for s in result.specialists_called) if result.specialists_called else "(none)")
            + f"\n\n## Final Response\n{result.final_text}\n",
            encoding="utf-8",
        )
        return result.final_text or "(manager returned empty response)"

    def _handle_convene_board(self, topic: str) -> str:
        try:
            debate = convene_board(
                topic,
                self.company,
                session_dir=self.session_dir,
                departments=self.departments,
                observer_summary=True,  # Always summarize — Riley sees a digest, not the raw transcript
                write_to_company=True,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR convening board: {exc}"
        # Return the observer summary as the primary tool result so the
        # orchestrator (you) can speak from the digest, not the raw debate.
        # The full transcript is on disk at debate.summary_path.
        summary_pointer = ""
        if debate.summary_path:
            try:
                rel = debate.summary_path.relative_to(self.company.company_dir)
                summary_pointer = f"\n\n[Full transcript + summary saved → {rel}]"
            except ValueError:
                summary_pointer = f"\n\n[Full transcript saved → {debate.summary_path}]"
        return (
            "=== ORCHESTRATOR OBSERVER SUMMARY ===\n"
            + (debate.observer_summary or "(no summary generated)")
            + summary_pointer
        )

    def _handle_publish_decision(
        self, title: str, summary: str, implications_by_dept: dict[str, str] | None = None
    ) -> str:
        decisions_dir = self.company.company_dir / "decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        # Slug: alnum + dash, collapse runs, strip leading/trailing dashes
        raw_slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in title.lower())[:60]
        slug = "-".join(part for part in raw_slug.split("-") if part) or "decision"
        date = datetime.now().strftime("%Y-%m-%d")
        path = decisions_dir / f"{date}-{slug}.md"
        # Append counter if path already taken (avoid silent overwrite)
        if path.exists():
            n = 2
            while (decisions_dir / f"{date}-{slug}-{n}.md").exists():
                n += 1
            path = decisions_dir / f"{date}-{slug}-{n}.md"

        lines = [
            f"---",
            f"title: {title}",
            f"date: {date}",
            f"session: {self.state.session_id}",
            f"---",
            "",
            f"# Decision: {title}",
            "",
            "## Summary",
            summary.strip(),
        ]
        if implications_by_dept:
            lines.extend(["", "## Implications by Department"])
            for dept, impl in implications_by_dept.items():
                lines.append(f"- **{dept}**: {impl}")
        path.write_text("\n".join(lines), encoding="utf-8")
        return f"Decision published → {path.relative_to(self.company.company_dir)}"

    def _handle_deliver_report(
        self,
        kind: str,
        urgency: str,
        title: str,
        body: str,
        vault_path: str | None = None,
    ) -> str:
        vp: Path | None = None
        if vault_path:
            p = Path(vault_path)
            if not p.is_absolute():
                p = self.company.company_dir / vault_path
            vp = p
        try:
            result = notify(
                kind=kind,  # type: ignore[arg-type]
                urgency=urgency,  # type: ignore[arg-type]
                title=title,
                body=body,
                vault_path=vp,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR delivering report: {exc}"
        return json.dumps(
            {
                "telegram_ok": result.telegram_ok,
                "email_business_ok": result.email_business_ok,
                "email_personal_ok": result.email_personal_ok,
                "suppressed_for_quiet": result.suppressed_for_quiet,
                "suppressed_for_dup": result.suppressed_for_dup,
                "notes": result.notes,
            }
        )

    def _handle_call_meeting(self, topic: str, participants: list[str]) -> str:
        try:
            transcript = run_cross_agent_meeting(
                company=self.company,
                departments=self.departments,
                participants=participants,
                topic=topic,
                session_dir=self.session_dir,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR running meeting: {exc}"
        # Surface unresolved-participants failure as ERROR (so model can retry)
        if (
            len(transcript.statements) == 1
            and transcript.statements[0].participant == "System"
        ):
            valid_depts = [d.name for d in self.departments]
            return (
                f"ERROR: no valid participants resolved from {participants}. "
                f"Valid dept names: {valid_depts}. Board members: "
                f"'board:Strategist', 'board:Storyteller', 'board:Analyst', "
                f"'board:Builder', 'board:Contrarian', 'board:KnowledgeElicitor'."
            )
        md = transcript.as_markdown()
        # Also persist
        meeting_file = self.session_dir / "cross-meeting.md"
        meeting_file.write_text(md, encoding="utf-8")
        return md

    def _handle_trigger_department_onboarding(self, department: str) -> str:
        matched = next((d for d in self.departments if d.name == department), None)
        if matched is None:
            return f"ERROR: department '{department}' not found."
        try:
            result = run_department_onboarding(self.company, matched)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR during onboarding of '{department}': {exc}"
        if result.skipped:
            return f"Department '{department}' was already onboarded — no action taken."
        specs = ", ".join(result.specialists_created) if result.specialists_created else "(none)"
        checklist = str(result.setup_checklist_path) if result.setup_checklist_path else "not generated"
        return (
            f"Department '{department}' onboarded.\n"
            f"Specialists created: {specs}\n"
            f"Setup checklist: {checklist}\n\n"
            f"Summary:\n{result.summary}"
        )

    def _handle_end_session(self, summary: str) -> str:
        digest_path = self.company.company_dir / "digest.md"
        archive_path = self.company.company_dir / "digest_archive.md"

        header = (
            f"# Session Digest — {self.state.session_id}\n"
            f"_Generated {datetime.now().isoformat(timespec='seconds')}_\n\n"
        )
        digest_content = header + summary.strip() + "\n"
        digest_path.write_text(digest_content, encoding="utf-8")

        # Append to archive
        if archive_path.exists():
            archive_path.write_text(
                archive_path.read_text(encoding="utf-8") + "\n\n---\n\n" + digest_content,
                encoding="utf-8",
            )
        else:
            archive_path.write_text(digest_content, encoding="utf-8")

        self.state.ended = True
        return f"Session ended. Digest → digest.md (archived in digest_archive.md)."

    # -- Tool registry ----------------------------------------------------
    # Chunk 1a.7 replaced the flat if/elif dispatch with a name → handler
    # mapping. Each handler accepts (self, args: dict[str, Any]) and
    # returns a result string. Extend via `register_tool(name, fn)` below.
    _TOOL_REGISTRY: dict[str, "Callable[[Orchestrator, dict[str, Any]], str]"] = {
        "dispatch_to_manager": lambda self, args: self._handle_dispatch_to_manager(
            args["manager"], args["brief"]
        ),
        "convene_board": lambda self, args: self._handle_convene_board(args["topic"]),
        "publish_decision": lambda self, args: self._handle_publish_decision(
            args["title"], args["summary"], args.get("implications_by_dept")
        ),
        "deliver_report": lambda self, args: self._handle_deliver_report(
            args["kind"],
            args["urgency"],
            args["title"],
            args["body"],
            args.get("vault_path"),
        ),
        "call_meeting": lambda self, args: self._handle_call_meeting(
            args["topic"], args["participants"]
        ),
        "trigger_department_onboarding": lambda self, args: (
            self._handle_trigger_department_onboarding(args["department"])
        ),
        "end_session": lambda self, args: self._handle_end_session(args["summary"]),
    }

    @classmethod
    def register_tool(
        cls,
        name: str,
        fn: "Callable[[Orchestrator, dict[str, Any]], str]",
    ) -> None:
        """Register a tool handler on the class-level registry.

        Extension point for future tool additions without modifying the
        orchestrator body. Later chunks (1a.8 hooks, Phase 2 skill-harness)
        extend via this entry point rather than editing `_TOOL_REGISTRY`
        inline.
        """
        cls._TOOL_REGISTRY[name] = fn

    def _dispatch_tool(self, name: str, args: dict[str, Any]) -> str:
        handler = self._TOOL_REGISTRY.get(name)
        if handler is None:
            return f"ERROR: unknown tool '{name}'"
        return handler(self, args)

    # -- Chat loop --------------------------------------------------------
    def chat(self, user_input: str) -> str:
        """Process one user turn through the tool-use loop. Returns the final
        assistant text for this turn."""
        self.state.append_user(user_input)

        last_text = ""
        for _ in range(ORCHESTRATOR_MAX_TURNS):
            response = single_turn(
                messages=self.state.messages,
                model=config.get_model("orchestrator"),
                cost_tag="orchestrator.chat",
                system=self._system_prompt,
                tools=self._tools,
                max_tokens=ORCHESTRATOR_MAX_TOKENS,
            )
            if response.error:
                last_text = f"(orchestrator LLM error: {response.error})"
                break

            # Persist assistant content
            self.state.append_assistant(response.content)

            # Collect tool_use blocks and text
            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            texts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
            if texts:
                last_text = "\n".join(t for t in texts if t.strip())

            if response.stop_reason == "end_turn" or not tool_uses:
                break

            # Run each tool, collect results, feed back
            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                result_text = self._dispatch_tool(tu.name, tu.input or {})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": result_text,
                    }
                )
            self.state.append_tool_results(tool_results)

            if self.state.ended:
                # end_session was called; return control
                break

        # Persist session json for audit
        (self.session_dir / "session.json").write_text(
            json.dumps(self.state.messages, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        return last_text or "(no response)"
