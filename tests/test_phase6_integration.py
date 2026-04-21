"""End-to-end Phase 6 integration — brand + taste flowing through state auth.

Asserts the full stack is wired: brand-DB entries and an A/B-learned
taste profile both produce valid Priority 4 / Priority 7 claims that
resolve_conflict() ranks correctly alongside KB and Founder claims.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.brand_db import (
    brand_entry_to_claim,
    load_all_entries,
)
from core.brand_db.store import IMAGES_SUBDIR, VOICE_SUBDIR
from core.primitives.ab import (
    ABOption,
    ABPair,
    ABPick,
    discover_axis,
    update_profile_from_picks,
)
from core.primitives.state import (
    AuthorityPriority,
    Claim,
    resolve_conflict,
)
from core.primitives.taste import (
    TasteProfile,
    load_profile,
    profile_to_claim,
    write_profile,
)

ISO = "2026-04-17T10:00:00+00:00"


def _seed_brand_db(vault: Path) -> None:
    vdir = vault / VOICE_SUBDIR
    vdir.mkdir(parents=True)
    (vdir / "lighthouse.md").write_text(
        "---\n"
        "added_at: '2026-04-17T10:00:00+00:00'\n"
        "verdict: gold\n"
        "tags:\n  - coastal\n"
        'description: "spare lighthouse voice"\n'
        "---\n"
        "We write like a keeper: short, spare, and lit from inside.",
        encoding="utf-8",
    )
    (vdir / "too-corporate.md").write_text(
        "---\n"
        "added_at: '2026-04-17T10:00:00+00:00'\n"
        "verdict: anti-exemplar\n"
        "---\n"
        "Synergize leverage disrupt bandwidth.",
        encoding="utf-8",
    )
    idir = vault / IMAGES_SUBDIR
    idir.mkdir(parents=True)
    (idir / "rfk.jpg").write_bytes(b"png-data")
    (idir / "rfk.jpg.yaml").write_text(
        "added_at: '2026-04-17T10:00:00+00:00'\n"
        "verdict: reference\n"
        "tags: [rfk, americana]\n"
        "description: RFK 1968 campaign photo\n",
        encoding="utf-8",
    )


def test_brand_entries_round_trip_into_claims(tmp_path: Path) -> None:
    _seed_brand_db(tmp_path)
    entries = load_all_entries(tmp_path)
    assert len(entries) == 3  # 2 voice + 1 image
    claims = [brand_entry_to_claim(e) for e in entries]
    assert all(c.priority is AuthorityPriority.BRAND for c in claims)
    assert all(c.priority.value == 4 for c in claims)


def test_ab_picks_build_profile_that_beats_assumption(tmp_path: Path) -> None:
    # Simulate a Taste Inbox session: the founder picks against every
    # "corporate" option. The learned profile should favour anti-corporate,
    # and the resulting Priority 7 claim should win over a Priority 8
    # assumption but lose to everything else.
    # corporate delta |0.8| dominates coastal delta |0.4| without clamping.
    picks = [
        ABPick(
            pair=ABPair(
                id=f"p{i}",
                a=ABOption(id=f"a{i}", axes={"corporate": 0.4, "coastal": -0.2}),
                b=ABOption(id=f"b{i}", axes={"corporate": -0.4, "coastal": 0.2}),
                shown_at=ISO,
            ),
            chosen="b",
            picked_at=ISO,
        )
        for i in range(8)
    ]
    start = TasteProfile(last_fit_at="", picks_used=0, confidence=0.0, axes={})
    learned = update_profile_from_picks(start, picks, now=ISO)
    assert learned.axes["corporate"] < 0
    assert learned.axes["coastal"] > 0
    # Discovery agrees: corporate is the dominant axis, negative polarity.
    hyp = discover_axis(picks)
    assert hyp is not None
    assert hyp.axis == "corporate"
    assert hyp.magnitude < 0

    # Write + read + claim.
    write_profile(tmp_path, learned)
    reloaded = load_profile(tmp_path)
    assert reloaded is not None
    claim = profile_to_claim(reloaded)
    assert claim.priority is AuthorityPriority.TASTE

    # Priority 7 beats Priority 8.
    prov = {
        "updated_at": ISO, "updated_by": "x", "source_path": "s", "ingested_at": ISO,
    }
    assumption = Claim(
        priority=AuthorityPriority.ASSUMPTION, content="x",
        ref="a", provenance=prov,
    )
    resolved = resolve_conflict(claim, assumption)
    assert resolved.winner is claim


def test_full_priority_ladder_ranks_correctly(tmp_path: Path) -> None:
    """Brand P4 beats Taste P7; Founder P1 beats everything."""
    _seed_brand_db(tmp_path)
    brand_entries = load_all_entries(tmp_path)
    brand_claim = brand_entry_to_claim(brand_entries[0])

    start = TasteProfile(last_fit_at="", picks_used=0, confidence=0.0, axes={})
    picks = [
        ABPick(
            pair=ABPair(
                id="p1",
                a=ABOption(id="a", axes={"spare": 1.0}),
                b=ABOption(id="b", axes={"spare": -1.0}),
                shown_at=ISO,
            ),
            chosen="a",
            picked_at=ISO,
        ),
    ]
    taste_claim = profile_to_claim(update_profile_from_picks(start, picks, now=ISO))

    prov = {
        "updated_at": ISO, "updated_by": "founder", "source_path": "context.md",
        "ingested_at": ISO,
    }
    founder_claim = Claim(
        priority=AuthorityPriority.FOUNDER, content="f",
        ref="priority_1_founder:context.md", provenance=prov,
    )

    # brand > taste
    assert resolve_conflict(brand_claim, taste_claim).winner is brand_claim
    # founder > brand
    assert resolve_conflict(founder_claim, brand_claim).winner is founder_claim
    # founder > taste
    assert resolve_conflict(founder_claim, taste_claim).winner is founder_claim
