"""
A/B pair lifecycle tests for the scenario ledger.

Covers:
  - start_pair: two runs, distinct ids, same pair_id, slots a + b
  - record_pair_verdict: winner +1, loser -1, tie → 0/0
  - runs_by_pair: grouping + ordering
  - rating survives round-trip through load_runs
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.scenario_ledger import (
    load_runs,
    persist_run,
    record_pair_verdict,
    runs_by_pair,
    start_pair,
)


class TestStartPair:
    def test_returns_two_runs_sharing_pair_id(self, tmp_path: Path):
        a, b, pair_id = start_pair(
            dept="marketing", scenario_name="test", brief="brief x",
        )
        assert a.pair_id == pair_id
        assert b.pair_id == pair_id
        assert a.pair_slot == "a"
        assert b.pair_slot == "b"
        assert a.id != b.id

    def test_persisted_pair_roundtrips(self, tmp_path: Path):
        a, b, pair_id = start_pair(
            dept="marketing", scenario_name="test", brief="brief y",
        )
        persist_run(tmp_path, a)
        persist_run(tmp_path, b)
        loaded = load_runs(tmp_path)
        assert len(loaded) == 2
        assert {r.pair_slot for r in loaded} == {"a", "b"}
        assert all(r.pair_id == pair_id for r in loaded)


class TestRecordVerdict:
    def _fresh_pair(self, tmp_path: Path) -> str:
        a, b, pid = start_pair(
            dept="finance", scenario_name="test", brief="brief z",
        )
        persist_run(tmp_path, a)
        persist_run(tmp_path, b)
        return pid

    def test_a_wins(self, tmp_path: Path):
        pid = self._fresh_pair(tmp_path)
        record_pair_verdict(tmp_path, pid, winner="a", notes="A was crisper")
        runs = load_runs(tmp_path)
        a = next(r for r in runs if r.pair_slot == "a")
        b = next(r for r in runs if r.pair_slot == "b")
        assert a.rating == 1
        assert b.rating == -1
        assert a.pair_verdict == "a"
        assert b.pair_verdict == "a"
        assert a.rating_notes == "A was crisper"

    def test_b_wins(self, tmp_path: Path):
        pid = self._fresh_pair(tmp_path)
        record_pair_verdict(tmp_path, pid, winner="b")
        runs = load_runs(tmp_path)
        a = next(r for r in runs if r.pair_slot == "a")
        b = next(r for r in runs if r.pair_slot == "b")
        assert a.rating == -1
        assert b.rating == 1

    def test_tie(self, tmp_path: Path):
        pid = self._fresh_pair(tmp_path)
        record_pair_verdict(tmp_path, pid, winner="tie")
        runs = load_runs(tmp_path)
        assert all(r.rating == 0 for r in runs)
        assert all(r.pair_verdict == "tie" for r in runs)

    def test_invalid_winner_raises(self, tmp_path: Path):
        pid = self._fresh_pair(tmp_path)
        with pytest.raises(ValueError):
            record_pair_verdict(tmp_path, pid, winner="c")


class TestRunsByPair:
    def test_groups_by_pair_id(self, tmp_path: Path):
        a1, b1, p1 = start_pair(dept="m", scenario_name="s1", brief="b1")
        a2, b2, p2 = start_pair(dept="m", scenario_name="s2", brief="b2")
        for r in (a1, b1, a2, b2):
            persist_run(tmp_path, r)
        pairs = runs_by_pair(tmp_path)
        assert set(pairs.keys()) == {p1, p2}
        assert len(pairs[p1]) == 2
        assert len(pairs[p2]) == 2

    def test_slot_a_first(self, tmp_path: Path):
        a, b, pid = start_pair(dept="m", scenario_name="s", brief="b")
        persist_run(tmp_path, b)  # insert out of order
        persist_run(tmp_path, a)
        pairs = runs_by_pair(tmp_path)
        assert pairs[pid][0].pair_slot == "a"
        assert pairs[pid][1].pair_slot == "b"

    def test_ignores_unpaired_runs(self, tmp_path: Path):
        from core.scenario_ledger import start_run
        solo = start_run(dept="m", scenario_name="solo", brief="b")
        persist_run(tmp_path, solo)
        assert runs_by_pair(tmp_path) == {}


class TestWebappAB:
    SLUG = "Old Press Wine Company LLC"

    @pytest.fixture(scope="class")
    def client(self, vault_dir, old_press_dir):
        import os
        os.environ.setdefault("COMPANY_OS_VAULT_DIR", str(vault_dir))
        from webapp.app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_pairs_route_renders(self, client):
        resp = client.get(f"/c/{self.SLUG}/ledger/pairs")
        assert resp.status_code == 200
        assert b"A/B pairs" in resp.data

    def test_compare_nonexistent_pair_404s(self, client):
        resp = client.get(f"/c/{self.SLUG}/ledger/compare/nonexistent-pair")
        assert resp.status_code == 404
