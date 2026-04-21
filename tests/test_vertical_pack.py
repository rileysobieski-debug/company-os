"""Vertical-pack demo brief loader (Phase 13.1)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.vertical_pack import (
    DeptBriefTemplate,
    VerticalPack,
    load_vertical_pack,
    render_dept_brief,
)


# ---------------------------------------------------------------------------
# Fake company (dataclass; the real CompanyConfig has properties that return
# from raw_config, but render_dept_brief treats it attribute-by-attribute)
# ---------------------------------------------------------------------------
@dataclass
class _FakeCompany:
    name: str = "Test Co"
    industry: str = "wine / beverage"
    settled_convictions: tuple[str, ...] = ()
    hard_constraints: tuple[str, ...] = ()
    priorities: tuple[str, ...] = ()


def _minimal_pack_yaml() -> str:
    return """
dept_briefs:
  marketing:
    title: Positioning memo
    template: |
      Run positioning work for {{ company.name }} ({{ company.industry }}).
      Constraints:
      {{ settled_convictions }}
      {{ hard_constraints }}
  finance:
    title: Cash plan
    template: |
      Cash plan for {{ company.name }}.
default_brief:
  title: Generic brief
  template: |
    Generic demo brief for {{ company.name }}.
"""


def _write_pack(tmp_path: Path, yaml_text: str, vertical: str = "wine-beverage") -> Path:
    root = tmp_path / "verticals"
    (root / vertical).mkdir(parents=True, exist_ok=True)
    path = root / vertical / "dept_briefs.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def test_load_reads_all_dept_briefs(tmp_path: Path) -> None:
    root = _write_pack(tmp_path, _minimal_pack_yaml())
    pack = load_vertical_pack("wine-beverage", root=root)
    assert set(pack.names()) == {"marketing", "finance"}
    assert pack.dept_briefs["marketing"].title == "Positioning memo"


def test_load_captures_default_brief(tmp_path: Path) -> None:
    root = _write_pack(tmp_path, _minimal_pack_yaml())
    pack = load_vertical_pack("wine-beverage", root=root)
    assert pack.default_brief is not None
    assert pack.default_brief.title == "Generic brief"


def test_load_missing_pack_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="wine-beverage"):
        load_vertical_pack("wine-beverage", root=tmp_path)


def test_load_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    root = _write_pack(tmp_path, "- not a mapping\n- just a list\n")
    with pytest.raises(ValueError, match="mapping"):
        load_vertical_pack("wine-beverage", root=root)


def test_load_rejects_empty_template(tmp_path: Path) -> None:
    root = _write_pack(tmp_path, """
dept_briefs:
  marketing:
    title: Empty
    template: ""
""")
    with pytest.raises(ValueError, match="empty template"):
        load_vertical_pack("wine-beverage", root=root)


def test_load_pack_with_only_default_is_valid(tmp_path: Path) -> None:
    root = _write_pack(tmp_path, """
default_brief:
  title: Generic
  template: |
    Generic demo brief.
""")
    pack = load_vertical_pack("wine-beverage", root=root)
    assert pack.names() == ()
    assert pack.default_brief is not None


# ---------------------------------------------------------------------------
# brief_for lookup
# ---------------------------------------------------------------------------
def test_brief_for_named_dept_returns_its_template(tmp_path: Path) -> None:
    root = _write_pack(tmp_path, _minimal_pack_yaml())
    pack = load_vertical_pack("wine-beverage", root=root)
    tmpl = pack.brief_for("marketing")
    assert tmpl.title == "Positioning memo"


def test_brief_for_unknown_dept_returns_default(tmp_path: Path) -> None:
    root = _write_pack(tmp_path, _minimal_pack_yaml())
    pack = load_vertical_pack("wine-beverage", root=root)
    tmpl = pack.brief_for("nonexistent")
    assert tmpl.title == "Generic brief"


def test_brief_for_unknown_dept_with_no_default_raises(tmp_path: Path) -> None:
    root = _write_pack(tmp_path, """
dept_briefs:
  marketing:
    title: T
    template: |
      X
