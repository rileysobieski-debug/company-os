"""
core/onboarding/orchestrator.py — interactive Q&A with Riley.
=============================================================
Prints questions to stdout, reads answers via the injected `input_fn`
(default `input()`), then a single LLM call synthesizes the answers
into a coordination charter stored at `<company_dir>/orchestrator-charter.md`.

Split out of the monolithic core/onboarding.py at Phase 2.3. Behavior
unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

from core import config
from core.company import CompanyConfig
from core.llm_client import single_turn
from core.onboarding.shared import (
    OnboardingResult,
    needs_orchestrator_onboarding,
)


_ORCH_QUESTIONS = [
    (
        "How would you describe your decision-making style when two departments conflict?\n"
        "(e.g. 'I want options presented, not a resolution' / 'Resolve at the lowest level, "
        "only escalate genuine deadlocks')"
    ),
    (
        "What categories of decision must ALWAYS come to you for approval before action?\n"
        "(e.g. 'Any spend over $X', 'Any customer-facing copy', 'Any vendor commitment')"
    ),
    (
        "How often do you want board debates vs. direct manager dispatch?\n"
        "(e.g. 'Board only for true strategic forks' / 'Board on any major spend decision')"
    ),
    (
        "What is the ONE thing you most want the Orchestrator to protect you from?\n"
        "(e.g. 'Scope creep', 'Premature decisions', 'Being out of the loop on key vendor moves')"
    ),
    (
        "What is your preferred session rhythm? How long should a typical session be?\n"
        "(e.g. 'Weekly, 30 min' / 'Whenever I need it, no rhythm' / 'Daily standup style')"
    ),
]

_ORCH_CHARTER_PROMPT = """You are the Orchestrator — Chairman of the Board — for {company_name}.
You are writing your own coordination charter based on the founder's answers to your onboarding interview.

=== FOUNDER'S ANSWERS ===
{qa_block}

=== COMPANY CONTEXT ===
{company_context}

Write a coordination charter (400-600 words) that:
1. Restates your operating mandate in your own voice — what you are here to do.
2. Documents Riley's stated decision protocol — what goes to Riley, what you
   handle, what the board handles.
3. Captures Riley's escalation thresholds precisely (spend limits, content
   gates, vendor rules — whatever was stated).
4. States the session rhythm Riley wants.
5. Names the one thing you are explicitly here to protect Riley from.

Write it as a standing operational document, first person (you are the Orchestrator).
It will be injected into your system prompt in every session.
"""


def run_orchestrator_onboarding(
    company: CompanyConfig,
    input_fn: Callable[[str], str] | None = None,
) -> OnboardingResult:
    """Interactive Q&A with Riley, then AI synthesis into a coordination charter.

    Prints questions to stdout. `input_fn` reads answers — defaults to the
    builtin `input()` for live terminals. Tests and non-interactive runs
    can inject a callable that returns scripted answers instead.
    """
    if input_fn is None:
        input_fn = input
    if not needs_orchestrator_onboarding(company):
        return OnboardingResult(
            entity_type="orchestrator",
            entity_name="orchestrator",
            skipped=True,
            summary="(already completed)",
        )

    print("\n" + "=" * 60)
    print(f"  Orchestrator Onboarding — {company.name}")
    print("=" * 60)
    print("\nI'll ask you a few questions to calibrate how the Orchestrator")
    print("should coordinate work and when to escalate to you.\n")

    qa_pairs: list[tuple[str, str]] = []
    for i, question in enumerate(_ORCH_QUESTIONS, start=1):
        print(f"[{i}/{len(_ORCH_QUESTIONS)}] {question}")
        try:
            answer = input_fn("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = "(skipped)"
        qa_pairs.append((question, answer))
        print()

    qa_block = "\n\n".join(
        f"Q{i}: {q}\nA: {a}" for i, (q, a) in enumerate(qa_pairs, start=1)
    )

    print("[onboarding] Synthesizing coordination charter...")
    prompt = _ORCH_CHARTER_PROMPT.format(
        company_name=company.name,
        qa_block=qa_block,
        company_context=company.context.strip(),
    )
    response = single_turn(
        messages=[{"role": "user", "content": prompt}],
        model=config.get_model("onboarding"),
        cost_tag="onboarding.orchestrator.charter",
        max_tokens=1500,
    )
    if response.error:
        raise RuntimeError(f"orchestrator charter LLM call failed: {response.error}")
    charter = response.text.strip()

    charter_path = company.company_dir / "orchestrator-charter.md"
    charter_path.write_text(
        f"# Orchestrator Coordination Charter — {company.name}\n\n{charter}\n",
        encoding="utf-8",
    )

    marker = company.company_dir / "orchestrator-onboarding.json"
    marker.write_text(
        json.dumps({
            "completed": True,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "entity_type": "orchestrator",
        }, indent=2),
        encoding="utf-8",
    )

    print(f"  [onboarding] Orchestrator charter written → orchestrator-charter.md\n")

    return OnboardingResult(
        entity_type="orchestrator",
        entity_name="orchestrator",
        summary=charter,
    )
