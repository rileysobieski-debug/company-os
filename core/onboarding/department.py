"""
core/onboarding/department.py — manager's first-time initialization.
====================================================================
The manager receives the company context, the department charter, and
two tools: `create_specialist()` and `configure_tech_stack()`. It
creates specialist files and a setup-checklist.md, then returns a
summary.

Split out of the monolithic core/onboarding.py at Phase 2.3. Behavior
is unchanged — this is a structural move to keep each onboarding flow
under a reasonable line count and let Phase 8 modify one flow without
touching the others.
"""
from __future__ import annotations

import json
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

from core import config
from core.company import CompanyConfig
from core.llm_client import single_turn
from core.managers.base import _company_preamble
from core.managers.loader import DepartmentConfig
from core.onboarding.shared import (
    ONBOARDING_MAX_TURNS,
    OnboardingResult,
    needs_onboarding,
    write_onboarding_marker,
)


def _dept_onboarding_system(company: CompanyConfig, dept: DepartmentConfig) -> str:
    return "\n".join([
        _company_preamble(company),
        "",
        f"=== YOUR ROLE: {dept.display_name} Manager (ONBOARDING) ===",
        "",
        "You are being initialized for the first time. You have no specialists yet.",
        "Your task in this session is to:",
        "  1. Understand the company context and your department's charter.",
        "  2. Create 2-5 specialist roles your department needs via create_specialist().",
        "  3. Identify the tech stack / platforms your department will require and",
        "     document the setup steps Riley must follow via configure_tech_stack().",
        "  4. Give a final summary of how this department will operate.",
        "",
        "=== YOUR DEPARTMENT CHARTER ===",
        dept.prompt_body or "(No charter written yet — infer from department name and company context.)",
    ])


def _create_specialist_tool() -> dict[str, Any]:
    return {
        "name": "create_specialist",
        "description": (
            "Create a new specialist role for your department. Writes a specialist.md "
            "file to disk. Call once per specialist. Create 2-5 specialists based on "
            "the department's actual work and the company's context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Slug identifier used for dispatch (e.g. 'brand-strategist', 'compliance-analyst'). Lowercase, hyphens.",
                },
                "description": {
                    "type": "string",
                    "description": "One sentence the manager reads when routing work. What do you call this specialist for?",
                },
                "attribute": {
                    "type": "string",
                    "description": "One-word core skill shown in the routing table (e.g. POSITIONING, COMPLIANCE, ANALYTICS, COPY).",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Agent-SDK tools this specialist needs. Choose from: Read, Write, Glob, Grep, WebSearch, WebFetch, Agent.",
                },
                "prompt_body": {
                    "type": "string",
                    "description": (
                        "Full specialist role definition (300-600 words). Cover: "
                        "(1) their role and core expertise, "
                        "(2) what work they handle, "
                        "(3) what they explicitly do NOT handle, "
                        "(4) their output format, "
                        "(5) their approach/mindset. "
                        "Make it specific to this company — not generic."
                    ),
                },
            },
            "required": ["name", "description", "attribute", "tools", "prompt_body"],
        },
    }


def _configure_tech_stack_tool() -> dict[str, Any]:
    return {
        "name": "configure_tech_stack",
        "description": (
            "Document the tech stack and setup steps this department needs. "
            "This produces setup-checklist.md — a step-by-step guide Riley will "
            "follow at their convenience to complete department configuration. "
            "Be specific: name actual platforms, not generic categories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "platforms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific platforms/tools needed (e.g. ['Mailchimp', 'Google Analytics 4', 'Canva Pro', 'Klaviyo']).",
                },
                "credentials_needed": {
                    "type": "array",
                    "description": "API keys, logins, or credentials Riley must gather before the department can operate.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "purpose": {"type": "string"},
                            "where_to_get": {"type": "string"},
                        },
                        "required": ["name", "purpose", "where_to_get"],
                    },
                },
                "integrations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Platform-to-platform integrations to wire up (e.g. 'Connect Shopify → Klaviyo for order events').",
                },
                "setup_steps": {
                    "type": "array",
                    "description": "Ordered, actionable setup steps Riley should complete. Be specific enough to follow without context.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "integer"},
                            "action": {"type": "string"},
                            "notes": {"type": "string"},
                            "estimated_time": {"type": "string"},
                        },
                        "required": ["step", "action"],
                    },
                },
                "estimated_total_setup_time": {
                    "type": "string",
                    "description": "Rough total time estimate for all setup steps (e.g. '2-3 hours').",
                },
            },
            "required": ["platforms", "setup_steps"],
        },
    }


