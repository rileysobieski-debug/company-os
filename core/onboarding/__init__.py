"""
core/onboarding — first-time initialization for all agent types.
=================================================================
Runs ONCE per entity. Detects whether onboarding is needed by checking
for `onboarding.json` in the relevant directory. Idempotent — safe to
call every session start; will skip already-completed entities.

Split into submodules at Phase 2.3 per the dev-team recommendation:

  * shared.py        — OnboardingResult, needs_onboarding, markers
  * department.py    — run_department_onboarding + tool defs
  * board.py         — run_board_onboarding (all six voices)
  * orchestrator.py  — run_orchestrator_onboarding (interactive Q&A)
  * runner.py        — check_and_run_all_onboarding (top-level loop)

This __init__.py re-exports the public surface so existing callers
(main.py, orchestrator.py, test_flow.py, tests/*) survive the split
without edits.

=== Entry points ===
  needs_onboarding(directory) -> bool
  needs_orchestrator_onboarding(company) -> bool
  run_department_onboarding(company, dept) -> OnboardingResult
  run_board_onboarding(company) -> OnboardingResult
  run_orchestrator_onboarding(company, input_fn=None) -> OnboardingResult
  check_and_run_all_onboarding(company, depts, interactive=True) -> list[OnboardingResult]
"""
from core.onboarding.board import run_board_onboarding
from core.onboarding.business_interview import (
    INTERVIEW_QUESTIONS,
    InterviewPhase,
    InterviewWriteResult,
    build_config_payload,
    validate_answers,
    write_interview_files,
)
from core.onboarding.department import run_department_onboarding
from core.onboarding.dept_selection import (
    VERTICAL_DEPARTMENTS,
    DepartmentChoice,
    apply_founder_override,
    dormant_departments,
    suggest_top_n_departments,
)
from core.onboarding.first_deliverable import (
    CONVICTIONS_SUMMARY,
    POSITIONING_STATEMENT,
    PRIORITY_RISK_MATRIX,
    DeliverableProposal,
    propose_first_deliverable,
)
from core.onboarding.orchestrator import run_orchestrator_onboarding
from core.onboarding.pre_warm import (
    JobStatus,
    ManagerOnboardingJob,
    PrewarmLedger,
    PrewarmMode,
    prewarm_jobs,
    schedule_manager_onboardings,
)
from core.onboarding.premortem import (
    PremortemContext,
    inject_premortem_context,
    is_premortem_injected,
    load_premortem_from_profile,
    strip_premortem_injection,
)
from core.onboarding.runner import check_and_run_all_onboarding
from core.onboarding.shared import (
    ONBOARDING_MAX_TURNS,
    OnboardingResult,
    needs_onboarding,
    needs_orchestrator_onboarding,
)

__all__ = [
    # shared / legacy
    "ONBOARDING_MAX_TURNS",
    "OnboardingResult",
    "needs_onboarding",
    "needs_orchestrator_onboarding",
    "run_board_onboarding",
    "run_department_onboarding",
    "run_orchestrator_onboarding",
    "check_and_run_all_onboarding",
    # Phase 8.1 — business interview
    "INTERVIEW_QUESTIONS",
    "InterviewPhase",
    "InterviewWriteResult",
    "build_config_payload",
    "validate_answers",
    "write_interview_files",
    # Phase 8.2 — dept selection
    "VERTICAL_DEPARTMENTS",
    "DepartmentChoice",
    "apply_founder_override",
    "dormant_departments",
    "suggest_top_n_departments",
    # Phase 8.3 — pre-mortem injection
    "PremortemContext",
    "inject_premortem_context",
    "is_premortem_injected",
    "load_premortem_from_profile",
    "strip_premortem_injection",
    # Phase 8.4 — first deliverable
    "CONVICTIONS_SUMMARY",
    "POSITIONING_STATEMENT",
    "PRIORITY_RISK_MATRIX",
    "DeliverableProposal",
    "propose_first_deliverable",
    # Phase 8.5 — pre-warm scheduling
    "JobStatus",
    "ManagerOnboardingJob",
    "PrewarmLedger",
    "PrewarmMode",
    "prewarm_jobs",
    "schedule_manager_onboardings",
]
