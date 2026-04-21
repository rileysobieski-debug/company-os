"""Governance module: trust observability + human dispatch retro-logging.

Phase 1 only. See `~/.claude/plans/how-would-an-agent-quiet-origami.md`
for the full roadmap. This phase is deliberately scoped to:

  - Read-only trust aggregation from existing rating sources.
  - SQLite-backed decisions log that retro-logs founder-initiated
    dispatches as source="human" rows.
  - One new page at /c/<slug>/governance that shows per-agent trust
    scores plus the recent decisions audit trail.

No agent-facing primitive. No evaluator. No enforcement. No budget
holds. Those live in the appendix of the plan and ship in a later
phase once a real wake-trigger mechanism exists.
"""
from core.governance.models import TrustSnapshot, DecisionRecord
from core.governance.storage import open_db, migrate
from core.governance.trust import aggregate_trust, discover_agent_ids
from core.governance.retrolog import (
    record_human_action, retrolog_dispatch, last_successful_retrolog_write,
)

__all__ = [
    "TrustSnapshot",
    "DecisionRecord",
    "open_db",
    "migrate",
    "aggregate_trust",
    "discover_agent_ids",
    "record_human_action",
    "retrolog_dispatch",
    "last_successful_retrolog_write",
]
