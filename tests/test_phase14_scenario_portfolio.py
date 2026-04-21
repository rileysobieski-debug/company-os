"""
Scenario portfolio structure tests.

Enforces the "every dept has one of each type" invariant so future
edits to the portfolio can't silently lose test coverage for a
department.
"""
from __future__ import annotations

import pytest

from core.scenario_portfolio import (
    ScenarioTemplate,
    ScenarioType,
    all_templates,
    department_coverage,
    templates_for_department,
    templates_of_type,
    as_webapp_groups,
)


EXPECTED_DEPARTMENTS = {
    "marketing", "finance", "operations", "product-design",
    "community", "editorial", "data", "ai-workflow", "ai-architecture",
}

EXPECTED_TYPES = {t.value for t in ScenarioType}


class TestPortfolioShape:
    def test_has_at_least_one_template(self):
        assert len(all_templates()) > 0

    def test_ids_are_unique(self):
        ids = [t.id for t in all_templates()]
        assert len(ids) == len(set(ids)), "duplicate scenario ids"

    def test_every_expected_dept_present(self):
        have = {t.department for t in all_templates()}
        missing = EXPECTED_DEPARTMENTS - have
        assert missing == set(), f"departments missing: {missing}"

    def test_every_dept_has_one_of_each_type(self):
        coverage = department_coverage()
        for dept in EXPECTED_DEPARTMENTS:
            for t in EXPECTED_TYPES:
                assert coverage[dept].get(t, 0) >= 1, (
                    f"{dept} missing scenario of type {t!r}"
                )

    def test_briefs_are_nontrivial(self):
        # Calibration briefs are intentionally short — their point is
        # to BE under-specified so the agent is forced to demand
        # context. Every non-calibration brief must be substantive.
        for t in all_templates():
            min_length = 15 if t.scenario_type is ScenarioType.CALIBRATION else 40
            assert len(t.brief) >= min_length, (
                f"{t.id} brief too short ({len(t.brief)} < {min_length})"
            )
            assert t.what_to_watch, f"{t.id} missing what_to_watch"

    def test_cadence_values_are_valid(self):
        allowed = {"daily", "weekly", "monthly", "ad-hoc"}
        for t in all_templates():
            assert t.cadence in allowed, f"{t.id} bad cadence: {t.cadence}"


class TestAccessors:
    def test_templates_for_department(self):
        mkt = templates_for_department("marketing")
        assert len(mkt) >= 5
        assert all(t.department == "marketing" for t in mkt)

    def test_templates_for_unknown_dept_empty(self):
        assert templates_for_department("nonexistent") == []

    def test_templates_of_type_returns_expected(self):
        convs = templates_of_type(ScenarioType.CONVERGENCE)
        # Every dept contributes one convergence → >= 9
        assert len(convs) >= 9
        assert all(t.scenario_type is ScenarioType.CONVERGENCE for t in convs)


class TestWebappShape:
    def test_as_webapp_groups_produces_dict_structure(self):
        depts = [
            {"name": "marketing", "display_name": "Marketing"},
            {"name": "finance", "display_name": "Finance"},
        ]
        groups = as_webapp_groups(depts)
        assert len(groups) == 2
        for g in groups:
            assert "dept" in g
            assert "label" in g
            assert "briefs" in g
            assert len(g["briefs"]) >= 5
            for b in g["briefs"]:
                assert set(b.keys()) >= {"id", "name", "brief", "scenario_type", "what_to_watch", "cadence"}

    def test_unknown_dept_gets_empty_briefs(self):
        depts = [{"name": "bogus", "display_name": "Bogus"}]
        groups = as_webapp_groups(depts)
        assert groups[0]["briefs"] == []
