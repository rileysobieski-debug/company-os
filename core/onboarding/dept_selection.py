"""
core/onboarding/dept_selection.py — Phase 8.2 — §5.5 top-3 dept picker
=======================================================================
Pure, deterministic keyword-scored mapper from priority text → department.
The orchestrator presents the suggestion; the founder confirms or
overrides. The production orchestrator may later delegate the mapping
step to an LLM, but the deterministic scorer is the ground-truth baseline
(testable, auditable, and runs before any API key is configured).

Scoring rules:
  * For each priority, count keyword hits per department.
  * Ties broken by the order departments appear in VERTICAL_DEPARTMENTS
    — which starts marketing/finance/operations because those are the
    most common top-3 picks in the wine-beverage pack.
  * Priorities picked in order; each priority claims the best-scored
    department not yet claimed by an earlier priority.
  * Zero-hit priorities still get assigned a dept (falls through to the
    first unclaimed dept in the vertical list) — the orchestrator
    surfaces "default assignment" in its question to the founder.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

VERTICAL_DEPARTMENTS: tuple[str, ...] = (
    # Ordered by default-active priority in the wine-beverage pack.
    "marketing",
    "finance",
    "operations",
    "product-design",
    "community",
    "editorial",
    "data",
    "ai-workflow",
    "ai-architecture",
)

# Keyword dictionary keyed by dept; one priority can match several.
_DEPT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "marketing": (
        "marketing", "positioning", "brand", "audience", "launch",
        "awareness", "messaging", "campaign",
    ),
    "finance": (
        "cash", "revenue", "budget", "capital", "funding", "pricing",
        "investor", "w-2", "income", "profit", "cost",
    ),
    "operations": (
        "operations", "ops", "supplier", "production", "vendor",
        "logistics", "compliance", "ttb", "licensing", "regulatory",
        "paperwork", "alternating-proprietor",
    ),
    "product-design": (
        "product", "design", "feature", "prototype", "label", "bottle",
        "packaging",
    ),
    "community": (
        "community", "list", "buyer", "subscriber", "pre-order",
        "customer", "newsletter audience", "audience-building",
    ),
    "editorial": (
        "editorial", "content", "copy", "newsletter", "writing",
        "articles", "story",
    ),
    "data": (
        "data", "analytics", "metrics", "reporting", "dashboard",
    ),
    "ai-workflow": (
        "workflow", "automation", "pipeline", "orchestration",
    ),
    "ai-architecture": (
        "architecture", "infrastructure", "platform", "system",
        "framework",
    ),
}


@dataclass(frozen=True)
class DepartmentChoice:
    dept: str
    rationale: str
    priority_index: int
    hit_count: int = 0


def _score_department(
    priority: str,
    *,
    available: Iterable[str] = VERTICAL_DEPARTMENTS,
) -> list[tuple[str, int]]:
    """Return [(dept, hit_count)] for every `available` dept, ranked.

    Ranking: higher hits first; ties break by position in the
    VERTICAL_DEPARTMENTS order."""
    text = priority.lower()
    available_list = list(available)
    index_by_dept = {d: i for i, d in enumerate(VERTICAL_DEPARTMENTS)}
    scored: list[tuple[str, int]] = []
    for dept in available_list:
        keywords = _DEPT_KEYWORDS.get(dept, ())
        hits = sum(1 for k in keywords if k in text)
        scored.append((dept, hits))
    scored.sort(key=lambda pair: (-pair[1], index_by_dept.get(pair[0], 999)))
    return scored


def suggest_top_n_departments(
    priorities: list[str],
    *,
    n: int = 3,
    available: Iterable[str] = VERTICAL_DEPARTMENTS,
) -> list[DepartmentChoice]:
    """Map the first `n` priorities → distinct departments.

    Order is priority order. Each priority claims the best-scored dept
    not already claimed by an earlier priority. On zero hits, falls
    through to the next unclaimed dept in the vertical-order list.
    """
    if n <= 0:
        return []
    available_list = list(available)
    claimed: set[str] = set()
    picked: list[DepartmentChoice] = []
    for i, priority in enumerate(priorities[:n]):
        scored = _score_department(priority, available=available_list)
        for dept, hits in scored:
            if dept in claimed:
                continue
            if hits == 0:
                rationale = "default assignment (no keyword match — founder confirms)"
            else:
                rationale = (
                    f"{hits} keyword hit(s) mapped priority "
                    f"{priority!r} → {dept}"
                )
            picked.append(DepartmentChoice(
                dept=dept, rationale=rationale,
                priority_index=i, hit_count=hits,
            ))
            claimed.add(dept)
            break
    return picked


def apply_founder_override(
    suggestions: list[DepartmentChoice],
    overrides: dict[int, str],
) -> list[DepartmentChoice]:
    """Replace suggested dept at `priority_index` with `overrides[priority_index]`.

    Duplicate departments across suggestions are rejected (ValueError) —
    the founder must pick distinct depts for distinct priorities.
    """
    merged: list[DepartmentChoice] = []
    seen: set[str] = set()
    for choice in suggestions:
        new_dept = overrides.get(choice.priority_index, choice.dept)
        if new_dept in seen:
            raise ValueError(
                f"duplicate department {new_dept!r} after override — "
                "each active department must be distinct"
            )
        if new_dept != choice.dept:
            rationale = (
                f"founder override: {choice.dept} → {new_dept} "
                f"for priority index {choice.priority_index}"
            )
            merged.append(DepartmentChoice(
                dept=new_dept, rationale=rationale,
                priority_index=choice.priority_index, hit_count=0,
            ))
        else:
            merged.append(choice)
        seen.add(new_dept)
    return merged


def dormant_departments(
    active: list[DepartmentChoice] | list[str],
    *,
    all_departments: Iterable[str] = VERTICAL_DEPARTMENTS,
) -> list[str]:
    """Return the departments NOT in `active`. Preserves vertical order.

    `active` accepts either a list of DepartmentChoice or plain strings —
    callers mix-and-match without adapting."""
    active_names = {
        a.dept if isinstance(a, DepartmentChoice) else a
        for a in active
    }
    return [d for d in all_departments if d not in active_names]
