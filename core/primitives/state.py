"""
core/primitives/state.py — Provenance + conflict resolution
===========================================================
Plan §1.5 specifies four required provenance fields on every persisted
entry so downstream code can trace where a fact came from and when it
was last refreshed:

  updated_at    — ISO timestamp of the last write
  updated_by    — principal (agent or human) that authored the write
  source_path   — filesystem path the fact was derived from
  ingested_at   — ISO timestamp of the original ingestion

An entry missing any of these fields fails `check_provenance()` and
must not be surfaced to a downstream consumer.

Phase 2 additions:

  * `AuthorityPriority` — the 8-tier authority ranking from §1.5.
  * `Claim` / `ResolvedClaim` — typed envelopes for conflicting claims.
  * `resolve_conflict(a, b)` — deterministic resolver: lower priority
    number wins, with one structural exception (Priority 2 Decision may
    supersede a higher-priority claim iff it explicitly lists that ref
    in `supersedes` AND is chronologically newer).
  * `render_state_authority_doc()` / `write_state_authority_doc()` —
    generate the per-company state-authority.md that agents load into
    their system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class ProvenanceStatus(Enum):
    """Result of `check_provenance()` — VALID if all required fields present."""

    VALID = "valid"
    INVALID = "invalid"


_REQUIRED_FIELDS = ("updated_at", "updated_by", "source_path", "ingested_at")

# §10 action item 3 (consolidated-2026-04-18): reject updated_at timestamps
# more than this many seconds in the future. Small tolerance accommodates
# clock drift between the engine and any external ingester. Gemini's
# year-2099 attack (a specialist writing updated_at='2099-12-31' to win
# chronological tiebreakers) is blocked here.
FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 300  # 5 minutes


def _parse_iso_timestamp(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parser. Returns None on any failure; callers
    treat None as 'unparseable, reject.' Accepts date-only strings as
    midnight UTC so KB `source_asof` values don't falsely fail."""
    if not isinstance(value, str) or not value:
        return None
    raw = value.strip()
    # Python 3.11 fromisoformat accepts 'Z' suffix; older versions need the
    # manual swap. Play safe and handle it.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        # Allow pure date strings (YYYY-MM-DD); normalize to midnight UTC.
        try:
            parsed = datetime.fromisoformat(raw + "T00:00:00+00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_future_timestamp(
    value: Any,
    *,
    now: datetime | None = None,
    tolerance_seconds: int = FUTURE_TIMESTAMP_TOLERANCE_SECONDS,
) -> bool:
    """Return True iff `value` is a parseable timestamp that sits beyond
    (now + tolerance). Unparseable values return False here — they're
    caught by `check_provenance()` as missing/invalid fields instead."""
    ts = _parse_iso_timestamp(value)
    if ts is None:
        return False
    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return ts > reference + timedelta(seconds=tolerance_seconds)


def check_provenance(
    entry: Mapping[str, Any],
    *,
    reject_future_timestamps: bool = True,
    now: datetime | None = None,
) -> ProvenanceStatus:
    """Validate that `entry` carries all required provenance fields.

    A field is "present" iff it exists on the mapping AND its value is
    not None and not an empty string. That guards against callers that
    populate the schema shape but forget to fill in the values.

    §10 action item 3 (consolidated-2026-04-18): when
    `reject_future_timestamps` is True (default), `updated_at` and
    `ingested_at` are additionally rejected if they parse to a moment
    more than FUTURE_TIMESTAMP_TOLERANCE_SECONDS in the future. This
    closes the Gemini year-2099 attack where a specialist writes
    `updated_at='2099-12-31'` to win chronological tiebreakers.
    """
    if not isinstance(entry, Mapping):
        return ProvenanceStatus.INVALID
    for key in _REQUIRED_FIELDS:
        value = entry.get(key)
        if value is None or value == "":
            return ProvenanceStatus.INVALID
    if reject_future_timestamps:
        for key in ("updated_at", "ingested_at"):
            if is_future_timestamp(entry.get(key), now=now):
                return ProvenanceStatus.INVALID
    return ProvenanceStatus.VALID


