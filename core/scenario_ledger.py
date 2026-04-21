"""
core/scenario_ledger.py — Phase 14 scenario + rating capture
============================================================

The user's 2026-04-18 directive was to collect iteration data so
Company OS outputs can feed the newsletter. The scenario ledger is
the smallest primitive that captures each run in a form that can
later be aggregated, compared, or exported.

Shape:

    ScenarioRun(
        id,
        dept,
        scenario_name,
        brief,
        job_id,               # webapp JOB_REGISTRY id; nullable
        started_at,
        completed_at,         # nullable until rated
        rating,               # -2..+2 (nullable until rated)
        rating_notes,         # founder free-text
        outcome_summary,      # first ~400 chars of the final synthesis
        tags,                 # free-form
    )

Storage:

    <company_dir>/scenarios/scenarios.jsonl     # append-only
    <company_dir>/scenarios/<id>.md             # human-readable companion

The .md companion is rendered for viewing in the webapp and for the
newsletter pipeline. The .jsonl is the durable record.

Why `-2..+2` and not `0..5`?
  - Matches `TrainingExample.founder_rank` in core.training (Phase 10).
  - Negative scores are useful signal too; 0 is "null / don't use."
  - Consistent with the benchmark-authoring pipeline so ratings can
    promote to benchmarks with no conversion.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping, Optional

SCENARIOS_SUBDIR = "scenarios"
LEDGER_FILENAME = "scenarios.jsonl"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").lower()).strip("-") or "anon"


@dataclass(frozen=True)
class ScenarioRun:
    id: str
    dept: str
    scenario_name: str
    brief: str
    started_at: str
    job_id: str = ""
    completed_at: str = ""
    rating: Optional[int] = None  # -2..+2
    rating_notes: str = ""
    outcome_summary: str = ""   # short preview (800 chars)
    full_output: str = ""        # the complete dispatch synthesis
    plain_summary: str = ""      # operator-translated summary (3-sentence)
    action_items: tuple[str, ...] = field(default_factory=tuple)
    flags: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    # A/B pairing: two runs fired from the same brief share a pair_id.
    # pair_slot is "a" or "b" (arbitrary but stable). pair_verdict records
    # the founder's pick: "a" / "b" / "tie" / "" (not yet judged).
    pair_id: str = ""
    pair_slot: str = ""
    pair_verdict: str = ""

    @property
    def is_rated(self) -> bool:
        return self.rating is not None

    @property
    def is_complete(self) -> bool:
        return bool(self.completed_at)


def _validate_rating(rating: Optional[int]) -> None:
    if rating is None:
        return
    if not isinstance(rating, int):
        raise TypeError("rating must be int or None")
    if rating < -2 or rating > 2:
        raise ValueError(f"rating must be in [-2, 2], got {rating}")


def _make_id(dept: str, scenario_name: str, started_at: str) -> str:
    payload = f"{dept}|{scenario_name}|{started_at}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:10]
    return f"{_slugify(dept)}--{_slugify(scenario_name)[:24]}--{digest}"


def start_run(
    *,
    dept: str,
    scenario_name: str,
    brief: str,
    job_id: str = "",
    tags: tuple[str, ...] = (),
    now: datetime | None = None,
    pair_id: str = "",
    pair_slot: str = "",
) -> ScenarioRun:
    """Construct a fresh ScenarioRun. Pure — caller persists."""
    started = (now or datetime.now(timezone.utc)).isoformat()
    return ScenarioRun(
        id=_make_id(dept, scenario_name, started),
        dept=dept,
        scenario_name=scenario_name or "unnamed",
        brief=brief,
        started_at=started,
        job_id=job_id,
        tags=tags,
        pair_id=pair_id,
        pair_slot=pair_slot,
    )


def start_pair(
    *,
    dept: str,
    scenario_name: str,
    brief: str,
    tags: tuple[str, ...] = (),
    now: datetime | None = None,
) -> tuple[ScenarioRun, ScenarioRun, str]:
    """Construct an A/B pair of ScenarioRuns. Both share `pair_id`;
    one is slot "a", the other "b". Caller persists both + fires two
    concurrent dispatches against the same brief.

    Returns (run_a, run_b, pair_id).
    """
    import uuid
    from datetime import timedelta
    t0 = now or datetime.now(timezone.utc)
    pair_id = f"pair-{uuid.uuid4().hex[:12]}"
    run_a = start_run(
        dept=dept, scenario_name=scenario_name, brief=brief,
        tags=tags, now=t0, pair_id=pair_id, pair_slot="a",
    )
    # Offset by 1ms so the two runs have distinct ids (id is hashed on
    # started_at). Otherwise identical-timestamp dispatches would
    # collide on `_make_id`.
    run_b = start_run(
        dept=dept, scenario_name=scenario_name, brief=brief,
        tags=tags, now=t0 + timedelta(milliseconds=1),
        pair_id=pair_id, pair_slot="b",
    )
    return run_a, run_b, pair_id


def runs_by_pair(company_dir: Path) -> dict[str, list[ScenarioRun]]:
    """Group runs by pair_id. Unpaired runs are skipped."""
    out: dict[str, list[ScenarioRun]] = {}
    for run in load_runs(company_dir):
        if not run.pair_id:
            continue
        out.setdefault(run.pair_id, []).append(run)
    # Order each pair by slot so "a" always comes first
    for rs in out.values():
        rs.sort(key=lambda r: (r.pair_slot, r.started_at))
    return out


def record_pair_verdict(
    company_dir: Path,
    pair_id: str,
    *,
    winner: str,  # "a" | "b" | "tie"
    notes: str = "",
) -> list[ScenarioRun]:
    """Record the founder's A/B pick. Side effects:
      - Both runs get `pair_verdict=winner`.
      - Winning slot gets rating=+1. Losing slot gets rating=-1.
      - "tie" → both get rating=0, pair_verdict="tie".
      - Any existing rating is overwritten (the A/B judgment
        supersedes prior one-shot ratings).
    Returns the updated runs."""
    if winner not in {"a", "b", "tie"}:
        raise ValueError(f"winner must be 'a' | 'b' | 'tie', got {winner!r}")
    runs = load_runs(company_dir)
    updated_ids: list[ScenarioRun] = []
    for run in runs:
        if run.pair_id != pair_id:
            continue
        if winner == "tie":
            new_rating = 0
        elif run.pair_slot == winner:
            new_rating = 1
        else:
            new_rating = -1
        updated = ScenarioRun(
            id=run.id,
            dept=run.dept,
            scenario_name=run.scenario_name,
            brief=run.brief,
            started_at=run.started_at,
            job_id=run.job_id,
            completed_at=run.completed_at,
            rating=new_rating,
            rating_notes=notes or run.rating_notes,
            outcome_summary=run.outcome_summary,
            full_output=run.full_output,
            plain_summary=run.plain_summary,
            action_items=run.action_items,
            flags=run.flags,
            tags=run.tags,
            pair_id=run.pair_id,
            pair_slot=run.pair_slot,
            pair_verdict=winner,
        )
        persist_run(company_dir, updated)
        updated_ids.append(updated)
    return updated_ids


def ledger_path(company_dir: Path) -> Path:
    return company_dir / SCENARIOS_SUBDIR / LEDGER_FILENAME


def md_path(company_dir: Path, run_id: str) -> Path:
    return company_dir / SCENARIOS_SUBDIR / f"{run_id}.md"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _write_jsonl_snapshot(company_dir: Path, entries: list[dict]) -> None:
    path = ledger_path(company_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    tmp.replace(path)


def _render_md(run: ScenarioRun) -> str:
    lines = [
        f"# Scenario — {run.scenario_name}",
        "",
        f"**Department:** {run.dept}  ",
        f"**Started:** {run.started_at}  ",
    ]
    if run.completed_at:
        lines.append(f"**Completed:** {run.completed_at}  ")
    if run.job_id:
        lines.append(f"**Job ID:** `{run.job_id}`  ")
    if run.tags:
        lines.append(f"**Tags:** {', '.join(run.tags)}  ")
    if run.is_rated:
        lines.append(f"**Rating:** {run.rating:+d}  ")
    lines.extend(["", "## Brief", "", run.brief, ""])
    if run.plain_summary:
        lines.extend(["## In plain English", "", run.plain_summary, ""])
    if run.action_items:
        lines.append("## Action items")
        lines.append("")
        for item in run.action_items:
            lines.append(f"- {item}")
        lines.append("")
    if run.flags:
        lines.append("## Flags & uncertainties")
        lines.append("")
        for f in run.flags:
            lines.append(f"- ⚠ {f}")
        lines.append("")
    if run.full_output:
        lines.extend(["## Full agent output", "", run.full_output, ""])
    elif run.outcome_summary:
        lines.extend(["## Outcome (summary)", "", run.outcome_summary, ""])
    if run.rating_notes:
        lines.extend(["## Founder notes", "", run.rating_notes, ""])
    return "\n".join(lines) + "\n"


def _append_md(company_dir: Path, run: ScenarioRun) -> Path:
    path = md_path(company_dir, run.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_md(run), encoding="utf-8")
    return path


def persist_run(company_dir: Path, run: ScenarioRun) -> ScenarioRun:
    """Write a run (or overwrite if same id) to both the JSONL ledger
    and the companion markdown. Idempotent by id."""
    _validate_rating(run.rating)
    existing = {r.id: asdict(r) for r in load_runs(company_dir)}
    existing[run.id] = asdict(run)
    # Sort by started_at for stability.
    ordered = sorted(existing.values(), key=lambda r: r.get("started_at", ""))
    _write_jsonl_snapshot(company_dir, ordered)
    _append_md(company_dir, run)
    return run


def complete_run(
    company_dir: Path,
    run_id: str,
    *,
    outcome_summary: str = "",
    full_output: str = "",
    now: datetime | None = None,
) -> ScenarioRun | None:
    """Mark a run complete. `full_output` carries the entire dispatch
    synthesis (not truncated); `outcome_summary` is the 800-char preview
    kept for fast ledger render. Returns the updated run, or None if
    not found."""
    runs = load_runs(company_dir)
    for i, run in enumerate(runs):
        if run.id != run_id:
            continue
        completed_at = (now or datetime.now(timezone.utc)).isoformat()
        updated = ScenarioRun(
            id=run.id,
            dept=run.dept,
            scenario_name=run.scenario_name,
            brief=run.brief,
            started_at=run.started_at,
            job_id=run.job_id,
            completed_at=completed_at,
            rating=run.rating,
            rating_notes=run.rating_notes,
            outcome_summary=outcome_summary or run.outcome_summary,
            full_output=full_output or run.full_output,
            plain_summary=run.plain_summary,
            action_items=run.action_items,
            flags=run.flags,
            tags=run.tags,
            pair_id=run.pair_id,
            pair_slot=run.pair_slot,
            pair_verdict=run.pair_verdict,
        )
        persist_run(company_dir, updated)
        return updated
    return None


_TRANSLATE_PROMPT_TEMPLATE = """You are translating a multi-agent dispatch output for a small-business owner who is NOT technical. They wrote a brief; the agent(s) returned a long synthesis. Your job is to compress it into three sections.

