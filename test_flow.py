"""
Company OS — Test Flow
======================
A simple, runnable script that exercises the new features in sequence:

  Phase 1: Department onboarding  (creates specialists + setup checklist)
  Phase 2: Board onboarding       (calibrates all 6 board member profiles)
  Phase 3: 6-voice board debate   (tests new Contrarian + KnowledgeElicitor)
  Phase 4: Department meeting     (manager + specialists discuss a topic)
  Phase 5: Cross-agent meeting    (two managers + one board member)

Usage:
  cd "C:/Users/riley_edejtwi/Obsidian Vault"
  python company-os/test_flow.py --company "Old Press Wine Company LLC"

The script targets an EXISTING company (one created by the wizard). It will:
  - Run onboarding for any entities that haven't been initialized yet.
  - Skip entities that are already onboarded (idempotent).
  - Write all output to the company folder and print summaries to stdout.

Flags:
  --company       Company name (resolved to vault/{name}/)
  --company-dir   Full path to the company folder
  --phase         Run only a specific phase (1-5). Default: all.
  --dept          Department name for Phases 1 + 4 (default: first configured dept)
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# UTF-8 fix
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from core.env import get_vault_dir, load_env
load_env()  # load ~/.company-os/.env before any anthropic client is created

from core.board import convene_board
from core.company import load_company
from core.managers.loader import load_departments
from core.meeting import run_cross_agent_meeting, run_department_meeting
from core.onboarding import (
    check_and_run_all_onboarding,
    run_board_onboarding,
    run_department_onboarding,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sep(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


def _resolve_dir(company: str | None, company_dir: str | None) -> Path:
    if company_dir:
        return Path(company_dir).expanduser().resolve()
    if company:
        return (get_vault_dir() / company).resolve()
    raise ValueError("--company or --company-dir required.")


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------
def phase1_department_onboarding(company, departments, dept_name: str | None) -> None:
    _sep("Phase 1: Department Onboarding")

    # Pick department
    if dept_name:
        dept = next((d for d in departments if d.name == dept_name), None)
        if dept is None:
            print(f"ERROR: department '{dept_name}' not found in {[d.name for d in departments]}")
            return
    elif departments:
        dept = departments[0]
    else:
        print("No departments found — skipping Phase 1.")
        return

    print(f"Target department: {dept.display_name} ({dept.name})")
    result = run_department_onboarding(company, dept)

    if result.skipped:
        print(f"Skipped — {dept.display_name} was already onboarded.")
        print("  Delete {dept.name}/onboarding.json to re-run.")
        return

    print(f"\nSpecialists created: {result.specialists_created or '(none)'}")
    if result.setup_checklist_path:
        print(f"Setup checklist: {result.setup_checklist_path.relative_to(company.company_dir)}")
    print(f"\nSummary:\n{result.summary[:600]}")


def phase2_board_onboarding(company) -> None:
    _sep("Phase 2: Board Onboarding (6 members)")
    result = run_board_onboarding(company)

    if result.skipped:
        print("Skipped — board was already onboarded.")
        print("  Delete board/onboarding.json to re-run.")
        return

    print(f"\n{result.summary}")


def phase3_board_debate(company, departments) -> None:
    _sep("Phase 3: 6-Voice Board Debate")

    topic = (
        "Should Old Press Wine Company prioritize DTC direct-to-consumer sales "
        "or wholesale distribution as its first revenue channel?\n\n"
        "Context: We are pre-revenue, solo founder, no outside equity. "
        "TTB compliance is required before any sales. Capital is limited."
    )
    print(f"Topic:\n{topic}\n")
    print("Running board debate (6 voices: Strategist → Storyteller → Analyst → Builder → Contrarian → KnowledgeElicitor)...")
    print("Board members will query department managers for operational context.\n")

    debate = convene_board(topic, company, departments=departments)

    print("\n" + debate.as_markdown())

    # Save to a test output file
    out_path = company.company_dir / "test-outputs" / "board-debate-test.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(debate.as_markdown(), encoding="utf-8")
    print(f"\n[saved → test-outputs/board-debate-test.md]")


def phase4_department_meeting(company, departments, dept_name: str | None) -> None:
    _sep("Phase 4: Department Meeting")

    if dept_name:
        dept = next((d for d in departments if d.name == dept_name), None)
    elif departments:
        dept = departments[0]
    else:
        dept = None

    if dept is None:
        print("No department available — skipping Phase 4.")
        return

    if not dept.specialists:
        print(f"Department '{dept.name}' has no specialists yet.")
        print("Run Phase 1 first to generate specialists via onboarding.")
        return

    topic = (
        f"What should {dept.display_name}'s first 30-day deliverable be, "
        f"given the company is pre-revenue and the founder's top priority is "
        f"producing the first TTB-approved bottle under the Old Press label?"
    )
    print(f"Department: {dept.display_name}")
    print(f"Specialists in meeting: {[s.name for s in dept.specialists]}")
    print(f"Topic:\n{topic}\n")
    print("Running department meeting...")

    session_dir = company.company_dir / "test-outputs"
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript = run_department_meeting(company, dept, topic, session_dir=session_dir)

    print("\n" + transcript.as_markdown())
    print(f"\n[saved → test-outputs/dept-meeting-{dept.name}.md]")


def phase5_cross_agent_meeting(company, departments) -> None:
    _sep("Phase 5: Cross-Agent Meeting")

    if len(departments) < 2:
        print("Need at least 2 departments for a cross-agent meeting.")
        print("Falling back to 1 department + 2 board members.")
        if departments:
            participants = [departments[0].name, "board:Analyst", "board:Contrarian"]
        else:
            print("No departments — skipping Phase 5.")
            return
    else:
        # Two managers + one board member closes
        participants = [departments[0].name, departments[1].name, "board:KnowledgeElicitor"]

    topic = (
        "How should we sequence the first three months of operations: "
        "regulatory/TTB compliance, product development, or brand/DTC infrastructure?\n\n"
        "We cannot do all three in parallel. Solo founder, limited capital. "
        "What is the right order and why?"
    )

    print(f"Participants: {participants}")
    print(f"Topic:\n{topic}\n")
    print("Running cross-agent meeting...")

    session_dir = company.company_dir / "test-outputs"
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript = run_cross_agent_meeting(company, departments, participants, topic, session_dir=session_dir)

    print("\n" + transcript.as_markdown())
    print(f"\n[saved → test-outputs/cross-meeting.md]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Company OS — Test Flow")
    parser.add_argument("--company", help="Company name (resolved to {vault}/{name}/).")
    parser.add_argument("--company-dir", help="Full path to the company folder.")
    parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2, 3, 4, 5],
        help="Run only this phase (default: all phases).",
    )
    parser.add_argument(
        "--dept",
        help="Department name to use for phase 1 + 4 (default: first configured dept).",
    )
    args = parser.parse_args()

    try:
        company_dir = _resolve_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        parser.print_help()
        sys.exit(2)

    try:
        company = load_company(company_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        sys.exit(2)

    departments = load_departments(company)

    print(f"\nCompany: {company.name}")
    print(f"Departments: {[d.name for d in departments]}")
    print(f"Phases to run: {[args.phase] if args.phase else [1, 2, 3, 4, 5]}")

    run_phase = args.phase  # None = all

    if run_phase in (None, 1):
        phase1_department_onboarding(company, departments, args.dept)

    # Reload departments after phase 1 — new specialists may have been created
    departments = load_departments(company)

    if run_phase in (None, 2):
        phase2_board_onboarding(company)

    if run_phase in (None, 3):
        phase3_board_debate(company, departments)

    if run_phase in (None, 4):
        phase4_department_meeting(company, departments, args.dept)

    if run_phase in (None, 5):
        phase5_cross_agent_meeting(company, departments)

    print("\n" + "=" * 60)
    print("  Test flow complete.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
