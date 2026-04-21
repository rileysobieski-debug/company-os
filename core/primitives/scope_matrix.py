"""
core/primitives/scope_matrix.py — Phase 9.1 — §6 scope matrix
==============================================================
Plan §6: each department declares `OWNS` (topics it is responsible for)
and `NEVER` (topics it must not produce). The scope matrix is per-
vertical (canonical wine-beverage matrix lands in Chunk 9.3) and
applies whether a dept is active or dormant — dormant depts still show
their OWNS / NEVER in the capability menu so the founder can decide
when to activate them.

This module is the data layer + per-dept validator:

  * `DepartmentScope(dept, owns, never)` — frozen
  * `ScopeMatrix(departments)` — frozen container; lookup by dept name
  * `load_scope_matrix(path)` / `parse_scope_matrix(text)` — YAML parser
  * `validate_output_in_scope(matrix, dept, topic) -> ScopeValidation`
    — keyword substring match, case-insensitive, deterministic.
    NEVER-hit loses even if OWNS also hits (exclusions are hard).

The matrix is the source of truth. The overlap validator (Chunk 9.2)
operates on this same type. The specialist-tool-use migration (Chunk
9.4) reuses the same matrix to gate declared tools.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

import yaml


@dataclass(frozen=True)
class DepartmentScope:
    dept: str
    owns: tuple[str, ...] = ()
    never: tuple[str, ...] = ()

    def covers(self, topic: str) -> tuple[str, ...]:
        """Return any OWNS entries that the topic text matches."""
        t = topic.lower()
        return tuple(item for item in self.owns if item.lower() in t)

    def excludes(self, topic: str) -> tuple[str, ...]:
        """Return any NEVER entries that the topic text matches."""
        t = topic.lower()
        return tuple(item for item in self.never if item.lower() in t)


@dataclass(frozen=True)
class ScopeMatrix:
    departments: tuple[DepartmentScope, ...]

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def __contains__(self, dept: str) -> bool:
        return any(d.dept == dept for d in self.departments)

    def has(self, dept: str) -> bool:
        return dept in self

    def __getitem__(self, dept: str) -> DepartmentScope:
        for d in self.departments:
            if d.dept == dept:
                return d
        raise KeyError(f"unknown department {dept!r}")

    def names(self) -> tuple[str, ...]:
        return tuple(d.dept for d in self.departments)

    # ------------------------------------------------------------------
    # Menu rendering (plan §6: capability menu even for dormant depts)
    # ------------------------------------------------------------------
    def render_capability_menu(
        self,
        *,
        active_departments: Iterable[str] = (),
    ) -> str:
        """Return a human-readable menu listing OWNS per dept.

        Active depts get an `[ACTIVE]` marker; dormant depts appear with
        `[DORMANT]` so the founder can see what is available to activate.
        """
        active_set = set(active_departments)
        lines = ["# Capability menu", ""]
        for scope in self.departments:
            tag = "[ACTIVE]" if scope.dept in active_set else "[DORMANT]"
            lines.append(f"## {scope.dept} {tag}")
            lines.append("")
            lines.append("**Owns:**")
            if scope.owns:
                lines.extend(f"- {o}" for o in scope.owns)
            else:
                lines.append("- (none declared)")
            if scope.never:
                lines.append("")
                lines.append("**Never:**")
                lines.extend(f"- {n}" for n in scope.never)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def parse_scope_matrix(text: str) -> ScopeMatrix:
    """Parse YAML of the form:

        departments:
          marketing:
            owns: [brand positioning, audience-building]
            never: [regulatory filings]
          finance:
            owns: [...]
            never: [...]
    """
    data = yaml.safe_load(text) or {}
    depts_raw = data.get("departments")
    if not isinstance(depts_raw, Mapping):
        raise ValueError(
            "scope_matrix YAML must have a top-level 'departments' mapping"
        )
    scopes: list[DepartmentScope] = []
    for name, body in depts_raw.items():
        if body is None:
            owns: tuple[str, ...] = ()
            never: tuple[str, ...] = ()
        elif isinstance(body, Mapping):
            owns = tuple(_coerce_list(body.get("owns")))
            never = tuple(_coerce_list(body.get("never")))
        else:
            raise ValueError(
                f"scope entry for {name!r} must be a mapping or null"
            )
        scopes.append(DepartmentScope(dept=str(name), owns=owns, never=never))
    return ScopeMatrix(departments=tuple(scopes))


def _coerce_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"expected list, got {type(value).__name__}: {value!r}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"scope entries must be strings, got {item!r}")
        item = item.strip()
        if item:
            out.append(item)
    return out


def load_scope_matrix(path: Path) -> ScopeMatrix:
    return parse_scope_matrix(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Per-dept validation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScopeValidation:
    ok: bool
    matched_owns: tuple[str, ...]
    matched_never: tuple[str, ...]
    reason: str


def validate_output_in_scope(
    matrix: ScopeMatrix,
    dept: str,
    topic: str,
) -> ScopeValidation:
    """Check whether `topic` fits `dept`'s OWNS/NEVER rules.

    * NEVER-hit loses outright (exclusions are hard).
    * OWNS-hit → ok.
    * No hit either way → ok=False with reason "topic not in dept OWNS"
      so the orchestrator can route to a better dept or ask the founder.
    """
    if dept not in matrix:
        return ScopeValidation(
            ok=False, matched_owns=(), matched_never=(),
            reason=f"dept {dept!r} not present in scope matrix",
        )
    scope = matrix[dept]
    never_hits = scope.excludes(topic)
    owns_hits = scope.covers(topic)
    if never_hits:
        return ScopeValidation(
            ok=False,
            matched_owns=owns_hits,
            matched_never=never_hits,
            reason=(
                f"topic hits {dept}'s NEVER rules: "
                f"{', '.join(never_hits)}"
            ),
        )
    if not owns_hits:
        return ScopeValidation(
            ok=False,
            matched_owns=(),
            matched_never=(),
            reason=f"topic not in {dept}'s OWNS rules",
        )
    return ScopeValidation(
        ok=True,
        matched_owns=owns_hits,
        matched_never=(),
        reason=f"topic covered by {dept}'s OWNS: {', '.join(owns_hits)}",
    )


# ---------------------------------------------------------------------------
# Cross-dept overlap validation (Phase 9.2)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScopeOverlap:
    """One topic claimed by two or more departments."""

    topic: str
    departments: tuple[str, ...]


@dataclass(frozen=True)
class ScopeContradiction:
    """A department lists the same topic in BOTH its OWNS and its NEVER —
    an internal incoherence that must be resolved before the matrix can
    be used. Cross-dept OWNS/NEVER pairs are NOT contradictions: a dept's
    NEVER is a self-disclaimer, and it is expected (and desirable) that
    the topic it disclaims is owned by another dept."""

    dept: str
    topic: str


@dataclass(frozen=True)
class OverlapReport:
    overlaps: tuple[ScopeOverlap, ...]
    contradictions: tuple[ScopeContradiction, ...]

    @property
    def ok(self) -> bool:
        return not self.overlaps and not self.contradictions

    def as_messages(self) -> tuple[str, ...]:
        """Human-readable issue strings for logging or display."""
        msgs: list[str] = []
        for o in self.overlaps:
            msgs.append(
                f"OWNS overlap: {o.topic!r} claimed by "
                f"{', '.join(o.departments)}"
            )
        for c in self.contradictions:
            msgs.append(
                f"Self-contradiction: dept {c.dept!r} lists "
                f"{c.topic!r} in both OWNS and NEVER"
            )
        return tuple(msgs)


def find_overlaps(matrix: ScopeMatrix) -> OverlapReport:
    """Identify matrix-level coherence issues.

      * `overlaps` — same topic (case-normalised) claimed by 2+ depts'
        OWNS lists. A topic can be owned by at most one dept.
      * `contradictions` — the SAME dept lists a topic in both OWNS and
        NEVER. These are local incoherences that must be resolved.

    Cross-dept OWNS/NEVER pairs are expected and NOT flagged here —
    one dept's NEVER is a self-disclaimer, and it is by design that the
    topic it disclaims is owned by another dept.

    The returned `OverlapReport.ok` is True only if BOTH collections are
    empty.
    """
    # Map of lowercase-topic → list[(dept, original_spelling)]
    owns_index: dict[str, list[tuple[str, str]]] = {}
    for scope in matrix.departments:
        for item in scope.owns:
            owns_index.setdefault(item.lower(), []).append((scope.dept, item))

    overlaps: list[ScopeOverlap] = []
    for _, hits in owns_index.items():
        unique_depts = tuple(sorted({d for d, _ in hits}))
        if len(unique_depts) > 1:
            # Preserve the canonical spelling from the first dept's entry.
            canonical = hits[0][1]
            overlaps.append(ScopeOverlap(topic=canonical, departments=unique_depts))

    contradictions: list[ScopeContradiction] = []
    for scope in matrix.departments:
        owns_set = {o.lower() for o in scope.owns}
        for nev in scope.never:
            if nev.lower() in owns_set:
                # Preserve original casing from OWNS for stable output.
                canonical = next(o for o in scope.owns if o.lower() == nev.lower())
                contradictions.append(ScopeContradiction(
                    dept=scope.dept, topic=canonical,
                ))

    return OverlapReport(
        overlaps=tuple(overlaps),
        contradictions=tuple(contradictions),
    )