**Brief the operator wrote:**
{brief}

**Full agent output:**
{output}

Return STRICT JSON with three top-level keys, no markdown, no prose outside the JSON:

{{
  "plain_summary": "2-3 sentences in plain English. What did the agent actually conclude? Write as if explaining to a business owner who has 30 seconds. Avoid jargon. No meta-commentary about 'I dispatched X'.",
  "action_items": ["3-6 short imperative bullets. Each must be something the operator can actually DO this week. Start each with a verb. Be specific (names, numbers, channels). Skip anything that's just 'consider' or 'think about'."],
  "flags": ["0-4 short bullets. Only include actual uncertainties, contested claims, or things the agent was guessing about. Empty array if nothing warrants a flag. No generic 'validate further' boilerplate."]
}}"""


def translate_run(
    company_dir: Path,
    run_id: str,
    *,
    model: str | None = None,
    max_output_chars: int = 24000,
) -> ScenarioRun | None:
    """Run the operator-translation pass over a completed scenario.

    Uses Haiku by default (cheap) and the core.llm_client.single_turn
    wrapper so cost is tracked. Writes back to the ledger. Returns
    the updated ScenarioRun, or None if the run is not found or has
    no full_output to translate.
    """
    runs = load_runs(company_dir)
    target = next((r for r in runs if r.id == run_id), None)
    if target is None:
        return None
    source_text = target.full_output or target.outcome_summary
    if not source_text.strip():
        return None
    # Truncate to max_output_chars to keep the call cheap on pathologically
    # long outputs.
    if len(source_text) > max_output_chars:
        source_text = source_text[:max_output_chars] + "\n\n[...truncated...]"

    # Lazy import — core.llm_client initializes env/config
    from core.llm_client import single_turn

    mdl = model or "claude-haiku-4-5-20251001"
    prompt = _TRANSLATE_PROMPT_TEMPLATE.format(
        brief=target.brief,
        output=source_text,
    )
    response = single_turn(
        messages=[{"role": "user", "content": prompt}],
        model=mdl,
        cost_tag=f"scenario-translate:{target.dept}",
        system="You produce JSON only. No markdown fences, no preamble.",
        max_tokens=1200,
    )
    if response.error:
        return persist_translation(
            company_dir, run_id,
            plain_summary="",
            action_items=(),
            flags=(f"translation failed: {response.error}",),
        )
    summary, actions, flags_out = _parse_translation_json(response.text or "")
    return persist_translation(
        company_dir, run_id,
        plain_summary=summary,
        action_items=actions,
        flags=flags_out,
    )


def _parse_translation_json(raw: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Best-effort JSON extraction. If the model wrapped it in a fence
    or added preamble, recover. If we can't, return the raw as summary
    so the user still sees something."""
    import json as _json
    text = (raw or "").strip()
    # Strip ```json fences if present
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # Find the first '{' and the matching last '}'
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        obj = _json.loads(text)
    except Exception:
        return (raw.strip()[:600], (), ("translation: JSON parse failed — raw output shown in summary",))
    summary = str(obj.get("plain_summary", "")).strip()
    actions = tuple(
        str(item).strip()
        for item in obj.get("action_items", [])
        if str(item).strip()
    )
    flags_out = tuple(
        str(item).strip()
        for item in obj.get("flags", [])
        if str(item).strip()
    )
    return summary, actions, flags_out


