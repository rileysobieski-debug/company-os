"""
core/dispatch/hooks.py — Phase 7.4 — pre/post hook factories
=============================================================
`dispatch_manager` already accepts `pre_hook(brief)` and
`post_hook(result)` callables (shipped in Chunk 1a.8). Phase 7 adds the
factories here that produce hooks wired to the real Phase 7 pieces:

  * `make_handshake_pre_hook` — stamps a Priority-5 handshake at dispatch
    start.
  * `make_evaluate_post_hook` — runs drift_guard, the evaluator, persists
    a Verdict, and writes both the output artifact (PASS→approved/,
    NEEDS_FOUNDER_REVIEW→pending-approval/, FAIL→rejected/) and the
    dated memory entries.

These factories return closures with the exact `(brief: str) -> None` /
`(result: ManagerResult) -> None` shape Chunk 1a.8 froze, so existing
dispatch callers wire them in without modification.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from core.dispatch.drift_guard import (
    DriftGuardReport,
    evaluate_dispatch,
)
from core.dispatch.evaluator import (
    RubricCriterion,
    ScoreFn,
    Verdict,
    evaluate_output,
    record_verdict,
)
from core.dispatch.handshake_runner import Handshake, record_handshake
from core.dispatch.memory_updater import (
    RouteResult,
    record_dispatch_outcome,
)

# Type aliases for clarity.
PreHook = Callable[[str], None]
PostHook = Callable[[object], None]
SummaryFn = Callable[[object], str]


@dataclass
class DispatchPostState:
    """Mutable container for post-hook observers (tests, founder dashboard).

    The post-hook populates these fields as it runs; callers can read
    them after dispatch to see the drift report / verdict / route result
    without having to parse disk. Kept as a plain mutable dataclass so
    hooks can write to it over a closure."""

    drift: DriftGuardReport | None = None
    verdict: Verdict | None = None
    route: RouteResult | None = None


# ---------------------------------------------------------------------------
# Pre-hook — stamp handshake
# ---------------------------------------------------------------------------
def make_handshake_pre_hook(
    *,
    company_dir: Path,
    session_id: str,
    sender: str,
    receiver: str,
    intent: str,
    deliverable: str,
    references: tuple[str, ...] = (),
    on_handshake: Callable[[Handshake], None] | None = None,
) -> PreHook:
    """Build a `pre_hook(brief)` that writes a handshake to disk.

    `intent` and `deliverable` are the canonical commitment strings; the
    hook does NOT fall back to the brief body because handshake intent
    is supposed to be agreed before dispatch, not derived from it.
    `on_handshake` lets observers capture the Handshake object.
    """

    def _pre(_brief: str) -> None:
        hs = record_handshake(
            company_dir,
            session_id=session_id,
            sender=sender,
            receiver=receiver,
            intent=intent,
            deliverable=deliverable,
            references=references,
        )
        if on_handshake is not None:
            on_handshake(hs)

    return _pre


# ---------------------------------------------------------------------------
# Post-hook — drift + evaluate + persist
# ---------------------------------------------------------------------------
def _default_summary(result: object) -> str:
    text = getattr(result, "final_text", "") or ""
    return text.strip().splitlines()[0][:300] if text.strip() else "(no output)"


def make_evaluate_post_hook(
    *,
    company_dir: Path,
    dept_dir: Path,
    session_id: str,
    specialist_id: str,
    skill_id: str,
    rubric: Iterable[RubricCriterion],
    judge: ScoreFn | None = None,
    summary_fn: SummaryFn | None = None,
    vault_dir: Path | None = None,
    references: tuple[str, ...] = (),
    state: DispatchPostState | None = None,
) -> PostHook:
    """Build a `post_hook(result)` that runs the full Phase 7 pipeline.

    Steps, in order:
      1. `evaluate_dispatch` over `result.final_text` — drift/watchdog
         check against `vault_dir` (defaults to `company_dir`).
      2. `evaluate_output` — rubric-based Verdict. If the result carries
         `max_iterations_hit`, it's passed through so the evaluator can
         auto-demote to NEEDS_FOUNDER_REVIEW (§4.1 constraint 5).
      3. `record_verdict` — persist Verdict to
         `<company>/evaluations/<date>/<session>-<specialist>-<ts>.json`.
      4. `record_dispatch_outcome` — write artifact + append manager/
         specialist memory. Routing is picked from Verdict.status:
           PASS → approved/, NEEDS_FOUNDER_REVIEW → pending-approval/,
           FAIL → rejected/.

    `state`, if provided, is populated with the intermediate results so
    callers can observe what happened without reading disk."""
    rubric_list = list(rubric)
    resolved_vault = vault_dir or company_dir

    def _post(result: object) -> None:
        output_text = getattr(result, "final_text", "") or ""
        brief = getattr(result, "brief", "") or ""
        max_iter_flag = bool(getattr(result, "max_iterations_hit", False))

        drift = evaluate_dispatch(output_text, resolved_vault)

        verdict = evaluate_output(
            brief=brief,
            output=output_text,
            rubric=rubric_list,
            specialist_id=specialist_id,
            skill_id=skill_id,
            session_id=session_id,
            max_iterations_hit=max_iter_flag,
            judge=judge,
        )
        record_verdict(company_dir, verdict)

        summary = (summary_fn or _default_summary)(result)
        route = record_dispatch_outcome(
            dept_dir,
            verdict=verdict,
            output_content=output_text,
            summary=summary,
            references=references,
        )

        if state is not None:
            state.drift = drift
            state.verdict = verdict
            state.route = route

    return _post
