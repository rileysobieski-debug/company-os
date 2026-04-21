"""Tests for comprehensive_demo after the Phase 13.2 vertical-pack rewrite.

No LLM calls. Verifies the resolution path from (company, dept, pack) → brief
and that the runner surface still exposes the functions webapp/services rely
on. Pre-13.2 tests that asserted Old Press-specific keywords in a hardcoded
DEPT_BRIEFS dict have been retired — that dict is now empty by design, and
company-specific text enters via template substitution in core.vertical_pack.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------
def test_demo_module_imports() -> None:
    import comprehensive_demo  # noqa: F401


def test_demo_exposes_phase_runners() -> None:
    import comprehensive_demo as cd
    assert callable(cd.run_department_demo)
    assert callable(cd.run_all_department_demos)
    assert callable(cd.run_orchestrator_synthesis)
    assert callable(cd.run_board_deliberation)
    assert callable(cd.write_index)


def test_demo_exposes_resolve_and_pack_surface() -> None:
    """Phase 13.2: the runner exposes `_resolve_brief` for inspection and
    imports `VerticalPack` / `load_vertical_pack` so callers can hand in a
    pack directly."""
    import comprehensive_demo as cd
    assert callable(cd._resolve_brief)
    assert cd.VerticalPack is not None
    assert callable(cd.load_vertical_pack)


def test_legacy_dept_briefs_is_now_empty_dict() -> None:
    """Backwards-compat alias: `DEPT_BRIEFS` is preserved as an empty dict
    so third-party callers that did `from comprehensive_demo import DEPT_BRIEFS`
    don't crash. Actual briefs now live in the vertical pack."""
    from comprehensive_demo import DEPT_BRIEFS
    assert DEPT_BRIEFS == {}


def test_fallback_brief_uses_company_name() -> None:
    """When no pack is loaded, the fallback brief is rendered with the
    company's name so the message isn't Old-Press-specific."""
    from comprehensive_demo import _fallback_brief

    @dataclass
    class _Co:
        name: str = "Test Company"

    out = _fallback_brief(_Co())
    assert "Test Company" in out


# ---------------------------------------------------------------------------
# _resolve_brief precedence
# ---------------------------------------------------------------------------
@dataclass
class _FakeCompany:
    name: str = "Old Press Wine Company"
    industry: str = "wine / beverage"
    settled_convictions: tuple[str, ...] = ()
    hard_constraints: tuple[str, ...] = ()
    priorities: tuple[str, ...] = ()


def test_resolve_brief_honors_override() -> None:
    from comprehensive_demo import _resolve_brief
    out = _resolve_brief(_FakeCompany(), "marketing", None, "BRIEF OVERRIDE")
    assert out == "BRIEF OVERRIDE"


def test_resolve_brief_uses_pack_when_dept_named() -> None:
    from comprehensive_demo import _resolve_brief
    from core.vertical_pack import load_vertical_pack

    pack = load_vertical_pack("wine-beverage")
    out = _resolve_brief(_FakeCompany(), "marketing", pack, None)
    # The rendered template should substitute the company name.
    assert "Old Press Wine Company" in out
    # And the template is long enough to elicit real work.
    assert len(out) > 300
    # Placeholders must be fully resolved.
    assert "{{ " not in out


def test_resolve_brief_falls_through_to_pack_default_for_unknown_dept() -> None:
    from comprehensive_demo import _resolve_brief
    from core.vertical_pack import load_vertical_pack

    pack = load_vertical_pack("wine-beverage")
    out = _resolve_brief(_FakeCompany(), "a-dept-not-in-the-pack", pack, None)
    # The pack has a default_brief; should render it with company substitution.
    assert "Old Press Wine Company" in out


def test_resolve_brief_falls_through_to_module_fallback_when_pack_none() -> None:
    from comprehensive_demo import _resolve_brief, DEFAULT_FALLBACK_BRIEF
    out = _resolve_brief(_FakeCompany(), "marketing", None, None)
    # The module-level fallback renders with {company_name}.
    assert "Old Press Wine Company" in out
    # And is derived from DEFAULT_FALLBACK_BRIEF.
    assert "Demonstrate your department" in out


def test_resolve_brief_covers_all_active_depts(company) -> None:
    """For every active dept on a live company, the wine-beverage pack
    either has a named brief or provides a default that renders cleanly."""
    from comprehensive_demo import _resolve_brief
    from core.vertical_pack import load_vertical_pack

    pack = load_vertical_pack("wine-beverage")
    for dept_name in company.active_departments:
        brief = _resolve_brief(company, dept_name, pack, None)
        assert brief, f"empty brief for {dept_name}"
        assert "{{ " not in brief, f"unresolved placeholders in {dept_name}"
        assert len(brief) > 200, f"brief for {dept_name} too short"


# ---------------------------------------------------------------------------
# Default board topic
# ---------------------------------------------------------------------------
def test_default_board_topic_references_dossier() -> None:
    from comprehensive_demo import DEFAULT_BOARD_TOPIC
    low = DEFAULT_BOARD_TOPIC.lower()
    assert "operating model" in low or "first commercial sale" in low


# ---------------------------------------------------------------------------
# Demo artifact path scaffolding
# ---------------------------------------------------------------------------
def test_demo_dirs_created(company) -> None:
    from comprehensive_demo import _ensure_demo_dirs
    root, depts = _ensure_demo_dirs(company)
    assert root.exists() and root.is_dir()
    assert depts.exists() and depts.is_dir()
    assert depts.parent == root


def test_demo_artifact_path_per_dept(company, departments) -> None:
    from comprehensive_demo import _demo_artifact_path
    for dept in departments:
        p = _demo_artifact_path(company, dept.name)
        assert p.suffix == ".md"
        assert p.name == f"{dept.name}-demo.md"
        # parent dir must already exist (ensured by helper)
        assert p.parent.exists()


# ---------------------------------------------------------------------------
# Synthesis prompt scaffolding (no API call)
# ---------------------------------------------------------------------------
def test_synthesis_system_template_has_required_slots() -> None:
    from comprehensive_demo import _SYNTHESIS_SYSTEM
    for slot in ("{company_name}", "{company_context}", "{settled_convictions}",
                 "{hard_constraints}", "{priorities}"):
        assert slot in _SYNTHESIS_SYSTEM, f"Missing slot in synthesis system: {slot}"


def test_synthesis_user_template_has_dept_blocks() -> None:
    from comprehensive_demo import _SYNTHESIS_USER
    assert "{dept_blocks}" in _SYNTHESIS_USER
    assert "{company_name}" in _SYNTHESIS_USER


# ---------------------------------------------------------------------------
# Legacy archive file
# ---------------------------------------------------------------------------
def test_legacy_archive_importable_and_inert() -> None:
    """The archived Old Press briefs stay importable for reference but the
    main runner must not import them."""
    import comprehensive_demo_legacy as legacy
    assert len(legacy.LEGACY_DEPT_BRIEFS) == 9
    assert "marketing" in legacy.LEGACY_DEPT_BRIEFS
    assert "Old Press" in legacy.LEGACY_DEFAULT_DEPT_BRIEF


def test_main_runner_does_not_import_legacy_archive() -> None:
    """Phase 13.2 acceptance: the runner must not pull the legacy
    hardcoded content back in."""
    import comprehensive_demo as cd
    # It should not have any attribute referencing the legacy dict.
    assert not hasattr(cd, "LEGACY_DEPT_BRIEFS")
    assert not hasattr(cd, "LEGACY_DEFAULT_DEPT_BRIEF")
