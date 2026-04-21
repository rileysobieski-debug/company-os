"""
cli/main.py — Company OS CLI (Phase 13.3 — plan §11.1)
========================================================
Subcommand surface per plan §11.1:

  companyos run <dept> --brief "<text>"     # full dispatch to a dept manager
  companyos talk-to <specialist> --message  # single-turn chat, no machinery
  companyos demo [--depts ...] [--force]    # comprehensive demo runner
  companyos adversary --on "<thesis>"       # invoke adversary review
  companyos kill <specialist>               # kill-switch + 3-question retro
  companyos costs [--month YYYY-MM]         # cost dashboard summary
  companyos assumptions                     # assumption freshness status

Every subcommand returns an integer exit code (0 success, non-zero failure)
so the CLI is scriptable in pipelines.

Design notes:
  * Each `cmd_*` handler imports its heavy deps lazily so the CLI module
    itself is cheap to import — `make_parser()` doesn't touch the SDK.
  * Subcommands that need a company context accept `--company <name>`
    or `--company-dir <path>`; resolution mirrors comprehensive_demo.py.
  * Subcommands that record state (`adversary`, `kill`) write to the
    company's `decisions/` dir via the existing persistence primitives
    in `core.adversary`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Company resolution (shared by several subcommands)
# ---------------------------------------------------------------------------
def _resolve_company_dir(
    company_arg: str | None, company_dir_arg: str | None
) -> Path:
    """Resolve a company dir from --company NAME or --company-dir PATH.

    Raises ValueError if neither is supplied or the resolved path doesn't
    exist. Lazy-imports `core.config` so the CLI module itself is cheap
    to load.
    """
    if company_dir_arg:
        path = Path(company_dir_arg).resolve()
        if not path.exists():
            raise ValueError(f"company dir does not exist: {path}")
        return path
    if not company_arg:
        raise ValueError("supply --company <name> or --company-dir <path>")
    from core.config import get_vault_dir
    vault = get_vault_dir()
    path = vault / company_arg
    if not path.exists():
        raise ValueError(
            f"no company folder at {path} "
            f"(vault={vault}, company={company_arg!r})"
        )
    return path


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    """Full-dispatch to `<dept>` with the given brief."""
    try:
        company_dir = _resolve_company_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from core.company import load_company
    from core.managers.base import dispatch_manager
    from core.managers.loader import load_departments

    company = load_company(company_dir)
    departments = load_departments(company)
    depts_by_name = {d.name: d for d in departments}
    if args.dept not in depts_by_name:
        print(
            f"ERROR: dept {args.dept!r} not active. "
            f"Available: {sorted(depts_by_name)}",
            file=sys.stderr,
        )
        return 2

    result = dispatch_manager(args.dept, args.brief, company, departments=departments)
    print(result.final_text or "(empty response)")
    return 0


def _add_run_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("run", help="Dispatch a brief to a department manager")
    p.add_argument("dept", help="Department name (e.g. marketing, finance)")
    p.add_argument("--brief", required=True, help="Brief text for the dept")
    p.add_argument("--company", help="Company name (resolved under vault dir)")
    p.add_argument("--company-dir", help="Absolute path to company folder")
    p.set_defaults(func=cmd_run)


# ---------------------------------------------------------------------------
# Subcommand: talk-to
# ---------------------------------------------------------------------------
def cmd_talk_to(args: argparse.Namespace) -> int:
    """Single-turn chat with a specialist — no handshake / evaluator loop."""
    try:
        company_dir = _resolve_company_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from core.company import load_company
    from core.llm_client import single_turn
    from core.managers.loader import load_departments

    company = load_company(company_dir)
    departments = load_departments(company)
    specialist = None
    for dept in departments:
        for s in dept.specialists:
            if s.name == args.specialist:
                specialist = s
                break
        if specialist:
            break

    if specialist is None:
        all_names = [s.name for d in departments for s in d.specialists]
        print(
            f"ERROR: specialist {args.specialist!r} not found. "
            f"Available: {sorted(all_names)}",
            file=sys.stderr,
        )
        return 2

    # Compose a minimal system prompt — no memory, no peers, no loop.
    system = (
        f"You are {specialist.name}, {specialist.description}\n\n"
        f"Working for {company.name}. Answer the user's message directly "
        "in one turn — no tool use, no delegation."
    )
    response = single_turn(
        system=system,
        user=args.message,
        model=specialist.model,
        max_tokens=1024,
    )
    print(response.text or "(empty response)")
    return 0


def _add_talk_to_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("talk-to", help="Single-turn chat with a specialist")
    p.add_argument("specialist", help="Specialist name")
    p.add_argument("--message", required=True, help="Message to the specialist")
    p.add_argument("--company")
    p.add_argument("--company-dir")
    p.set_defaults(func=cmd_talk_to)


# ---------------------------------------------------------------------------
# Subcommand: demo
# ---------------------------------------------------------------------------
def cmd_demo(args: argparse.Namespace) -> int:
    """Delegate to comprehensive_demo.main() with matching args."""
    argv: list[str] = []
    if args.company:
        argv.extend(["--company", args.company])
    if args.company_dir:
        argv.extend(["--company-dir", args.company_dir])
    if args.depts:
        argv.append("--depts")
        argv.extend(args.depts)
    if args.force:
        argv.append("--force")
    if args.skip_board:
        argv.append("--skip-board")
    if args.skip_synthesis:
        argv.append("--skip-synthesis")
    if args.vertical:
        argv.extend(["--vertical", args.vertical])

    sys.argv = ["comprehensive_demo"] + argv
    import comprehensive_demo
    comprehensive_demo.main()
    return 0


def _add_demo_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("demo", help="Run the comprehensive demo")
    p.add_argument("--company")
    p.add_argument("--company-dir")
    p.add_argument("--depts", nargs="+")
    p.add_argument("--force", action="store_true")
    p.add_argument("--skip-board", action="store_true")
    p.add_argument("--skip-synthesis", action="store_true")
    p.add_argument("--vertical", default="wine-beverage")
    p.set_defaults(func=cmd_demo)


# ---------------------------------------------------------------------------
# Subcommand: adversary
# ---------------------------------------------------------------------------
def cmd_adversary(args: argparse.Namespace) -> int:
    """Record an adversary review for `--on <thesis>`. Writes a stub
    review file to `<company>/decisions/adversary-reviews/`. The
    LLM-backed adversary agent invocation is out of scope for 13.3 —
    this command lands the file skeleton so a human or downstream runner
    can fill the objections."""
    try:
        company_dir = _resolve_company_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from core.adversary import (
        ActivationReason,
        AdversaryReview,
        write_review,
        _now_iso,
    )

    review = AdversaryReview(
        milestone=args.milestone or "manual-invocation",
        thesis=args.on,
        activation_reason=ActivationReason.MANUAL,
        created_at=_now_iso(),
    )
    path = write_review(company_dir, review)
    print(f"Adversary review scaffold written: {path}")
    print("Fill in objections/citations or run the adversary agent "
          "to populate them.")
    return 0


def _add_adversary_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("adversary", help="Invoke / record an adversary review")
    p.add_argument("--on", required=True, help="Thesis to stress-test")
    p.add_argument("--milestone", help="Milestone slug (default: manual-invocation)")
    p.add_argument("--company")
    p.add_argument("--company-dir")
    p.set_defaults(func=cmd_adversary)


# ---------------------------------------------------------------------------
# Subcommand: kill
# ---------------------------------------------------------------------------
def cmd_kill(args: argparse.Namespace) -> int:
    """Kill-switch: record a 3-question retro for `<specialist>`."""
    try:
        company_dir = _resolve_company_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from core.adversary import KillSwitchRetro, write_retro, _now_iso

    retro = KillSwitchRetro(
        specialist_id=args.specialist,
        created_at=_now_iso(),
        expected=args.expected or "",
        saw=args.saw or "",
        fix=args.fix or "",
        last_known_good_prompt_ref=args.prompt_ref or "",
    )
    path = write_retro(company_dir, retro)
    print(f"Kill-switch retro recorded: {path}")
    if not (args.expected and args.saw and args.fix):
        print(
            "Note: one or more of --expected/--saw/--fix was not supplied; "
            "the retro scaffold is on disk — fill it in before reviving "
            f"`{args.specialist}`."
        )
    return 0


def _add_kill_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("kill", help="Pause a specialist + record a 3-question retro")
    p.add_argument("specialist")
    p.add_argument("--expected", help="What did you expect?")
    p.add_argument("--saw", help="What did you see?")
    p.add_argument("--fix", help="What would fix it?")
    p.add_argument("--prompt-ref", help="Last-known-good prompt ref")
    p.add_argument("--company")
    p.add_argument("--company-dir")
    p.set_defaults(func=cmd_kill)


# ---------------------------------------------------------------------------
# Subcommand: eval-compare
# ---------------------------------------------------------------------------
_EVAL_EXPECTED_SECTIONS: tuple[str, ...] = (
    "Section 1",  # Prior art check
    "Section 2",  # Attack surface analysis
    "Section 3",  # Ambient + relationship layer assessment
    "Section 4",  # Research direction ranking
    "Section 5",  # What's missing
)


def cmd_eval_compare(args: argparse.Namespace) -> int:
    """Validate two evaluation-response files and emit metadata.

    This is the thin CLI half of the `/company-os:eval-compare` plugin
    skill. The plugin skill uses this to pre-flight the response files
    before spending tokens on deep consolidation analysis.

    Checks:
      * both files exist
      * each contains all five expected section headers
      * word counts are non-trivial (> 500 words each)

    Emits a JSON summary on stdout when `--json` is set; otherwise emits
    a human-readable report. Non-zero exit code iff a file is missing
    (hard failure) — missing sections are warnings, not errors.
    """
    grok_path = Path(args.grok).resolve()
    gemini_path = Path(args.gemini).resolve()

    missing: list[str] = []
    if not grok_path.exists():
        missing.append(str(grok_path))
    if not gemini_path.exists():
        missing.append(str(gemini_path))
    if missing:
        print(
            f"ERROR: response file(s) not found: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2

    grok_stats = _eval_file_stats(grok_path)
    gemini_stats = _eval_file_stats(gemini_path)

    if args.json:
        payload = {
            "grok": {"path": str(grok_path), **grok_stats},
            "gemini": {"path": str(gemini_path), **gemini_stats},
            "expected_sections": list(_EVAL_EXPECTED_SECTIONS),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    # Human-readable report
    print("Validating evaluation response files...")
    _print_eval_stats("Grok", grok_path, grok_stats)
    _print_eval_stats("Gemini", gemini_path, gemini_stats)
    print()
    print("Section presence (both files):")
    for section in _EVAL_EXPECTED_SECTIONS:
        grok_ok = section in grok_stats["sections_found"]
        gemini_ok = section in gemini_stats["sections_found"]
        mark = (
            "OK"
            if grok_ok and gemini_ok
            else "WARN"
        )
        print(
            f"  [{mark}] {section}   "
            f"grok={'found' if grok_ok else 'missing'}  "
            f"gemini={'found' if gemini_ok else 'missing'}"
        )
    if grok_stats["word_count"] < 500 or gemini_stats["word_count"] < 500:
        print()
        print("WARNING: one or both responses are under 500 words — "
              "analysis quality will be poor.")
    print()
    print("Files are valid and ready for consolidation.")
    print("Next: invoke /company-os:eval-compare in Claude Code to "
          "produce the 10-section consolidated analysis.")
    return 0


def _eval_file_stats(path: Path) -> dict:
    """Return {word_count, line_count, sections_found} for an eval file."""
    text = path.read_text(encoding="utf-8")
    word_count = len(text.split())
    line_count = len(text.splitlines())
    sections_found = [
        s for s in _EVAL_EXPECTED_SECTIONS if s in text
    ]
    return {
        "word_count": word_count,
        "line_count": line_count,
        "sections_found": sections_found,
    }


def _print_eval_stats(label: str, path: Path, stats: dict) -> None:
    n_sections = len(stats["sections_found"])
    print(
        f"  {label}: {path.name} "
        f"({stats['word_count']:,} words, "
        f"{n_sections}/{len(_EVAL_EXPECTED_SECTIONS)} sections detected)"
    )


def _add_eval_compare_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "eval-compare",
        help="Validate two evaluation-response files and emit metadata "
             "(pre-flight for /company-os:eval-compare plugin skill)",
    )
    p.add_argument("grok", help="Path to Grok's evaluation response (.md)")
    p.add_argument("gemini", help="Path to Gemini's evaluation response (.md)")
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable report",
    )
    p.set_defaults(func=cmd_eval_compare)


# ---------------------------------------------------------------------------
# Subcommand: add-dept
# ---------------------------------------------------------------------------
def cmd_add_dept(args: argparse.Namespace) -> int:
    """Create a new department in the active company.

    Thin wrapper over `core.onboarding.dept_creation.add_department`.
    """
    try:
        company_dir = _resolve_company_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from core.onboarding.dept_creation import add_department

    try:
        result = add_department(
            company_dir=company_dir,
            slug=args.slug,
            display_name=args.display_name,
            prompt_body=args.prompt or "",
            manager_model=args.manager_model,
            owns=args.owns or (),
            never=args.never or (),
            activate=not args.no_activate,
            vertical=args.vertical,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Created department: {result.dept_dir}")
    print(f"  department.md    : {result.department_md}")
    print(f"  manager-memory.md: {result.manager_memory}")
    if result.config_updated:
        print(f"  config.json      : active_departments updated")
    if result.scope_matrix_updated:
        print(f"  scope_matrix.yaml: OWNS/NEVER appended")
    if not args.prompt:
        print(
            "\nNOTE: department.md has a placeholder scaffold. "
            "Edit it to match the dept's actual charter before dispatching."
        )
    return 0


def _add_add_dept_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "add-dept",
        help="Create a new department folder + department.md + update config",
    )
    p.add_argument(
        "slug",
        help="Kebab-case dept name (e.g. subscriber-ops, legal)",
    )
    p.add_argument(
        "--display-name",
        help="Human-readable name (default: derived from slug)",
    )
    p.add_argument(
        "--prompt",
        help=(
            "Department.md body (the manager's prompt). Empty = placeholder "
            "scaffold the founder should edit later."
        ),
    )
    p.add_argument(
        "--manager-model",
        help="Claude model ID for this manager (default: get_model('default'))",
    )
    p.add_argument(
        "--owns",
        nargs="+",
        help="Scope-matrix OWNS entries for this dept",
    )
    p.add_argument(
        "--never",
        nargs="+",
        help="Scope-matrix NEVER entries for this dept",
    )
    p.add_argument(
        "--vertical",
        default="wine-beverage",
        help="Vertical pack whose scope matrix to update (default: wine-beverage)",
    )
    p.add_argument(
        "--no-activate",
        action="store_true",
        help="Skip adding to config.json's active_departments",
    )
    p.add_argument("--company")
    p.add_argument("--company-dir")
    p.set_defaults(func=cmd_add_dept)


# ---------------------------------------------------------------------------
# Subcommand: meeting
# ---------------------------------------------------------------------------
def cmd_meeting(args: argparse.Namespace) -> int:
    """Convene a dept-wide, company-wide, or cross-agent meeting.

    Mode selection (exactly one must be supplied):
      --dept <name>            department-wide (manager + its specialists)
      --company-wide           all active dept managers
      --cross-agent --participants X Y Z ...
                               arbitrary subset; supports "board:Role" syntax
    """
    try:
        company_dir = _resolve_company_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Validate mode selection — exactly one mode.
    modes_selected = sum(bool(x) for x in (args.dept, args.company_wide, args.cross_agent))
    if modes_selected != 1:
        print(
            "ERROR: supply exactly one of --dept <name>, --company-wide, "
            "or --cross-agent --participants <list>",
            file=sys.stderr,
        )
        return 2

    if not args.topic:
        print("ERROR: --topic is required", file=sys.stderr)
        return 2

    # Cross-agent requires --participants — check before loading the company
    # so the caller gets a clean error without waiting for disk I/O.
    if args.cross_agent and not args.participants:
        print(
            "ERROR: --cross-agent requires --participants X Y Z [board:Role ...]",
            file=sys.stderr,
        )
        return 2

    from core.company import load_company
    from core.managers.loader import load_departments
    from core.meeting import run_cross_agent_meeting, run_department_meeting

    company = load_company(company_dir)
    departments = load_departments(company)
    sessions_dir = company_dir / "decisions" / "meetings"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Dept-wide
    if args.dept:
        depts_by_name = {d.name: d for d in departments}
        if args.dept not in depts_by_name:
            print(
                f"ERROR: dept {args.dept!r} not active. "
                f"Available: {sorted(depts_by_name)}",
                file=sys.stderr,
            )
            return 2
        transcript = run_department_meeting(
            company=company,
            dept=depts_by_name[args.dept],
            topic=args.topic,
            invited_specialists=args.invite or None,
            session_dir=sessions_dir,
            all_departments=departments,
        )
        _print_transcript(transcript)
        print(f"\nTranscript saved: {sessions_dir / f'dept-meeting-{args.dept}.md'}")
        return 0

    # Company-wide → all active dept managers as participants
    if args.company_wide:
        participants = [d.name for d in departments]
        if not participants:
            print("ERROR: no active departments — nothing to convene.", file=sys.stderr)
            return 2
        transcript = run_cross_agent_meeting(
            company=company,
            departments=departments,
            participants=participants,
            topic=args.topic,
            session_dir=sessions_dir,
        )
        _print_transcript(transcript)
        print(f"\nTranscript saved: {sessions_dir / 'cross-meeting.md'}")
        return 0

    # Cross-agent (arbitrary participants) — --participants already validated above.
    transcript = run_cross_agent_meeting(
        company=company,
        departments=departments,
        participants=args.participants,
        topic=args.topic,
        session_dir=sessions_dir,
    )
    _print_transcript(transcript)
    print(f"\nTranscript saved: {sessions_dir / 'cross-meeting.md'}")
    return 0


def _print_transcript(transcript) -> None:
    """Stream the meeting transcript to stdout with clear speaker tags."""
    print(f"\n=== {transcript.meeting_type.upper()} MEETING ===")
    print(f"Topic: {transcript.topic}\n")
    for stmt in transcript.statements:
        print(f"\n─── {stmt.participant} ───")
        print(stmt.content)


def _add_meeting_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "meeting",
        help="Convene a dept-wide, company-wide, or cross-agent meeting",
    )
    # Mode selectors (exactly one)
    p.add_argument(
        "--dept",
        help="Department-wide meeting: manager + its specialists discuss the topic",
    )
    p.add_argument(
        "--company-wide",
        action="store_true",
        help="Company-wide meeting: all active department managers convene",
    )
    p.add_argument(
        "--cross-agent",
        action="store_true",
        help="Cross-agent meeting: arbitrary subset via --participants",
    )
    # Shared
    p.add_argument("--topic", required=True, help="The meeting topic / question")
    p.add_argument(
        "--invite",
        nargs="+",
        help="(Dept meetings only) specific specialists to invite. Default: all.",
    )
    p.add_argument(
        "--participants",
        nargs="+",
        help=(
            "(Cross-agent only) participant specs. Dept names "
            "(e.g. marketing) or board members (board:Strategist)."
        ),
    )
    p.add_argument("--company")
    p.add_argument("--company-dir")
    p.set_defaults(func=cmd_meeting)


# ---------------------------------------------------------------------------
# Subcommand: costs
# ---------------------------------------------------------------------------
def cmd_costs(args: argparse.Namespace) -> int:
    """Print cost summary from `<company>/cost-log.jsonl`."""
    try:
        company_dir = _resolve_company_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    path = company_dir / "cost-log.jsonl"
    if not path.exists():
        print(f"No cost log at {path} — nothing to report.")
        return 0

    totals = {"input": 0, "output": 0, "calls": 0}
    by_tag: dict[str, dict[str, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if args.month and not str(entry.get("timestamp", "")).startswith(args.month):
            continue
        totals["input"] += int(entry.get("input_tokens", 0) or 0)
        totals["output"] += int(entry.get("output_tokens", 0) or 0)
        totals["calls"] += 1
        tag = str(entry.get("cost_tag", "<untagged>"))
        bt = by_tag.setdefault(tag, {"input": 0, "output": 0, "calls": 0})
        bt["input"] += int(entry.get("input_tokens", 0) or 0)
        bt["output"] += int(entry.get("output_tokens", 0) or 0)
        bt["calls"] += 1

    scope = f"month {args.month}" if args.month else "all time"
    print(f"Cost summary — {scope}")
    print(f"  Calls   : {totals['calls']}")
    print(f"  Input   : {totals['input']:,} tokens")
    print(f"  Output  : {totals['output']:,} tokens")
    if by_tag:
        print("  By tag:")
        for tag, bt in sorted(by_tag.items(), key=lambda kv: -kv[1]["calls"]):
            print(f"    {tag}: {bt['calls']} calls, "
                  f"{bt['input']:,} in / {bt['output']:,} out")
    return 0


def _add_costs_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("costs", help="Print cost summary from cost-log.jsonl")
    p.add_argument("--month", help="Restrict to YYYY-MM (matches ISO timestamp prefix)")
    p.add_argument("--company")
    p.add_argument("--company-dir")
    p.set_defaults(func=cmd_costs)


# ---------------------------------------------------------------------------
# Subcommand: assumptions
# ---------------------------------------------------------------------------
def cmd_assumptions(args: argparse.Namespace) -> int:
    """Print assumption freshness status from `<company>/assumptions-log.jsonl`.

    Skips entries whose shape doesn't match `core.primitives.freshness.Assumption`;
    anything else prints in order of last-use time, oldest first."""
    try:
        company_dir = _resolve_company_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    path = company_dir / "assumptions-log.jsonl"
    if not path.exists():
        print(f"No assumptions log at {path} — nothing to report.")
        return 0

    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not entries:
        print("Assumptions log is empty.")
        return 0

    entries.sort(key=lambda e: str(e.get("last_used_at", "")))
    print(f"{len(entries)} assumption entries:")
    for e in entries:
        status = e.get("status", "?")
        uses = e.get("uses", 0)
        last = e.get("last_used_at", "")
        label = e.get("label") or e.get("id") or "?"
        print(f"  [{status:<15}] uses={uses:<3} last={last}  {label}")
    return 0


def _add_assumptions_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("assumptions", help="Assumption freshness status")
    p.add_argument("--company")
    p.add_argument("--company-dir")
    p.set_defaults(func=cmd_assumptions)


# ---------------------------------------------------------------------------
# Parser assembly
# ---------------------------------------------------------------------------
def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="companyos",
        description="Company OS CLI — run, talk-to, demo, adversary, kill, costs, assumptions",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
    _add_talk_to_parser(sub)
    _add_demo_parser(sub)
    _add_adversary_parser(sub)
    _add_kill_parser(sub)
    _add_meeting_parser(sub)
    _add_add_dept_parser(sub)
    _add_eval_compare_parser(sub)
    _add_costs_parser(sub)
    _add_assumptions_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)
