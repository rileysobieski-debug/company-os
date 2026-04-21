"""
core/vertical_pack.py — Phase 13.1 — §13 Phase 13 vertical-pack loader
======================================================================
Plan §13 Phase 13 line 690:

  "comprehensive_demo.py REWRITE+ARCHIVE... rewrite as a thin vertical-
   agnostic runner driven by the active vertical pack (Old Press content
   archived, not re-embedded)."

This primitive loads per-vertical demo-brief templates from
`verticals/<name>/dept_briefs.yaml` and renders them with company-
specific context. Company-specific text (company name, positioning,
settled convictions, hard constraints, priorities) comes from
`CompanyConfig` at render time — nothing company-specific lives in the
vertical pack.

Template placeholders (all optional — missing values render as empty):
  {{ company.name }}        — company.name
  {{ company.industry }}    — company.industry
  {{ settled_convictions }} — bullet-rendered from company.settled_convictions
  {{ hard_constraints }}    — bullet-rendered from company.hard_constraints
  {{ priorities }}          — numbered list from company.priorities

Public surface:
  * `DeptBriefTemplate(title, template)` frozen
  * `VerticalPack(vertical_name, dept_briefs, default_brief)` frozen
  * `load_vertical_pack(vertical_name, *, root=None)` — YAML loader
  * `render_dept_brief(template, *, company)` — substitute placeholders
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


_DEFAULT_VERTICALS_ROOT = Path(__file__).resolve().parent.parent / "verticals"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeptBriefTemplate:
    title: str
    template: str


@dataclass(frozen=True)
class VerticalPack:
    vertical_name: str
    dept_briefs: dict[str, DeptBriefTemplate] = field(default_factory=dict)
    default_brief: DeptBriefTemplate | None = None

    def has(self, dept: str) -> bool:
        return dept in self.dept_briefs

    def brief_for(self, dept: str) -> DeptBriefTemplate:
        """Return the dept's template or the default (if configured).

        Raises KeyError when neither the dept nor a default is present —
        that's a vertical-pack misconfiguration.
        """
        if dept in self.dept_briefs:
            return self.dept_briefs[dept]
        if self.default_brief is not None:
            return self.default_brief
        raise KeyError(
            f"no brief for dept {dept!r} and no default_brief configured"
        )

    def names(self) -> tuple[str, ...]:
        return tuple(self.dept_briefs.keys())


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def load_vertical_pack(
    vertical_name: str,
    *,
    root: Path | None = None,
) -> VerticalPack:
    """Load `verticals/<vertical_name>/dept_briefs.yaml` into a VerticalPack.

    Raises FileNotFoundError when the YAML is missing. Missing fields
    are tolerated: a pack with only a `default_brief` and no named dept
    briefs is valid (every dept falls through).
    """
    root_dir = Path(root) if root is not None else _DEFAULT_VERTICALS_ROOT
    pack_path = root_dir / vertical_name / "dept_briefs.yaml"
    if not pack_path.exists():
        raise FileNotFoundError(
            f"no vertical pack at {pack_path} "
            f"(vertical={vertical_name!r})"
        )
    data = yaml.safe_load(pack_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        raise ValueError(
            f"vertical pack at {pack_path} must be a mapping; "
            f"got {type(data).__name__}"
        )

    dept_briefs: dict[str, DeptBriefTemplate] = {}
    for name, body in (data.get("dept_briefs") or {}).items():
        dept_briefs[str(name)] = _coerce_template(body, where=f"{name}")

    default_body = data.get("default_brief")
    default_brief = (
        _coerce_template(default_body, where="default_brief")
        if default_body else None
    )

    return VerticalPack(
        vertical_name=vertical_name,
        dept_briefs=dept_briefs,
        default_brief=default_brief,
    )


def _coerce_template(body: Any, *, where: str) -> DeptBriefTemplate:
    if not isinstance(body, Mapping):
        raise ValueError(
            f"brief entry {where!r} must be a mapping with "
            "`title` + `template`"
        )
    title = str(body.get("title") or "").strip()
    template = str(body.get("template") or "").strip()
    if not template:
        raise ValueError(f"brief entry {where!r} has empty template")
    return DeptBriefTemplate(title=title, template=template)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_dept_brief(
    template: DeptBriefTemplate,
    *,
    company: Any,
) -> str:
    """Substitute `{{ company.name }}`, `{{ settled_convictions }}`, etc.

    Unknown placeholders render as empty strings (permissive) rather
    than raising — the rendered brief is still usable, just missing a
    detail. The caller can grep the output for specific markers to
    detect an incomplete render if they care.
    """
    values: dict[str, str] = {
        "company.name": _get(company, "name"),
        "company.industry": _get(company, "industry"),
        "settled_convictions": _render_bullets(
            _get_list(company, "settled_convictions")
        ),
        "hard_constraints": _render_bullets(
            _get_list(company, "hard_constraints")
        ),
        "priorities": _render_numbered(
            _get_list(company, "priorities")
        ),
    }

    def _replace(match: re.Match) -> str:
        key = match.group(1).strip()
        return values.get(key, "")

    rendered = _PLACEHOLDER_RE.sub(_replace, template.template)
    return rendered.strip() + "\n"


def _get(obj: Any, attr: str) -> str:
    """Safe attribute/key accessor returning a string."""
    try:
        value = getattr(obj, attr)
    except AttributeError:
        try:
            value = obj[attr]  # support plain dict fixtures in tests
        except (TypeError, KeyError):
            return ""
    return "" if value is None else str(value)


def _get_list(obj: Any, attr: str) -> list[str]:
    try:
        value = getattr(obj, attr)
    except AttributeError:
        try:
            value = obj[attr]
        except (TypeError, KeyError):
            return []
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(v) for v in value]
    except TypeError:
        return []


def _render_bullets(items: list[str]) -> str:
    if not items:
        return ""
    return "\n".join(f"  - {item}" for item in items)


def _render_numbered(items: list[str]) -> str:
    if not items:
        return ""
    return "\n".join(f"  {i}. {item}" for i, item in enumerate(items, start=1))
