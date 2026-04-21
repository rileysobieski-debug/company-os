"""
core/onboarding/shared.py — types and helpers used by every onboarding flow.
===========================================================================
Split out of the monolithic core/onboarding.py at Phase 2.3. Contains
the idempotency markers, the result dataclass, and the maximum-turn
constant used by the tool-loop flows (department + board).

The three run_* functions live in their own submodules:
  * department.py — run_department_onboarding
  * board.py      — run_board_onboarding
  * orchestrator.py — run_orchestrator_onboarding

And the top-level wiring lives in runner.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ONBOARDING_MODEL removed in chunk 1a.5 — callers now read from
# core.config.get_model("onboarding"). The role mapping lives in
# core/config.py:_MODEL_BY_ROLE so role-tuning happens in one place.
ONBOARDING_MAX_TURNS = 30


@dataclass
class OnboardingResult:
    entity_type: str            # "department" | "board" | "orchestrator"
    entity_name: str            # dept.name, "board", "orchestrator"
    specialists_created: list[str] = field(default_factory=list)
    setup_checklist_path: Path | None = None
    transcript_path: Path | None = None
    summary: str = ""
    skipped: bool = False       # True if onboarding was already complete


def needs_onboarding(directory: Path) -> bool:
    """Return True if `directory/onboarding.json` does not exist or is not
    marked completed. Safe to call on any path — returns True if the path
    doesn't exist."""
    marker = directory / "onboarding.json"
    if not marker.exists():
        return True
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return not data.get("completed", False)
    except (json.JSONDecodeError, OSError):
        return True


def needs_orchestrator_onboarding(company) -> bool:
    """Orchestrator marker is a file (not a dir), so it needs its own check.

    `company` is a `core.company.CompanyConfig`; deliberately untyped here
    to avoid an import cycle with core.company → core.onboarding for
    onboarding-aware callers.
    """
    marker = company.company_dir / "orchestrator-onboarding.json"
    if not marker.exists():
        return True
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return not data.get("completed", False)
    except (json.JSONDecodeError, OSError):
        return True


def write_onboarding_marker(directory: Path, data: dict[str, Any]) -> None:
    """Write the standard `onboarding.json` completion marker.

    Exposed without a leading underscore so sibling submodules can call
    it directly; still treated as internal to the onboarding package.
    """
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "completed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    (directory / "onboarding.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
