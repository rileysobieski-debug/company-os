"""
core/conversation.py — Multi-turn conversation threads
=======================================================

Conversation threads are how the founder talks directly to a specific
manager (or the orchestrator). Used for:

  - Phase-2 founder interviews during department onboarding. The
    manager asks one question at a time using its domain brief as
    context; the founder answers; the manager synthesizes into a
    founder-brief at the end.

  - Ad-hoc "talk to" sessions where the founder wants to think out
    loud with a particular specialty lens on.

  - Orchestrator-level strategy sessions.

Shape:

    ConversationThread(
      id,
      target_agent,          # "orchestrator" | "manager:marketing" | "specialist:..."
      purpose,               # "founder_interview" | "chat" | "strategy"
      created_at,
      started_by,            # "founder" by default
      messages: tuple[Message, ...],
      context_refs: tuple[str, ...],   # paths loaded as seed context
      status,                # "open" | "closed"
      summary_path,          # where the closing synthesis landed
      dept,                  # optional — for interview threads
      onboarding_phase,      # optional
    )

    Message(
      role,                  # "user" | "assistant" | "system"
      content,
      created_at,
      job_id,                # optional — links to the dispatch job
      token_usage,           # optional dict
    )

Storage: `<company_dir>/conversations/<thread_id>.json`

Turn execution: each user message triggers a `single_turn` call to the
target agent's model. System prompt carries the manager persona +
context refs (read at call time, not stored in messages). Full message
history (minus system) becomes the `messages` argument to
`single_turn`. Haiku-first per the existing model policy.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

CONVERSATIONS_SUBDIR = "conversations"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Message:
    role: str              # user | assistant | system
    content: str
    created_at: str = ""
    job_id: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationThread:
    id: str
    target_agent: str      # "orchestrator" | "manager:<dept>" | etc.
    purpose: str           # "founder_interview" | "chat" | "strategy"
    created_at: str
    started_by: str = "founder"
    messages: tuple[Message, ...] = field(default_factory=tuple)
    context_refs: tuple[str, ...] = field(default_factory=tuple)
    status: str = "open"   # "open" | "closed"
    summary_path: str = ""
    dept: str = ""
    onboarding_phase: str = ""
    title: str = ""        # human-readable label

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def turn_count(self) -> int:
        return sum(1 for m in self.messages if m.role in {"user", "assistant"})

    @property
    def last_user_message(self) -> Message | None:
        for m in reversed(self.messages):
            if m.role == "user":
                return m
        return None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def thread_path(company_dir: Path, thread_id: str) -> Path:
    return company_dir / CONVERSATIONS_SUBDIR / f"{thread_id}.json"


def new_thread_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Construction + persistence
# ---------------------------------------------------------------------------
def start_thread(
    *,
    target_agent: str,
    purpose: str = "chat",
    started_by: str = "founder",
    context_refs: tuple[str, ...] = (),
    dept: str = "",
    onboarding_phase: str = "",
    title: str = "",
    seed_system: str = "",
    seed_assistant: str = "",
) -> ConversationThread:
    """Construct a fresh thread. `seed_system` (if provided) is recorded
    as the first message with role=system so the transcript carries it.
    `seed_assistant` lets the manager open the conversation with a first
    question — useful for interview threads where the manager leads."""
    tid = new_thread_id()
    t = _now()
    messages: list[Message] = []
    if seed_system:
        messages.append(Message(role="system", content=seed_system, created_at=t))
    if seed_assistant:
        messages.append(Message(role="assistant", content=seed_assistant, created_at=t))
    return ConversationThread(
        id=tid,
        target_agent=target_agent,
        purpose=purpose,
        created_at=t,
        started_by=started_by,
        messages=tuple(messages),
        context_refs=context_refs,
        dept=dept,
        onboarding_phase=onboarding_phase,
        title=title or f"{purpose} with {target_agent}",
    )


def persist_thread(company_dir: Path, thread: ConversationThread) -> Path:
    path = thread_path(company_dir, thread.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(thread), sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return path


def load_thread(company_dir: Path, thread_id: str) -> ConversationThread | None:
    path = thread_path(company_dir, thread_id)
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return ConversationThread(
        id=obj.get("id", thread_id),
        target_agent=obj.get("target_agent", ""),
        purpose=obj.get("purpose", "chat"),
        created_at=obj.get("created_at", ""),
        started_by=obj.get("started_by", "founder"),
        messages=tuple(
            Message(
                role=m.get("role", "user"),
                content=m.get("content", ""),
                created_at=m.get("created_at", ""),
                job_id=m.get("job_id", ""),
                token_usage=m.get("token_usage", {}),
            )
            for m in obj.get("messages", [])
        ),
        context_refs=tuple(obj.get("context_refs", [])),
        status=obj.get("status", "open"),
        summary_path=obj.get("summary_path", ""),
        dept=obj.get("dept", ""),
        onboarding_phase=obj.get("onboarding_phase", ""),
        title=obj.get("title", ""),
    )


def list_threads(company_dir: Path) -> list[ConversationThread]:
    subdir = company_dir / CONVERSATIONS_SUBDIR
    if not subdir.exists():
        return []
    out: list[ConversationThread] = []
    for p in sorted(subdir.glob("*.json")):
        t = load_thread(company_dir, p.stem)
        if t is not None:
            out.append(t)
    out.sort(key=lambda t: t.created_at, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------
def append_message(
    company_dir: Path,
    thread_id: str,
    *,
    role: str,
    content: str,
    job_id: str = "",
    token_usage: dict[str, Any] | None = None,
) -> ConversationThread | None:
    thread = load_thread(company_dir, thread_id)
    if thread is None:
        return None
    msg = Message(
        role=role,
        content=content,
        created_at=_now(),
        job_id=job_id,
        token_usage=token_usage or {},
    )
    updated = replace(thread, messages=thread.messages + (msg,))
    persist_thread(company_dir, updated)
    return updated


def close_thread(
    company_dir: Path,
    thread_id: str,
    *,
    summary_path: str = "",
) -> ConversationThread | None:
    thread = load_thread(company_dir, thread_id)
    if thread is None:
        return None
    updated = replace(
        thread,
        status="closed",
        summary_path=summary_path or thread.summary_path,
    )
    persist_thread(company_dir, updated)
    return updated


# ---------------------------------------------------------------------------
# Turn execution (one user message → one agent reply)
# ---------------------------------------------------------------------------
def _format_context_block(company_dir: Path, refs: tuple[str, ...]) -> str:
    """Read each context_ref file (safely) and concatenate into a
    system-context block. Failures are silently skipped."""
    if not refs:
        return ""
    sections: list[str] = []
    for ref in refs:
        path = (company_dir / ref).resolve()
        try:
            path.relative_to(company_dir.resolve())
        except ValueError:
            continue
        if not path.exists() or not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        sections.append(f"## Context from `{ref}`\n\n{body}")
    if not sections:
        return ""
    return "\n\n".join(sections)


def _system_prompt_for_thread(
    thread: ConversationThread,
    company_dir: Path,
) -> str:
    """Build the system prompt — target-agent persona + any
    context_refs. Interview threads get an interview-specific preamble."""
    lines: list[str] = []
    if thread.purpose == "founder_interview":
        lines.append(
            "You are conducting a founder interview. The founder is your "
            "primary stakeholder. Your job is to ask specialty-specific "
            "questions that will let you do production-grade work on their "
            "business. Ask ONE question at a time. Wait for the answer. "
            "Build on prior answers rather than reading a static script. "
            "Keep each question short (≤30 words). If the founder's answer "
            "is thin, probe gently. When you have enough context (usually "
            "8–15 turns), propose closing and ask if they want to add "
            "anything.\n"
        )
    else:
        lines.append(
            f"You are the {thread.target_agent}. Respond conversationally "
            f"and concisely. Challenge assumptions. Cite evidence when making "
            f"claims. If asked a question outside your specialty, say so and "
            f"suggest who to ask instead.\n"
        )
    if thread.dept:
        lines.append(f"You are the manager of the **{thread.dept}** department.\n")
    if thread.target_agent == "orchestrator":
        lines.append(
            "You are the orchestrator. You can reference any department's "
            "memory or knowledge base. You do NOT own specialty decisions — "
            "defer those to the right manager.\n"
        )
    ctx = _format_context_block(company_dir, thread.context_refs)
    if ctx:
        lines.append("\n# Loaded context\n\n" + ctx)
    return "\n".join(lines)


def _messages_for_llm(thread: ConversationThread) -> list[dict]:
    """Map thread messages to the Anthropic format, dropping system
    messages (they're passed separately)."""
    out: list[dict] = []
    for m in thread.messages:
        if m.role == "system":
            continue
        out.append({"role": m.role, "content": m.content})
    return out


def send_and_reply(
    company_dir: Path,
    thread_id: str,
    user_content: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 1200,
) -> ConversationThread | None:
    """Append the founder's message, call the target agent, append the
    reply. Returns the updated thread. Errors from the LLM are
    surfaced as an assistant message starting with 'ERROR:' so the UI
    never silently drops a turn."""
    # 1) Append user message
    thread = append_message(
        company_dir, thread_id, role="user", content=user_content,
    )
    if thread is None:
        return None

    # 2) Call the LLM
    from core.llm_client import single_turn

    system = _system_prompt_for_thread(thread, company_dir)
    messages = _messages_for_llm(thread)
    cost_tag = f"conversation:{thread.purpose}:{thread.target_agent}"
    response = single_turn(
        messages=messages,
        model=model,
        cost_tag=cost_tag,
        system=system,
        max_tokens=max_tokens,
    )

    if response.error:
        reply_text = f"ERROR: {response.error}"
    else:
        reply_text = (response.text or "").strip() or "(empty response)"

    # 3) Append assistant message
    updated = append_message(
        company_dir, thread_id,
        role="assistant",
        content=reply_text,
        token_usage=response.usage or {},
    )
    return updated


# ---------------------------------------------------------------------------
# Interview synthesis (closing phase 2)
# ---------------------------------------------------------------------------
SYNTHESIS_PROMPT = """You just conducted a founder interview. Synthesize the conversation into a **founder brief** that captures EVERYTHING the founder told you.

Return the brief as markdown with these sections:
  ## Verbatim quotes
  Direct, verbatim quotes from the founder that are load-bearing for future decisions. Min 5, max 20.

  ## What I now know
  Plain-English synthesis of the facts the founder shared, organized by topic.

  ## Open questions
  What's still ambiguous after this interview. Each with a one-line why-it-matters.

  ## My starting assumptions post-interview
  3–6 assumptions I'm carrying forward, each marked [confident] / [provisional] / [speculative].

Do NOT summarize the meta-conversation. Do NOT include "then the founder said." Write as if you're producing the working document you'll reference next week.
"""


def synthesize_interview(
    company_dir: Path,
    thread_id: str,
    *,
    output_path: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 2500,
    prompt_override: str = "",
) -> tuple[ConversationThread | None, str]:
    """Ask the agent to produce a synthesis document from the thread.
    Defaults to the founder-brief prompt; pass `prompt_override` for
    different synthesis targets (e.g. a sub-agent's skill-scope).
    Writes to `output_path` (vault-relative). Returns (updated thread,
    written path)."""
    thread = load_thread(company_dir, thread_id)
    if thread is None:
        return None, ""

    from core.llm_client import single_turn

    transcript_messages = _messages_for_llm(thread)
    transcript_messages.append({
        "role": "user",
        "content": (prompt_override or SYNTHESIS_PROMPT),
    })

    system = _system_prompt_for_thread(thread, company_dir)
    response = single_turn(
        messages=transcript_messages,
        model=model,
        cost_tag=f"conversation:synthesize:{thread.dept or thread.target_agent}",
        system=system,
        max_tokens=max_tokens,
    )

    if response.error:
        body = f"# Founder brief (synthesis failed)\n\nError: {response.error}\n"
    else:
        body = response.text or ""

    target = (company_dir / output_path).resolve()
    try:
        target.relative_to(company_dir.resolve())
    except ValueError:
        raise ValueError(f"output_path {output_path!r} escapes company_dir")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    updated = close_thread(company_dir, thread_id, summary_path=output_path)
    return updated, output_path
