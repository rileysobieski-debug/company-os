"""Phase 7.4 hook factories: make_handshake_pre_hook + make_evaluate_post_hook."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.dispatch import (
    DispatchPostState,
    make_evaluate_post_hook,
    make_handshake_pre_hook,
)
from core.dispatch.evaluator import (
    CriterionResult,
    RubricCriterion,
    VerdictStatus,
)
from core.dispatch.handshake_runner import HANDSHAKES_SUBDIR


@dataclass
class _FakeResult:
    """Stand-in for ManagerResult — only the attrs the post-hook reads."""

    final_text: str
    brief: str = "draft positioning statement"
    max_iterations_hit: bool = False


# ---------------------------------------------------------------------------
# make_handshake_pre_hook
# ---------------------------------------------------------------------------
def test_pre_hook_writes_handshake_under_session_dir(tmp_path: Path) -> None:
    pre = make_handshake_pre_hook(
        company_dir=tmp_path,
        session_id="sess-1",
        sender="orchestrator",
        receiver="marketing-manager",
        intent="Draft positioning",
        deliverable="1-page positioning doc",
    )
    pre("any brief")

    session_dir = tmp_path / HANDSHAKES_SUBDIR / "sess-1"
    files = list(session_dir.glob("*.json"))
    assert len(files) == 1


def test_pre_hook_invokes_on_handshake_callback(tmp_path: Path) -> None:
    captured = []
    pre = make_handshake_pre_hook(
        company_dir=tmp_path,
        session_id="s2", sender="o", receiver="m",
        intent="i", deliverable="d",
        on_handshake=captured.append,
    )
    pre("brief body")
    assert len(captured) == 1
    assert captured[0].session_id == "s2"
    assert captured[0].intent == "i"


def test_pre_hook_ignores_brief_content_for_intent(tmp_path: Path) -> None:
    """Intent is agreed before dispatch, not derived from brief."""
    pre = make_handshake_pre_hook(
        company_dir=tmp_path,
        session_id="s3", sender="o", receiver="m",
        intent="declared intent",
        deliverable="declared deliverable",
    )
    pre("completely unrelated brief text")
    files = list((tmp_path / HANDSHAKES_SUBDIR / "s3").glob("*.json"))
    import json
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["intent"] == "declared intent"


# ---------------------------------------------------------------------------
# make_evaluate_post_hook
# ---------------------------------------------------------------------------
def _keyword_rubric() -> list[RubricCriterion]:
    return [
        RubricCriterion(id="positioning", min_score=0.5, weight=1.0),
    ]


def _strict_rubric() -> list[RubricCriterion]:
    return [RubricCriterion(id="missing-keyword", min_score=0.5)]


def test_post_hook_pass_routes_artifact_to_approved(tmp_path: Path) -> None:
    dept_dir = tmp_path / "departments" / "marketing"
    dept_dir.mkdir(parents=True)
    state = DispatchPostState()
    post = make_evaluate_post_hook(
        company_dir=tmp_path,
        dept_dir=dept_dir,
        session_id="s1",
        specialist_id="writer",
        skill_id="positioning-draft",
        rubric=_keyword_rubric(),
        state=state,
    )
    post(_FakeResult(final_text="Our positioning is clear and focused."))

    assert state.verdict is not None
    assert state.verdict.status is VerdictStatus.PASS
    assert state.route is not None
    assert state.route.destination == "approved"
    assert state.route.artifact_path.exists()
    assert state.route.manager_memory_path.exists()
    assert state.route.specialist_memory_path.exists()


def test_post_hook_fail_routes_to_rejected(tmp_path: Path) -> None:
    dept_dir = tmp_path / "departments" / "marketing"
    dept_dir.mkdir(parents=True)
    state = DispatchPostState()
    post = make_evaluate_post_hook(
        company_dir=tmp_path,
        dept_dir=dept_dir,
        session_id="s1",
        specialist_id="writer",
        skill_id="draft",
        rubric=_strict_rubric(),  # keyword will NOT appear in output
        state=state,
    )
    post(_FakeResult(final_text="Body text not containing the required token."))

    assert state.verdict.status is VerdictStatus.FAIL
    assert state.route.destination == "rejected"


def test_post_hook_max_iterations_auto_demotes_to_pending_approval(
    tmp_path: Path,
) -> None:
    """§4.1 constraint 5: max_iterations_hit → NEEDS_FOUNDER_REVIEW → pending."""
    dept_dir = tmp_path / "departments" / "marketing"
    dept_dir.mkdir(parents=True)
    state = DispatchPostState()
    post = make_evaluate_post_hook(
        company_dir=tmp_path,
        dept_dir=dept_dir,
        session_id="s1",
        specialist_id="writer",
        skill_id="draft",
        rubric=_keyword_rubric(),
        state=state,
    )
    post(_FakeResult(
        final_text="Our positioning is crystal clear.",
        max_iterations_hit=True,
    ))

    assert state.verdict.status is VerdictStatus.NEEDS_FOUNDER_REVIEW
    assert state.route.destination == "pending-approval"


def test_post_hook_persists_verdict_to_evaluations(tmp_path: Path) -> None:
    dept_dir = tmp_path / "departments" / "marketing"
    dept_dir.mkdir(parents=True)
    post = make_evaluate_post_hook(
        company_dir=tmp_path,
        dept_dir=dept_dir,
        session_id="s1",
        specialist_id="writer",
        skill_id="draft",
        rubric=_keyword_rubric(),
    )
    post(_FakeResult(final_text="positioning done"))

    eval_files = list((tmp_path / "evaluations").rglob("*.json"))
    assert len(eval_files) == 1


def test_post_hook_drift_report_populated(tmp_path: Path) -> None:
    dept_dir = tmp_path / "departments" / "marketing"
    dept_dir.mkdir(parents=True)
    state = DispatchPostState()
    post = make_evaluate_post_hook(
        company_dir=tmp_path,
        dept_dir=dept_dir,
        session_id="s1",
        specialist_id="writer",
        skill_id="draft",
        rubric=_keyword_rubric(),
        state=state,
    )
    post(_FakeResult(final_text="positioning clear"))
    assert state.drift is not None
    # No references → watchdog reports zero, no issues.
    assert state.drift.watchdog.references_checked == 0


def test_post_hook_respects_custom_judge(tmp_path: Path) -> None:
    """Injecting a `judge` bypasses the keyword-fallback scorer."""
    dept_dir = tmp_path / "departments" / "marketing"
    dept_dir.mkdir(parents=True)
    state = DispatchPostState()

    def _always_fail(c, *_):
        return CriterionResult(
            criterion_id=c.id, score=0.0, passed=False, comment="custom fail"
        )

    post = make_evaluate_post_hook(
        company_dir=tmp_path, dept_dir=dept_dir,
        session_id="s1", specialist_id="w", skill_id="d",
        rubric=_keyword_rubric(),
        judge=_always_fail,
        state=state,
    )
    post(_FakeResult(final_text="positioning clear"))  # keyword present
    assert state.verdict.status is VerdictStatus.FAIL
    assert state.verdict.criterion_results[0].comment == "custom fail"


def test_post_hook_respects_custom_summary(tmp_path: Path) -> None:
    dept_dir = tmp_path / "departments" / "marketing"
    dept_dir.mkdir(parents=True)
    state = DispatchPostState()
    post = make_evaluate_post_hook(
        company_dir=tmp_path, dept_dir=dept_dir,
        session_id="s1", specialist_id="w", skill_id="d",
        rubric=_keyword_rubric(),
        summary_fn=lambda _r: "custom summary text",
        state=state,
    )
    post(_FakeResult(final_text="positioning ok"))
    mem = state.route.manager_memory_path.read_text(encoding="utf-8")
    assert "custom summary text" in mem


def test_post_hook_empty_output_still_writes_artifact(tmp_path: Path) -> None:
    """An empty final_text routes to rejected (rubric fails) but the artifact
    file is still written — work isn't lost."""
    dept_dir = tmp_path / "departments" / "marketing"
    dept_dir.mkdir(parents=True)
    state = DispatchPostState()
    post = make_evaluate_post_hook(
        company_dir=tmp_path, dept_dir=dept_dir,
        session_id="s1", specialist_id="w", skill_id="d",
        rubric=_keyword_rubric(),
        state=state,
    )
    post(_FakeResult(final_text=""))
    assert state.route.artifact_path.exists()
    assert state.route.destination == "rejected"
