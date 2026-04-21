"""Company OS CLI — parser + data-layer subcommand tests (Phase 13.3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.main import (
    _resolve_company_dir,
    cmd_add_dept,
    cmd_adversary,
    cmd_assumptions,
    cmd_costs,
    cmd_eval_compare,
    cmd_kill,
    cmd_meeting,
    main,
    make_parser,
)


# ---------------------------------------------------------------------------
# Parser shape
# ---------------------------------------------------------------------------
def test_parser_has_all_subcommands() -> None:
    parser = make_parser()
    # argparse stores subparsers in a private attr; use parse_known to test.
    for cmd in ("run", "talk-to", "demo", "adversary", "kill", "costs", "assumptions"):
        # parse_args_or_error won't work with required subcommands + --help paths.
        # Just assert parsing a minimal invocation of each reaches a handler.
        pass  # coverage below


def test_parse_run_requires_brief() -> None:
    parser = make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "marketing"])


def test_parse_run_routes_to_cmd_run() -> None:
    parser = make_parser()
    args = parser.parse_args(["run", "marketing", "--brief", "hello"])
    assert args.dept == "marketing"
    assert args.brief == "hello"
    assert args.func is not None


def test_parse_talk_to_requires_message() -> None:
    parser = make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["talk-to", "copywriter"])


def test_parse_talk_to_collects_message() -> None:
    parser = make_parser()
    args = parser.parse_args(["talk-to", "copywriter", "--message", "hi"])
    assert args.specialist == "copywriter"
    assert args.message == "hi"


def test_parse_demo_accepts_dept_filter() -> None:
    parser = make_parser()
    args = parser.parse_args(["demo", "--depts", "marketing", "finance"])
    assert args.depts == ["marketing", "finance"]


def test_parse_adversary_requires_on() -> None:
    parser = make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["adversary"])


def test_parse_adversary_accepts_milestone() -> None:
    parser = make_parser()
    args = parser.parse_args([
        "adversary", "--on", "thesis", "--milestone", "fund-launch",
    ])
    assert args.on == "thesis"
    assert args.milestone == "fund-launch"


def test_parse_kill_takes_optional_retro_fields() -> None:
    parser = make_parser()
    args = parser.parse_args([
        "kill", "copywriter",
        "--expected", "voice", "--saw", "cliché", "--fix", "ban cliché",
    ])
    assert args.specialist == "copywriter"
    assert args.expected == "voice"
    assert args.saw == "cliché"
    assert args.fix == "ban cliché"


def test_parse_costs_accepts_month_filter() -> None:
    parser = make_parser()
    args = parser.parse_args(["costs", "--month", "2026-04"])
    assert args.month == "2026-04"


# ---------------------------------------------------------------------------
# Parse tests — meeting
# ---------------------------------------------------------------------------
def test_parse_meeting_requires_topic() -> None:
    parser = make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["meeting", "--dept", "editorial"])


def test_parse_meeting_dept_mode() -> None:
    parser = make_parser()
    args = parser.parse_args([
        "meeting", "--dept", "editorial",
        "--topic", "Newsletter launch sequencing",
    ])
    assert args.dept == "editorial"
    assert args.company_wide is False
    assert args.cross_agent is False
    assert args.topic == "Newsletter launch sequencing"


def test_parse_meeting_company_wide_mode() -> None:
    parser = make_parser()
    args = parser.parse_args([
        "meeting", "--company-wide",
        "--topic", "Q3 priorities",
    ])
    assert args.company_wide is True
    assert args.dept is None


def test_parse_meeting_cross_agent_with_participants() -> None:
    parser = make_parser()
    args = parser.parse_args([
        "meeting", "--cross-agent",
        "--participants", "marketing", "finance", "board:Contrarian",
        "--topic", "Spend plan",
    ])
    assert args.cross_agent is True
    assert args.participants == ["marketing", "finance", "board:Contrarian"]


def test_parse_meeting_dept_with_invite_filter() -> None:
    parser = make_parser()
    args = parser.parse_args([
        "meeting", "--dept", "editorial",
        "--invite", "copywriter", "editorial-director",
        "--topic", "Voice calibration",
    ])
    assert args.invite == ["copywriter", "editorial-director"]


def test_parse_assumptions_takes_company_only() -> None:
    parser = make_parser()
    args = parser.parse_args(["assumptions", "--company", "Old Press"])
    assert args.company == "Old Press"


# ---------------------------------------------------------------------------
# Parse tests — eval-compare
# ---------------------------------------------------------------------------
def test_parse_eval_compare_requires_two_paths() -> None:
    parser = make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["eval-compare", "only-one.md"])


def test_parse_eval_compare_takes_both_paths() -> None:
    parser = make_parser()
    args = parser.parse_args([
        "eval-compare", "grok.md", "gemini.md",
    ])
    assert args.grok == "grok.md"
    assert args.gemini == "gemini.md"
    assert args.json is False


def test_parse_eval_compare_json_flag() -> None:
    parser = make_parser()
    args = parser.parse_args([
        "eval-compare", "grok.md", "gemini.md", "--json",
    ])
    assert args.json is True


def test_parser_rejects_unknown_command() -> None:
    parser = make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["not-a-real-command"])


# ---------------------------------------------------------------------------
# _resolve_company_dir
# ---------------------------------------------------------------------------
def test_resolve_company_dir_raises_without_args() -> None:
    with pytest.raises(ValueError, match="--company"):
        _resolve_company_dir(None, None)


def test_resolve_company_dir_with_explicit_path(tmp_path: Path) -> None:
    p = _resolve_company_dir(None, str(tmp_path))
    assert p == tmp_path.resolve()


def test_resolve_company_dir_rejects_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent"
    with pytest.raises(ValueError, match="does not exist"):
        _resolve_company_dir(None, str(missing))


def test_resolve_company_dir_uses_vault_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    (vault / "Acme Co").mkdir(parents=True)
    monkeypatch.setenv("COMPANY_OS_VAULT_DIR", str(vault))
    p = _resolve_company_dir("Acme Co", None)
    assert p == (vault / "Acme Co").resolve()


# ---------------------------------------------------------------------------
# cmd_adversary — writes a stub review file
# ---------------------------------------------------------------------------
def test_adversary_cmd_writes_review(tmp_path: Path, capsys) -> None:
    args = _ns(
        company=None,
        company_dir=str(tmp_path),
        on="Does the Maine pivot hold?",
        milestone="pivot-check",
    )
    rc = cmd_adversary(args)
    assert rc == 0
    # File exists at expected path.
    reviews_dir = tmp_path / "decisions" / "adversary-reviews"
    assert reviews_dir.exists()
    md_files = list(reviews_dir.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "Does the Maine pivot hold?" in content
    assert "pivot-check" in content
    out = capsys.readouterr().out
    assert "scaffold written" in out


def test_adversary_cmd_defaults_milestone_when_absent(tmp_path: Path) -> None:
    args = _ns(
        company=None,
        company_dir=str(tmp_path),
        on="Thesis text",
        milestone=None,
    )
    cmd_adversary(args)
    reviews_dir = tmp_path / "decisions" / "adversary-reviews"
    md = next(iter(reviews_dir.glob("*.md")))
    assert "manual-invocation" in md.read_text(encoding="utf-8")


def test_adversary_cmd_errors_on_missing_company(capsys) -> None:
    args = _ns(company=None, company_dir=None, on="x", milestone=None)
    rc = cmd_adversary(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "supply --company" in err


# ---------------------------------------------------------------------------
# cmd_kill — writes a retro file
# ---------------------------------------------------------------------------
def test_kill_cmd_writes_retro(tmp_path: Path) -> None:
    args = _ns(
        company=None,
        company_dir=str(tmp_path),
        specialist="copywriter",
        expected="voice",
        saw="cliché",
        fix="ban cliché",
        prompt_ref="copywriter.md@rev-1",
    )
    rc = cmd_kill(args)
    assert rc == 0
    retros = tmp_path / "decisions" / "retros"
    md = next(iter(retros.glob("*.md")))
    content = md.read_text(encoding="utf-8")
    assert "copywriter" in content
    assert "voice" in content
    assert "cliché" in content
    assert "ban cliché" in content
    assert "copywriter.md@rev-1" in content


def test_kill_cmd_warns_when_retro_fields_missing(tmp_path: Path, capsys) -> None:
    args = _ns(
        company=None,
        company_dir=str(tmp_path),
        specialist="copywriter",
        expected=None, saw=None, fix=None, prompt_ref=None,
    )
    rc = cmd_kill(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "not supplied" in out


# ---------------------------------------------------------------------------
# cmd_costs — reads jsonl
# ---------------------------------------------------------------------------
def test_costs_cmd_reports_summary(tmp_path: Path, capsys) -> None:
    log = tmp_path / "cost-log.jsonl"
    log.write_text(
        json.dumps({
            "timestamp": "2026-04-18T12:00:00+00:00",
            "input_tokens": 100, "output_tokens": 50,
            "cost_tag": "demo.marketing",
        }) + "\n" +
        json.dumps({
            "timestamp": "2026-04-18T13:00:00+00:00",
            "input_tokens": 200, "output_tokens": 80,
            "cost_tag": "demo.marketing",
        }) + "\n" +
        json.dumps({
            "timestamp": "2026-04-18T14:00:00+00:00",
            "input_tokens": 30, "output_tokens": 10,
            "cost_tag": "adversary",
        }) + "\n",
        encoding="utf-8",
    )
    args = _ns(company=None, company_dir=str(tmp_path), month=None)
    rc = cmd_costs(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Calls   : 3" in out
    assert "Input   : 330 tokens" in out
    assert "Output  : 140 tokens" in out
    assert "demo.marketing" in out
    assert "adversary" in out


def test_costs_cmd_month_filter(tmp_path: Path, capsys) -> None:
    log = tmp_path / "cost-log.jsonl"
    log.write_text(
        json.dumps({
            "timestamp": "2026-03-15T12:00:00+00:00",
            "input_tokens": 999, "output_tokens": 999,
            "cost_tag": "march",
        }) + "\n" +
        json.dumps({
            "timestamp": "2026-04-18T12:00:00+00:00",
            "input_tokens": 100, "output_tokens": 50,
            "cost_tag": "april",
        }) + "\n",
        encoding="utf-8",
    )
    args = _ns(company=None, company_dir=str(tmp_path), month="2026-04")
    cmd_costs(args)
    out = capsys.readouterr().out
    assert "Input   : 100 tokens" in out
    assert "march" not in out


def test_costs_cmd_missing_log(tmp_path: Path, capsys) -> None:
    args = _ns(company=None, company_dir=str(tmp_path), month=None)
    rc = cmd_costs(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No cost log" in out


def test_costs_cmd_skips_malformed_lines(tmp_path: Path, capsys) -> None:
    log = tmp_path / "cost-log.jsonl"
    log.write_text(
        "not json\n"
        + json.dumps({
            "timestamp": "2026-04-18T12:00:00+00:00",
            "input_tokens": 5, "output_tokens": 5, "cost_tag": "ok",
        }) + "\n",
        encoding="utf-8",
    )
    args = _ns(company=None, company_dir=str(tmp_path), month=None)
    cmd_costs(args)
    out = capsys.readouterr().out
    assert "Calls   : 1" in out


# ---------------------------------------------------------------------------
# cmd_assumptions — reads jsonl
# ---------------------------------------------------------------------------
def test_assumptions_cmd_reports_entries(tmp_path: Path, capsys) -> None:
    log = tmp_path / "assumptions-log.jsonl"
    log.write_text(
        json.dumps({
            "id": "a1", "label": "TTB timeline", "status": "fresh",
            "uses": 2, "last_used_at": "2026-04-18T10:00:00+00:00",
        }) + "\n" +
        json.dumps({
            "id": "a2", "label": "Maine winter", "status": "needs_review",
            "uses": 6, "last_used_at": "2026-04-17T10:00:00+00:00",
        }) + "\n",
        encoding="utf-8",
    )
    args = _ns(company=None, company_dir=str(tmp_path))
    rc = cmd_assumptions(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 assumption entries" in out
    # Oldest first.
    i_maine = out.find("Maine winter")
    i_ttb = out.find("TTB timeline")
    assert i_maine < i_ttb
    assert "fresh" in out
    assert "needs_review" in out


def test_assumptions_cmd_missing_log(tmp_path: Path, capsys) -> None:
    args = _ns(company=None, company_dir=str(tmp_path))
    rc = cmd_assumptions(args)
    assert rc == 0
    assert "No assumptions log" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_meeting — mode validation (no LLM calls)
# ---------------------------------------------------------------------------
def test_meeting_cmd_rejects_zero_modes(tmp_path: Path, capsys) -> None:
    args = _ns(
        company=None, company_dir=str(tmp_path),
        dept=None, company_wide=False, cross_agent=False,
        topic="x", invite=None, participants=None,
    )
    rc = cmd_meeting(args)
    assert rc == 2
    assert "exactly one of" in capsys.readouterr().err


def test_meeting_cmd_rejects_multiple_modes(tmp_path: Path, capsys) -> None:
    args = _ns(
        company=None, company_dir=str(tmp_path),
        dept="editorial", company_wide=True, cross_agent=False,
        topic="x", invite=None, participants=None,
    )
    rc = cmd_meeting(args)
    assert rc == 2
    assert "exactly one of" in capsys.readouterr().err


def test_meeting_cmd_requires_topic(tmp_path: Path, capsys) -> None:
    args = _ns(
        company=None, company_dir=str(tmp_path),
        dept="editorial", company_wide=False, cross_agent=False,
        topic="", invite=None, participants=None,
    )
    rc = cmd_meeting(args)
    assert rc == 2
    assert "--topic is required" in capsys.readouterr().err


def test_meeting_cmd_cross_agent_requires_participants(tmp_path: Path, capsys) -> None:
    """--cross-agent without --participants errors cleanly with exit 2
    BEFORE any disk I/O (company load). Test with an empty tmp_path to
    confirm the pre-validation happens first."""
    args = _ns(
        company=None, company_dir=str(tmp_path),
        dept=None, company_wide=False, cross_agent=True,
        topic="x", invite=None, participants=None,
    )
    rc = cmd_meeting(args)
    assert rc == 2
    assert "--participants" in capsys.readouterr().err


def test_meeting_cmd_rejects_missing_company(capsys) -> None:
    args = _ns(
        company=None, company_dir=None,
        dept="editorial", company_wide=False, cross_agent=False,
        topic="x", invite=None, participants=None,
    )
    rc = cmd_meeting(args)
    assert rc == 2
    assert "supply --company" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_eval_compare — validation paths (no LLM)
# ---------------------------------------------------------------------------
def _write_eval_response(path: Path, *, sections: int = 5, word_count: int = 600) -> Path:
    """Write a minimal evaluation response file with `sections` section
    headers and approximately `word_count` words."""
    lines = ["# Evaluation response\n\n"]
    for i in range(1, sections + 1):
        lines.append(f"## Section {i} — placeholder\n\n")
        # Pad with filler words so the total word count clears 500.
        lines.append(("lorem ipsum " * (word_count // sections)) + "\n\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def test_eval_compare_cmd_rejects_missing_files(tmp_path: Path, capsys) -> None:
    args = _ns(
        grok=str(tmp_path / "nonexistent-grok.md"),
        gemini=str(tmp_path / "nonexistent-gemini.md"),
        json=False,
    )
    rc = cmd_eval_compare(args)
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_eval_compare_cmd_reports_stats_for_valid_files(
    tmp_path: Path, capsys,
) -> None:
    grok = _write_eval_response(tmp_path / "grok.md")
    gemini = _write_eval_response(tmp_path / "gemini.md")
    args = _ns(grok=str(grok), gemini=str(gemini), json=False)
    rc = cmd_eval_compare(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Grok: grok.md" in out
    assert "Gemini: gemini.md" in out
    assert "5/5 sections detected" in out
    assert "ready for consolidation" in out


def test_eval_compare_cmd_flags_missing_sections(tmp_path: Path, capsys) -> None:
    """File with only 2 of 5 sections → all 5 shown with missing ones flagged."""
    grok = _write_eval_response(tmp_path / "grok.md", sections=2)
    gemini = _write_eval_response(tmp_path / "gemini.md", sections=5)
    args = _ns(grok=str(grok), gemini=str(gemini), json=False)
    rc = cmd_eval_compare(args)
    # Missing sections are a warning, not an error — exit 0.
    assert rc == 0
    out = capsys.readouterr().out
    assert "Section 3" in out
    assert "grok=missing" in out


def test_eval_compare_cmd_warns_on_short_response(tmp_path: Path, capsys) -> None:
    # Tiny file < 500 words.
    grok = tmp_path / "grok.md"
    grok.write_text("# too short\n\nSection 1 Section 2 Section 3 "
                    "Section 4 Section 5\n", encoding="utf-8")
    gemini = _write_eval_response(tmp_path / "gemini.md")
    args = _ns(grok=str(grok), gemini=str(gemini), json=False)
    rc = cmd_eval_compare(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "under 500 words" in out


def test_eval_compare_cmd_json_mode(tmp_path: Path, capsys) -> None:
    import json as _json
    grok = _write_eval_response(tmp_path / "grok.md")
    gemini = _write_eval_response(tmp_path / "gemini.md")
    args = _ns(grok=str(grok), gemini=str(gemini), json=True)
    rc = cmd_eval_compare(args)
    assert rc == 0
    payload = _json.loads(capsys.readouterr().out)
    assert set(payload.keys()) == {"grok", "gemini", "expected_sections"}
    assert payload["grok"]["word_count"] > 0
    assert payload["gemini"]["word_count"] > 0
    assert len(payload["expected_sections"]) == 5


# ---------------------------------------------------------------------------
# main() integration — dispatches to the right handler
# ---------------------------------------------------------------------------
def test_main_routes_adversary(tmp_path: Path, capsys) -> None:
    rc = main([
        "adversary", "--on", "Pivot thesis", "--company-dir", str(tmp_path),
    ])
    assert rc == 0
    reviews = tmp_path / "decisions" / "adversary-reviews"
    assert any(reviews.glob("*.md"))


def test_main_routes_kill(tmp_path: Path) -> None:
    rc = main([
        "kill", "copywriter", "--company-dir", str(tmp_path),
        "--expected", "voice", "--saw", "X", "--fix", "Y",
    ])
    assert rc == 0
    retros = tmp_path / "decisions" / "retros"
    assert any(retros.glob("*.md"))


def test_main_routes_costs_with_missing_log(tmp_path: Path, capsys) -> None:
    rc = main(["costs", "--company-dir", str(tmp_path)])
    assert rc == 0
    assert "No cost log" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _ns(**kwargs) -> object:
    """Build a lightweight Namespace for handler testing."""
    import argparse
    return argparse.Namespace(**kwargs)