def _handle_create_specialist(dept: DepartmentConfig, args: dict[str, Any]) -> tuple[str, str]:
    """Write a specialist.md file. Returns (result_message, spec_name)."""
    name = str(args.get("name", "")).strip()
    if not name:
        return ("ERROR: 'name' is required.", "")

    spec_dir = dept.dept_dir / name
    spec_dir.mkdir(parents=True, exist_ok=True)

    tools = args.get("tools") or ["Read", "Write", "Agent"]
    tools_inline = ", ".join(str(t) for t in tools)

    content = "\n".join([
        "---",
        f"name: {name}",
        f"description: {args.get('description', '').strip()}",
        f"attribute: {args.get('attribute', '').strip()}",
        f"tools: [{tools_inline}]",
        "---",
        "",
        str(args.get("prompt_body", "")).strip(),
        "",
    ])
    (spec_dir / "specialist.md").write_text(content, encoding="utf-8")
    return (f"Specialist '{name}' created at {spec_dir.name}/specialist.md", name)


def _handle_configure_tech_stack(dept: DepartmentConfig, args: dict[str, Any]) -> str:
    """Write setup-checklist.md. Returns result message."""
    platforms = args.get("platforms") or []
    credentials = args.get("credentials_needed") or []
    integrations = args.get("integrations") or []
    setup_steps = args.get("setup_steps") or []
    total_time = args.get("estimated_total_setup_time", "unknown")

    lines = [
        f"# {dept.display_name} — Department Setup Checklist",
        f"_Generated {datetime.now().strftime('%Y-%m-%d')}. Complete these steps at your convenience._",
        f"_Estimated total setup time: {total_time}_",
        "",
        "---",
        "",
        "## Platforms to Acquire / Activate",
        "",
    ]
    if platforms:
        for p in platforms:
            lines.append(f"- [ ] {p}")
    else:
        lines.append("_(none specified)_")

    lines.extend(["", "## Credentials to Gather", ""])
    if credentials:
        for c in credentials:
            where = c.get("where_to_get", "")
            lines.append(f"- [ ] **{c.get('name', '')}** — {c.get('purpose', '')}")
            if where:
                lines.append(f"      _Where to get it: {where}_")
    else:
        lines.append("_(none specified)_")

    lines.extend(["", "## Integrations to Wire Up", ""])
    if integrations:
        for i in integrations:
            lines.append(f"- [ ] {i}")
    else:
        lines.append("_(none specified)_")

    lines.extend(["", "## Step-by-Step Setup", ""])
    if setup_steps:
        for step in sorted(setup_steps, key=lambda s: s.get("step", 0)):
            num = step.get("step", "?")
            action = step.get("action", "")
            notes = step.get("notes", "")
            est = step.get("estimated_time", "")
            time_str = f" _{est}_" if est else ""
            lines.append(f"### Step {num}{time_str}")
            lines.append(f"- [ ] {action}")
            if notes:
                lines.append(f"> {notes}")
            lines.append("")
    else:
        lines.append("_(no steps specified)_")

    content = "\n".join(lines)
    checklist_path = dept.dept_dir / "setup-checklist.md"
    checklist_path.write_text(content, encoding="utf-8")
    return f"Setup checklist written → {dept.name}/setup-checklist.md"


