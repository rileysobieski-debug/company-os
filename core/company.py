"""
CompanyConfig
=============
Loads a company's config.json, context.md, and domain.md from its folder on
disk and exposes them to the rest of the engine.

Every agent receives the company's context via system-prompt injection. The
cwd for every SDK query is set to the company folder so all file operations
(memory reads/writes, knowledge-base, output folders) are scoped correctly.

Two companies can run simultaneously with fully isolated file scopes — each
pointed at its own directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CompanyConfig:
    """In-memory view of a company's root folder.

    Attributes are the raw content of the config files, loaded once at
    session start. Agents receive these as injected prompt fragments.
    """

    company_dir: Path
    raw_config: dict[str, Any]
    context: str
    domain: str

    # --- Convenience accessors (sourced from raw_config) ---
    @property
    def company_id(self) -> str:
        return self.raw_config["company_id"]

    @property
    def name(self) -> str:
        return self.raw_config["company_name"]

    @property
    def industry(self) -> str:
        return self.raw_config.get("industry", "")

    @property
    def active_departments(self) -> list[str]:
        return list(self.raw_config.get("active_departments", []))

    @property
    def priorities(self) -> list[str]:
        return list(self.raw_config.get("priorities", []))

    @property
    def settled_convictions(self) -> list[str]:
        return list(self.raw_config.get("settled_convictions", []))

    @property
    def hard_constraints(self) -> list[str]:
        return list(self.raw_config.get("hard_constraints", []))

    @property
    def delegation(self) -> dict[str, Any]:
        return dict(self.raw_config.get("delegation", {}))

    # --- Prompt fragments (shared across all agent classes) ---
    def settled_convictions_block(self) -> str:
        """Verbatim block injected into every agent prompt. The rule that
        binds all agents: do not re-examine these decisions."""
        if not self.settled_convictions:
            return ""
        lines = [
            "=== SETTLED CONVICTIONS — DO NOT RE-EXAMINE ===",
            "Riley has made these decisions. Do not research alternatives.",
            "Do not suggest reconsidering them. Do not include them in",
            "\"open questions.\" If a task implicitly contradicts a conviction,",
            "flag it to the manager rather than working around it.",
            "",
        ]
        for i, conviction in enumerate(self.settled_convictions, start=1):
            lines.append(f"{i}. {conviction}")
        return "\n".join(lines)

    def hard_constraints_block(self) -> str:
        """Bright-line rules that cannot be crossed under any circumstances."""
        if not self.hard_constraints:
            return ""
        lines = [
            "=== HARD CONSTRAINTS — NEVER CROSS THESE LINES ===",
        ]
        for rule in self.hard_constraints:
            lines.append(f"- {rule}")
        return "\n".join(lines)

    def priorities_block(self) -> str:
        """Current priority stack, sourced from config.json; priorities.md is
        the human-editable long form."""
        if not self.priorities:
            return ""
        lines = ["=== CURRENT PRIORITIES ==="]
        for i, p in enumerate(self.priorities, start=1):
            lines.append(f"{i}. {p}")
        return "\n".join(lines)


def load_company(company_dir: str | Path) -> CompanyConfig:
    """Load a company from its folder.

    Expects:
      company_dir/config.json   — structured settings (required)
      company_dir/context.md    — founder + company prose (required)
      company_dir/domain.md     — industry knowledge (optional; empty string if missing)

    Raises FileNotFoundError with an actionable message if required files are missing.
    """
    path = Path(company_dir).resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"Company folder does not exist: {path}\n"
            f"Run `python company-os/main.py --new-company --company-dir \"{path}\"` "
            f"to create it via the wizard."
        )

    config_path = path / "config.json"
    context_path = path / "context.md"
    domain_path = path / "domain.md"

    if not config_path.exists():
        raise FileNotFoundError(
            f"Missing required file: {config_path}\n"
            f"Every company folder needs config.json. Run the wizard or hand-write it."
        )
    if not context_path.exists():
        raise FileNotFoundError(
            f"Missing required file: {context_path}\n"
            f"Every company folder needs context.md."
        )

    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    context = context_path.read_text(encoding="utf-8")
    domain = domain_path.read_text(encoding="utf-8") if domain_path.exists() else ""

    return CompanyConfig(
        company_dir=path,
        raw_config=raw_config,
        context=context,
        domain=domain,
    )
