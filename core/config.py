"""
core/config.py — Canonical runtime configuration accessors
==========================================================
Single source of truth for runtime knobs that were previously scattered as
module-level constants across loader.py, env.py, webapp/services.py, etc.

Every accessor is a plain function (not a dataclass property) so that:
  - the import is cheap — no env-var reads happen at import time,
  - callers have a stable signature to mock in tests,
  - future migration to a config file (TOML / JSON) is a swap of the
    function body, not a refactor of every call site.

Scope after chunk 1a.1:
  - get_model(role)           — maps role name → Claude model ID
  - get_cost_envelope()       — per-dispatch cost guardrails
  - get_vault_dir()           — lazy accessor, relocated from core/env.py
  - get_output_subdirs()      — canonical output-folder names
  - get_permission_mode()     — Claude Code SDK permission mode

Downstream chunks (1a.2+) migrate callers off local literals and onto
these accessors. See roadmap chunks 1a.5–1a.7 for the call-site list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_VAULT_DIR_ENV = "COMPANY_OS_VAULT_DIR"
# Phase 9.5 migration rollout: comma-separated list of departments whose
# managers use the skill-agent path (Chunk 9.4b) instead of the legacy
# `build_workers()` shim. Special value "*" migrates ALL departments.
# Unset → no depts migrated (legacy path, same behavior as pre-Phase 9).
_SKILL_AGENTS_DEPTS_ENV = "COMPANY_OS_SKILL_AGENTS_DEPTS"

# Default model used by any role that does not specify one. Moved from
# core/managers/loader.py:201 — keep the value identical so nothing shifts
# during 1a.1. Per-role overrides will be layered in during 1a.5–1a.7.
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Role → model map. "default" is the fallback for unknown roles.
# Roles added by chunk 1a.5+ as each caller migrates:
#   onboarding — added in 1a.5 (preserves prior ONBOARDING_MODEL constant)
#   board / meeting / observer — added in 1a.6
_MODEL_BY_ROLE: dict[str, str] = {
    "default": _DEFAULT_MODEL,
    "onboarding": "claude-sonnet-4-6",
    "board": "claude-sonnet-4-6",
    "meeting": "claude-sonnet-4-6",
    "observer": "claude-opus-4-6",
    "orchestrator": "claude-opus-4-6",
}

# Canonical output-folder names under each department's output/ root.
# Moved from core/managers/loader.py:305–308. String values match the
# literal folder names on disk — do NOT change without a migration.
_OUTPUT_SUBDIRS: dict[str, str] = {
    "pending_approval": "pending-approval",
    "approved": "approved",
    "rejected": "rejected",
}

# Claude Code SDK permission mode for managed dispatches. Mirrors
# core/managers/base.py:467 which passes this same string literal today.
_PERMISSION_MODE = "bypassPermissions"


# ---------------------------------------------------------------------------
# Cost envelope
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CostEnvelope:
    """Soft guardrails applied per-dispatch by downstream cost-tracking code.

    All values in USD. `per_call_max` is the hard kill-switch; `per_session_max`
    is checked by the orchestrator before starting a new meeting. `warn_ratio`
    is the fraction of per_call_max at which notify.py emits a yellow warning.
    """

    per_call_max: float = 1.00
    per_session_max: float = 5.00
    warn_ratio: float = 0.75


_DEFAULT_COST_ENVELOPE = CostEnvelope()


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------
def get_model(role: str = "default") -> str:
    """Return the Claude model ID for `role`, falling back to the default."""
    return _MODEL_BY_ROLE.get(role, _DEFAULT_MODEL)


def get_cost_envelope() -> CostEnvelope:
    """Return the project-wide default CostEnvelope."""
    return _DEFAULT_COST_ENVELOPE


def get_vault_dir() -> Path:
    """Return the vault root Path from the COMPANY_OS_VAULT_DIR env var.

    Lazy — raises RuntimeError only when called with the env var unset,
    never at module import time. This is the single source of truth for
    vault location across production and test code. Relocated from
    core/env.py in chunk 1a.1; core/env.py re-exports this name so legacy
    imports keep working.
    """
    raw = os.environ.get(_VAULT_DIR_ENV)
    if not raw:
        raise RuntimeError(
            f"{_VAULT_DIR_ENV} environment variable is not set. "
            "Set it to the absolute path of your Obsidian vault (e.g. "
            "`export COMPANY_OS_VAULT_DIR='C:/Users/you/Obsidian Vault'`) "
            "or add it to ~/.company-os/.env before importing this module."
        )
    return Path(raw).resolve()


def get_output_subdirs() -> dict[str, str]:
    """Return a fresh copy of the canonical output-subdir name map."""
    return dict(_OUTPUT_SUBDIRS)


def get_permission_mode() -> str:
    """Return the Claude Code SDK permission mode used by managed dispatches."""
    return _PERMISSION_MODE


def get_skill_agent_depts() -> frozenset[str]:
    """Return the set of department names migrated to the skill-agent path
    (Phase 9.5). Reads `COMPANY_OS_SKILL_AGENTS_DEPTS` at call time — no
    caching — so tests can flip the env var between runs.

      * Unset or empty → empty set (legacy worker path for every dept).
      * `"*"` → special sentinel matching every dept name.
      * Comma-separated names (e.g. `marketing,finance`) → exactly those.

    Whitespace around names is stripped; empty segments are dropped.
    """
    raw = os.environ.get(_SKILL_AGENTS_DEPTS_ENV, "").strip()
    if not raw:
        return frozenset()
    if raw == "*":
        return frozenset({"*"})
    parts = [p.strip() for p in raw.split(",")]
    return frozenset(p for p in parts if p)


def is_dept_on_skill_agents(dept_name: str) -> bool:
    """Return True iff `dept_name` is on the skill-agent path."""
    depts = get_skill_agent_depts()
    if not depts:
        return False
    if "*" in depts:
        return True
    return dept_name in depts