# ---------------------------------------------------------------------------
# Authority ranking (Phase 2 — §1.5)
# ---------------------------------------------------------------------------
class AuthorityPriority(Enum):
    """The 8-tier authority ranking. Lower value = higher authority.

    Mirrors the table in plan §1.5. The enum value is the priority number
    used by `resolve_conflict()`; the name is the store identity used in
    citation references (e.g. `priority_1_founder`).
    """

    FOUNDER = 1       # context.md, founder_profile.md, priorities.md, settled_convictions
    DECISION = 2      # decisions/<date>-<slug>.md — can supersede FOUNDER iff explicit
    KB = 3            # knowledge-base/chunks/*.md
    BRAND = 4         # brand-db/voice, brand-db/images
    HANDSHAKE = 5     # handshakes/<session>/*.json
    MEMORY = 6        # departments/**/manager-memory.md, specialist memory
    TASTE = 7         # taste/profile.yaml — preference signal only
    ASSUMPTION = 8    # assumptions-log.jsonl — provisional, has TTL


@dataclass(frozen=True)
class Claim:
    """A single authority-tagged claim with provenance.

    Two claims that disagree on the same topic are resolved by
    `resolve_conflict()`. The `supersedes` field is only consulted when
    this claim is a Priority 2 Decision overriding a higher-priority
    store — see the special rule in §1.5.
    """

    priority: AuthorityPriority
    content: Any
    ref: str
    provenance: Mapping[str, Any]
    supersedes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ResolvedClaim:
    """The winner of a `resolve_conflict()` call plus a human-readable reason."""

    winner: Claim
    loser: Claim
    reason: str


def _has_founder_signature(provenance: Mapping[str, Any]) -> bool:
    """A Decision that supersedes a Priority 1 Founder claim must carry
    an explicit founder signature. We accept either `founder_signature: true`
    or `updated_by` being a value in the FOUNDER_PRINCIPALS set.

    §10 action item 3 (consolidated-2026-04-18): prevents a specialist
    from writing `supersedes: ["priority_1_founder/..."]` into a Decision
    it authored. Only the founder (or a tool acting on behalf of the
    founder) can override Priority 1.
    """
    if not isinstance(provenance, Mapping):
        return False
    sig = provenance.get("founder_signature")
    if sig is True or (isinstance(sig, str) and sig.strip().lower() == "true"):
        return True
    updated_by = provenance.get("updated_by", "")
    if isinstance(updated_by, str) and updated_by.strip().lower() in FOUNDER_PRINCIPALS:
        return True
    return False


FOUNDER_PRINCIPALS = frozenset({"founder", "riley", "rileysobieski@gmail.com"})


def _decision_override(a: Claim, b: Claim) -> ResolvedClaim | None:
    """Check whether one claim is a Priority 2 Decision that legally
    supersedes the other. Returns the ResolvedClaim if so, else None.

    The §1.5 rule: a Decision may override a higher-priority claim only
    if (1) the Decision explicitly names the other claim's ref in its
    `supersedes` list AND (2) the Decision is chronologically newer.

    §10 action item 3 (consolidated-2026-04-18): superseding a Priority 1
    Founder claim additionally requires an explicit founder signature in
    the Decision's provenance (`founder_signature: true` or
    `updated_by` in FOUNDER_PRINCIPALS). Without it, the supersede is
    silently ignored and the Founder claim wins on priority.
    """
    for decision, other in ((a, b), (b, a)):
        if decision.priority is not AuthorityPriority.DECISION:
            continue
        if other.priority.value >= decision.priority.value:
            continue
        if other.ref not in decision.supersedes:
            continue
        if decision.provenance["updated_at"] <= other.provenance["updated_at"]:
            continue
        # §10 action item 3: Priority 1 overrides require founder signature.
        if other.priority is AuthorityPriority.FOUNDER and not _has_founder_signature(decision.provenance):
            # Silently refuse the override; fall through to normal priority
            # resolution (which means `other` wins). Logged reason is set
            # by the caller's ResolvedClaim when it picks the founder.
            continue
        return ResolvedClaim(
            winner=decision,
            loser=other,
            reason=(
                f"Decision {decision.ref} supersedes "
                f"priority_{other.priority.value}_{other.priority.name.lower()} "
                f"ref {other.ref} per explicit supersedes list"
            ),
        )
    return None


