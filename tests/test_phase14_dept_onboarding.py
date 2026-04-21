"""
Tests for the per-department 5-phase onboarding lifecycle.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.dept_onboarding import (
    DepartmentOnboardingState,
    IllegalTransitionError,
    OnboardingPhase,
    PhaseArtifact,
    SignoffStatus,
    attach_artifact,
    begin_phase,
    ensure_state,
    list_all_states,
    load_state,
    new_state,
    overall_progress,
    persist_state,
    render_domain_research_brief,
    reset_to_phase,
    signoff_phase,
)


class TestStateBasics:
    def test_new_state_starts_pending(self):
        s = new_state("marketing")
        assert s.phase == OnboardingPhase.PENDING.value
        assert s.dept == "marketing"
        assert s.artifacts == ()
        assert not s.is_complete

    def test_persist_and_load_roundtrip(self, tmp_path: Path):
        s = new_state("marketing")
        persist_state(tmp_path, s)
        loaded = load_state(tmp_path, "marketing")
        assert loaded is not None
        assert loaded.dept == "marketing"
        assert loaded.phase == s.phase

    def test_ensure_state_creates_missing(self, tmp_path: Path):
        assert load_state(tmp_path, "finance") is None
        s = ensure_state(tmp_path, "finance")
        assert s.dept == "finance"
        assert load_state(tmp_path, "finance") is not None

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert load_state(tmp_path, "nonexistent") is None


class TestPhaseTransitions:
    def test_begin_phase_writes_artifact(self, tmp_path: Path):
        begin_phase(
            tmp_path, "marketing", OnboardingPhase.DOMAIN_RESEARCH,
            artifact_path="marketing/domain-brief.md", job_id="job-1",
        )
        s = load_state(tmp_path, "marketing")
        assert s.phase == OnboardingPhase.DOMAIN_RESEARCH.value
        assert len(s.artifacts) == 1
        art = s.artifacts[0]
        assert art.phase == OnboardingPhase.DOMAIN_RESEARCH.value
        assert art.path == "marketing/domain-brief.md"
        assert art.signoff == SignoffStatus.NONE.value

    def test_attach_artifact_updates_most_recent(self, tmp_path: Path):
        begin_phase(tmp_path, "marketing", OnboardingPhase.DOMAIN_RESEARCH)
        attach_artifact(
            tmp_path, "marketing", OnboardingPhase.DOMAIN_RESEARCH,
            artifact_path="marketing/domain-brief.md", job_id="job-final",
        )
        s = load_state(tmp_path, "marketing")
        assert s.artifacts[-1].path == "marketing/domain-brief.md"
        assert s.artifacts[-1].job_id == "job-final"

    def test_approved_advances_phase(self, tmp_path: Path):
        begin_phase(tmp_path, "marketing", OnboardingPhase.DOMAIN_RESEARCH)
        signoff_phase(
            tmp_path, "marketing", OnboardingPhase.DOMAIN_RESEARCH,
            status=SignoffStatus.APPROVED, rating=2,
        )
        s = load_state(tmp_path, "marketing")
        assert s.phase == OnboardingPhase.FOUNDER_INTERVIEW.value
        assert OnboardingPhase.DOMAIN_RESEARCH.value in s.completed_phases
        assert s.artifacts[-1].rating == 2
        assert s.artifacts[-1].signoff == SignoffStatus.APPROVED.value

    def test_scope_calibration_advances_to_domain_research(self, tmp_path: Path):
        """SCOPE_CALIBRATION is now the first real phase. Approving it
        should land the state on DOMAIN_RESEARCH, not FOUNDER_INTERVIEW."""
        begin_phase(tmp_path, "marketing", OnboardingPhase.SCOPE_CALIBRATION)
        signoff_phase(
            tmp_path, "marketing", OnboardingPhase.SCOPE_CALIBRATION,
            status=SignoffStatus.APPROVED, rating=1,
        )
        s = load_state(tmp_path, "marketing")
        assert s.phase == OnboardingPhase.DOMAIN_RESEARCH.value
        assert OnboardingPhase.SCOPE_CALIBRATION.value in s.completed_phases

    def test_rejected_stays_on_phase(self, tmp_path: Path):
        begin_phase(tmp_path, "marketing", OnboardingPhase.DOMAIN_RESEARCH)
        signoff_phase(
            tmp_path, "marketing", OnboardingPhase.DOMAIN_RESEARCH,
            status=SignoffStatus.REJECTED, rating=-1, notes="too generic",
        )
        s = load_state(tmp_path, "marketing")
        assert s.phase == OnboardingPhase.DOMAIN_RESEARCH.value  # no advance
        assert OnboardingPhase.DOMAIN_RESEARCH.value not in s.completed_phases
        assert s.artifacts[-1].signoff == SignoffStatus.REJECTED.value

    def test_skipped_advances_but_records_skip(self, tmp_path: Path):
        begin_phase(tmp_path, "editorial", OnboardingPhase.INTEGRATIONS)
        signoff_phase(
            tmp_path, "editorial", OnboardingPhase.INTEGRATIONS,
            status=SignoffStatus.SKIPPED,
        )
        s = load_state(tmp_path, "editorial")
        assert s.phase == OnboardingPhase.CHARTER.value
        assert OnboardingPhase.INTEGRATIONS.value in s.skipped_phases

    def test_full_lifecycle_to_complete(self, tmp_path: Path):
        # Approve every phase in order; land on COMPLETE.
        ordered = [
            OnboardingPhase.SCOPE_CALIBRATION,
            OnboardingPhase.DOMAIN_RESEARCH,
            OnboardingPhase.FOUNDER_INTERVIEW,
            OnboardingPhase.KB_INGESTION,
            OnboardingPhase.INTEGRATIONS,
            OnboardingPhase.CHARTER,
        ]
        for p in ordered:
            begin_phase(tmp_path, "finance", p)
            signoff_phase(tmp_path, "finance", p, status=SignoffStatus.APPROVED)
        s = load_state(tmp_path, "finance")
        assert s.phase == OnboardingPhase.COMPLETE.value
        assert s.is_complete
        assert len(s.completed_phases) == 6

    def test_rating_out_of_range_rejected(self, tmp_path: Path):
        begin_phase(tmp_path, "m", OnboardingPhase.DOMAIN_RESEARCH)
        with pytest.raises(ValueError):
            signoff_phase(
                tmp_path, "m", OnboardingPhase.DOMAIN_RESEARCH,
                status=SignoffStatus.APPROVED, rating=5,
            )

    def test_reset_to_earlier_phase(self, tmp_path: Path):
        begin_phase(tmp_path, "ops", OnboardingPhase.DOMAIN_RESEARCH)
        signoff_phase(tmp_path, "ops", OnboardingPhase.DOMAIN_RESEARCH, status=SignoffStatus.APPROVED)
        begin_phase(tmp_path, "ops", OnboardingPhase.FOUNDER_INTERVIEW)
        signoff_phase(tmp_path, "ops", OnboardingPhase.FOUNDER_INTERVIEW, status=SignoffStatus.APPROVED)
        # Now reset
        reset_to_phase(tmp_path, "ops", OnboardingPhase.DOMAIN_RESEARCH)
        s = load_state(tmp_path, "ops")
        assert s.phase == OnboardingPhase.DOMAIN_RESEARCH.value
        # Artifact history is preserved
        assert len(s.artifacts) == 2


class TestAggregates:
    def test_list_all_states_fills_missing(self, tmp_path: Path):
        states = list_all_states(tmp_path, ["marketing", "finance", "ops"])
        assert len(states) == 3
        assert {s.dept for s in states} == {"marketing", "finance", "ops"}
        assert all(s.phase == OnboardingPhase.PENDING.value for s in states)

    def test_overall_progress_counts(self, tmp_path: Path):
        names = ["a", "b", "c"]
        # 'a' complete, 'b' in progress, 'c' pending
        for p in [OnboardingPhase.SCOPE_CALIBRATION, OnboardingPhase.DOMAIN_RESEARCH,
                  OnboardingPhase.FOUNDER_INTERVIEW, OnboardingPhase.KB_INGESTION,
                  OnboardingPhase.INTEGRATIONS, OnboardingPhase.CHARTER]:
            begin_phase(tmp_path, "a", p)
            signoff_phase(tmp_path, "a", p, status=SignoffStatus.APPROVED)
        begin_phase(tmp_path, "b", OnboardingPhase.SCOPE_CALIBRATION)
        ensure_state(tmp_path, "c")  # stays pending
        states = list_all_states(tmp_path, names)
        p = overall_progress(states)
        assert p["depts"] == 3
        assert p["complete"] == 1
        assert p["in_progress"] == 1
        assert p["pending"] == 1


class TestScopeCalibrationPrompt:
    def test_locks_primary_to_industry(self):
        from core.dept_onboarding import render_scope_calibration_prompt
        prompt = render_scope_calibration_prompt(
            dept="marketing", dept_label="Marketing",
            company_name="Old Press Wine Co.",
            industry="wine / beverage alcohol",
        )
        assert "wine / beverage alcohol" in prompt
        # Hire framing, not interview framing
        # "hire" or "arrival note" framing — both signal the employee
        # metaphor the prompt is built around.
        assert "hire" in prompt.lower() or "arrival" in prompt.lower()
        # First-person voice rule MUST be explicit — this is the
        # regression guard against the company-voice failure mode.
        assert "first person" in prompt.lower()
        # Secondary is serendipitous, agent-chosen
        assert "serendip" in prompt.lower() or "you do" in prompt.lower()

    def test_secondary_is_ambient_not_operational(self):
        """The prompt must tell the agent NOT to shoehorn its secondary
        into every output. It's background context, not a lens."""
        from core.dept_onboarding import render_scope_calibration_prompt
        prompt = render_scope_calibration_prompt(
            dept="finance", dept_label="Finance",
            company_name="Old Press", industry="wine / beverage alcohol",
        )
        assert "ambient" in prompt.lower()
        assert "shoehorn" in prompt.lower() or "not apply it" in prompt.lower() or "not as a lens" in prompt.lower()

    def test_injects_four_random_serendipity_fields(self):
        """Four random fields from SERENDIPITY_POOL should be injected
        as inspiration to widen the agent's search space beyond the
        mode-collapse defaults."""
        import random
        from core.dept_onboarding import render_scope_calibration_prompt, SERENDIPITY_POOL
        rng = random.Random(42)  # deterministic for test
        prompt = render_scope_calibration_prompt(
            dept="x", dept_label="X", company_name="Y",
            industry="z", rng=rng,
        )
        # At least four distinct pool members appear
        hits = sum(1 for field in SERENDIPITY_POOL if field in prompt)
        assert hits >= 4

    def test_different_seeds_produce_different_inspirations(self):
        """Sanity — two distinct seeds should draw different inspiration
        samples. This is what makes the hire actually serendipitous
        across re-rolls."""
        import random
        from core.dept_onboarding import render_scope_calibration_prompt
        p1 = render_scope_calibration_prompt(
            dept="x", dept_label="X", company_name="Y", industry="z",
            rng=random.Random(1),
        )
        p2 = render_scope_calibration_prompt(
            dept="x", dept_label="X", company_name="Y", industry="z",
            rng=random.Random(99),
        )
        assert p1 != p2


