"""Tests for `dispatch_manager()` pre/post hooks + permission_mode move.

Chunk 1a.8 acceptance. Tests mock out `anyio.run` so we never call the
real SDK; we only verify that hooks fire at the correct points and that
the default `None` behavior is identical to the prior signature.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_manager_result():
    """Build a ManagerResult instance without spinning up a real Manager."""
    from core.managers.base import ManagerResult
    return ManagerResult(
        manager_name="mktg",
        brief="test brief",
        final_text="done",
    )


@pytest.fixture
def patched_dispatch(monkeypatch, fake_manager_result):
    """Patch `dispatch_manager`'s internals so it never hits the real SDK.

    Returns the `dispatch_manager` callable. Caller supplies the kwargs.
    """
    from core.managers import base as base_mod

    # Fake department so the name lookup inside dispatch_manager passes.
    fake_dept = MagicMock()
    fake_dept.name = "mktg"

    monkeypatch.setattr(base_mod, "load_departments", lambda _co: [fake_dept])
    monkeypatch.setattr(base_mod, "Manager", MagicMock())
    monkeypatch.setattr(base_mod.anyio, "run", lambda _fn, _brief: fake_manager_result)

    return base_mod.dispatch_manager


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_dispatch_manager_backward_compat_no_hooks(patched_dispatch, fake_manager_result) -> None:
    """With pre_hook=None, post_hook=None the result is the same ManagerResult."""
    result = patched_dispatch("mktg", "test brief", company=MagicMock())
    assert result is fake_manager_result


def test_pre_hook_receives_brief_before_dispatch(patched_dispatch) -> None:
    """pre_hook fires exactly once with the brief string before anyio.run."""
    captured: list[str] = []

    def _pre(brief: str) -> None:
        captured.append(brief)

    patched_dispatch(
        "mktg",
        "the brief content",
        company=MagicMock(),
        pre_hook=_pre,
    )
    assert captured == ["the brief content"]


def test_post_hook_receives_manager_result_after_dispatch(
    patched_dispatch, fake_manager_result
) -> None:
    """post_hook fires once with the ManagerResult returned by the SDK."""
    from core.managers.base import ManagerResult

    captured: list[ManagerResult] = []

    def _post(result: ManagerResult) -> None:
        captured.append(result)

    patched_dispatch(
        "mktg",
        "brief",
        company=MagicMock(),
        post_hook=_post,
    )
    assert len(captured) == 1
    assert captured[0] is fake_manager_result
    assert isinstance(captured[0], ManagerResult)


def test_permission_mode_reads_from_config() -> None:
    """core.config.get_permission_mode() is the source of truth."""
    from core.config import get_permission_mode
    assert get_permission_mode() == "bypassPermissions"