def persist_translation(
    company_dir: Path,
    run_id: str,
    *,
    plain_summary: str,
    action_items: tuple[str, ...],
    flags: tuple[str, ...] = (),
) -> ScenarioRun | None:
    """Save the operator-translation (plain summary + action items +
    flags) onto an existing run. Called by `translate_run` after it
    calls the LLM."""
    runs = load_runs(company_dir)
    for run in runs:
        if run.id != run_id:
            continue
        updated = ScenarioRun(
            id=run.id,
            dept=run.dept,
            scenario_name=run.scenario_name,
            brief=run.brief,
            started_at=run.started_at,
            job_id=run.job_id,
            completed_at=run.completed_at,
            rating=run.rating,
            rating_notes=run.rating_notes,
            outcome_summary=run.outcome_summary,
            full_output=run.full_output,
            plain_summary=plain_summary,
            action_items=tuple(action_items),
            flags=tuple(flags),
            tags=run.tags,
            pair_id=run.pair_id,
            pair_slot=run.pair_slot,
            pair_verdict=run.pair_verdict,
        )
        persist_run(company_dir, updated)
        return updated
    return None


def rate_run(
    company_dir: Path,
    run_id: str,
    *,
    rating: int,
    notes: str = "",
) -> ScenarioRun | None:
    """Founder rating + optional notes. Returns updated run or None."""
    _validate_rating(rating)
    runs = load_runs(company_dir)
    for run in runs:
        if run.id != run_id:
            continue
        updated = ScenarioRun(
            id=run.id,
            dept=run.dept,
            scenario_name=run.scenario_name,
            brief=run.brief,
            started_at=run.started_at,
            job_id=run.job_id,
            completed_at=run.completed_at,
            rating=rating,
            rating_notes=notes,
            outcome_summary=run.outcome_summary,
            full_output=run.full_output,
            plain_summary=run.plain_summary,
            action_items=run.action_items,
            flags=run.flags,
            tags=run.tags,
            pair_id=run.pair_id,
            pair_slot=run.pair_slot,
            pair_verdict=run.pair_verdict,
        )
        persist_run(company_dir, updated)
        return updated
    return None