class TestSerendipityPool:
    def test_pool_is_diverse_and_large(self):
        from core.dept_onboarding import SERENDIPITY_POOL
        assert len(SERENDIPITY_POOL) >= 50
        # No duplicates
        assert len(SERENDIPITY_POOL) == len(set(SERENDIPITY_POOL))
        # Every entry non-empty string
        assert all(isinstance(f, str) and f.strip() for f in SERENDIPITY_POOL)

    def test_sample_returns_requested_count(self):
        import random
        from core.dept_onboarding import sample_serendipity
        rng = random.Random(7)
        sample = sample_serendipity(5, rng=rng)
        assert len(sample) == 5
        assert len(set(sample)) == 5  # no duplicates in one sample

    def test_sample_honors_exclusions(self):
        """When excluding known members, the sample should not contain them —
        useful for re-rolling a hire without repeating prior secondaries."""
        import random
        from core.dept_onboarding import sample_serendipity, SERENDIPITY_POOL
        rng = random.Random(11)
        first = sample_serendipity(4, rng=rng, excluding=())
        rng2 = random.Random(11)
        second = sample_serendipity(4, rng=rng2, excluding=first)
        assert not (set(first) & set(second))


class TestDomainResearchBrief:
    def test_renders_with_both_primary_and_secondary(self):
        """Brief must demand research on BOTH primary (industry-locked)
        and secondary (skill-scope-derived)."""
        scope = (
            "## Primary expertise (industry-locked)\n"
            "Finance applied to wine/beverage: TTB compliance, three-tier, DTC.\n\n"
            "## Secondary expertise (founder-calibrated)\n"
            "1. **Agricultural grant finance** — SBA + USDA programs [founder-directed]\n"
        )
        text = render_domain_research_brief(
            dept="finance", dept_label="Finance",
            company_name="Old Press Wine Co.",
            industry="wine / beverage alcohol",
            skill_scope_content=scope,
        )
        # Both scopes appear in the brief
        assert "wine / beverage alcohol" in text
        assert "Agricultural grant finance" in text
        # Template sections both exist
        assert "primary" in text.lower()
        assert "secondary" in text.lower()

    def test_brief_warns_against_primary_only(self):
        """The brief must call out that spending >80% on primary
        defeats the purpose — this is the explicit anti-narrowing guard."""
        text = render_domain_research_brief(
            dept="marketing", dept_label="Marketing",
            company_name="Old Press",
            industry="wine / beverage alcohol",
            skill_scope_content="## Secondary\n1. Regional hospitality",
        )
        assert "80%" in text or "defeated the point" in text.lower() or "ignore the secondary" in text.lower()

    def test_brief_empty_scope_flags_speculation(self):
        """With no skill-scope, the brief tells the manager to flag
        every secondary claim as speculative but still demands full
        primary rigor."""
        text = render_domain_research_brief(
            dept="marketing", dept_label="Marketing",
            company_name="Old Press",
            industry="wine / beverage alcohol",
        )
        assert "speculative" in text.lower()
        # Primary still locked
        assert "wine / beverage alcohol" in text


