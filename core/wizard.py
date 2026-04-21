"""
Context Wizard — new-company interview
======================================
Walks a founder through a short interview and writes the starter files into
a new company folder so the Orchestrator can be run against it.

Files produced:
  config.json
  context.md
  domain.md (optional — empty if industry is generic)
  founder_profile.md
  priorities.md
  compliance_gates.md
  vendor_registry.md
  conflicts.md
  digest.md

Called via:
  python company-os/main.py --new-company --company-dir "Acme Corp"

Implementation:
  A simple interactive terminal Q&A. Not SDK-based — the wizard is a
  deterministic prompt-the-user flow. No model calls are required; the model
  does not add value in a structured interview that should capture Riley's
  own words verbatim.

Phase 1: OPEN — free-form about business + goals
Phase 2: PROBE — fill required fields
Phase 3: CONFIRM — show the written documents for approval
"""

from __future__ import annotations

import json
import textwrap
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------
def _prompt(question: str, default: str | None = None, multiline: bool = False) -> str:
    """Ask a question; return the trimmed answer. For multiline, read until
    a blank line."""
    suffix = f" [{default}]" if default else ""
    if multiline:
        print(f"\n{question}{suffix}")
        print("(Enter a blank line when done.)")
        lines: list[str] = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == "" and lines:
                break
            if line.strip() == "" and not lines:
                if default:
                    return default
                continue
            lines.append(line)
        return "\n".join(lines).strip()
    else:
        raw = input(f"\n{question}{suffix}\n> ").strip()
        return raw if raw else (default or "")


