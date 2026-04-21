"""
Phase 14 webapp smoke tests — /office, /awareness, /scenario.

These exercise the three new routes at the surface level: server
returns 200, template renders, the org JSON embedded in /office
contains the expected keys, the scenario page reports seeded briefs
for every onboarded department.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def client(vault_dir, old_press_dir):
    import os
    os.environ.setdefault("COMPANY_OS_VAULT_DIR", str(vault_dir))
    from webapp.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestOfficeRoute:
    SLUG = "Old Press Wine Company LLC"

    def test_returns_200(self, client):
        resp = client.get(f"/c/{self.SLUG}/office")
        assert resp.status_code == 200

    def test_embeds_org_json(self, client):
        resp = client.get(f"/c/{self.SLUG}/office")
        html = resp.data.decode("utf-8")
        assert "org-data" in html
        assert "Orchestrator" in html

    def test_contains_all_active_departments(self, client, company):
        resp = client.get(f"/c/{self.SLUG}/office")
        html = resp.data.decode("utf-8")
        for dept in company.active_departments:
            # The dept name appears as data-id somewhere in the JSON payload.
            assert dept in html, f"{dept} missing from office page"


class TestAwarenessRoute:
    SLUG = "Old Press Wine Company LLC"

    def test_returns_200(self, client):
        resp = client.get(f"/c/{self.SLUG}/awareness")
        assert resp.status_code == 200

    def test_shows_active_count(self, client):
        resp = client.get(f"/c/{self.SLUG}/awareness")
        html = resp.data.decode("utf-8")
        assert "active" in html.lower()


class TestScenarioRoute:
    SLUG = "Old Press Wine Company LLC"

    def test_returns_200(self, client):
        resp = client.get(f"/c/{self.SLUG}/scenario")
        assert resp.status_code == 200

    def test_seeded_scenarios_rendered(self, client):
        resp = client.get(f"/c/{self.SLUG}/scenario")
        html = resp.data.decode("utf-8")
        # Marketing always has at least one seeded scenario.
        assert "Q3 campaign" in html or "Competitive scan" in html

    def test_dept_select_has_active_departments(self, client, company):
        resp = client.get(f"/c/{self.SLUG}/scenario")
        html = resp.data.decode("utf-8")
        for dept in company.active_departments:
            assert f'value="{dept}"' in html


class TestNavBar:
    SLUG = "Old Press Wine Company LLC"

    def test_nav_includes_new_entries(self, client):
        resp = client.get(f"/c/{self.SLUG}/")
        html = resp.data.decode("utf-8")
        for entry in ("Office", "Awareness", "Scenario"):
            assert f">{entry}<" in html, f"Nav missing {entry}"
