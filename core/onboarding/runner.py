"""
core/onboarding/runner.py — top-level check-and-run coordinator.
================================================================
Called at session start to walk the three onboarding flows in order,
skipping any that have already been marked complete. Idempotent — safe
to invoke every session.

Also owns the "pre-built department" detection path: a department that
was hand-built (has specialists) but never ran through onboarding gets
auto-marked complete instead of being re-onboarded.

Split out of the monolithic core/onboarding.py at Phase 2.3.
"""
from __future__ import annotations

from core.company import CompanyConfig
from core.managers.loader import DepartmentConfig
from core.onboarding.board import run_board_onboarding
from core.onboarding.department import run_department_onboarding
from core.onboarding.orchestrator import run_orchestrator_onboarding
from core.onboarding.shared import (
    OnboardingResult,
    needs_onboarding,
    needs_orchestrator_onboarding,
    write_onboarding_marker,
)


def _has_existing_specialists(dept: DepartmentConfig) -> bool:
    """True if the dept folder already has at least one specialist.md file.
    Used to detect manually-built departments that don't need AI onboarding."""
    return len(dept.specialists) > 0


def _auto_mark_dept_complete(dept: DepartmentConfig) -> None:
    """Write a completed onboarding.json for a department that was manually
    built (has specialists but no onboarding.json)."""
    write_onboarding_marker(dept.dept_dir, {
        "entity_type": "department",
        "entity_name": dept.name,
        "note": "auto-marked — department was pre-built before onboarding system was added",
        "specialists_found": [s.name for s in dept.specialists],
    })


def check_and_run_all_onboarding(
    company: CompanyConfig,
    departments: list[DepartmentConfig],
    interactive: bool = True,
) -> list[OnboardingResult]:
    """Check all entities and run onboarding for any that haven't been
    initialized yet.

    Call this at session start (before the chat loop). It is idempotent — safe
    to call every session; already-complete entities are skipped silently.

    Parameters
    ----------
    company : CompanyConfig
    departments : list[DepartmentConfig]
    interactive : bool
        If True, orchestrator onboarding (which requires terminal Q&A) is
        included. Set False for non-interactive / test runs.
    """
    results: list[OnboardingResult] = []

    if interactive and needs_orchestrator_onboarding(company):
        results.append(run_orchestrator_onboarding(company))

    board_dir = company.company_dir / "board"
    if needs_onboarding(board_dir):
        results.append(run_board_onboarding(company))

    for dept in departments:
        if not needs_onboarding(dept.dept_dir):
            continue
        if _has_existing_specialists(dept):
            _auto_mark_dept_complete(dept)
            print(f"[onboarding] {dept.display_name}: pre-built dept auto-marked complete "
                  f"({len(dept.specialists)} specialists found).")
        else:
            results.append(run_department_onboarding(company, dept))

    return results
