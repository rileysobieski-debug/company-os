"""Dataclasses for the governance Phase 1 data model.

TrustSnapshot: the aggregated trust score for a single agent at a
single point in time. Written to `trust_snapshots` on every
computation; read for UI rendering.

DecisionRecord: one row in the `decisions` audit log. In Phase 1 every
row has source="human" because all dispatches are founder-initiated.
When the agent-facing tool ships (deferred), agent-initiated rows will
use source="agent" and the full ActionRequest model joins here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TrustSnapshot:
    """Per-agent aggregated trust, normalized to -1.0..+1.0.

    `score`: weighted mean of all rating samples plus a neutral
        baseline anchor. Half-life decay applied to rating weights.
    `sample_count`: number of real rating samples that contributed
        (the neutral baseline is NOT counted).
    `last_sample_at`: ISO timestamp of the most recent rating sample.
        None if the agent has zero samples.
    `computed_at`: ISO timestamp of this snapshot.
    `breakdown`: dict mapping source-type -> contribution summary, e.g.
        {"signoff": {"count": 3, "weighted_contribution": 0.42}, ...}
    """

    agent_id: str
    score: float
    sample_count: int
    last_sample_at: Optional[str]
    computed_at: str
    breakdown: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionRecord:
    """One row in the decisions audit log."""

    decision_id: str           # uuid4 hex
    source: str                # "human" | "agent"
    agent_id: Optional[str]    # None when the decision isn't bound to a specific agent
    action_type: str           # short token describing what happened
    action_summary: str        # human-readable one-line description
    outcome: str               # "dispatched" | "approved" | "rejected" | "skipped" | ...
    decided_at: str            # ISO timestamp
    founder_trigger_route: Optional[str]   # the webapp route that was clicked
    job_id: Optional[str]      # link to JOB_REGISTRY entry if the action fired a background job
    notes: str = ""