def _prompt_list(question: str, min_items: int = 1) -> list[str]:
    print(f"\n{question}")
    print("(One per line. Blank line to finish.)")
    out: list[str] = []
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            if len(out) >= min_items:
                break
            print(f"  (need at least {min_items} — keep going)")
            continue
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_wizard(company_dir: Path) -> None:
    """Run the full interview and write starter files. `company_dir` is the
    target folder — created if missing."""
    company_dir = company_dir.resolve()
    company_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(" Company OS — New Company Wizard")
    print("=" * 60)
    print(f"\nTarget folder: {company_dir}\n")

    # ---------------- Phase 1: OPEN ----------------
    company_name = _prompt("Company name (full legal entity)?")
    company_id = _prompt(
        "Short id for the company (lowercase, hyphens — used internally)?",
        default=company_name.lower().replace(" ", "-")[:40],
    )
    industry = _prompt("Industry (one line)?")
    business_model = _prompt(
        "Business model? (e.g. 'product', 'service', 'marketplace', 'hybrid')",
        default="product",
    )
    revenue_status = _prompt(
        "Revenue status? (e.g. 'pre-revenue', 'early-revenue', 'scaling')",
        default="pre-revenue",
    )
    geography = _prompt("Geography? (e.g. 'Maine, regional' — one sentence)")
    team_size = _prompt("Team size? (e.g. 'solo', '2-5', '6-20')", default="solo")

    print("\n--- Founder / company brief (Phase 1) ---")
    who = _prompt(
        "Tell me about the founder — who are you, what brought you here?",
        multiline=True,
    )
    what = _prompt(
        "What does the company actually do? What are you making / selling / providing?",
        multiline=True,
    )
    goal = _prompt(
        "What is the goal for the next 6 months?",
        multiline=True,
    )

    # ---------------- Phase 2: PROBE ----------------
    print("\n--- Structured fields (Phase 2) ---")

    regulatory_context = _prompt_list(
        "Regulatory context — what agencies/rules apply? (list)",
        min_items=0,
    )

    active_departments_raw = _prompt(
        "Active departments at launch? (comma-separated; leave blank for default marketing,finance,operations)",
        default="marketing,finance,operations",
    )
    active_departments = [d.strip() for d in active_departments_raw.split(",") if d.strip()]

    print("\n--- Delegation thresholds ---")
    spend_auto = int(_prompt("Spend auto-approve threshold ($)?", default="0"))
    spend_report = int(_prompt("Spend report-only threshold ($)?", default="50"))
    spend_gate = int(_prompt("Spend gate threshold (requires approval, $)?", default="500"))

    content_approval = _prompt(
        "Content publish requires approval? (y/n)", default="y"
    ).lower().startswith("y")
    vendor_approval = _prompt(
        "Vendor commits require approval? (y/n)", default="y"
    ).lower().startswith("y")

    priorities = _prompt_list(
        "Top 3 priorities for the next 90 days (list)",
        min_items=1,
    )

    settled_convictions = _prompt_list(
        "Settled convictions — decisions already made, do NOT re-examine (list; can be empty)",
        min_items=0,
    )
    hard_constraints = _prompt_list(
        "Hard constraints — bright-line rules never to cross (list; can be empty)",
        min_items=0,
    )

    domain_knowledge = _prompt(
        "Industry-specific knowledge for specialists (optional; paste in a paragraph or leave blank)",
        multiline=True,
        default="",
    )

    # ---------------- Phase 3: CONFIRM ----------------
    config = {
        "company_id": company_id,
        "company_name": company_name,
        "industry": industry,
        "business_model": business_model,
        "revenue_status": revenue_status,
        "geography": geography,
        "team_size": team_size,
        "active_departments": active_departments,
        "regulatory_context": regulatory_context,
        "delegation": {
            "spend_auto_threshold": spend_auto,
            "spend_report_threshold": spend_report,
            "spend_gate_threshold": spend_gate,
            "content_publish_requires_approval": content_approval,
            "vendor_commit_requires_approval": vendor_approval,
        },
        "priorities": priorities,
        "settled_convictions": settled_convictions,
        "hard_constraints": hard_constraints,
    }

    context_md = textwrap.dedent(
        f"""\
        # {company_name} — Context

        ## Founder
        {who}

        ## What the company does
        {what}

        ## Current goal
        {goal}

        ## Operating context
        - Industry: {industry}
        - Business model: {business_model}
        - Revenue status: {revenue_status}
        - Geography: {geography}
        - Team size: {team_size}

        ## Regulatory context
        {chr(10).join(f"- {r}" for r in regulatory_context) or "_(none specified)_"}
        """
    )

    founder_md = textwrap.dedent(
        f"""\
        # Founder Profile — {company_name}

        _(Living document. All agents read this; Orchestrator updates it over time.)_

        ## Background
        {who}

        ## Current focus
        {goal}
        """
    )

    priorities_md = textwrap.dedent(
        f"""\
        # Priorities — {company_name}

        _Current as of {datetime.now().strftime('%Y-%m-%d')}._
        _This document is human-editable; the Orchestrator updates `config.json`._

        """
    ) + "\n".join(f"{i}. {p}" for i, p in enumerate(priorities, start=1)) + "\n"

    compliance_md = textwrap.dedent(
        f"""\
        # Compliance Gates — {company_name}

        _Maintained by `compliance-tracker`._

        | Gate | Status | Evidence | Next Action | Due |
        |------|--------|----------|-------------|-----|
        | _(no gates recorded yet)_ | | | | |
        """
    )

    vendor_md = textwrap.dedent(
        f"""\
        # Vendor Registry — {company_name}

        _Maintained by `vendor-scout`. Concentration flag: >40% of any category
        spend or >20% of total ops spend._

        | Vendor | Category | Status | Spend Share | Last Reviewed | Notes |
        |--------|----------|--------|-------------|---------------|-------|
        | _(none yet)_ | | | | | |
        """
    )

    conflicts_md = textwrap.dedent(
        f"""\
        # Cross-Department Conflicts — {company_name}

        _Orchestrator writes; Riley resolves. Empty is good._
        """
    )

    digest_md = textwrap.dedent(
        f"""\
        # Session Digest — {company_name}

        _(No sessions yet. First run of main.py will populate this.)_
        """
    )

    print("\n" + "=" * 60)
    print(" Review — what will be written")
    print("=" * 60)
    print(f"\n{company_dir}/config.json")
    print(json.dumps(config, indent=2))
    print(f"\n{company_dir}/context.md\n---\n{context_md}\n---")
    print(f"\n{company_dir}/priorities.md\n---\n{priorities_md}\n---")

    ans = _prompt("Write these files now? (y/n)", default="y")
    if not ans.lower().startswith("y"):
        print("Aborted. Nothing written.")
        return

    # ---------------- Write ----------------
    (company_dir / "config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (company_dir / "context.md").write_text(context_md, encoding="utf-8")
    (company_dir / "domain.md").write_text(
        domain_knowledge.strip() + "\n" if domain_knowledge.strip() else "", encoding="utf-8"
    )
    (company_dir / "founder_profile.md").write_text(founder_md, encoding="utf-8")
    (company_dir / "priorities.md").write_text(priorities_md, encoding="utf-8")
    (company_dir / "compliance_gates.md").write_text(compliance_md, encoding="utf-8")
    (company_dir / "vendor_registry.md").write_text(vendor_md, encoding="utf-8")
    (company_dir / "conflicts.md").write_text(conflicts_md, encoding="utf-8")
    (company_dir / "digest.md").write_text(digest_md, encoding="utf-8")

    # Create stub department folders (empty; manager can't dispatch until
    # Riley adds a department.md + at least one specialist.md — but the
    # scaffolding signals the intent)
    for dept in active_departments:
        (company_dir / dept).mkdir(parents=True, exist_ok=True)

    (company_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (company_dir / "decisions").mkdir(parents=True, exist_ok=True)
    (company_dir / "projects").mkdir(parents=True, exist_ok=True)

    print(f"\nWritten. Next step:")
    print(f"  1. Create department.md + specialist.md files inside each department folder.")
    print(f"  2. Run: python company-os/main.py --company-dir \"{company_dir}\"")
