"""
Company OS — CLI entry point
============================
Usage:
  python company-os/main.py --company "Old Press Wine Company LLC"
  python company-os/main.py --company-dir "C:/path/to/Company Folder"
  python company-os/main.py --new-company --company-dir "C:/path/to/New Folder"
  python company-os/main.py --new-company --company "New Company Name"

If neither --company nor --company-dir is provided and --new-company is NOT
set, prints help and exits.

Behavior:
  --new-company   : runs the wizard to write starter files into --company-dir
                    (or into "{vault}/{company-name}/" for --company).
  otherwise       : loads the company, starts an interactive chat loop with
                    the Orchestrator. Each Riley message triggers one chat()
                    turn. Type /exit or Ctrl-C to quit.
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime
from pathlib import Path

# --- Windows UTF-8 fix (must run before any print with unicode) ---
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Make sibling `core/` importable regardless of cwd
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from core.env import get_vault_dir, load_env  # noqa: E402
load_env()  # load ~/.company-os/.env before any anthropic client is created
from core.company import load_company  # noqa: E402
from core.managers.loader import load_departments  # noqa: E402
from core.onboarding import check_and_run_all_onboarding  # noqa: E402
from core.orchestrator import Orchestrator  # noqa: E402
from core.wizard import run_wizard  # noqa: E402


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def resolve_company_dir(company: str | None, company_dir: str | None) -> Path:
    """Resolve a company folder from either --company (name within vault) or
    --company-dir (full path)."""
    if company_dir:
        return Path(company_dir).expanduser().resolve()
    if company:
        return (get_vault_dir() / company).resolve()
    raise ValueError("Either --company or --company-dir is required.")


# ---------------------------------------------------------------------------
# Session folder
# ---------------------------------------------------------------------------
def _make_session_dir(company_dir: Path) -> Path:
    """Create sessions/{YYYY-MM-DD}-N/ with monotonically increasing N."""
    base = company_dir / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    n = 1
    while True:
        candidate = base / f"{today}-{n:03d}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
        n += 1


# ---------------------------------------------------------------------------
# Interactive chat loop
# ---------------------------------------------------------------------------
def run_chat_loop(orchestrator: Orchestrator) -> None:
    print("=" * 60)
    print(f"  Company OS — {orchestrator.company.name}")
    print(f"  Session:  {orchestrator.session_dir.name}")
    print(f"  Folder:   {orchestrator.company.company_dir}")
    print(f"  Depts:    {[d.name for d in orchestrator.departments]}")
    print("=" * 60)
    print("\nType your message to the Orchestrator. /exit to end session.\n")

    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n(ending session)")
            break
        if not line:
            continue
        if line.lower() in ("/exit", "/quit", ":q"):
            break

        try:
            reply = orchestrator.chat(line)
        except Exception as exc:  # noqa: BLE001
            print(f"\n[ERROR] Orchestrator raised: {exc}\n")
            continue

        print(f"\nOrchestrator:\n{reply}\n")

        if orchestrator.state.ended:
            print("(session has been ended by orchestrator; exiting)\n")
            break


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Company OS — multi-agent orchestrator.")
    parser.add_argument("--company", help="Company name (resolved to {vault}/{name}/).")
    parser.add_argument("--company-dir", help="Full path to the company folder.")
    parser.add_argument(
        "--new-company",
        action="store_true",
        help="Run the wizard to create a new company folder.",
    )
    args = parser.parse_args()

    # --new-company: run the wizard, then exit
    if args.new_company:
        try:
            company_dir = resolve_company_dir(args.company, args.company_dir)
        except ValueError:
            # If no path provided, ask the wizard to construct one (by name)
            print("ERROR: --new-company requires --company or --company-dir to know where to write.")
            sys.exit(2)
        run_wizard(company_dir)
        return

    # Load + run
    try:
        company_dir = resolve_company_dir(args.company, args.company_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}\n")
        parser.print_help()
        sys.exit(2)

    try:
        company = load_company(company_dir)
    except FileNotFoundError as exc:
        print(f"ERROR loading company:\n{exc}")
        sys.exit(2)

    # --- Onboarding check (runs once per entity; skips already-complete) ---
    departments = load_departments(company)
    check_and_run_all_onboarding(company, departments, interactive=True)

    session_dir = _make_session_dir(company_dir)
    orchestrator = Orchestrator(company, session_dir)
    run_chat_loop(orchestrator)


if __name__ == "__main__":
    main()
