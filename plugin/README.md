# Company OS — Claude Code plugin

Slash commands for invoking Company OS (multi-agent business orchestration
framework) from inside a Claude Code session. Every skill here is a thin
wrapper around `python -m cli <subcommand>`; the plugin is the
interactive surface, the CLI is the executor.

## Installation

1. Symlink or copy this `plugin/` directory into Claude Code's plugin
   search path. The canonical location on a local install:

       ~/.claude/plugins/company-os/

   (Alternatively, use `claude plugin install <path>` if your Claude
   Code version supports it.)

2. Restart the Claude Code session so the plugin is picked up.

3. Verify: `/plugins` should list `company-os` as installed. Slash
   commands register under the `company-os:` namespace:

       /company-os:adversary
       /company-os:kill
       /company-os:costs
       /company-os:assumptions
       /company-os:run-dept
       /company-os:talk-to
       /company-os:demo

## Environment prerequisites

All skills shell out to the Company OS CLI and require:

* `COMPANY_OS_VAULT_DIR` env var pointing at the vault root
  (`C:/Users/.../Obsidian Vault` or similar).
* The `company-os/` package checked out under the vault so `python -m cli`
  resolves.
* An active company folder (e.g. `Old Press Wine Company LLC`) with the
  canonical subdirs in place.
* For LLM-backed subcommands (`run-dept`, `talk-to`, `demo`), an
  `ANTHROPIC_API_KEY` at `~/.company-os/.env`.

## Skill directory

| Slash command               | CLI equivalent                                  | Side effect                                |
|-----------------------------|-------------------------------------------------|--------------------------------------------|
| `/company-os:adversary`     | `python -m cli adversary --on "<thesis>"`       | Writes a scaffold review to `decisions/adversary-reviews/` |
| `/company-os:kill`          | `python -m cli kill <specialist>`               | Writes a 3-question retro to `decisions/retros/` |
| `/company-os:costs`         | `python -m cli costs [--month YYYY-MM]`         | Reads `cost-log.jsonl`, prints summary     |
| `/company-os:assumptions`   | `python -m cli assumptions`                     | Reads `assumptions-log.jsonl`, prints list |
| `/company-os:run-dept`      | `python -m cli run <dept> --brief "<text>"`     | Full hierarchical dispatch (LLM cost)      |
| `/company-os:talk-to`       | `python -m cli talk-to <specialist> --message`  | Single-turn chat (LLM cost)                |
| `/company-os:demo`          | `python -m cli demo [...flags]`                 | Runs the comprehensive demo (LLM cost)     |
| `/company-os:meeting`       | `python -m cli meeting --dept/--company-wide/--cross-agent` | Orchestrated meeting with transcript (LLM cost) |
| `/company-os:eval-compare`  | `python -m cli eval-compare <grok-path> <gemini-path>`      | Consolidate two AI-model evaluations of Company OS into a structured 10-section analysis |

## Design

These are **model-invoked skills** — each `SKILL.md` tells Claude when
to use the command and what arguments to expect. The skill body runs
the actual `python -m cli ...` invocation via the Bash tool.

The split keeps the plugin thin:

* Argument parsing + subcommand routing → `cli/main.py`
* Data-layer work (writing retros, reading cost logs, etc.) → core primitives
* Shell interaction + user-facing prompting → this plugin's skills

Add a new command by:

1. Adding a subcommand handler in `cli/main.py`.
2. Adding a `skills/<name>/SKILL.md` here that invokes it.
3. Testing the CLI path via `tests/test_cli.py`.
