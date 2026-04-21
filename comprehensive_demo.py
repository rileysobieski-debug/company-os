"""
Company OS — Comprehensive Demo Runner
======================================
Generates a full demo dossier showing every layer of the system in action:

  1. DEPARTMENT DEMOS — every active department receives a real, scoped brief
     and produces an artifact demonstrating its specialists' understanding of
     the company. Output → `demo-artifacts/depts/{dept}-demo.md`.

  2. ORCHESTRATOR SYNTHESIS — the Orchestrator reads all department demos and
     produces ONE consolidated readiness report.
     Output → `demo-artifacts/orchestrator-synthesis.md`.

  3. BOARD DELIBERATION — the board debates a strategic question, with the
     full department dossier injected as context. Each board member can also
     query managers live. The Orchestrator silently observes and produces a
     summary. Output → `board/meetings/{date}-{topic-slug}.md`.

  4. INDEX — a `demo-artifacts/INDEX.md` linking everything together.

This module is reusable: the web GUI calls into the same functions
(`run_department_demo`, `run_orchestrator_synthesis`, `run_board_deliberation`)
to expose them as one-click actions.

CLI usage:
  python company-os/comprehensive_demo.py --company "Old Press Wine Company LLC"
  python company-os/comprehensive_demo.py --company "..." --depts marketing finance
  python company-os/comprehensive_demo.py --company "..." --skip-board

The runner is idempotent at the artifact level — if `--force` is not passed,
existing dept demo files are kept and skipped.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# UTF-8 fix for Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from core.env import get_vault_dir, load_env
load_env()

import anthropic

from core.board import convene_board
from core.company import CompanyConfig, load_company
from core.managers.base import dispatch_manager
from core.managers.loader import DepartmentConfig, load_departments
from core.vertical_pack import (
    VerticalPack,
    load_vertical_pack,
    render_dept_brief,
)


# ---------------------------------------------------------------------------
# Phase 13.2: department briefs come from the active vertical pack.
# =================================================================
# Previous revisions shipped a hardcoded `DEPT_BRIEFS` dict with Old
# Press-specific text baked in. Plan §13 Phase 13 line 690 required
# that content be archived (not re-embedded) and the runner rewritten
# as a thin vertical-agnostic dispatcher driven by the pack.
#
#   - Generic templates:  verticals/<vertical>/dept_briefs.yaml
#   - Archived originals: comprehensive_demo_legacy.py (not imported)
#
# The helper below resolves briefs in precedence order:
#   1. explicit `brief_override` passed to the runner (per-dept CLI wins)
#   2. vertical pack template, rendered with company context
#   3. the pack's default_brief if the dept has no named template
#   4. a minimal fallback string (used only when no pack is loadable)
# ---------------------------------------------------------------------------
DEFAULT_FALLBACK_BRIEF = (
    "DEMO BRIEF: Demonstrate your department's role and value to "
    "{company_name} in the context of the constraints supplied in the "
    "company's config.json, founder profile, and priorities.\n\n"
    "Produce a real artifact (not a meta-description of what your "
    "department would do): a memo, plan, framework, or analysis specific "
    "to the company's current priorities. Use your specialists. Show "
    "role understanding by what you produce, not by claiming it."
)


def _fallback_brief(company: CompanyConfig) -> str:
    return DEFAULT_FALLBACK_BRIEF.format(company_name=company.name)


def _resolve_brief(
    company: CompanyConfig,
    dept_name: str,
    pack: VerticalPack | None,
    brief_override: str | None,
) -> str:
    """Resolve a demo brief for `dept_name` using the precedence above."""
    if brief_override:
        return brief_override
    if pack is not None:
        try:
            template = pack.brief_for(dept_name)
        except KeyError:
            pass
        else:
            return render_dept_brief(template, company=company)
    return _fallback_brief(company)


# Legacy alias — prior revisions exported a hardcoded dict of Old
# Press-specific briefs as DEPT_BRIEFS. The vertical-pack refactor
# (Phase 13.2) moved the templates into verticals/<name>/dept_briefs.yaml.
# DEPT_BRIEFS is preserved as an empty dict so any third-party caller
# expecting the attribute does not crash; the fallback path handles
# resolution. Archived content: comprehensive_demo_legacy.py.
DEPT_BRIEFS: dict[str, str] = {}



# ---------------------------------------------------------------------------
# Per-department demo runner
# ---------------------------------------------------------------------------
def _ensure_demo_dirs(company: CompanyConfig) -> tuple[Path, Path]:
    """Create and return (root, depts_dir)."""
    root = company.company_dir / "demo-artifacts"
    depts = root / "depts"
    depts.mkdir(parents=True, exist_ok=True)
    return root, depts


def _demo_artifact_path(company: CompanyConfig, dept_name: str) -> Path:
    _, depts = _ensure_demo_dirs(company)
    return depts / f"{dept_name}-demo.md"


def run_department_demo(
    company: CompanyConfig,
    dept: DepartmentConfig,
    departments: list[DepartmentConfig],
    *,
    force: bool = False,
    brief_override: str | None = None,
    pack: VerticalPack | None = None,
) -> dict[str, object]:
    """Run one department's demo dispatch.

    Returns a dict with: dept, status (generated|skipped|error), path, summary,
    specialists_called, error (if any).

    If `force=False` and the artifact already exists, skips and returns the
    existing path. `pack` is the active vertical pack; when None, the runner
    falls back to the generic `DEFAULT_FALLBACK_BRIEF`.
    """
    out_path = _demo_artifact_path(company, dept.name)

    if out_path.exists() and not force:
        return {
            "dept": dept.name,
            "status": "skipped",
            "path": str(out_path),
            "summary": "(existing artifact preserved; pass force=True to regenerate)",
            "specialists_called": [],
        }

    brief = _resolve_brief(company, dept.name, pack, brief_override)
    print(f"\n[demo] Department: {dept.display_name}")
    print(f"[demo]   specialists available: {[s.name for s in dept.specialists]}")
    print(f"[demo]   dispatching brief ({len(brief)} chars)...")
    t0 = time.time()

    try:
        result = dispatch_manager(
            dept.name, brief, company, departments=departments
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[demo]   ERROR: {exc}")
        return {
            "dept": dept.name,
            "status": "error",
            "path": "",
            "summary": "",
            "specialists_called": [],
            "error": str(exc),
        }

    elapsed = time.time() - t0
    print(f"[demo]   complete in {elapsed:.1f}s — specialists called: {result.specialists_called}")

    # Compose the artifact file
    md = "\n".join([
        f"# {dept.display_name} — Department Demo",
        f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} for {company.name}_",
        f"_Run time: {elapsed:.1f}s — Specialists called: {', '.join(str(s) for s in result.specialists_called) or '(none)'}_",
        "",
        "## Brief",
        "```",
        brief,
        "```",
        "",
        "## Manager Synthesis",
        "",
        result.final_text or "(empty response)",
        "",
        "---",
        "",
        "## Tool Trace",
        "",
        f"- Total assistant messages: {result.raw_messages_count}",
        f"- Tool calls made: {len(result.tool_calls)}",
        f"- Specialists dispatched: {result.specialists_called}",
    ])
    out_path.write_text(md, encoding="utf-8")
    print(f"[demo]   artifact saved → {out_path.relative_to(company.company_dir)}")

    return {
        "dept": dept.name,
        "status": "generated",
        "path": str(out_path),
        "summary": result.final_text[:500] if result.final_text else "",
        # Coerce SpecialistResult → str at the dict boundary. Chunk 1b.4.
        "specialists_called": [str(s) for s in result.specialists_called],
        "elapsed_seconds": round(elapsed, 1),
    }


DEFAULT_VERTICAL = "wine-beverage"


def run_all_department_demos(
    company: CompanyConfig,
    departments: list[DepartmentConfig],
    *,
    only: list[str] | None = None,
    force: bool = False,
    pack: VerticalPack | None = None,
    vertical: str = DEFAULT_VERTICAL,
) -> list[dict[str, object]]:
    """Run demos for every department (or a filtered subset).

    Departments run sequentially to keep API rate manageable on a single
    machine. Each result is appended even if some fail.

    Pack resolution: if `pack` is supplied, use it; otherwise try to load
    `vertical` (default: "wine-beverage"). A missing pack falls through
    to the module-level fallback brief without failing — the GUI's
    'Run full demo' button keeps working even on a company with no
    configured vertical.
    """
    if pack is None:
        try:
            pack = load_vertical_pack(vertical)
        except FileNotFoundError:
            pack = None

    targets = departments
    if only:
        targets = [d for d in departments if d.name in only]
        missing = [n for n in only if n not in {d.name for d in departments}]
        for m in missing:
            print(f"[demo] WARNING: requested dept '{m}' not found in active depts.")

    results: list[dict[str, object]] = []
    for dept in targets:
        result = run_department_demo(
            company, dept, departments, force=force, pack=pack
        )
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Orchestrator synthesis — reads all dept demos, produces one report
# ---------------------------------------------------------------------------
SYNTHESIS_MODEL = "claude-opus-4-6"
SYNTHESIS_MAX_TOKENS = 4096


_SYNTHESIS_SYSTEM = """You are the Orchestrator — Chairman of the Board — for {company_name}.

