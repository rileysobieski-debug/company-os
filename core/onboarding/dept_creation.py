"""
core/onboarding/dept_creation.py — operator-initiated department creation
=========================================================================
The loader (core.managers.loader.load_departments) discovers any folder
under a company dir that contains `department.md`. This module provides
an operator-facing primitive for creating that folder + file cleanly,
updating config.json's `active_departments` list, and optionally
registering the dept with the vertical pack's scope matrix.

Used by:
  * `cli add-dept` CLI subcommand (Chunk 13.5)
  * the webapp's "+ Add Department" GUI form
  * tests that need to stand up a dept without hand-building files

Safety:
  * Slug must be kebab-case and not collide with existing dept folders
    or reserved company-dir filenames.
  * Existing departments are never overwritten — create fails loudly.
  * Scope-matrix updates are optional; failure to locate the matrix
    is logged, not fatal.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import yaml


_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_RESERVED_NAMES = frozenset({
    # Company-level files; never shadow as a dept name.
    "config.json",
    "context.md",
    "priorities.md",
    "founder_profile.md",
    "domain.md",
    "state-authority.md",
    "compliance_gates.md",
    "vendor_registry.md",
    "digest.md",
    "cost-log.jsonl",
    "assumptions-log.jsonl",
    # Company-level subdirs; collision would break loaders/indexers.
    "knowledge-base",
    "brand-db",
    "taste",
    "handshakes",
    "decisions",
    "autoresearch-runs",
    "sessions",
    "board",
    "demo-artifacts",
    "evaluations",
    "output",
})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeptCreationResult:
    """Everything the caller needs to know after the create succeeded."""

    dept_dir: Path
    department_md: Path
    manager_memory: Path
    config_updated: bool
    scope_matrix_updated: bool


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def add_department(
    company_dir: Path,
    slug: str,
    *,
    display_name: str | None = None,
    prompt_body: str = "",
    manager_model: str | None = None,
    owns: Sequence[str] = (),
    never: Sequence[str] = (),
    activate: bool = True,
    vertical: str = "wine-beverage",
    repo_root: Path | None = None,
) -> DeptCreationResult:
    """Create a new department on disk.

    Parameters
    ----------
    company_dir
        Root of the company folder (must exist).
    slug
        Kebab-case dept name — becomes both the folder name and the
        lookup key used by `load_departments`.
    display_name
        Optional human-readable name for the manager's prompt header.
        Defaults to `slug.replace("-", " ").title()`.
    prompt_body
        The body of `department.md` below the frontmatter. This becomes
        the manager's prompt. Empty string produces a minimal scaffold
        the founder is expected to edit later.
    manager_model
        Optional Claude model ID for this manager (defaults to what
        `config.get_model("default")` resolves to at load time).
    owns, never
        Optional scope matrix entries. If the vertical pack's
        `scope_matrix.yaml` exists at `<repo_root>/verticals/<vertical>/`,
        this dept will be appended to it.
    activate
        If True (default) and config.json exists, append `slug` to
        `active_departments` so the loader picks up the new dept
        without requiring an explicit reload.
    vertical
        Which vertical pack to update the scope matrix for. Default is
        `wine-beverage` (the only pack currently shipped).
    repo_root
        Root of the company-os repo (the dir containing `verticals/`).
        Default: this module's parent-parent — usually correct in dev.

    Returns
    -------
    DeptCreationResult
        Paths written + flags indicating which side-effects fired.

    Raises
    ------
    FileNotFoundError
        If `company_dir` does not exist.
    ValueError
        If `slug` is malformed, reserved, or already in use.
    """
    _validate_slug(slug)

    company_dir = Path(company_dir).resolve()
    if not company_dir.is_dir():
        raise FileNotFoundError(f"company dir does not exist: {company_dir}")

    dept_dir = company_dir / slug
    if dept_dir.exists():
        raise ValueError(
            f"a folder already exists at {dept_dir} — dept {slug!r} "
            "cannot be created. Pick a different slug or remove the "
            "existing folder first."
        )

    dept_dir.mkdir(parents=True)
    department_md = dept_dir / "department.md"
    manager_memory = dept_dir / "manager-memory.md"

    _write_department_md(
        department_md,
        slug=slug,
        display_name=display_name or _slug_to_display(slug),
        prompt_body=prompt_body.strip(),
        manager_model=manager_model,
    )
    manager_memory.touch()

    config_updated = False
    if activate:
        config_updated = _append_to_active_departments(
            company_dir / "config.json", slug,
        )

    scope_matrix_updated = False
    if owns or never:
        if repo_root is None:
            repo_root = Path(__file__).resolve().parent.parent.parent
        scope_matrix_updated = _append_to_scope_matrix(
            repo_root / "verticals" / vertical / "scope_matrix.yaml",
            slug, owns, never,
        )

    return DeptCreationResult(
        dept_dir=dept_dir,
        department_md=department_md,
        manager_memory=manager_memory,
        config_updated=config_updated,
        scope_matrix_updated=scope_matrix_updated,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_slug(slug: str) -> None:
    if not slug:
        raise ValueError("slug must be non-empty")
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"slug {slug!r} must be kebab-case: "
            "start with a letter, lowercase letters/digits/hyphens only"
        )
    if slug in _RESERVED_NAMES:
        raise ValueError(
            f"slug {slug!r} collides with a reserved company-dir name"
        )


def _slug_to_display(slug: str) -> str:
    return slug.replace("-", " ").title()


def _write_department_md(
    path: Path,
    *,
    slug: str,
    display_name: str,
    prompt_body: str,
    manager_model: str | None,
) -> None:
    """Compose a clean department.md with YAML frontmatter + body."""
    fm_lines = [
        "---",
        f"display_name: {display_name}",
    ]
    if manager_model:
        fm_lines.append(f"manager_model: {manager_model}")
    fm_lines.append("---")
    fm = "\n".join(fm_lines)

    body = prompt_body or _default_dept_prompt(slug, display_name)
    path.write_text(fm + "\n\n" + body.strip() + "\n", encoding="utf-8")


def _default_dept_prompt(slug: str, display_name: str) -> str:
    """Minimal manager prompt used when the caller doesn't provide one.

    This is intentionally thin — the founder is expected to edit
    `department.md` to match how they actually want this dept to work.
    Leaving placeholders here (vs. fabricating a plausible-looking
    prompt) is the honest default."""
    return (
        f"# {display_name} — Department Charter\n\n"
        "## What this department owns\n\n"
        "_(Replace with the department's actual scope — what it is "
        "accountable for, what outputs it produces, what success "
        "looks like.)_\n\n"
        "## How this department works\n\n"
        "_(Replace with the dept's operating cadence, specialist "
        "roster intent, and any coordination rules with other depts.)_\n\n"
        "## What this department never does\n\n"
        "_(Replace with the topics this dept routes elsewhere. "
        "Enforced by the scope matrix if populated.)_\n"
    )


def _append_to_active_departments(config_path: Path, slug: str) -> bool:
    """Append `slug` to config.json's `active_departments` list.

    Idempotent: already-present → no-op. Returns True iff the file was
    written. Missing config.json → False (not an error)."""
    if not config_path.exists():
        return False
    data = json.loads(config_path.read_text(encoding="utf-8"))
    existing = list(data.get("active_departments") or [])
    if slug in existing:
        return False
    existing.append(slug)
    data["active_departments"] = existing
    config_path.write_text(
        json.dumps(data, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return True


def _append_to_scope_matrix(
    matrix_path: Path,
    slug: str,
    owns: Iterable[str],
    never: Iterable[str],
) -> bool:
    """Add a dept entry to the vertical pack's scope_matrix.yaml.

    Idempotent: existing dept → merge owns/never sets (union). Missing
    matrix file → False. Returns True iff the file was written."""
    if not matrix_path.exists():
        return False
    data = yaml.safe_load(matrix_path.read_text(encoding="utf-8")) or {}
    depts = data.setdefault("departments", {})
    entry = depts.get(slug) or {}
    owns_set = list(dict.fromkeys(list(entry.get("owns") or []) + list(owns)))
    never_set = list(dict.fromkeys(list(entry.get("never") or []) + list(never)))
    depts[slug] = {"owns": owns_set, "never": never_set}
    matrix_path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return True
