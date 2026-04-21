"""
core/dispatch/drift_guard.py — Phase 7.4 — composed drift defense
==================================================================
`drift_guard` bundles the Phase 5 primitives
(`watchdog_check` + `TurnCapLedger` + `check_provenance`) into a single
`evaluate_dispatch()` call. This is what the dispatch post-hook invokes
after a manager or specialist returns — one call gives a unified pass/fail
verdict with every drift issue surfaced.

Composition:

  1. Watchdog (§7.3) — verify every `references` entry in the message
     resolves on disk and that cited claims appear verbatim in the
     referenced message.
  2. Turn cap (§7.1) — if a capability and ledger are passed, check
     whether the running inter-agent-turn count has hit the cap.
  3. Provenance (§1.5) — if the caller passes any Claims that were
     produced in this dispatch, verify each one carries the four
     required provenance fields.

`evaluate_dispatch` is pure: it only reads disk (via the watchdog) and
never writes. The caller decides what to do with a failing report —
surface to the founder, reject the artifact, or (in permissive mode)
just annotate the memory entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from core.primitives.drift import (
    DriftAssessment,
    WatchdogMode,
    watchdog_check,
)
from core.primitives.integrity import verify_claim_integrity
from core.primitives.state import (
    AuthorityPriority,
    Claim,
    ProvenanceStatus,
    check_provenance,
)
from core.primitives.turn_cap import (
    TurnCapAssessment,
    TurnCapLedger,
    TurnCapStatus,
)

# Phase 14 — consolidated-2026-04-18 §10.1. Default set of priorities
# that MUST carry a matching integrity hash on the referenced source
# file. KB chunks are hashed at ingest (see core/kb/ingest.py); BRAND
# hashing is planned. Other priorities are exempt until their write
# paths emit hashes.
DEFAULT_INTEGRITY_REQUIRED = (AuthorityPriority.KB,)


# ---------------------------------------------------------------------------
# Report type
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DriftGuardReport:
    """Aggregate verdict across all drift checks."""

    ok: bool
    watchdog: DriftAssessment
    turn_cap: TurnCapAssessment | None = None
    provenance_issues: tuple[str, ...] = field(default_factory=tuple)
    integrity_issues: tuple[str, ...] = field(default_factory=tuple)
    summary: str = ""

    @property
    def issues(self) -> tuple[str, ...]:
        """Flattened list of every issue across all sub-checks."""
        items: list[str] = list(self.watchdog.issues)
        if self.turn_cap and self.turn_cap.status is TurnCapStatus.ESCALATE:
            items.append(f"turn_cap escalate: {self.turn_cap.reason}")
        items.extend(self.provenance_issues)
        items.extend(self.integrity_issues)
        return tuple(items)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_provenance_bundle(
    claims: Iterable[Claim | Mapping],
) -> tuple[str, ...]:
    """Verify every Claim (or raw provenance mapping) has the four §1.5 fields.

    Accepts either `Claim` envelopes or bare provenance dicts so the
    dispatch layer can pass in whatever it has without adapting first.
    """
    issues: list[str] = []
    for idx, item in enumerate(claims):
        provenance = item.provenance if isinstance(item, Claim) else item
        if check_provenance(provenance) is not ProvenanceStatus.VALID:
            ref_hint = getattr(item, "ref", None) or f"claims[{idx}]"
            issues.append(
                f"provenance missing or invalid on {ref_hint}: "
                f"{dict(provenance) if provenance else '(none)'}"
            )
    return tuple(issues)


def _check_integrity_bundle(
    claims: Iterable[Claim | Mapping],
    vault_dir: Path,
    required_priorities: tuple[AuthorityPriority, ...],
) -> tuple[str, ...]:
    """Phase 14 — consolidated §10.1. Verify the hash-backed integrity
    of source files referenced by Claims whose priority is in
    `required_priorities`. Raw-mapping entries are skipped (no priority
    to check against). Empty `required_priorities` is a no-op."""
    if not required_priorities:
        return ()
    issues: list[str] = []
    required_set = set(required_priorities)
    for idx, item in enumerate(claims):
        if not isinstance(item, Claim):
            continue
        if item.priority not in required_set:
            continue
        check = verify_claim_integrity(item.provenance, vault_dir)
        if not check.ok:
            issues.append(
                f"integrity failed for {item.ref} "
                f"(priority={item.priority.name}, reason={check.reason})"
            )
    return tuple(issues)


def _build_summary(
    watchdog: DriftAssessment,
    turn_cap: TurnCapAssessment | None,
    prov_issues: tuple[str, ...],
    integrity_issues: tuple[str, ...],
    ok: bool,
) -> str:
    parts = [
        f"watchdog: {len(watchdog.issues)} issue(s) over "
        f"{watchdog.references_checked} reference(s)",
    ]
    if turn_cap is not None:
        parts.append(
            f"turn_cap: {turn_cap.turns_used}/{turn_cap.cap} "
            f"({turn_cap.status.value})"
        )
    parts.append(f"provenance: {len(prov_issues)} issue(s)")
    parts.append(f"integrity: {len(integrity_issues)} issue(s)")
    parts.append(f"verdict: {'ok' if ok else 'fail'}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def evaluate_dispatch(
    message: str,
    vault_dir: Path,
    *,
    turn_ledger: TurnCapLedger | None = None,
    capability: str | None = None,
    claims: Iterable[Claim | Mapping] = (),
    watchdog_mode: WatchdogMode = WatchdogMode.PERMISSIVE,
    integrity_required_priorities: tuple[AuthorityPriority, ...] = DEFAULT_INTEGRITY_REQUIRED,
) -> DriftGuardReport:
    """Run every drift check over a dispatch output.

    Args:
      message: the returned text (manager synthesis or specialist output).
      vault_dir: company vault root — the watchdog resolves references
        against this.
      turn_ledger, capability: if both provided, the turn-cap is checked
        (without incrementing — recording is the caller's job).
      claims: any new Claims produced in this dispatch; each is provenance-
        checked. Can be empty.
      watchdog_mode: see `core.primitives.drift.WatchdogMode`.
      integrity_required_priorities: Phase 14 — claims whose priority is
        in this tuple additionally have their source_path file's
        integrity_hash verified. Defaults to (KB,) — the only store
        that currently bakes hashes at write time. Pass `()` to disable.

    Returns a DriftGuardReport with `ok=True` only if every sub-check
    passes. Watchdog permissive mode still allows `ok=True` when only
    "annotation" level issues exist — see drift.py for the current split.
    """
    # Materialize claims once — the iterable may be a generator.
    claims_list = list(claims)

    watchdog = watchdog_check(message, vault_dir, mode=watchdog_mode)

    turn_cap: TurnCapAssessment | None = None
    if turn_ledger is not None and capability:
        turn_cap = turn_ledger.check(capability)

    prov_issues = _check_provenance_bundle(claims_list)
    integrity_issues = _check_integrity_bundle(
        claims_list, vault_dir, integrity_required_priorities
    )

    ok = (
        watchdog.ok
        and (turn_cap is None or turn_cap.status is TurnCapStatus.OK)
        and not prov_issues
        and not integrity_issues
    )
    return DriftGuardReport(
        ok=ok,
        watchdog=watchdog,
        turn_cap=turn_cap,
        provenance_issues=prov_issues,
        integrity_issues=integrity_issues,
        summary=_build_summary(watchdog, turn_cap, prov_issues, integrity_issues, ok),
    )
