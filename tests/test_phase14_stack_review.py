"""
Tests for the post-onboarding departmental stack review.

Covers corpus assembly, dossier rendering, synthesizer-output parsing,
persistence, proposal lifecycle, and the auto-trigger gate.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.dept_stack_review import (
    DeptArtifactBundle,
    ProposalKind,
    ProposalStatus,
    StackReview,
    StackReviewCorpus,
    StackReviewProposal,
    all_departments_complete,
    build_review_id,
    list_reviews,
    load_review,
    load_review_corpus,
    mark_proposal_status,
    parse_review,
    persist_review,
    render_dossier,
)


# ---------------------------------------------------------------------------
# Corpus + dossier
# ---------------------------------------------------------------------------
class TestCorpus:
    def test_load_corpus_reads_all_artifacts(self, tmp_path: Path):
        """Corpus loader should pick up whatever artifacts exist on disk
        per department, tolerating missing files."""
        # Layout: marketing has scope + founder-brief; finance has nothing.
        (tmp_path / "marketing").mkdir()
        (tmp_path / "marketing" / "skill-scope.md").write_text(
            "## Primary\nWine marketing.\n", encoding="utf-8",
        )
        (tmp_path / "marketing" / "founder-brief.md").write_text(
            "Riley wants tight voice.\n", encoding="utf-8",
        )
        (tmp_path / "finance").mkdir()  # empty — test missing-artifact path

        corpus = load_review_corpus(
            tmp_path,
            company_name="Old Press Wine Co.",
            industry="wine / beverage alcohol",
            active_departments=["marketing", "finance"],
            priorities=["first SKU", "DTC infra"],
            settled_convictions=["stewardship-focused voice"],
            hard_constraints=["no marketing <21"],
        )
        assert corpus.company_name == "Old Press Wine Co."
        assert len(corpus.dept_artifacts) == 2
        mkt = next(b for b in corpus.dept_artifacts if b.dept == "marketing")
        fin = next(b for b in corpus.dept_artifacts if b.dept == "finance")
        assert "Wine marketing" in mkt.skill_scope
        assert fin.skill_scope == ""

    def test_render_dossier_includes_priorities_and_constraints(self, tmp_path: Path):
        corpus = StackReviewCorpus(
            company_name="X", industry="wine",
            active_departments=("marketing",),
            priorities=("ship first SKU",),
            settled_convictions=("stewardship first",),
            hard_constraints=("no under-21 marketing",),
            orchestrator_charter="You are the orchestrator.",
            dept_artifacts=(
                DeptArtifactBundle(
                    dept="marketing", phase="complete",
                    skill_scope="## Primary\nWine marketing.",
                    founder_brief="", charter="",
                ),
            ),
        )
        dossier = render_dossier(corpus)
        assert "ship first SKU" in dossier
        assert "stewardship first" in dossier
        assert "no under-21 marketing" in dossier
        assert "Wine marketing" in dossier
        assert "orchestrator" in dossier.lower()

    def test_render_dossier_flags_missing_artifacts(self, tmp_path: Path):
        corpus = StackReviewCorpus(
            company_name="X", industry="wine",
            active_departments=("marketing",),
            priorities=(), settled_convictions=(), hard_constraints=(),
            orchestrator_charter="",
            dept_artifacts=(
                DeptArtifactBundle(
                    dept="marketing", phase="domain_research",
                    skill_scope="", founder_brief="", charter="",
                ),
            ),
        )
        dossier = render_dossier(corpus)
        # Each missing artifact should be called out so the Board
        # knows which depts have weak signal.
        assert "no skill-scope" in dossier.lower()
        assert "no founder-brief" in dossier.lower()
        assert "no charter" in dossier.lower()


# ---------------------------------------------------------------------------
# Synthesizer output parsing
# ---------------------------------------------------------------------------
class TestParseReview:
    def test_extracts_gaps_bullet_list(self):
        text = """## Gaps

- No dept owns regulatory compliance.
- Editorial scope overlaps marketing.

## Executive summary

The stack is incomplete.

## Proposals (JSON)

```json
{"proposals": []}
```
"""
        gaps, summary, proposals = parse_review(text)
        assert len(gaps) == 2
        assert "regulatory compliance" in gaps[0]
        assert proposals == ()
        assert "incomplete" in summary

    def test_extracts_executive_summary(self):
        text = """## Gaps