def _format_dept_transcript(messages: list[dict], dept_name: str) -> str:
    lines = [f"# Department Onboarding Transcript — {dept_name}", ""]
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str):
            lines.append(f"**{role.upper()}:** {content[:2000]}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        lines.append(f"**{role.upper()}:** {block.get('text', '')[:2000]}")
                    elif block.get("type") == "tool_use":
                        lines.append(f"**TOOL CALL ({block.get('name', '')}):** {json.dumps(block.get('input', {}))[:500]}")
                    elif block.get("type") == "tool_result":
                        lines.append(f"**TOOL RESULT:** {str(block.get('content', ''))[:500]}")
        lines.append("")
    return "\n".join(lines)


def run_department_onboarding(
    company: CompanyConfig,
    dept: DepartmentConfig,
) -> OnboardingResult:
    """Run the manager's first-time onboarding.

    The manager receives the company context, department charter, and two
    tools: create_specialist() and configure_tech_stack(). It creates
    specialist files and a setup checklist, then returns a summary.
    """
    if not needs_onboarding(dept.dept_dir):
        return OnboardingResult(
            entity_type="department",
            entity_name=dept.name,
            skipped=True,
            summary="(already completed)",
        )

    print(f"\n[onboarding] Initializing department: {dept.display_name}")

    system = _dept_onboarding_system(company, dept)
    tools = [_create_specialist_tool(), _configure_tech_stack_tool()]

    task_message = textwrap.dedent(f"""
        You are being initialized as the {dept.display_name} Manager for {company.name}.

        Your onboarding tasks (in order):

        1. Call configure_tech_stack() first to document the platforms, credentials,
           integrations, and setup steps this department requires. Think carefully
           about what's needed for THIS company in THIS industry.

        2. Call create_specialist() 2-5 times to define the specialist roles your
           department needs. Make each specialist prompt specific to {company.name},
           not generic. Each specialist should have a distinct, non-overlapping scope.

        3. After all tools have been called, write a 1-2 paragraph summary of how
           this department will operate, what its primary outputs are, and what the
           first 90 days of work will focus on.

        Be specific to the actual company context — do not write generic prompts.
    """).strip()

    messages: list[dict[str, Any]] = [{"role": "user", "content": task_message}]
    specialists_created: list[str] = []
    checklist_path: Path | None = None
    final_text = ""

    for _ in range(ONBOARDING_MAX_TURNS):
        response = single_turn(
            messages=messages,
            model=config.get_model("onboarding"),
            cost_tag=f"onboarding.department.{dept.name}",
            system=system,
            tools=tools,
            max_tokens=4096,
        )
        if response.error:
            raise RuntimeError(f"onboarding LLM call failed: {response.error}")

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

        messages.append({"role": "assistant", "content": content_blocks})

        if texts:
            final_text = "\n".join(t for t in texts if t.strip())

        if response.stop_reason == "end_turn" or not tool_uses:
            break

        tool_results: list[dict[str, Any]] = []
        for tu in tool_uses:
            inp = tu.input or {}
            if tu.name == "create_specialist":
                result, spec_name = _handle_create_specialist(dept, inp)
                if spec_name:
                    specialists_created.append(spec_name)
                    print(f"  [onboarding]   + specialist: {spec_name}")
            elif tu.name == "configure_tech_stack":
                result = _handle_configure_tech_stack(dept, inp)
                checklist_path = dept.dept_dir / "setup-checklist.md"
                print(f"  [onboarding]   + tech stack configured")
            else:
                result = f"Unknown tool: {tu.name}"

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })
        messages.append({"role": "user", "content": tool_results})

    transcript_path = dept.dept_dir / "onboarding-transcript.md"
    transcript_path.write_text(
        _format_dept_transcript(messages, dept.display_name), encoding="utf-8"
    )

    write_onboarding_marker(dept.dept_dir, {
        "entity_type": "department",
        "entity_name": dept.name,
        "specialists_created": specialists_created,
    })

    print(f"  [onboarding] {dept.display_name} complete — {len(specialists_created)} specialist(s) created.")
    if checklist_path:
        print(f"  [onboarding] Setup checklist → {dept.name}/setup-checklist.md")

    return OnboardingResult(
        entity_type="department",
        entity_name=dept.name,
        specialists_created=specialists_created,
        setup_checklist_path=checklist_path,
        transcript_path=transcript_path,
        summary=final_text,
    )
