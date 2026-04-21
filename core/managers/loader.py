"""
Department + specialist loader (file-driven)
============================================
Auto-discovers a company's active departments and their specialists by walking
the company folder on disk. No hard-coded rosters.

  Company folder / {dept}/ department.md            → department config
  Company folder / {dept}/ {specialist}/ specialist.md → specialist config

Both files use YAML frontmatter followed by the markdown body used as the
prompt (or as supporting prompt content). This lets Riley add, remove, or
edit specialists without touching Python — just drop a new specialist.md in
a new folder and it's live on next session.

Public:
  load_departments(company) -> list[DepartmentConfig]
  load_specialists_for_department(dept_dir) -> list[SpecialistConfig]
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import config
from core.company import CompanyConfig


# ---------------------------------------------------------------------------
# Minimal YAML frontmatter parser
# ---------------------------------------------------------------------------
def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body) from a markdown file that starts with
    --- ... --- frontmatter. If no frontmatter is present, returns ({}, text).

    Supports:
      key: string-value
      key: [a, b, c]
      key: true / false / null
      key: 42
    and block-style lists across multiple lines:
      key:
        - a
        - b

    Intentionally narrow — no nested maps, no quoting edge cases. Keep the
    specialist.md files inside this grammar.
    """
    stripped = text.lstrip("\ufeff")  # strip BOM if any
    if not stripped.startswith("---"):
        return {}, text

    # Split on the closing ---
    lines = stripped.splitlines()
    if not lines or not lines[0].startswith("---"):
        return {}, text

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")

    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in fm_lines:
        # Detect block-style list continuation
        if current_list_key is not None and raw_line.lstrip().startswith("- "):
            data[current_list_key].append(raw_line.lstrip()[2:].strip())
            continue
        current_list_key = None

        if ":" not in raw_line:
            continue
        key, _, rest = raw_line.partition(":")
        key = key.strip()
        rest = rest.strip()

        if rest == "":
            # Block-style list follows
            data[key] = []
            current_list_key = key
            continue

        # Inline list: [a, b, c]
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            if not inner:
                data[key] = []
            else:
                data[key] = [part.strip().strip("\"'") for part in inner.split(",")]
            continue

        # Scalars
        lower = rest.lower()
        if lower == "true":
            data[key] = True
        elif lower == "false":
            data[key] = False
        elif lower in ("null", "none", "~"):
            data[key] = None
        else:
            # Try int
            try:
                data[key] = int(rest)
            except ValueError:
                # Strip quotes if present
                if (rest.startswith('"') and rest.endswith('"')) or (
                    rest.startswith("'") and rest.endswith("'")
                ):
                    rest = rest[1:-1]
                data[key] = rest

    return data, body


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------
@dataclass
class SpecialistConfig:
    """Everything the loader knows about one specialist.

    - name: stable identifier used as the agent-dispatch key
    - description: brief shown to the manager for selection
    - prompt_body: the markdown body of specialist.md (role, how-you-work,
      deliverables, etc.) — appended to the runtime-built prompt preamble
    - attribute: the one-word core attribute (POSITION, CRAFT, ...) surfaced
      in the manager's routing prompt
    - tools: Agent-SDK tool list for this specialist
    - model: model id; defaults to haiku-4.5
    - department: which department this specialist belongs to
    - is_scout: True if scout==true in frontmatter
    - memory_path: absolute path to this specialist's memory.md
    - reference_dir: absolute path to this specialist's reference folder
    - specialist_dir: the folder containing specialist.md
    """

    name: str
    description: str
    prompt_body: str
    attribute: str
    tools: list[str]
    model: str
    department: str
    is_scout: bool
    memory_path: Path
    reference_dir: Path
    specialist_dir: Path

    def reference_files(self) -> list[Path]:
        """List of files Riley has dropped in reference/ — the manager's
        prompt builder includes these paths so the specialist knows what's
        there without the loader reading file contents (files may be large)."""
        if not self.reference_dir.exists():
            return []
        return sorted(
            p for p in self.reference_dir.iterdir() if p.is_file() and not p.name.startswith(".")
        )


@dataclass
class DepartmentConfig:
    """Everything the loader knows about one department.

    - name: stable identifier used as the manager-dispatch key (e.g. "marketing")
    - display_name: human-readable (from frontmatter if provided, else titlecased)
    - prompt_body: markdown body of department.md — appended to manager prompt
    - manager_model: model id for this manager
    - manager_tools: allowed tools for this manager (defaults include Read, Glob, Grep, Agent)
    - dept_dir: the department folder
    - manager_memory_path: path to manager-memory.md
    - reference_dir: dept-level reference/
    - knowledge_base_dir: dept-level knowledge-base/
    - output_dirs: dict of the three output folders
    - specialists: list of SpecialistConfig discovered under this dept
    """

    name: str
    display_name: str
    prompt_body: str
    manager_model: str
    manager_tools: list[str]
    dept_dir: Path
    manager_memory_path: Path
    reference_dir: Path
    knowledge_base_dir: Path
    output_dirs: dict[str, Path]
    specialists: list[SpecialistConfig]