Nothing serious.

## Executive summary

The stack looks coherent; no new depts needed.

## Proposals (JSON)

```json
{"proposals": []}
```
"""
        _, summary, _ = parse_review(text)
        assert "coherent" in summary

    def test_extracts_new_department_proposal(self):
        text = """## Gaps

- No regulatory compliance dept.

## Executive summary

Add compliance.

## Proposals (JSON)

```json
{
  "proposals": [
    {
      "kind": "new_department",
      "title": "Add regulatory-compliance department",
      "rationale": "Analyst noted no dept owns TTB/COLA tracking.",
      "proposed_dept_name": "compliance",
      "proposed_dept_owns": ["TTB filings", "state ABC licensing"],
      "proposed_dept_never": ["marketing copy", "product design"]
    }
  ]
}
```
"""
        _, _, proposals = parse_review(text)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.kind == ProposalKind.NEW_DEPARTMENT.value
        assert p.proposed_dept_name == "compliance"
        assert "TTB filings" in p.proposed_dept_owns
        assert "marketing copy" in p.proposed_dept_never
        assert p.id.startswith("p01-new_department-")

    def test_extracts_orchestrator_amendment(self):
        text = """## Gaps

- Orchestrator routes compliance questions to legal specialist that doesn't exist.

## Executive summary

Rewire.

## Proposals (JSON)

```json
{
  "proposals": [
    {
      "kind": "orchestrator_amendment",
      "title": "Route compliance queries to operations, not legal",
      "rationale": "Contrarian flagged that legal specialist is not in roster.",
      "orchestrator_delta": "Route any query mentioning TTB / COLA / ABC licensing to operations manager, not legal."
    }
  ]
}
```
"""
        _, _, proposals = parse_review(text)
        assert len(proposals) == 1
        assert proposals[0].kind == ProposalKind.ORCHESTRATOR_AMENDMENT.value
        assert "TTB" in proposals[0].orchestrator_delta

    def test_rejects_unknown_kind(self):
        text = """## Gaps

x.

## Executive summary

x.

## Proposals (JSON)

```json
{"proposals": [{"kind": "random_thing", "title": "x", "rationale": "x"}]}
```
"""
        _, _, proposals = parse_review(text)
        assert proposals == ()

    def test_returns_empty_on_malformed_json(self):
        text = """## Proposals (JSON)