""")
    pack = load_vertical_pack("wine-beverage", root=root)
    with pytest.raises(KeyError, match="no default"):
        pack.brief_for("finance")


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def test_render_substitutes_company_name() -> None:
    tmpl = DeptBriefTemplate(
        title="t",
        template="Work for {{ company.name }} in {{ company.industry }}.",
    )
    out = render_dept_brief(tmpl, company=_FakeCompany(name="Old Press", industry="wine"))
    assert "Work for Old Press in wine." in out


def test_render_substitutes_settled_convictions_as_bullets() -> None:
    tmpl = DeptBriefTemplate(
        title="t",
        template="Constraints:\n{{ settled_convictions }}\nDone.",
    )
    out = render_dept_brief(tmpl, company=_FakeCompany(
        settled_convictions=("Maine base", "No equity"),
    ))
    assert "  - Maine base" in out
    assert "  - No equity" in out


def test_render_substitutes_priorities_as_numbered_list() -> None:
    tmpl = DeptBriefTemplate(
        title="t",
        template="Priorities:\n{{ priorities }}",
    )
    out = render_dept_brief(tmpl, company=_FakeCompany(
        priorities=("Secure W-2", "Finish TTB"),
    ))
    assert "  1. Secure W-2" in out
    assert "  2. Finish TTB" in out


def test_render_missing_value_yields_empty_placeholder() -> None:
    tmpl = DeptBriefTemplate(
        title="t",
        template="A={{ company.name }}, B={{ nonexistent }}, C={{ company.industry }}.",
    )
    out = render_dept_brief(tmpl, company=_FakeCompany(name="X", industry="Y"))
    # Unknown placeholder renders empty; known ones substitute.
    assert "A=X" in out
    assert "C=Y" in out
    assert "{{ nonexistent }}" not in out  # consumed by the regex


def test_render_empty_list_renders_empty_string() -> None:
    tmpl = DeptBriefTemplate(
        title="t",
        template="Prefix\n{{ settled_convictions }}\nSuffix",
    )
    out = render_dept_brief(tmpl, company=_FakeCompany())
    assert "Prefix" in out
    assert "Suffix" in out
    # No bullet lines.
    assert "  -" not in out


def test_render_accepts_dict_company() -> None:
    """Callers can hand in plain dicts for testing — the accessor falls
    back to item lookup when attribute access fails."""
    tmpl = DeptBriefTemplate(
        title="t",
        template="Co={{ company.name }}",
    )
    out = render_dept_brief(tmpl, company={"name": "Dict Co", "industry": ""})
    assert "Co=Dict Co" in out


# ---------------------------------------------------------------------------
# Real wine-beverage pack sanity
# ---------------------------------------------------------------------------
def test_real_wine_beverage_pack_loads() -> None:
    """The canonical pack shipped in verticals/wine-beverage/dept_briefs.yaml
    should load and cover all 9 VERTICAL_DEPARTMENTS dept names."""
    from core.onboarding.dept_selection import VERTICAL_DEPARTMENTS

    pack = load_vertical_pack("wine-beverage")
    assert set(pack.names()) == set(VERTICAL_DEPARTMENTS)
    assert pack.default_brief is not None


def test_real_pack_renders_cleanly_for_old_press_like_company() -> None:
    pack = load_vertical_pack("wine-beverage")
    company = _FakeCompany(
        name="Old Press Wine Company",
        industry="wine / beverage",
        settled_convictions=("Coastal Maine is the operational base",),
        hard_constraints=("No selling through distributors in Year 1",),
    )
    rendered = render_dept_brief(pack.brief_for("marketing"), company=company)
    assert "Old Press Wine Company" in rendered
    assert "Coastal Maine" in rendered
    assert "{{ " not in rendered  # no un-substituted placeholders


def test_real_pack_default_brief_fallback() -> None:
    pack = load_vertical_pack("wine-beverage")
    tmpl = pack.brief_for("a-dept-the-pack-has-never-heard-of")
    assert tmpl == pack.default_brief
