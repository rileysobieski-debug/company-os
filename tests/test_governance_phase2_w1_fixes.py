"""Tests for the three Week 1 immediate-breakage fixes (Phase 2 plan).

Covers:

  1. Path-traversal guard on `webapp.app._company_or_404`.
  2. 60-second staleness guard on `trust_snapshots` inserts.
  3. `_extract_job_id_from_response` helper in `core.governance.retrolog`.

No LLM calls. No network. Uses a temporary vault so we never touch the
live founder vault.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. Path-traversal guard
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    """A tiny vault with one real company folder. Sets the env var so
    `core.env.get_vault_dir` points at it for the duration of the test."""
    vault = tmp_path / "vault"
    vault.mkdir()
    real = vault / "real-company"
    real.mkdir()
    # Minimal config.json + context.md so load_company_safe returns a value.
    (real / "config.json").write_text(
        json.dumps({"company": {"name": "Real Co LLC", "legal_name": "Real Co LLC"}}),
        encoding="utf-8",
    )
    (real / "context.md").write_text("# Real Co\n\nFixture company.\n", encoding="utf-8")
    # Outside-the-vault neighbor that must never be reachable.
    neighbor = tmp_path / "secret"
    neighbor.mkdir()
    (neighbor / "config.json").write_text(
        json.dumps({"company": {"name": "Secret LLC"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMPANY_OS_VAULT_DIR", str(vault))
    return vault


def _client(monkeypatch):
    # Import lazily so the env var is in place before core.env reads it.
    from webapp.app import app
    app.config["TESTING"] = True
    return app.test_client()


def test_traversal_dotdot_rejected(temp_vault, monkeypatch):
    client = _client(monkeypatch)
    # `..` should never escape to a sibling dir.
    resp = client.get("/c/../secret/")
    # Flask will normalize some of these but any escape attempt ends in 404
    # or 308-redirect-to-404; the critical property is we never serve the
    # neighbor's content.
    assert resp.status_code in (301, 308, 404)
    if resp.status_code in (301, 308):
        # Follow the redirect and confirm it still lands on a 404.
        loc = resp.headers.get("Location", "")
        # After normalization Flask may redirect to `/secret/`; re-issue.
        resp2 = client.get(loc)
        assert resp2.status_code == 404


def test_traversal_absolute_path_rejected(temp_vault, monkeypatch):
    client = _client(monkeypatch)
    # An absolute path in the slug slot is nonsense and must 404.
    resp = client.get("/c/%2Fetc%2Fpasswd/")
    assert resp.status_code == 404


def test_traversal_backslash_rejected(temp_vault, monkeypatch):
    client = _client(monkeypatch)
    # Windows separator inside a slug must be rejected.
    resp = client.get("/c/real%5Ccompany/")
    assert resp.status_code == 404


def test_real_company_still_loads(temp_vault, monkeypatch):
    # Call the guard directly rather than the full dashboard route, which
    # pulls in cost-log and department loaders the minimal fixture does
    # not satisfy. The guard itself is what this test covers.
    from webapp.app import _company_or_404
    company, departments = _company_or_404("real-company")
    assert company is not None
    assert Path(company.company_dir).name == "real-company"


def test_empty_slug_rejected(temp_vault, monkeypatch):
    from webapp.app import _company_or_404
    from werkzeug.exceptions import NotFound
    for bad in ("", ".", "..", "/", "\\", "real\x00inject"):
        with pytest.raises(NotFound):
            _company_or_404(bad)


# ---------------------------------------------------------------------------
# 2. trust_snapshots staleness guard
# ---------------------------------------------------------------------------
def test_persist_trust_snapshot_if_stale_skips_fresh_row(tmp_path):
    from core.governance.models import TrustSnapshot
    from core.governance.storage import (
        open_db, persist_trust_snapshot_if_stale, latest_trust_snapshot,
    )

    company_dir = tmp_path / "co"
    company_dir.mkdir()
    conn = open_db(company_dir)
    try:
        base = datetime.datetime(2026, 4, 22, 12, 0, 0, tzinfo=datetime.timezone.utc)
        snap1 = TrustSnapshot(
            agent_id="manager:finance",
            score=0.5,
            sample_count=2,
            last_sample_at=base.isoformat(),
            computed_at=base.isoformat(),
            breakdown={},
        )
        wrote = persist_trust_snapshot_if_stale(conn, snap1, min_interval_seconds=60)
        assert wrote is True

        # 30s later: must be skipped.
        snap2 = TrustSnapshot(
            agent_id="manager:finance",
            score=0.6,
            sample_count=3,
            last_sample_at=base.isoformat(),
            computed_at=(base + datetime.timedelta(seconds=30)).isoformat(),
            breakdown={},
        )
        wrote = persist_trust_snapshot_if_stale(conn, snap2, min_interval_seconds=60)
        assert wrote is False

        latest = latest_trust_snapshot(conn, "manager:finance")
        assert latest is not None
        assert latest.score == pytest.approx(0.5)

        # 90s later: must write.
        snap3 = TrustSnapshot(
            agent_id="manager:finance",
            score=0.7,
            sample_count=4,
            last_sample_at=base.isoformat(),
            computed_at=(base + datetime.timedelta(seconds=90)).isoformat(),
            breakdown={},
        )
        wrote = persist_trust_snapshot_if_stale(conn, snap3, min_interval_seconds=60)
        assert wrote is True

        latest = latest_trust_snapshot(conn, "manager:finance")
        assert latest.score == pytest.approx(0.7)
    finally:
        conn.close()


def test_aggregate_trust_does_not_balloon_rows(tmp_path):
    """Hit aggregate_trust multiple times in quick succession, confirm
    row count stays at 1 per agent rather than growing by 1 per call."""
    from core.governance.trust import aggregate_trust
    from core.governance.storage import open_db

    company_dir = tmp_path / "co"
    (company_dir / "onboarding").mkdir(parents=True)
    (company_dir / "onboarding" / "finance.json").write_text(
        json.dumps({
            "dept": "finance",
            "artifacts": [
                {
                    "rating": 2,
                    "created_at": "2026-04-20T12:00:00Z",
                    "path": "finance/skill-scope.md",
                    "phase": "scope_calibration",
                    "signoff": "approved",
                    "job_id": "x",
                    "notes": "",
                },
            ],
        }),
        encoding="utf-8",
    )

    for _ in range(5):
        aggregate_trust(company_dir)

    conn = open_db(company_dir)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS n FROM trust_snapshots WHERE agent_id = ?",
            ("manager:finance",),
        )
        row = cur.fetchone()
        assert row["n"] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. _extract_job_id_from_response helper
# ---------------------------------------------------------------------------
def test_extract_job_id_from_string_url():
    from core.governance.retrolog import _extract_job_id_from_response
    assert _extract_job_id_from_response("/c/acme/j/abc-123") == "abc-123"
    assert _extract_job_id_from_response("/c/acme/j/abc-123/detail") == "abc-123"
    assert _extract_job_id_from_response("https://x.example/c/acme/j/job_42") == "job_42"


def test_extract_job_id_from_response_with_location_header():
    from core.governance.retrolog import _extract_job_id_from_response

    class FakeResp:
        def __init__(self, location):
            self.headers = {"Location": location}
            self.location = location

    assert _extract_job_id_from_response(FakeResp("/c/acme/j/xyz")) == "xyz"


def test_extract_job_id_from_tuple_response():
    from core.governance.retrolog import _extract_job_id_from_response

    class FakeResp:
        def __init__(self, location):
            self.headers = {"Location": location}
            self.location = location

    assert _extract_job_id_from_response((FakeResp("/c/a/j/abc"), 302)) == "abc"


def test_extract_job_id_returns_none_on_unmatched():
    from core.governance.retrolog import _extract_job_id_from_response
    assert _extract_job_id_from_response(None) is None
    assert _extract_job_id_from_response("/c/acme/dashboard") is None
    assert _extract_job_id_from_response(object()) is None
    assert _extract_job_id_from_response("") is None
