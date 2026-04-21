"""Tests for core/config.py — canonical runtime-config accessors.

No LLM calls, no vault access. These tests exercise the five public
accessors introduced in chunk 1a.1 and guard their default contracts
so that downstream chunks (1a.2+) can rely on them.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_get_model_default_returns_haiku() -> None:
    from core.config import get_model
    assert get_model("default") == "claude-haiku-4-5-20251001"
    # Unknown role also falls back to the default.
    assert get_model("does-not-exist") == "claude-haiku-4-5-20251001"


def test_get_vault_dir_raises_when_env_unset(monkeypatch) -> None:
    from core.config import get_vault_dir
    monkeypatch.delenv("COMPANY_OS_VAULT_DIR", raising=False)
    with pytest.raises(RuntimeError, match="COMPANY_OS_VAULT_DIR"):
        get_vault_dir()


def test_get_vault_dir_returns_path_when_env_set(monkeypatch, tmp_path: Path) -> None:
    from core.config import get_vault_dir
    monkeypatch.setenv("COMPANY_OS_VAULT_DIR", str(tmp_path))
    result = get_vault_dir()
    assert isinstance(result, Path)
    assert result == tmp_path.resolve()


def test_get_output_subdirs_has_three_expected_keys() -> None:
    from core.config import get_output_subdirs
    subdirs = get_output_subdirs()
    assert set(subdirs.keys()) == {"pending_approval", "approved", "rejected"}
    assert subdirs["pending_approval"] == "pending-approval"
    assert subdirs["approved"] == "approved"
    assert subdirs["rejected"] == "rejected"


def test_get_permission_mode_returns_bypass() -> None:
    from core.config import get_permission_mode
    assert get_permission_mode() == "bypassPermissions"
