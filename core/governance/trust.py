"""Trust aggregation: reads existing rating sources and computes a
normalized -1..+1 trust score per agent with half-life weighting and
a neutral baseline anchor.

Phase 1 reads three write-only rating sources the founder has already
been populating:

  1. Onboarding signoff ratings: `<dept>/onboarding/*.json::artifacts[*].rating`
     Attributed to `manager:<dept>` because that is the agent being
     rated at signoff time.
  2. Scenario-ledger pair verdicts and one-shot ratings:
     `<company>/scenarios/scenarios.jsonl::rating` plus
     `pair_verdict` / `pair_slot` when present. Attributed to
     `manager:<dept>` (dept comes from the ledger row).
  3. Training-example rankings: `<specialist>/training/*-training.md`
     with a `founder_rank` YAML key in the frontmatter or a parsable
     `Rank: N` line. Attributed to the specialist agent.

The aggregator is pure: given a vault path it returns a TrustSnapshot
per agent, fully computed in Python. It also persists each snapshot
to the governance SQLite DB so we get a historical record.

Known limitations (acceptable for Phase 1, documented in the plan):

  - Agent discovery is implicit. An agent with zero ratings does not
    appear. Retired agents continue to appear with their last score.
  - Rating source files grow unbounded. Phase 2 will add an
    invalidation-based cache; for now we recompute on every call.
"""
from __future__ import annotations

import datetime
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from core.governance.models import TrustSnapshot
from core.governance.storage import open_db, persist_trust_snapshot_if_stale

# Minimum gap between persisted snapshots for the same agent. Aggregation
# runs on every page load; without the gap the table accumulates a new
# row per agent per hit. 60 seconds preserves per-minute observability
# while killing row-bloat under normal UI traffic.
SNAPSHOT_STALENESS_SECONDS = 60


_logger = logging.getLogger("governance.trust")

# Half-life for the weight applied to historical rating samples.
HALF_LIFE_DAYS = 30.0

# Fixed weight of the neutral baseline anchor. 1.0 was chosen so the
# baseline counts roughly as much as one fresh (<1 day old) rating, so
# a lone stale rating can't pin the score at an extreme.
NEUTRAL_BASELINE_WEIGHT = 1.0

# Everything older than this is considered "dormant" for UI purposes
# (also gated in the evaluator once Phase 2+ ships).
DORMANT_THRESHOLD_DAYS = 60


# ---------------------------------------------------------------------------
# Sample type
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _RatingSample:
    agent_id: str
    value: float              # normalized to -1..+1
    sample_at: datetime.datetime
    source: str               # "signoff" | "scenario" | "training"


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------
def _parse_iso(ts: str | None) -> datetime.datetime | None:
    if not ts:
        return None
    try:
        s = ts.strip()
        # Normalize trailing Z to +00:00 for fromisoformat parity
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except ValueError:
        return None