Each of your department managers just produced a demo artifact responding to a
scoped brief. You will read all of them and synthesize a single Riley-facing
readiness report.

You have NOT done the underlying work — your value is the cross-department view.
You see what aligns, what conflicts, what's missing, and what the next move is.
Speak directly. Do not flatter the departments. Do not pad.

=== COMPANY CONTEXT ===
{company_context}

{settled_convictions}

{hard_constraints}

{priorities}
"""


_SYNTHESIS_USER = """=== DEPARTMENT DEMO ARTIFACTS ===

{dept_blocks}

=== YOUR TASK ===
Produce a synthesis report titled "Cross-Department Readiness Report — {company_name}"
in this exact structure:

## Executive Summary
3-4 sentences. The state of company readiness as the demos reveal it. Be candid.

## What Each Department Demonstrated
A 2-4 sentence assessment per department. NOT a recap of their output — your
*assessment* of how well they understood their role and the company. Identify
the strongest specific point each one made.

## Cross-Department Alignment
Where 2+ departments converged on the same point. Name the departments.

## Cross-Department Conflicts
Where departments contradict each other or hold incompatible assumptions.
Be specific. Name the departments and the substance of the conflict.

## What is Still Missing
Gaps the dossier collectively does not address. Strategic, operational, or
informational. Be specific.