class TestRerunScopeCalibration:
    """The founder can reset any dept back to SCOPE_CALIBRATION."""

    def test_reset_to_scope_after_approval(self, tmp_path: Path):
        """A dept that advanced past SCOPE_CALIBRATION can be reset
        back to it for a fresh interview. Artifact history is preserved.
        """
        begin_phase(tmp_path, "marketing", OnboardingPhase.SCOPE_CALIBRATION)
        signoff_phase(
            tmp_path, "marketing", OnboardingPhase.SCOPE_CALIBRATION,
            status=SignoffStatus.APPROVED, rating=1,
        )
        begin_phase(tmp_path, "marketing", OnboardingPhase.DOMAIN_RESEARCH)
        # Now reset. This simulates what the webapp re-run endpoint does.
        from dataclasses import replace
        from core.dept_onboarding import load_state, persist_state
        state = load_state(tmp_path, "marketing")
        assert "scope_calibration" in state.completed_phases
        reset_state = replace(
            state,
            phase=OnboardingPhase.SCOPE_CALIBRATION.value,
            completed_phases=tuple(
                p for p in state.completed_phases if p != "scope_calibration"
            ),
        )
        persist_state(tmp_path, reset_state)
        reloaded = load_state(tmp_path, "marketing")
        assert reloaded.phase == OnboardingPhase.SCOPE_CALIBRATION.value
        assert "scope_calibration" not in reloaded.completed_phases
        # Artifacts still preserved
        assert len(reloaded.artifacts) >= 2


class TestWebappOnboarding:
    SLUG = "Old Press Wine Company LLC"

    @pytest.fixture(scope="class")
    def client(self, vault_dir, old_press_dir):
        import os
        os.environ.setdefault("COMPANY_OS_VAULT_DIR", str(vault_dir))
        from webapp.app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_dashboard_route_renders(self, client):
        resp = client.get(f"/c/{self.SLUG}/onboarding")
        assert resp.status_code == 200
        assert b"Department onboarding" in resp.data

    def test_dept_route_renders(self, client):
        resp = client.get(f"/c/{self.SLUG}/onboarding/marketing")
        assert resp.status_code == 200
        assert b"marketing" in resp.data.lower()

    def test_nav_includes_onboarding(self, client):
        resp = client.get(f"/c/{self.SLUG}/")
        assert b">Onboarding<" in resp.data