def load_runs(company_dir: Path) -> list[ScenarioRun]:
    path = ledger_path(company_dir)
    if not path.exists():
        return []
    runs: list[ScenarioRun] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        runs.append(
            ScenarioRun(
                id=obj.get("id", ""),
                dept=obj.get("dept", ""),
                scenario_name=obj.get("scenario_name", ""),
                brief=obj.get("brief", ""),
                started_at=obj.get("started_at", ""),
                job_id=obj.get("job_id", ""),
                completed_at=obj.get("completed_at", ""),
                rating=obj.get("rating"),
                rating_notes=obj.get("rating_notes", ""),
                outcome_summary=obj.get("outcome_summary", ""),
                full_output=obj.get("full_output", ""),
                plain_summary=obj.get("plain_summary", ""),
                action_items=tuple(obj.get("action_items", [])),
                flags=tuple(obj.get("flags", [])),
                tags=tuple(obj.get("tags", [])),
                pair_id=obj.get("pair_id", ""),
                pair_slot=obj.get("pair_slot", ""),
                pair_verdict=obj.get("pair_verdict", ""),
            )
        )
    return runs


def iter_runs_reverse(company_dir: Path) -> Iterator[ScenarioRun]:
    """Newest first — matches what the webapp wants for the ledger page."""
    for run in reversed(load_runs(company_dir)):
        yield run