## Recommended Next Operational Moves
Max 5 bulleted items. Each: dept, action, why it's next.

## Decisions That Belong to Riley
Max 3. Each: the tradeoff named, the cost of delay.

## What I Would Take to the Board
ONE strategic question worth a board deliberation, with the framing.

Length: 1200-2000 words. Direct. No hype. No false consensus."""


def run_orchestrator_synthesis(
    company: CompanyConfig,
    dept_results: list[dict[str, object]],
    *,
    out_path: Path | None = None,
) -> dict[str, object]:
    """Read all dept demo artifacts and produce one consolidated report.

    Returns: {path, summary (full text), tokens_used (if available)}.
    """
    print("\n[demo] Orchestrator synthesizing cross-department readiness report...")
    root, _ = _ensure_demo_dirs(company)
    if out_path is None:
        out_path = root / "orchestrator-synthesis.md"

    # Read each dept artifact
    dept_blocks: list[str] = []
    for r in dept_results:
        path_str = r.get("path") or ""
        if not path_str:
            continue
        p = Path(str(path_str))
        if not p.exists():
            continue
        content = p.read_text(encoding="utf-8")
        # Strip the front-matter / brief block so the orchestrator sees the
        # actual synthesis without re-reading the brief verbatim
        # (but keep the dept name as a header)
        dept_blocks.append(f"\n### Department: {r['dept']}\n\n{content}\n")

    if not dept_blocks:
        msg = "No department artifacts available to synthesize."
        print(f"[demo]   {msg}")
        out_path.write_text(f"# Orchestrator Synthesis\n\n{msg}\n", encoding="utf-8")
        return {"path": str(out_path), "summary": msg}

    client = anthropic.Anthropic()
    system = _SYNTHESIS_SYSTEM.format(
        company_name=company.name,
        company_context=company.context.strip(),
        settled_convictions=company.settled_convictions_block(),
        hard_constraints=company.hard_constraints_block(),
        priorities=company.priorities_block(),
    )
    user = _SYNTHESIS_USER.format(
        company_name=company.name,
        dept_blocks="\n---\n".join(dept_blocks),
    )

    t0 = time.time()
    response = client.messages.create(
        model=SYNTHESIS_MODEL,
        max_tokens=SYNTHESIS_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    elapsed = time.time() - t0
    text = "\n".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    ).strip()

    md = "\n".join([
        f"# Cross-Department Readiness Report — {company.name}",
        f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by the Orchestrator_",
        f"_Synthesizing {len(dept_blocks)} department artifact(s) — generation time {elapsed:.1f}s_",
        "",
        "---",
        "",
        text,
        "",
        "---",
        "",
        "## Source Artifacts",
        "",
    ] + [
        f"- [{r['dept']}](depts/{Path(str(r['path'])).name}) — {r.get('status', 'unknown')}"
        for r in dept_results if r.get("path")
    ])
    out_path.write_text(md, encoding="utf-8")
    print(f"[demo]   synthesis saved → {out_path.relative_to(company.company_dir)}")

    return {
        "path": str(out_path),
        "summary": text,
        "elapsed_seconds": round(elapsed, 1),
    }


# ---------------------------------------------------------------------------
# Board deliberation — references the dept dossier
# ---------------------------------------------------------------------------
DEFAULT_BOARD_TOPIC = (
    "Given the readiness dossier just produced by every department, is "
    "Old Press Wine Company ready to commit to its operating model and start "
    "the path to first commercial sale — or are there gaps that must close "
    "before that commitment can be made?\n\n"
    "Specifically: pick ONE operating model (own bonded winery / alternating "
    "proprietor / négociant / private-label) you would defend as the right "
    "first move from a Maine base. State your reasoning and the strongest "
    "objection to your choice."
)


def run_board_deliberation(
    company: CompanyConfig,
    departments: list[DepartmentConfig],
    *,
    topic: str = DEFAULT_BOARD_TOPIC,
    include_dept_dossier: bool = True,
    session_dir: Path | None = None,
) -> dict[str, object]:
    """Run a board deliberation that references the just-produced dept dossier.

    The dossier is injected into the topic itself so each board member sees the
    department artifacts as part of their brief. Members can also live-query
    managers via the existing query_manager tool.

    The Orchestrator silently observes (via convene_board's observer_summary)
    and produces a Riley-facing summary.

    Returns: {topic, summary_path, transcript_path, observer_summary}.
    """
    print("\n[demo] Convening board deliberation (with dept dossier injected)...")

    augmented_topic = topic
    if include_dept_dossier:
        root, depts_dir = _ensure_demo_dirs(company)
        synthesis_path = root / "orchestrator-synthesis.md"
        dossier_lines = ["", "=== DEPARTMENT DOSSIER REFERENCE ===",
                         "Each department recently produced a demo artifact (paths below). "
                         "You may also query any manager live via query_manager() for "
                         "follow-up. Treat this dossier as the current baseline of "
                         "company knowledge:"]
        if synthesis_path.exists():
            dossier_lines.append(f"  - Orchestrator synthesis: {synthesis_path.relative_to(company.company_dir)}")
        for dept in departments:
            artifact = depts_dir / f"{dept.name}-demo.md"
            if artifact.exists():
                dossier_lines.append(f"  - {dept.display_name}: {artifact.relative_to(company.company_dir)}")

        # Also embed the orchestrator synthesis text directly so board members
        # see it without needing file reads (which they don't have).
        if synthesis_path.exists():
            synth_text = synthesis_path.read_text(encoding="utf-8")
            dossier_lines.append("")
            dossier_lines.append("=== ORCHESTRATOR'S READINESS REPORT (full text) ===")
            dossier_lines.append(synth_text)

        augmented_topic = topic + "\n" + "\n".join(dossier_lines)

    debate = convene_board(
        topic=augmented_topic,
        company=company,
        session_dir=session_dir,
        departments=departments,
        observer_summary=True,
        write_to_company=True,
    )

    return {
        "topic": topic,
        "augmented_topic_length": len(augmented_topic),
        "summary_path": str(debate.summary_path) if debate.summary_path else "",
        "transcript_path": str(debate.transcript_path) if debate.transcript_path else "",
        "observer_summary": debate.observer_summary,
        "statements_count": len(debate.statements),
        "queries_made_total": sum(len(s.queries_made) for s in debate.statements),
    }


# ---------------------------------------------------------------------------
# Index file
# ---------------------------------------------------------------------------
def write_index(
    company: CompanyConfig,
    dept_results: list[dict[str, object]],
    synthesis_result: dict[str, object] | None,
    board_result: dict[str, object] | None,
) -> Path:
    """Write demo-artifacts/INDEX.md linking everything."""
    root, _ = _ensure_demo_dirs(company)
    index_path = root / "INDEX.md"

    lines = [
        f"# Comprehensive Demo Index — {company.name}",
        f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        "## Department Demonstrations",
        "",
    ]
    for r in dept_results:
        path = Path(str(r.get("path", "")))
        rel = path.name if path.exists() else "(missing)"
        status = r.get("status", "?")
        specs = r.get("specialists_called") or []
        elapsed = r.get("elapsed_seconds")
        elapsed_str = f" — {elapsed}s" if elapsed else ""
        lines.append(
            f"- **{r['dept']}** ([{rel}](depts/{rel})) — {status}{elapsed_str} — "
            f"specialists: {', '.join(str(s) for s in specs) or '(none)'}"
        )

    if synthesis_result:
        lines.extend([
            "",
            "## Orchestrator Synthesis",
            "",
            f"- [orchestrator-synthesis.md](orchestrator-synthesis.md) — "
            f"cross-department readiness report",
        ])

    if board_result:
        lines.extend([
            "",
            "## Board Deliberation",
            "",
            f"- Topic: {board_result.get('topic', '')[:200]}",
            f"- Statements: {board_result.get('statements_count')}",
            f"- Live manager queries: {board_result.get('queries_made_total')}",
        ])
        sp = board_result.get("summary_path")
        if sp:
            try:
                rel = Path(str(sp)).relative_to(company.company_dir)
                lines.append(f"- Summary + transcript: [{rel}](../{rel})")
            except ValueError:
                lines.append(f"- Summary + transcript: {sp}")

    lines.extend([
        "",
        "---",
        "",
        "## How to Read This Dossier",
        "",
        "1. Start with **orchestrator-synthesis.md** — the cross-department view "
        "of where Old Press stands.",
        "2. Then read the **board summary** for the strategic deliberation that "
        "uses the dept work as input.",
        "3. Drill into individual department demos in `depts/` for full detail.",
        "",
        "All artifacts were generated by the Company OS multi-agent system "
        "(Orchestrator → Manager → Specialist → Worker, with a 6-voice advisory "
        "Board on the side). Every dept manager dispatched its own specialists "
        "to produce its artifact; the Orchestrator synthesized; the Board "
        "deliberated with live access to query managers; the Orchestrator "
        "silently observed and summarized.",
    ])

    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[demo] Index saved → {index_path.relative_to(company.company_dir)}")
    return index_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _resolve_dir(company: str | None, company_dir: str | None) -> Path:
    if company_dir:
        return Path(company_dir).expanduser().resolve()
    if company:
        return (get_vault_dir() / company).resolve()
    raise ValueError("--company or --company-dir required.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Company OS — Comprehensive Demo Runner")
    parser.add_argument("--company", help="Company name (resolved to {vault}/{name}/).")
    parser.add_argument("--company-dir", help="Full path to the company folder.")
    parser.add_argument(
        "--depts",
        nargs="+",
        help="Limit to specific departments (e.g. --depts marketing finance). "
        "Default: all active departments.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate department artifacts even if they already exist.",
    )
    parser.add_argument(
        "--skip-board",
        action="store_true",
        help="Skip the final board deliberation (depts + synthesis only).",
    )
    parser.add_argument(
        "--skip-synthesis",
        action="store_true",
        help="Skip the orchestrator synthesis step.",
    )
    parser.add_argument(
        "--board-topic",
        help="Override the default board deliberation topic.",
    )
    parser.add_argument(
        "--vertical",
        default="wine-beverage",
        help=(
            "Vertical pack name for demo briefs (default: wine-beverage). "
            "Resolves to verticals/<name>/dept_briefs.yaml."
        ),
    )
    args = parser.parse_args()

    try:
        company_dir = _resolve_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        parser.print_help()
        sys.exit(2)

    company = load_company(company_dir)
    departments = load_departments(company)

    print("=" * 70)
    print(f"  COMPREHENSIVE DEMO — {company.name}")
    print("=" * 70)
    print(f"  Company dir: {company.company_dir}")
    print(f"  Departments: {[d.name for d in departments]}")
    if args.depts:
        print(f"  Filter: {args.depts}")
    if args.force:
        print("  Force regenerate: YES")
    print(f"  Skip synthesis: {args.skip_synthesis}")
    print(f"  Skip board: {args.skip_board}")
    print("=" * 70)

    overall_t0 = time.time()
    summary: dict[str, object] = {
        "company": company.name,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }

    # --- Phase 1: department demos ---
    print("\n" + "─" * 70)
    print("PHASE 1: Department Demonstrations")
    print("─" * 70)

    # Load the active vertical pack; if missing, run with None (fallback).
    try:
        pack = load_vertical_pack(args.vertical)
        print(f"[demo]   vertical pack: {args.vertical} ({len(pack.names())} named briefs)")
    except FileNotFoundError as exc:
        pack = None
        print(f"[demo]   WARNING: {exc} — using fallback brief for every dept.")

    dept_results = run_all_department_demos(
        company, departments, only=args.depts, force=args.force, pack=pack,
    )
    summary["dept_results"] = dept_results

    # --- Phase 2: orchestrator synthesis ---
    synthesis_result: dict[str, object] | None = None
    if not args.skip_synthesis and any(r.get("status") in ("generated", "skipped") for r in dept_results):
        print("\n" + "─" * 70)
        print("PHASE 2: Orchestrator Synthesis")
        print("─" * 70)
        synthesis_result = run_orchestrator_synthesis(company, dept_results)
        summary["synthesis_result"] = synthesis_result

    # --- Phase 3: board deliberation ---
    board_result: dict[str, object] | None = None
    if not args.skip_board:
        print("\n" + "─" * 70)
        print("PHASE 3: Board Deliberation (with silent observer)")
        print("─" * 70)
        topic = args.board_topic or DEFAULT_BOARD_TOPIC
        board_result = run_board_deliberation(
            company, departments, topic=topic, include_dept_dossier=True
        )
        summary["board_result"] = {
            k: v for k, v in board_result.items() if k != "observer_summary"
        }

    # --- Phase 4: index ---
    print("\n" + "─" * 70)
    print("PHASE 4: Index")
    print("─" * 70)
    index_path = write_index(company, dept_results, synthesis_result, board_result)
    summary["index_path"] = str(index_path)

    # Persist run summary
    elapsed = time.time() - overall_t0
    summary["elapsed_seconds"] = round(elapsed, 1)
    summary["completed_at"] = datetime.now().isoformat(timespec="seconds")
    root, _ = _ensure_demo_dirs(company)
    (root / "_run-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n" + "=" * 70)
    print(f"  COMPREHENSIVE DEMO COMPLETE — {elapsed/60:.1f} min")
    print("=" * 70)
    print(f"  Index: {index_path.relative_to(company.company_dir)}")
    print(f"  Run summary: demo-artifacts/_run-summary.json")
    print()


if __name__ == "__main__":
    main()
