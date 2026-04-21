"""Quiet-hours UTC-aware behavior (chunk 0.5.3 of Phase 0.5).

Verifies that core.notify._is_quiet_now() uses timezone-aware arithmetic
and that the UTC quiet window covers both ET-4 (summer) and ET-5 (winter)
boundaries. Full zoneinfo / DST-accurate handling is a Phase 3+ task.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.notify import _is_quiet_now


def test_quiet_hours_utc_aware() -> None:
    # (1) A UTC time inside the quiet window returns True.
    # 05:00 UTC = midnight ET-5 (winter) / 01:00 ET-4 (summer) — both quiet.
    inside = datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc)
    assert _is_quiet_now(inside) is True

    # (2) A UTC time outside the quiet window returns False.
    # 18:00 UTC = 13:00 ET-5 (winter) / 14:00 ET-4 (summer) — business hours.
    outside = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
    assert _is_quiet_now(outside) is False

    # (3) Midnight-crossing edge case, summer boundary:
    # 02:00 UTC = 22:00 ET-4 — must read as quiet even though it is 02:00 UTC.
    summer_boundary = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    assert _is_quiet_now(summer_boundary) is True

    # (4) Winter boundary: 03:00 UTC = 22:00 ET-5 — must also read as quiet.
    winter_boundary = datetime(2026, 1, 15, 3, 0, tzinfo=timezone.utc)
    assert _is_quiet_now(winter_boundary) is True