```json
{not valid json
```
"""
        _, _, proposals = parse_review(text)
        assert proposals == ()

    def test_no_fenced_block_returns_empty(self):
        text = "No structured output here at all."
        gaps, summary, proposals = parse_review(text)
        assert gaps == ()
        assert summary == ""
        assert proposals == ()


# ---------------------------------------------------------------------------
# Persistence + status lifecycle
# ---------------------------------------------------------------------------
class TestPersistence:
    def _fresh_review(self, *, with_proposal: bool = True) -> StackReview:
        proposals: tuple[StackReviewProposal, ...] = ()
        if with_proposal:
            proposals = (
                StackReviewProposal(
                    id="p01-new_department-add-compliance",
                    kind=ProposalKind.NEW_DEPARTMENT.value,
                    title="Add compliance department",
                    rationale="Coverage gap.",
                    proposed_dept_name="compliance",
                    proposed_dept_owns=("TTB",),
                    proposed_dept_never=("marketing copy",),
                ),
            )
        return StackReview(
            id=build_review_id(),
            created_at="2026-04-19T12:00:00+00:00",
            corpus_summary={"dept_count": 9},
            gaps=("Gap 1.",),
            proposals=proposals,
            board_transcript_path="decisions/stack-reviews/_transcripts/cross-meeting.md",
            notes="Stack is healthy but compliance is missing.",
        )

    def test_persist_and_load_roundtrip(self, tmp_path: Path):
        review = self._fresh_review()
        persist_review(tmp_path, review, synthesizer_text="raw text")
        loaded = load_review(tmp_path, review.id)
        assert loaded is not None
        assert loaded.id == review.id
        assert len(loaded.proposals) == 1
        assert loaded.proposals[0].proposed_dept_name == "compliance"

    def test_list_reviews_reverse_chronological(self, tmp_path: Path):
        from core.dept_stack_review import reviews_dir
        r1 = self._fresh_review(with_proposal=False)
        persist_review(tmp_path, r1, synthesizer_text="")
        # Second review with a later slug
        import dataclasses
        r2 = dataclasses.replace(r1, id="2099-12-31-review")
        persist_review(tmp_path, r2, synthesizer_text="")
        listed = list_reviews(tmp_path)
        assert len(listed) == 2
        assert listed[0].id == r2.id  # newer first

    def test_mark_proposal_accepted(self, tmp_path: Path):
        review = self._fresh_review()
        persist_review(tmp_path, review, synthesizer_text="")
        updated = mark_proposal_status(
            tmp_path, review.id, review.proposals[0].id,
            status=ProposalStatus.ACCEPTED, notes="building it",
        )
        assert updated is not None
        assert updated.proposals[0].status == ProposalStatus.ACCEPTED.value
        assert updated.proposals[0].implementation_notes == "building it"
        assert updated.proposals[0].implemented_at  # non-empty

    def test_mark_proposal_rejected_no_timestamp(self, tmp_path: Path):
        review = self._fresh_review()
        persist_review(tmp_path, review, synthesizer_text="")
        updated = mark_proposal_status(
            tmp_path, review.id, review.proposals[0].id,
            status=ProposalStatus.REJECTED,
        )
        assert updated.proposals[0].status == ProposalStatus.REJECTED.value
        # Rejection does NOT set implemented_at — that's reserved for accepts
        assert updated.proposals[0].implemented_at == ""

    def test_mark_missing_proposal_returns_none(self, tmp_path: Path):
        review = self._fresh_review()
        persist_review(tmp_path, review, synthesizer_text="")
        assert mark_proposal_status(
            tmp_path, review.id, "p99-ghost",
            status=ProposalStatus.ACCEPTED,
        ) is None


# ---------------------------------------------------------------------------
# Auto-trigger gate
# ---------------------------------------------------------------------------
class TestAutoTrigger:
    def test_returns_true_when_all_complete(self, tmp_path: Path):
        from core.dept_onboarding import (
            OnboardingPhase, begin_phase, signoff_phase, SignoffStatus,
        )
        # Single dept fully onboarded
        for phase in [
            OnboardingPhase.SCOPE_CALIBRATION,
            OnboardingPhase.DOMAIN_RESEARCH,
            OnboardingPhase.FOUNDER_INTERVIEW,
            OnboardingPhase.KB_INGESTION,
            OnboardingPhase.INTEGRATIONS,
            OnboardingPhase.CHARTER,
        ]:
            begin_phase(tmp_path, "marketing", phase)
            signoff_phase(
                tmp_path, "marketing", phase,
                status=SignoffStatus.APPROVED,
            )
        assert all_departments_complete(tmp_path, ["marketing"]) is True

    def test_returns_false_when_any_incomplete(self, tmp_path: Path):
        from core.dept_onboarding import ensure_state
        ensure_state(tmp_path, "marketing")  # pending
        ensure_state(tmp_path, "finance")    # pending
        assert all_departments_complete(tmp_path, ["marketing", "finance"]) is False

    def test_empty_active_list_returns_false(self, tmp_path: Path):
        # A company with zero departments should NOT auto-trigger
        assert all_departments_complete(tmp_path, []) is False


# ---------------------------------------------------------------------------
# Webapp surface
# ---------------------------------------------------------------------------
class TestWebappStackReview:
    SLUG = "Old Press Wine Company LLC"

    @pytest.fixture(scope="class")
    def client(self, vault_dir, old_press_dir):
        import os
        os.environ.setdefault("COMPANY_OS_VAULT_DIR", str(vault_dir))
        from webapp.app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_stack_review_index_renders(self, client):
        resp = client.get(f"/c/{self.SLUG}/stack-review")
        assert resp.status_code == 200
        assert b"Stack" in resp.data

    def test_missing_review_404s(self, client):
        resp = client.get(f"/c/{self.SLUG}/stack-review/ghost-review")
        assert resp.status_code == 404

    def test_nav_includes_stack_review(self, client):
        resp = client.get(f"/c/{self.SLUG}/")
        assert b"Stack Review" in resp.data or b"stack-review" in resp.data
