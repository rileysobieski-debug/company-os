"""Claude Code plugin skeleton (Phase 13.4)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "plugin"


EXPECTED_SKILLS = (
    "adversary",
    "kill",
    "costs",
    "assumptions",
    "run-dept",
    "talk-to",
    "demo",
    "meeting",
    "eval-compare",
)


# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------
def test_plugin_root_exists() -> None:
    assert PLUGIN_ROOT.is_dir()


def test_manifest_file_exists() -> None:
    assert (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").exists()


def test_skills_directory_exists() -> None:
    assert (PLUGIN_ROOT / "skills").is_dir()


def test_readme_exists() -> None:
    assert (PLUGIN_ROOT / "README.md").exists()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def test_manifest_has_required_fields() -> None:
    data = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert data["name"] == "company-os"
    assert "description" in data
    assert "version" in data


def test_manifest_name_is_kebab_case() -> None:
    data = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    # Claude Code plugin naming convention: lowercase-with-dashes.
    assert re.fullmatch(r"[a-z][a-z0-9-]*", data["name"])


def test_manifest_version_is_semver_ish() -> None:
    data = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    # Loose semver check — major.minor.patch with numeric segments.
    assert re.fullmatch(r"\d+\.\d+\.\d+", data["version"])


# ---------------------------------------------------------------------------
# Skill files
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
def test_skill_file_exists(skill_name: str) -> None:
    path = PLUGIN_ROOT / "skills" / skill_name / "SKILL.md"
    assert path.exists(), f"missing SKILL.md for {skill_name}"


@pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
def test_skill_has_frontmatter(skill_name: str) -> None:
    path = PLUGIN_ROOT / "skills" / skill_name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{skill_name} missing frontmatter opener"
    # Second `---` closes the frontmatter.
    lines = text.splitlines()
    closers = [i for i, line in enumerate(lines) if line.strip() == "---"]
    assert len(closers) >= 2, f"{skill_name} frontmatter not closed"


@pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
def test_skill_frontmatter_has_description(skill_name: str) -> None:
    path = PLUGIN_ROOT / "skills" / skill_name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    # Parse the frontmatter block for a `description:` line.
    lines = text.splitlines()
    closers = [i for i, line in enumerate(lines) if line.strip() == "---"]
    fm_block = "\n".join(lines[1:closers[1]])
    assert re.search(r"^description:\s*\S", fm_block, re.MULTILINE), (
        f"{skill_name} SKILL.md missing `description:` frontmatter key"
    )


@pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
def test_skill_body_references_cli(skill_name: str) -> None:
    """Every skill's body should instruct the agent to shell out to
    `python -m cli <subcommand>` — the plugin is a thin interactive
    surface on top of the CLI."""
    path = PLUGIN_ROOT / "skills" / skill_name / "SKILL.md"
    body = path.read_text(encoding="utf-8")
    assert "python -m cli" in body, (
        f"{skill_name} SKILL.md doesn't reference the CLI — did you wire "
        "the command body to the `cli/` entry point?"
    )


# ---------------------------------------------------------------------------
# Skill ↔ CLI alignment
# ---------------------------------------------------------------------------
def test_every_plugin_skill_maps_to_a_cli_subcommand() -> None:
    """Each plugin skill must invoke a subcommand that actually exists
    in `cli.main.make_parser`."""
    from cli.main import make_parser

    parser = make_parser()
    # argparse stashes subparsers under the "_subparsers" _actions list.
    sub_action = next(
        a for a in parser._actions if isinstance(a, type(parser._subparsers._actions[0]))
        if False
    ) if False else None  # noqa: F841 — the clean helper is below

    cli_commands = set()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            cli_commands.update(choices.keys())

    assert cli_commands, "couldn't enumerate CLI subcommands"

    # Map plugin skill names to CLI command names (mostly 1:1; `run-dept` → `run`).
    skill_to_cli = {
        "adversary": "adversary",
        "kill": "kill",
        "costs": "costs",
        "assumptions": "assumptions",
        "run-dept": "run",
        "talk-to": "talk-to",
        "demo": "demo",
        "meeting": "meeting",
        "eval-compare": "eval-compare",
    }
    for skill_name, cli_name in skill_to_cli.items():
        assert cli_name in cli_commands, (
            f"plugin skill {skill_name!r} refers to CLI subcommand "
            f"{cli_name!r} which is not registered in cli.main.make_parser"
        )


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------
def test_readme_documents_every_skill() -> None:
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    for skill in EXPECTED_SKILLS:
        assert f"/company-os:{skill}" in readme, (
            f"README missing reference to /company-os:{skill}"
        )


def test_readme_mentions_installation_path() -> None:
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    assert "~/.claude/plugins" in readme or "claude plugin install" in readme