def resolve_conflict_with_integrity(
    a: Claim,
    b: Claim,
    vault_dir: Path,
    *,
    required_priorities: tuple["AuthorityPriority", ...] = (),
) -> ResolvedClaim:
    """Variant of `resolve_conflict` that verifies each claim's
    integrity hash (via `core.primitives.integrity.verify_claim_integrity`)
    before delegating to the standard resolver.

    `required_priorities` is a tuple of AuthorityPriority values for
    which integrity verification is mandatory — any claim whose
    priority is in that tuple must (a) carry a source_path, (b) point
    to a file with a matching integrity_hash. A failure raises
    ValueError with the reason.

    Default (empty tuple) means integrity verification is a soft check
    only: failures are silent. This preserves back-compat during the
    Phase 14 rollout. Production deployments should pass at minimum
    `(AuthorityPriority.KB, AuthorityPriority.BRAND)` since those are
    the stores kb/ingest currently hashes.
    """
    # Lazy import — integrity module depends on nothing here but keeping
    # the import local matches the kb/ingest pattern and avoids cycles.
    from core.primitives.integrity import verify_claim_integrity

    for claim in (a, b):
        if claim.priority not in required_priorities:
            continue
        result = verify_claim_integrity(claim.provenance, vault_dir)
        if not result.ok:
            raise ValueError(
                f"Integrity verification failed for claim {claim.ref!r} "
                f"(priority={claim.priority.name}, reason={result.reason!r}, "
                f"expected={result.expected!r}, actual={result.actual!r})"
            )
    return resolve_conflict(a, b)


def resolve_conflict(a: Claim, b: Claim) -> ResolvedClaim:
    """Deterministic winner between two conflicting claims.

    Rules, in order:
      1. Both claims must carry valid provenance (ValueError otherwise).
      2. A Priority 2 Decision may supersede a higher-priority claim iff
         it lists the other ref in `supersedes` AND is chronologically
         newer. This is the §1.5 escape hatch.
      3. Otherwise: lower priority number wins.
      4. Tie on priority: newer `updated_at` wins.
      5. Tie on priority AND timestamp: lexicographic order of `ref`.
         Structural tiebreak so the function is pure.
    """
    if check_provenance(a.provenance) is not ProvenanceStatus.VALID:
        raise ValueError(f"Claim a has invalid provenance (ref={a.ref!r})")
    if check_provenance(b.provenance) is not ProvenanceStatus.VALID:
        raise ValueError(f"Claim b has invalid provenance (ref={b.ref!r})")

    override = _decision_override(a, b)
    if override is not None:
        return override

    if a.priority.value < b.priority.value:
        return ResolvedClaim(
            winner=a, loser=b,
            reason=(
                f"priority_{a.priority.value}_{a.priority.name.lower()} "
                f"outranks priority_{b.priority.value}_{b.priority.name.lower()}"
            ),
        )
    if b.priority.value < a.priority.value:
        return ResolvedClaim(
            winner=b, loser=a,
            reason=(
                f"priority_{b.priority.value}_{b.priority.name.lower()} "
                f"outranks priority_{a.priority.value}_{a.priority.name.lower()}"
            ),
        )

    ts_a = a.provenance["updated_at"]
    ts_b = b.provenance["updated_at"]
    if ts_a > ts_b:
        return ResolvedClaim(
            winner=a, loser=b,
            reason=f"Same priority ({a.priority.name}); newer updated_at wins ({ts_a} > {ts_b})",
        )
    if ts_b > ts_a:
        return ResolvedClaim(
            winner=b, loser=a,
            reason=f"Same priority ({b.priority.name}); newer updated_at wins ({ts_b} > {ts_a})",
        )

    if a.ref <= b.ref:
        return ResolvedClaim(
            winner=a, loser=b,
            reason=f"Identical priority and updated_at; lexicographic ref tiebreak (a.ref={a.ref!r})",
        )
    return ResolvedClaim(
        winner=b, loser=a,
        reason=f"Identical priority and updated_at; lexicographic ref tiebreak (b.ref={b.ref!r})",
    )


