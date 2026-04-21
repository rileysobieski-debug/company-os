"""Reasoning-required flag toggle + Opus escalation (Phase 10.3 — §9)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.skill_registry import SkillRegistry, SkillSpec
from core.skill_runner import _select_model
from core.training import mark_reasoning_required


def _write_skill_yaml(path: Path, **overrides) -> None:
    data = {
        "skill_id": "web-researcher",
        "description": "Searches the web.",
        "mode": "agentic",
        "tools": ["WebSearch", "WebFetch"],
        "max_tool_iterations": 5,
        "max_tokens": 4096,
        "model": "claude-haiku-4-5-20251001",
        "inputs": ["query"],
        "outputs": ["findings"],
        "benchmarks_yaml_path": "",
        "rubric": "Cite every claim.",
    }
    data.update(overrides)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# SkillSpec parsing
# ---------------------------------------------------------------------------
def test_spec_defaults_reasoning_required_false() -> None:
    spec = SkillSpec.from_dict({
        "skill_id": "s", "description": "", "mode": "agentic",
    })
    assert spec.reasoning_required is False


def test_spec_parses_reasoning_required_flag() -> None:
    spec = SkillSpec.from_dict({
        "skill_id": "s", "description": "", "mode": "agentic",
        "reasoning_required": True,
    })
    assert spec.reasoning_required is True


def test_registry_roundtrips_flag_from_yaml(tmp_path: Path) -> None:
    _write_skill_yaml(
        tmp_path / "web-researcher.yaml",
        reasoning_required=True,
    )
    reg = SkillRegistry()
    count = reg.load(tmp_path)
    assert count == 1
    assert reg.get("web-researcher").reasoning_required is True


# ---------------------------------------------------------------------------
# Model selection honors the flag
# ---------------------------------------------------------------------------
def test_reasoning_required_selects_opus() -> None:
    spec = SkillSpec(
        skill_id="s", description="", mode="agentic",
        reasoning_required=True,
    )
    assert _select_model(spec, {}) == "claude-opus-4-6"


def test_reasoning_required_beats_synthesis_difficulty() -> None:
    spec = SkillSpec(
        skill_id="s", description="", mode="agentic",
        reasoning_required=True,
        synthesis_difficulty="high",
    )
    # Opus wins over Sonnet.
    assert _select_model(spec, {}) == "claude-opus-4-6"


def test_inputs_reasoning_required_override() -> None:
    """Per-invocation override — `inputs.reasoning_required=True` forces Opus
    even when the spec has the flag off."""
    spec = SkillSpec(
        skill_id="s", description="", mode="agentic",
        reasoning_required=False,
    )
    assert _select_model(spec, {"reasoning_required": True}) == "claude-opus-4-6"


def test_no_flag_defaults_to_haiku() -> None:
    spec = SkillSpec(skill_id="s", description="", mode="agentic")
    model = _select_model(spec, {})
    assert "haiku" in model


def test_synthesis_difficulty_still_picks_sonnet_without_reasoning() -> None:
    spec = SkillSpec(
        skill_id="s", description="", mode="agentic",
        synthesis_difficulty="high",
    )
    assert _select_model(spec, {}) == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Toggle primitive
# ---------------------------------------------------------------------------
def test_mark_true_writes_flag(tmp_path: Path) -> None:
    _write_skill_yaml(tmp_path / "web-researcher.yaml")
    changed = mark_reasoning_required("web-researcher", tmp_path, required=True)
    assert changed is True
    data = yaml.safe_load(
        (tmp_path / "web-researcher.yaml").read_text(encoding="utf-8")
    )
    assert data["reasoning_required"] is True


def test_mark_true_is_idempotent(tmp_path: Path) -> None:
    _write_skill_yaml(tmp_path / "web-researcher.yaml", reasoning_required=True)
    changed = mark_reasoning_required("web-researcher", tmp_path, required=True)
    assert changed is False


def test_mark_false_turns_flag_off(tmp_path: Path) -> None:
    _write_skill_yaml(tmp_path / "web-researcher.yaml", reasoning_required=True)
    changed = mark_reasoning_required("web-researcher", tmp_path, required=False)
    assert changed is True
    data = yaml.safe_load(
        (tmp_path / "web-researcher.yaml").read_text(encoding="utf-8")
    )
    assert data["reasoning_required"] is False


def test_mark_preserves_other_fields(tmp_path: Path) -> None:
    _write_skill_yaml(
        tmp_path / "web-researcher.yaml",
        description="A specific description that must survive.",
        max_tokens=2048,
    )
    mark_reasoning_required("web-researcher", tmp_path, required=True)
    data = yaml.safe_load(
        (tmp_path / "web-researcher.yaml").read_text(encoding="utf-8")
    )
    assert data["description"] == "A specific description that must survive."
    assert data["max_tokens"] == 2048
    assert data["skill_id"] == "web-researcher"


def test_mark_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        mark_reasoning_required("nonexistent", tmp_path, required=True)


def test_mark_after_registry_reload_surfaces_new_flag(tmp_path: Path) -> None:
    """End-to-end: edit file → reload registry → flag takes effect."""
    _write_skill_yaml(tmp_path / "web-researcher.yaml")
    reg = SkillRegistry()
    reg.load(tmp_path)
    assert reg.get("web-researcher").reasoning_required is False

    mark_reasoning_required("web-researcher", tmp_path, required=True)

    reg2 = SkillRegistry()
    reg2.load(tmp_path)
    assert reg2.get("web-researcher").reasoning_required is True