# ---------------------------------------------------------------------------
# Aggregates (for dashboards, not enforcement)
# ---------------------------------------------------------------------------
def render_newsletter_digest(
    runs: list[ScenarioRun],
    *,
    since: datetime | None = None,
    only_rated: bool = True,
) -> str:
    """Render a markdown digest of scenario runs for newsletter consumption.

    Shape is deliberately tight — the newsletter editor can paste the
    output directly, or an agent can summarize further. Each run includes:
    header, brief, rating, notes, outcome summary excerpt.

    `only_rated` defaults True so the export doesn't drag along
    in-progress runs. `since` filters by `started_at`.
    """
    filtered: list[ScenarioRun] = []
    cutoff_iso = since.isoformat() if since else ""
    for run in runs:
        if only_rated and not run.is_rated:
            continue
        if cutoff_iso and run.started_at < cutoff_iso:
            continue
        filtered.append(run)
    if not filtered:
        return "# Scenario digest\n\n_No rated scenarios in window._\n"

    summary = rating_summary(filtered)
    out = [
        "# Scenario digest",
        "",
        f"**Runs:** {len(filtered)}  ",
        f"**Rated:** {summary.get('count', 0)}  ",
        f"**Average rating:** {summary.get('avg', '—')}  ",
    ]
    if summary.get("by_dept"):
        per_dept = ", ".join(
            f"{d}: {v:+.1f}" for d, v in summary["by_dept"].items()
        )
        out.append(f"**By department:** {per_dept}  ")
    if cutoff_iso:
        out.append(f"**Since:** {cutoff_iso}  ")
    out.append("")

    # Newest first — matches the UI
    for run in sorted(filtered, key=lambda r: r.started_at, reverse=True):
        rating_badge = f"{run.rating:+d}" if run.is_rated else "unrated"
        out.append(f"## {run.scenario_name} — `{run.dept}` — {rating_badge}")
        out.append("")
        out.append(f"_Started {run.started_at[:10]}_")
        out.append("")
        out.append("**Brief:**")
        out.append("")
        out.append(run.brief)
        out.append("")
        if run.outcome_summary:
            out.append("**Outcome (first 800 chars):**")
            out.append("")
            out.append(run.outcome_summary)
            out.append("")
        if run.rating_notes:
            out.append("**Founder notes:**")
            out.append("")
            out.append(run.rating_notes)
            out.append("")
        out.append("---")
        out.append("")
    return "\n".join(out)


def rating_summary(runs: list[ScenarioRun]) -> dict[str, object]:
    """Quick descriptive stats over rated runs. Returned as a plain
    dict for templating. Empty when there are no rated runs."""
    rated = [r for r in runs if r.is_rated]
    if not rated:
        return {"count": 0}
    scores = [r.rating for r in rated]
    avg = sum(scores) / len(scores)
    # Per-dept average
    by_dept: dict[str, list[int]] = {}
    for r in rated:
        by_dept.setdefault(r.dept, []).append(r.rating)
    dept_avgs = {d: round(sum(v) / len(v), 2) for d, v in by_dept.items()}
    return {
        "count": len(rated),
        "avg": round(avg, 2),
        "by_dept": dept_avgs,
        "positive": sum(1 for s in scores if s > 0),
        "negative": sum(1 for s in scores if s < 0),
        "neutral": sum(1 for s in scores if s == 0),
    }
