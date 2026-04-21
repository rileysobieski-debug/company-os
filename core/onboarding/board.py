"""
core/onboarding/board.py — calibrate each of the 6 advisory voices.
===================================================================
Each board member writes a 300-450 word first-person calibration
profile stored at `<company_dir>/board/<role>-profile.md`. These
profiles are injected into every future board convening.

Split out of the monolithic core/onboarding.py at Phase 2.3. Behavior
unchanged.
"""
from __future__ import annotations

from core import config
from core.board import ALL_BOARD_ROLES, _ROLE_PROMPTS
from core.company import CompanyConfig
from core.llm_client import single_turn
from core.onboarding.shared import (
    OnboardingResult,
    needs_onboarding,
    write_onboarding_marker,
)


_BOARD_ONBOARDING_PROMPT = """You are being onboarded to an advisory board for {company_name}.
Your role on this board is: {role}.

{role_prompt}

Your task is to write a calibration profile — a document that will be injected
into your system prompt in every future board session. This profile should:

1. State your core lens in 2-3 sentences, in your own voice.
2. Describe, specifically for {company_name} in the {industry} industry, what kinds
   of questions you are BEST positioned to evaluate. Name the actual strategic
   questions this company is likely to face.
3. State explicitly what you are NOT well-positioned to evaluate (defer to whom?).
4. Name one or two things you see in the company context that you'd want to
   stress-test immediately if convened.

Write 300-450 words. Write in first person. Be specific — not generic board member
boilerplate. Your value on this board is your specific angle, clearly stated.

=== COMPANY CONTEXT ===
{company_context}

{settled_convictions}

{hard_constraints}
"""


def run_board_onboarding(company: CompanyConfig) -> OnboardingResult:
    """Run onboarding for all six board members.

    Each member writes a self-calibration profile stored as:
      {company_dir}/board/{role.lower()}-profile.md

    These profiles are loaded by convene_board() for every future debate.
    """
    board_dir = company.company_dir / "board"

    if not needs_onboarding(board_dir):
        return OnboardingResult(
            entity_type="board",
            entity_name="board",
            skipped=True,
            summary="(already completed)",
        )

    print("\n[onboarding] Initializing board of supervisors (6 members)...")
    board_dir.mkdir(parents=True, exist_ok=True)

    profiles_written: list[str] = []
    for role in ALL_BOARD_ROLES:
        print(f"  [onboarding]   calibrating: {role}")
        prompt = _BOARD_ONBOARDING_PROMPT.format(
            company_name=company.name,
            role=role,
            role_prompt=_ROLE_PROMPTS.get(role, ""),
            industry=company.industry or "this industry",
            company_context=company.context.strip(),
            settled_convictions=company.settled_convictions_block(),
            hard_constraints=company.hard_constraints_block(),
        )
        response = single_turn(
            messages=[{"role": "user", "content": prompt}],
            model=config.get_model("onboarding"),
            cost_tag=f"onboarding.board.{role.lower()}",
            max_tokens=1000,
        )
        if response.error:
            raise RuntimeError(f"board onboarding LLM call failed: {response.error}")
        profile_text = response.text.strip()

        profile_path = board_dir / f"{role.lower()}-profile.md"
        profile_path.write_text(
            f"# Board Profile — {role}\n\n{profile_text}\n", encoding="utf-8"
        )
        profiles_written.append(role)

    write_onboarding_marker(board_dir, {
        "entity_type": "board",
        "roles_calibrated": profiles_written,
    })
    print(f"  [onboarding] Board complete — {len(profiles_written)} profiles written.")

    return OnboardingResult(
        entity_type="board",
        entity_name="board",
        summary=f"Profiles written for: {', '.join(profiles_written)}",
    )
