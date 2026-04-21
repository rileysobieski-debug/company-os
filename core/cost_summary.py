"""
core/cost_summary.py

Reads a cost log (JSONL, one line per LLM call) and returns structured
daily plus monthly spend rollups. Used by the webapp dashboard to surface
a real-time API spend clock.

Pricing is model-specific and set in `MODEL_PRICING` below. Prices are
USD per million tokens. Cache tokens follow Anthropic's standard
multipliers: cache reads are priced at 10% of the base input rate, and
cache creation at 125%. Unknown models fall back to a conservative
sonnet-tier estimate so an unpriced model never silently reads as free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# Prices in USD per million tokens. Tune as Anthropic updates pricing.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {"in": 15.00, "out": 75.00},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
    "claude-haiku-4-5-20251001": {"in": 1.00, "out": 5.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
}

DEFAULT_PRICING = {"in": 3.00, "out": 15.00}

CACHE_READ_MULTIPLIER = 0.10        # cache reads = 10% of input price
CACHE_CREATE_MULTIPLIER = 1.25      # cache creation = 125% of input price


def _price_for(model: str) -> dict[str, float]:
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # soft-match: strip date suffixes like -20251001
    base = model.rsplit("-", 1)[0] if "-" in model else model
    if base in MODEL_PRICING:
        return MODEL_PRICING[base]
    return DEFAULT_PRICING


def dollars_for(entry: dict) -> float:
    """Compute USD cost for one cost-log entry."""
    model = str(entry.get("model") or "")
    p = _price_for(model)
    in_tok = int(entry.get("input_tokens", 0) or 0)
    out_tok = int(entry.get("output_tokens", 0) or 0)
    cache_r = int(entry.get("cache_read_input_tokens", 0) or 0)
    cache_c = int(entry.get("cache_creation_input_tokens", 0) or 0)
    cost = (
        (in_tok / 1_000_000.0) * p["in"]
        + (out_tok / 1_000_000.0) * p["out"]
        + (cache_r / 1_000_000.0) * p["in"] * CACHE_READ_MULTIPLIER
        + (cache_c / 1_000_000.0) * p["in"] * CACHE_CREATE_MULTIPLIER
    )
    return round(cost, 6)


@dataclass
class SpendBucket:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, entry: dict) -> None:
        self.calls += 1
        self.input_tokens += int(entry.get("input_tokens", 0) or 0)
        self.output_tokens += int(entry.get("output_tokens", 0) or 0)
        self.cost_usd += dollars_for(entry)


@dataclass
class SpendSummary:
    log_exists: bool = False
    log_path: str = ""
    today: SpendBucket = field(default_factory=SpendBucket)
    month: SpendBucket = field(default_factory=SpendBucket)
    lifetime: SpendBucket = field(default_factory=SpendBucket)
    by_tag_today: dict[str, SpendBucket] = field(default_factory=dict)
    by_model_today: dict[str, SpendBucket] = field(default_factory=dict)
    last_call_at: str = ""


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_spend(company_dir: Path, *, now: datetime | None = None) -> SpendSummary:
    """Load `<company_dir>/cost-log.jsonl` and return rolled-up spend.

    Windows are based on UTC date for `today` and UTC year-month for `month`.
    A missing log file returns an empty summary with log_exists=False.
    """
    now = now or datetime.now(timezone.utc)
    today_key = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")
    log_path = Path(company_dir) / "cost-log.jsonl"
    summary = SpendSummary(log_path=str(log_path))
    if not log_path.exists():
        return summary
    summary.log_exists = True
    try:
        raw_text = log_path.read_text(encoding="utf-8")
    except OSError:
        return summary

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(str(entry.get("timestamp") or ""))
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        summary.lifetime.add(entry)
        if ts.strftime("%Y-%m") == month_key:
            summary.month.add(entry)
        if ts.strftime("%Y-%m-%d") == today_key:
            summary.today.add(entry)
            tag = str(entry.get("cost_tag") or "(untagged)")
            model = str(entry.get("model") or "(unknown)")
            summary.by_tag_today.setdefault(tag, SpendBucket()).add(entry)
            summary.by_model_today.setdefault(model, SpendBucket()).add(entry)
        if not summary.last_call_at or ts.isoformat() > summary.last_call_at:
            summary.last_call_at = ts.isoformat()
    return summary


def format_usd(amount: float) -> str:
    """Render a USD amount with appropriate precision for the dashboard."""
    if amount >= 10.0:
        return f"${amount:,.2f}"
    if amount >= 0.10:
        return f"${amount:.3f}"
    return f"${amount:.4f}"
