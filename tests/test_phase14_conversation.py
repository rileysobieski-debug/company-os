"""
Tests for the conversation primitive + chat webapp routes.

Doesn't hit the real LLM — verifies data-layer correctness + route
wiring only. The LLM call inside send_and_reply is exercised in
integration tests when COMPANY_OS_VAULT_DIR is set and the API key is
available; skipped otherwise.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.conversation import (
    ConversationThread,
    Message,
    append_message,
    close_thread,
    list_threads,
    load_thread,
    new_thread_id,
    persist_thread,
    start_thread,
    thread_path,
)


class TestThreadConstruction:
    def test_new_thread_id_unique(self):
        assert new_thread_id() != new_thread_id()

    def test_start_thread_defaults(self):
        t = start_thread(target_agent="manager:marketing")
        assert t.target_agent == "manager:marketing"
        assert t.purpose == "chat"
        assert t.is_open
        assert t.turn_count == 0
        assert t.started_by == "founder"

    def test_start_thread_with_seed_assistant(self):
        t = start_thread(
            target_agent="manager:finance",
            purpose="founder_interview",
            seed_assistant="Welcome. First question: who is your primary bank?",
        )
        assert t.turn_count == 1  # assistant counts as a turn
        assert t.messages[0].role == "assistant"
        assert "primary bank" in t.messages[0].content

    def test_start_thread_with_seed_system_hidden_in_transcript(self):
        t = start_thread(
            target_agent="orchestrator",
            seed_system="You are the orchestrator...",
        )
        assert t.turn_count == 0  # system doesn't count
        assert t.messages[0].role == "system"


class TestPersistence:
    def test_persist_and_load_roundtrip(self, tmp_path: Path):
        t = start_thread(target_agent="manager:marketing", title="test")
        persist_thread(tmp_path, t)
        loaded = load_thread(tmp_path, t.id)
        assert loaded is not None
        assert loaded.id == t.id
        assert loaded.target_agent == "manager:marketing"
        assert loaded.title == "test"

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert load_thread(tmp_path, "nonexistent") is None

    def test_list_threads_empty_without_dir(self, tmp_path: Path):
        assert list_threads(tmp_path) == []

    def test_list_threads_orders_newest_first(self, tmp_path: Path):
        t1 = start_thread(target_agent="orchestrator", title="first")
        import time
        time.sleep(0.01)
        t2 = start_thread(target_agent="orchestrator", title="second")
        persist_thread(tmp_path, t1)
        persist_thread(tmp_path, t2)
        threads = list_threads(tmp_path)
        assert threads[0].id == t2.id
        assert threads[1].id == t1.id


class TestMutation:
    def test_append_user_message(self, tmp_path: Path):
        t = start_thread(target_agent="manager:marketing")
        persist_thread(tmp_path, t)
        updated = append_message(tmp_path, t.id, role="user", content="hello")
        assert updated is not None
        assert updated.messages[-1].role == "user"
        assert updated.messages[-1].content == "hello"
        # Persisted too
        reloaded = load_thread(tmp_path, t.id)
        assert reloaded.messages[-1].content == "hello"

    def test_append_assistant_message(self, tmp_path: Path):
        t = start_thread(target_agent="manager:marketing")
        persist_thread(tmp_path, t)
        append_message(tmp_path, t.id, role="user", content="q1")
        updated = append_message(
            tmp_path, t.id, role="assistant", content="a1",
            token_usage={"input_tokens": 42, "output_tokens": 10},
        )
        assert updated.messages[-1].role == "assistant"
        assert updated.messages[-1].token_usage["output_tokens"] == 10

    def test_append_to_missing_thread_returns_none(self, tmp_path: Path):
        result = append_message(tmp_path, "ghost", role="user", content="x")
        assert result is None

    def test_close_thread(self, tmp_path: Path):
        t = start_thread(target_agent="manager:marketing")
        persist_thread(tmp_path, t)
        closed = close_thread(tmp_path, t.id, summary_path="m/founder-brief.md")
        assert closed is not None
        assert not closed.is_open
        assert closed.summary_path == "m/founder-brief.md"


class TestFormatting:
    def test_system_prompt_for_interview_contains_rules(self, tmp_path: Path):
        from core.conversation import _system_prompt_for_thread
        t = start_thread(
            target_agent="manager:finance",
            purpose="founder_interview",
            dept="finance",
        )
        text = _system_prompt_for_thread(t, tmp_path)
        assert "one question at a time" in text.lower()
        assert "finance" in text.lower()

    def test_context_refs_loaded_into_system(self, tmp_path: Path):
        from core.conversation import _system_prompt_for_thread
        brief = tmp_path / "marketing" / "domain-brief.md"
        brief.parent.mkdir()
        brief.write_text("# Marketing brief\n\nKey insight: X.", encoding="utf-8")
        t = start_thread(
            target_agent="manager:marketing",
            purpose="founder_interview",
            context_refs=("marketing/domain-brief.md",),
        )
        text = _system_prompt_for_thread(t, tmp_path)
        assert "Key insight: X" in text

    def test_context_path_traversal_rejected(self, tmp_path: Path):
        from core.conversation import _format_context_block
        outside = tmp_path.parent / "outside.md"
        outside.write_text("should not leak", encoding="utf-8")
        block = _format_context_block(tmp_path, ("../outside.md",))
        assert "should not leak" not in block

    def test_messages_for_llm_strips_system(self):
        from core.conversation import _messages_for_llm
        t = start_thread(
            target_agent="orchestrator",
            seed_system="hidden",
            seed_assistant="hello",
        )
        t2 = t.__class__(
            id=t.id, target_agent=t.target_agent, purpose=t.purpose,
            created_at=t.created_at, started_by=t.started_by,
            messages=t.messages + (Message(role="user", content="hi"),),
            context_refs=t.context_refs, status=t.status,
            summary_path=t.summary_path, dept=t.dept,
            onboarding_phase=t.onboarding_phase, title=t.title,
        )
        msgs = _messages_for_llm(t2)
        # system dropped
        assert all(m["role"] != "system" for m in msgs)
        # assistant + user kept
        roles = [m["role"] for m in msgs]
        assert "assistant" in roles
        assert "user" in roles


class TestWebappChat:
    SLUG = "Old Press Wine Company LLC"

    @pytest.fixture(scope="class")
    def client(self, vault_dir, old_press_dir):
        import os
        os.environ.setdefault("COMPANY_OS_VAULT_DIR", str(vault_dir))
        from webapp.app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_chat_index_renders(self, client):
        resp = client.get(f"/c/{self.SLUG}/chat")
        assert resp.status_code == 200
        assert b"Conversations" in resp.data

    def test_new_thread_requires_target(self, client):
        resp = client.post(
            f"/c/{self.SLUG}/chat/new",
            data={"title": "test"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_nav_exposes_chat_entry_point(self, client):
        """The dedicated 'Chat' nav link was replaced 2026-04-19 by the
        Inbox drawer. The entry point to /chat still exists — the
        drawer's 'All threads' link goes to /chat — but the test no
        longer looks for the literal '>Chat<' string."""
        resp = client.get(f"/c/{self.SLUG}/")
        # At minimum: something links to the /chat route.
        assert b"/chat" in resp.data

    def test_missing_thread_404s(self, client):
        resp = client.get(f"/c/{self.SLUG}/chat/ghost")
        assert resp.status_code == 404
