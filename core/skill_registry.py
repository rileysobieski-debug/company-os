"""
core/skill_registry.py — Skill metadata index
=============================================
Walks `skills/employees/*.yaml` (and later `skills/specialists/*.yaml`)
and exposes each entry as a `SkillSpec`. Chunk 1a.4 shipped this as a
stub; chunk 1b.2 activates the YAML loader and the richer SkillSpec
schema that matches plan §4.1.

YAML file shape (one skill per file):

    ---
    skill_id: file-fetcher
    description: Retrieves files from the vault by path or pattern.
    mode: agentic
    tools: [Read, Glob, Grep]
    max_tool_iterations: 5
    max_tokens: 4096
    model: claude-haiku-4-5-20251001
    inputs: [path]
    outputs: [content]
    benchmarks_yaml_path: skills/benchmarks/file-fetcher.yaml
    rubric: |
      ...

`SkillRegistry.load(root=None)` defaults to `<repo>/skills/employees/`
relative to the `core/` package. Passing an explicit `root` is the
extension point for test fixtures and alternate skill namespaces
(e.g. per-company overrides in Phase 8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# SkillSpec — richer schema matching plan §4.1
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SkillSpec:
    """Metadata describing one skill."""

    skill_id: str
    description: str
    mode: str  # "pure" | "agentic"
    tools: tuple[str, ...] = ()
    max_tool_iterations: int = 5
    max_tokens: int = 4096
    model: str = "claude-haiku-4-5-20251001"
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    benchmarks_yaml_path: str = ""
    rubric: str = ""
    synthesis_difficulty: str | None = None  # None | "low" | "high"
    reasoning_required: bool = False  # §9 founder-approved Opus opt-in

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillSpec:
        """Build a SkillSpec from a parsed YAML dict. Extra keys are ignored."""
        def _tuple(x: Any) -> tuple[str, ...]:
            if isinstance(x, (list, tuple)):
                return tuple(str(v) for v in x)
            return ()

        return cls(
            skill_id=str(data["skill_id"]),
            description=str(data.get("description", "")),
            mode=str(data.get("mode", "agentic")),
            tools=_tuple(data.get("tools")),
            max_tool_iterations=int(data.get("max_tool_iterations", 5)),
            max_tokens=int(data.get("max_tokens", 4096)),
            model=str(data.get("model", "claude-haiku-4-5-20251001")),
            inputs=_tuple(data.get("inputs")),
            outputs=_tuple(data.get("outputs")),
            benchmarks_yaml_path=str(data.get("benchmarks_yaml_path", "")),
            rubric=str(data.get("rubric", "")),
            synthesis_difficulty=(
                str(data["synthesis_difficulty"])
                if data.get("synthesis_difficulty") is not None
                else None
            ),
            reasoning_required=bool(data.get("reasoning_required", False)),
        )


# ---------------------------------------------------------------------------
# Loader path resolution
# ---------------------------------------------------------------------------
# `core/` sits inside the company-os package; the skills/ tree lives at
# the sibling level. Resolve once; override per call via `load(root=…)`.
_DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills" / "employees"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class SkillRegistry:
    """In-memory index of available skills.

    `load()` is idempotent — calling it twice with the same root returns
    the same count and leaves the registry state unchanged.
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillSpec] = {}
        self._loaded: bool = False

    def load(self, root: Path | None = None) -> int:
        """Walk `root/*.yaml`, parse each, register as SkillSpec.

        Returns the number of NEW skills registered on this call. If
        `root` is None, defaults to `<repo>/skills/employees/`. A missing
        root directory returns 0 (not an error — the catalogue may be
        empty pre-1b.2 or during bootstrap).
        """
        root = Path(root) if root is not None else _DEFAULT_SKILLS_ROOT
        self._loaded = True
        if not root.exists() or not root.is_dir():
            return 0

        added = 0
        for path in sorted(root.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ValueError(f"skill YAML parse failed at {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError(
                    f"skill YAML at {path} must be a mapping, got {type(data).__name__}"
                )
            try:
                spec = SkillSpec.from_dict(data)
            except KeyError as exc:
                raise ValueError(
                    f"skill YAML at {path} missing required field: {exc}"
                ) from exc
            if spec.skill_id not in self._skills:
                added += 1
            self._skills[spec.skill_id] = spec
        return added

    def get(self, skill_id: str) -> SkillSpec:
        """Return the SkillSpec for `skill_id` or raise KeyError."""
        if not self._loaded:
            raise KeyError(
                f"skill {skill_id!r} not found — registry not yet loaded "
                "(call SkillRegistry.load() first)"
            )
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise KeyError(f"skill {skill_id!r} not found") from exc

    def ids(self) -> list[str]:
        """Return the list of registered skill IDs."""
        return sorted(self._skills.keys())

    def register(self, spec: SkillSpec) -> None:
        """Direct insertion for tests and alternate loaders."""
        self._skills[spec.skill_id] = spec
        self._loaded = True


default_registry = SkillRegistry()


def load(root: Path | None = None) -> int:
    """Load the default registry. Shim for function-style callers."""
    return default_registry.load(root)


def get(skill_id: str) -> SkillSpec:
    """Look up a skill on the default registry."""
    return default_registry.get(skill_id)