def _normalize_rating(raw: float | int) -> float:
    """Map a -2..+2 rating scale into -1..+1."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    v = max(-2.0, min(2.0, v))
    return v / 2.0


def _iter_signoff_samples(company_dir: Path) -> Iterable[_RatingSample]:
    onboarding_dir = company_dir / "onboarding"
    if not onboarding_dir.exists():
        return
    for json_path in sorted(onboarding_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dept = (data.get("dept") or json_path.stem or "").strip()
        if not dept:
            continue
        agent_id = f"manager:{dept}"
        for artifact in data.get("artifacts", []) or []:
            rating = artifact.get("rating")
            if rating is None:
                continue
            sample_at = _parse_iso(artifact.get("created_at"))
            if sample_at is None:
                continue
            yield _RatingSample(
                agent_id=agent_id,
                value=_normalize_rating(rating),
                sample_at=sample_at,
                source="signoff",
            )


def _iter_scenario_samples(company_dir: Path) -> Iterable[_RatingSample]:
    jsonl_path = company_dir / "scenarios" / "scenarios.jsonl"
    if not jsonl_path.exists():
        return
    try:
        lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rating = row.get("rating")
        if rating is None:
            # If rating is None but pair_verdict is set, derive the
            # rating from the verdict. Winners get +1, losers -1, ties 0.
            verdict = (row.get("pair_verdict") or "").strip().lower()
            slot = (row.get("pair_slot") or "").strip().lower()
            if verdict and slot in ("a", "b"):
                if verdict == slot:
                    rating = 1
                elif verdict == "tie":
                    rating = 0
                else:
                    rating = -1
        if rating is None:
            continue
        dept = (row.get("dept") or "").strip()
        if not dept:
            continue
        agent_id = f"manager:{dept}"
        sample_at = (
            _parse_iso(row.get("completed_at"))
            or _parse_iso(row.get("started_at"))
        )
        if sample_at is None:
            continue
        yield _RatingSample(
            agent_id=agent_id,
            value=_normalize_rating(rating),
            sample_at=sample_at,
            source="scenario",
        )


_TRAINING_RANK_RE = re.compile(
    r"^(?:founder_rank|founder-rank|rank)\s*[:=]\s*(-?\d+)",
    flags=re.IGNORECASE | re.MULTILINE,
)


def _iter_training_samples(company_dir: Path) -> Iterable[_RatingSample]:
    # Training files live at <dept>/<specialist>/training/*-training.md
    # (per Phase 10 convention). Walk the tree and pull founder_rank.
    for path in company_dir.rglob("*-training.md"):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _TRAINING_RANK_RE.search(body)
        if not m:
            continue
        try:
            raw_rank = int(m.group(1))
        except ValueError:
            continue
        # path shape: <company>/<dept>/<specialist>/training/xxx-training.md
        try:
            rel = path.relative_to(company_dir)
        except ValueError:
            continue
        parts = rel.parts
        if len(parts) < 4 or parts[-2] != "training":
            continue
        dept = parts[0]
        specialist = parts[1]
        agent_id = f"subagent:{dept}:{specialist}"
        mtime = datetime.datetime.fromtimestamp(
            path.stat().st_mtime, tz=datetime.timezone.utc,
        )
        yield _RatingSample(
            agent_id=agent_id,
            value=_normalize_rating(raw_rank),
            sample_at=mtime,
            source="training",
        )


def _all_samples(company_dir: Path) -> list[_RatingSample]:
    out: list[_RatingSample] = []
    out.extend(_iter_signoff_samples(company_dir))
    out.extend(_iter_scenario_samples(company_dir))
    out.extend(_iter_training_samples(company_dir))
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _weight_for(sample_at: datetime.datetime, now: datetime.datetime) -> float:
    """Half-life weight: w = 0.5 ** (days_since / HALF_LIFE_DAYS)."""
    days = max(0.0, (now - sample_at).total_seconds() / 86400.0)
    return 0.5 ** (days / HALF_LIFE_DAYS)


def _aggregate_for_agent(
    agent_id: str,
    samples: list[_RatingSample],
    *,
    now: datetime.datetime,
) -> TrustSnapshot:
    """Compute a weighted mean with a neutral baseline anchor.

    sum_num = neutral_weight * 0.0 + sum(value_i * weight_i)
    sum_den = neutral_weight + sum(weight_i)
    score = sum_num / sum_den

    Without the neutral baseline the math breaks on a lone stale
    rating (one +2 from 300 days ago produces a weight of ~0.001, but
    because it's the only weight the score still equals 1.0). The
    baseline anchors the mean so stale relative-weight collapses
    correctly toward 0.
    """
    breakdown_num: dict[str, float] = {"signoff": 0.0, "scenario": 0.0, "training": 0.0}
    breakdown_count: dict[str, int] = {"signoff": 0, "scenario": 0, "training": 0}
    breakdown_weight: dict[str, float] = {"signoff": 0.0, "scenario": 0.0, "training": 0.0}

    total_num = 0.0
    total_weight = NEUTRAL_BASELINE_WEIGHT  # neutral value 0.0 contributes 0 to num
    last_sample: datetime.datetime | None = None

    for s in samples:
        w = _weight_for(s.sample_at, now)
        total_num += s.value * w
        total_weight += w
        breakdown_num[s.source] += s.value * w
        breakdown_count[s.source] += 1
        breakdown_weight[s.source] += w
        if last_sample is None or s.sample_at > last_sample:
            last_sample = s.sample_at

    score = total_num / total_weight if total_weight > 0 else 0.0
    # Clamp to the normalization range just in case of FP drift.
    score = max(-1.0, min(1.0, score))

    breakdown = {
        src: {
            "count": breakdown_count[src],
            "weighted_contribution": round(breakdown_num[src], 6),
            "weight_sum": round(breakdown_weight[src], 6),
        }
        for src in ("signoff", "scenario", "training")
    }
    # Also surface what the neutral baseline weight is relative to
    # the total so the UI can show "50% of score comes from the
    # anchor" signals when samples are sparse.
    breakdown["_neutral_baseline_weight"] = NEUTRAL_BASELINE_WEIGHT
    breakdown["_total_weight"] = round(total_weight, 6)

    return TrustSnapshot(
        agent_id=agent_id,
        score=round(score, 6),
        sample_count=sum(breakdown_count.values()),
        last_sample_at=last_sample.isoformat() if last_sample else None,
        computed_at=now.isoformat(),
        breakdown=breakdown,
    )


def aggregate_trust(
    company_dir: Path,
    *,
    now: datetime.datetime | None = None,
    persist: bool = True,
) -> dict[str, TrustSnapshot]:
    """Compute a TrustSnapshot for every agent with at least one sample
    across the three rating sources. Optionally persist snapshots."""
    if now is None:
        now = datetime.datetime.now(tz=datetime.timezone.utc)

    samples = _all_samples(company_dir)
    buckets: dict[str, list[_RatingSample]] = {}
    for s in samples:
        buckets.setdefault(s.agent_id, []).append(s)

    out: dict[str, TrustSnapshot] = {
        agent_id: _aggregate_for_agent(agent_id, agent_samples, now=now)
        for agent_id, agent_samples in sorted(buckets.items())
    }

    if persist and out:
        try:
            conn = open_db(company_dir)
            try:
                for snap in out.values():
                    persist_trust_snapshot_if_stale(
                        conn, snap,
                        min_interval_seconds=SNAPSHOT_STALENESS_SECONDS,
                    )
            finally:
                conn.close()
        except Exception:
            _logger.error("failed to persist trust snapshots", exc_info=True)

    return out


def discover_agent_ids(company_dir: Path) -> list[str]:
    """All agent ids that currently have at least one rating sample.
    Used by the governance UI to populate the per-agent list."""
    ids = set()
    for s in _all_samples(company_dir):
        ids.add(s.agent_id)
    return sorted(ids)


def is_dormant(snapshot: TrustSnapshot, *, now: datetime.datetime | None = None) -> bool:
    """True if the most recent sample is older than the dormancy
    threshold. UI badge only in Phase 1; Phase 2+ gates on this in
    the evaluator."""
    if not snapshot.last_sample_at:
        return True
    last = _parse_iso(snapshot.last_sample_at)
    if last is None:
        return True
    if now is None:
        now = datetime.datetime.now(tz=datetime.timezone.utc)
    return (now - last).total_seconds() / 86400.0 > DORMANT_THRESHOLD_DAYS