# ---------------------------------------------------------------------------
# Loader entry points
# ---------------------------------------------------------------------------
_DEFAULT_MANAGER_TOOLS = ["Read", "Glob", "Grep", "Agent"]
# _DEFAULT_MODEL moved to core/config.py:_DEFAULT_MODEL in chunk 1a.1.
# Call sites below now go through config.get_model("default").


def _slug_to_display(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").title()


def load_specialists_for_department(dept_dir: Path, dept_name: str) -> list[SpecialistConfig]:
    """Walk one department folder, return all specialists whose folder
    contains a specialist.md file."""
    results: list[SpecialistConfig] = []
    if not dept_dir.exists() or not dept_dir.is_dir():
        return results

    for child in sorted(dept_dir.iterdir()):
        if not child.is_dir():
            continue
        spec_file = child / "specialist.md"
        if not spec_file.exists():
            continue

        text = spec_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)

        # Name: prefer frontmatter, fall back to folder name
        name = str(fm.get("name") or child.name)
        description = str(fm.get("description") or "").strip()
        attribute = str(fm.get("attribute") or "").strip()
        tools_raw = fm.get("tools") or []
        tools = [str(t) for t in tools_raw] if isinstance(tools_raw, list) else []
        model = str(fm.get("model") or config.get_model("default"))
        is_scout = bool(fm.get("scout") or False)

        memory_filename = str(fm.get("memory_file") or "memory.md")
        reference_subdir = str(fm.get("reference_dir") or "reference")

        memory_path = child / memory_filename
        reference_dir = child / reference_subdir

        results.append(
            SpecialistConfig(
                name=name,
                description=description,
                prompt_body=body.strip(),
                attribute=attribute,
                tools=tools,
                model=model,
                department=dept_name,
                is_scout=is_scout,
                memory_path=memory_path,
                reference_dir=reference_dir,
                specialist_dir=child,
            )
        )

    return results


def load_departments(company: CompanyConfig) -> list[DepartmentConfig]:
    """Auto-discover every active department in the company folder.

    A department exists iff its folder contains a department.md file. The
    `active_departments` list in config.json provides the canonical ordering
    and may filter which discovered departments are enabled; if that key is
    absent or empty, ALL discovered departments are returned.
    """
    # TODO: add functools.lru_cache to load_departments() before Phase 8.
    # Orchestrator.__init__ and dispatch_manager() both call this on each
    # invocation, which becomes a bottleneck in the Phase 8 onboarding loop.
    # See plan §17 risk #5. Caching requires CompanyConfig to be hashable
    # (currently a frozen dataclass — check before enabling).
    company_dir = company.company_dir
    configured = company.active_departments

    results: list[DepartmentConfig] = []
    candidate_names: list[str]
    if configured:
        candidate_names = configured
    else:
        # Fall back to whatever subfolders contain a department.md
        candidate_names = [
            p.name
            for p in company_dir.iterdir()
            if p.is_dir() and (p / "department.md").exists()
        ]

    for dept_name in candidate_names:
        dept_dir = company_dir / dept_name
        dept_file = dept_dir / "department.md"
        if not dept_file.exists():
            continue

        text = dept_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)

        display_name = str(fm.get("display_name") or _slug_to_display(dept_name))
        manager_model = str(fm.get("manager_model") or config.get_model("default"))
        tools_raw = fm.get("manager_tools") or _DEFAULT_MANAGER_TOOLS
        manager_tools = (
            [str(t) for t in tools_raw]
            if isinstance(tools_raw, list)
            else list(_DEFAULT_MANAGER_TOOLS)
        )

        manager_memory_path = dept_dir / "manager-memory.md"
        reference_dir = dept_dir / "reference"
        knowledge_base_dir = dept_dir / "knowledge-base"
        output_root = dept_dir / "output"
        output_dirs = {
            key: output_root / subdir
            for key, subdir in config.get_output_subdirs().items()
        }

        specialists = load_specialists_for_department(dept_dir, dept_name)

        results.append(
            DepartmentConfig(
                name=dept_name,
                display_name=display_name,
                prompt_body=body.strip(),
                manager_model=manager_model,
                manager_tools=manager_tools,
                dept_dir=dept_dir,
                manager_memory_path=manager_memory_path,
                reference_dir=reference_dir,
                knowledge_base_dir=knowledge_base_dir,
                output_dirs=output_dirs,
                specialists=specialists,
            )
        )

    return results