# ---------------------------------------------------------------------------
# state-authority.md generation (Phase 2 — §1.5, §5.2)
# ---------------------------------------------------------------------------
_AUTHORITY_DOC_TEMPLATE = """# State Authority — {company_name}

This document defines which store wins when sources conflict. Every agent
loads it into its system prompt. If memory contradicts a higher-priority
store, memory is wrong and gets corrected — never the other way around.

## Authority ranking (top wins)

| Priority | Store | Authoritative for |
|----------|-------|-------------------|
| 1 | Founder authority files (`context.md`, `founder_profile.md`, `priorities.md`, `settled_convictions`) | Identity, convictions, non-negotiables |
| 2 | Decisions (`decisions/<date>-<slug>.md`) | Chronological tiebreaker between 1 and anything else. A newer Decision supersedes 1 only if it explicitly cites the superseded ref. |
| 3 | Knowledge Base (`knowledge-base/chunks/*.md`) | External-world facts. As-of-date required. |
| 4 | Brand DB (`brand-db/voice/*.md`, `brand-db/images/*`) | Voice and aesthetic reference. Not facts. |
| 5 | Handshakes (`handshakes/<session>/*.json`) | Audit trail only. Never a source of new claims. |
| 6 | Memory files (`departments/*/manager-memory.md`, specialist memory) | Derived summary of past work. Never authoritative on its own. |
| 7 | Taste profile (`taste/profile.yaml`) | Preference signal only. Never cited as fact or constraint. |
| 8 | Assumption log (`assumptions-log.jsonl`) | Provisional; has TTL (see §7.4 of the plan). |

## Required provenance fields (every persisted entry)

- `updated_at` — ISO timestamp of the last write
- `updated_by` — principal (agent slug or human) that authored the write
- `source_path` — filesystem path the fact was derived from
- `ingested_at` — ISO timestamp of the original ingestion

Entries missing any of these fail `check_provenance()` and are rejected
at the watchdog.

## Rules

1. **Higher priority wins** in a conflict.
2. **Decision override is narrow.** A Priority 2 Decision may override a
   higher-priority claim only if the Decision lists the superseded ref in
   its `supersedes` field AND is chronologically newer. A Decision without
   an explicit `supersedes` cannot override a Founder file.
3. **Citations name the store.** Every load-bearing claim must be cited
   as `priority_N_<store>` (e.g. `priority_1_founder`, `priority_3_kb`).
4. **No cross-store synthesis without a provenance chain.** When combining
   sources, the output carries every source citation. "Based on various
   sources" is rejected.
5. **Same priority, conflicting claims** are resolved chronologically
   (newer `updated_at` wins), then lexicographically by `ref` as a
   structural tiebreak.

## Edits to this file

Edits are logged as a Priority 2 Decision themselves. Do not edit in place
without creating a `decisions/<date>-state-authority-update.md` entry that
supersedes the prior version.
"""


def render_state_authority_doc(company_name: str) -> str:
    """Render the canonical state-authority.md content for a company.

    Deterministic — same company name produces the same bytes. Callers
    that want to extend the doc with company-specific rules should edit
    the file after write and log a Decision per the `Edits to this file`
    section.
    """
    return _AUTHORITY_DOC_TEMPLATE.format(company_name=company_name)


def write_state_authority_doc(company_dir: Path, company_name: str) -> Path:
    """Write `state-authority.md` to `company_dir`. Returns the written path.

    Idempotent: an existing file is overwritten with the current template.
    Any company-specific rules added by the founder will be lost on
    rewrite — that's the trade-off for keeping the generator deterministic.
    A Decision entry is the correct place for per-company edits (§1.5).
    """
    company_dir.mkdir(parents=True, exist_ok=True)
    target = company_dir / "state-authority.md"
    target.write_text(render_state_authority_doc(company_name), encoding="utf-8")
    return target
