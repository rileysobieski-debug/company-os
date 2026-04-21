"""
Meeting system — structured multi-agent discussions
====================================================
Two meeting types:

  DepartmentMeeting  — manager + their specialists discuss a topic.
                       Manager opens and closes; specialists speak in order.
                       Each participant sees the full transcript so far.

  CrossAgentMeeting  — any combination of department managers and/or board
                       members discuss a topic. Participant specs are strings:
                         "marketing"          → marketing manager
                         "finance"            → finance manager
                         "board:Strategist"   → board Strategist member
                         "board:Contrarian"   → board Contrarian member

Meetings go through `core.llm_client.single_turn()` (NOT the SDK agent
loop) because participants are DISCUSSING, not executing tasks. No tools
are available to meeting participants.

Each participant receives:
  - Their full base system prompt (manager prompt or board member prompt)
  - A meeting-mode overlay (200-350 word discussion turn, not task output)
  - The growing transcript of all prior speakers

Outputs:
  MeetingTranscript — contains all statements; call .as_markdown() to render.
  If session_dir is provided, the transcript is written to disk.

Entry points:
  run_department_meeting(company, dept, topic, invited_specialists=None, session_dir=None)
  run_cross_agent_meeting(company, departments, participants, topic, session_dir=None)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import config
from core.board import (
    build_system_prompt as _board_member_prompt,
    ORDER as BOARD_ORDER,
    load_board_profiles,
)
from core.company import CompanyConfig
from core.llm_client import single_turn
from core.managers.base import build_manager_prompt, build_specialist_prompt
from core.managers.loader import DepartmentConfig


# MEETING_MODEL moved to core/config.py in chunk 1a.6 — callers now read
# from config.get_model("meeting").
MEETING_MAX_TOKENS = 1000  # meetings are discussion turns, not full task outputs

_MEETING_OVERLAY = """
=== MEETING MODE ===
You are participating in a structured discussion, not executing a task.
Read the topic and the transcript of prior speakers carefully.
Contribute your perspective in 200-350 words. Lead with your clearest position,
then your reasoning. If you disagree with a prior speaker, name them and say why.
Do NOT attempt to summarize the whole debate or synthesize everyone's views —
that is the closing role's job. Stay in your own lane and be direct.
"""

_CLOSING_OVERLAY = """
=== MEETING MODE — CLOSING VOICE ===
You are the closing voice in this structured discussion.
Read the full transcript above. Your job: synthesize what was said, identify
where there is genuine consensus vs. genuine conflict, and state a recommended
path or the clearest remaining decision point. 300-450 words. Be direct.
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class MeetingStatement:
    participant: str
    content: str


@dataclass
class MeetingTranscript:
    topic: str
    meeting_type: str  # "department" | "cross-agent"
    statements: list[MeetingStatement] = field(default_factory=list)

    def as_markdown(self) -> str:
        lines = [
            f"# Meeting Transcript",
            f"**Type:** {self.meeting_type}",
            f"**Topic:** {self.topic}",
            "",
        ]
        for s in self.statements:
            lines.append(f"## {s.participant}")
            lines.append("")
            lines.append(s.content.strip())
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _build_turn_message(topic: str, transcript: list[MeetingStatement]) -> str:
    parts = [f"=== MEETING TOPIC ===\n{topic.strip()}"]
    if transcript:
        parts.append("\n=== TRANSCRIPT SO FAR ===")
        for s in transcript:
            parts.append(f"\n--- {s.participant} ---\n{s.content.strip()}")
    parts.append("\n=== YOUR TURN ===\nSpeak now.")
    return "\n".join(parts)


def _invoke_participant(
    display_name: str,
    system_prompt: str,
    topic: str,
    transcript: list[MeetingStatement],
    is_closing: bool = False,
) -> str:
    overlay = _CLOSING_OVERLAY if is_closing else _MEETING_OVERLAY
    full_system = system_prompt.strip() + "\n" + overlay
    user_message = _build_turn_message(topic, transcript)

    response = single_turn(
        messages=[{"role": "user", "content": user_message}],
        model=config.get_model("meeting"),
        cost_tag=f"meeting.turn.{display_name}",
        system=full_system,
        max_tokens=MEETING_MAX_TOKENS,
    )
    if response.error:
        return f"(meeting participant '{display_name}' LLM error: {response.error})"
    return response.text.strip()


def _write_transcript(transcript: MeetingTranscript, session_dir: Path, filename: str) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / filename).write_text(transcript.as_markdown(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Department meeting
# ---------------------------------------------------------------------------
def run_department_meeting(
    company: CompanyConfig,
    dept: DepartmentConfig,
    topic: str,
    invited_specialists: list[str] | None = None,
    session_dir: Path | None = None,
    all_departments: list[DepartmentConfig] | None = None,
) -> MeetingTranscript:
    """Convene a department meeting: manager opens, specialists speak,
    manager closes with synthesis.

    Parameters
    ----------
    company : CompanyConfig
    dept : DepartmentConfig
    topic : str
        The meeting topic / question.
    invited_specialists : list[str] | None
        Names of specialists to invite. If None, all department specialists
        are included.
    session_dir : Path | None
        If provided, writes `{session_dir}/dept-meeting-{dept.name}.md`.
    """
    transcript = MeetingTranscript(topic=topic, meeting_type="department")
    manager_prompt = build_manager_prompt(company, dept, all_departments)

    # Resolve which specialists participate
    specialists = dept.specialists
    if invited_specialists is not None:
        specialists = [s for s in specialists if s.name in invited_specialists]

    # No specialists → not a meeting; record and return without burning tokens
    if not specialists:
        transcript.statements.append(
            MeetingStatement(
                participant="System",
                content=(
                    f"Department '{dept.name}' has no specialists configured "
                    f"(or none of the invited specialists were found). "
                    f"A meeting requires at least one non-manager voice. "
                    f"Run department onboarding or invite valid specialists."
                ),
            )
        )
        if session_dir is not None:
            _write_transcript(transcript, session_dir, f"dept-meeting-{dept.name}.md")
        return transcript

    # Manager opens
    opening = _invoke_participant(
        f"{dept.display_name} Manager",
        manager_prompt,
        topic,
        transcript.statements,
        is_closing=False,
    )
    transcript.statements.append(
        MeetingStatement(participant=f"{dept.display_name} Manager (opening)", content=opening)
    )

    # Specialists speak in order
    for spec in specialists:
        spec_prompt = build_specialist_prompt(company, dept, spec, all_departments)
        response = _invoke_participant(
            spec.name,
            spec_prompt,
            topic,
            transcript.statements,
            is_closing=False,
        )
        transcript.statements.append(MeetingStatement(participant=spec.name, content=response))

    # Manager closes with synthesis (rebuild prompt — nothing changed but be explicit)
    closing = _invoke_participant(
        f"{dept.display_name} Manager",
        manager_prompt,
        topic,
        transcript.statements,
        is_closing=True,
    )
    transcript.statements.append(
        MeetingStatement(participant=f"{dept.display_name} Manager (synthesis)", content=closing)
    )

    if session_dir is not None:
        _write_transcript(transcript, session_dir, f"dept-meeting-{dept.name}.md")

    return transcript


# ---------------------------------------------------------------------------
# Cross-agent meeting
# ---------------------------------------------------------------------------
def _resolve_participant(
    spec: str,
    company: CompanyConfig,
    departments: list[DepartmentConfig],
) -> tuple[str, str] | None:
    """Resolve a participant spec string to (display_name, system_prompt).

    Supported formats:
      "marketing"          → marketing department manager
      "board:Strategist"   → board Strategist member
      "board:Contrarian"   → board Contrarian member
      etc.

    Returns None if the spec cannot be resolved (logs a warning).
    """
    spec = spec.strip()

    # Board member: "board:RoleName" — case-insensitive role lookup
    if spec.lower().startswith("board:"):
        raw_role = spec[6:].strip()
        # Match against canonical BOARD_ORDER ignoring case
        role = next((r for r in BOARD_ORDER if r.lower() == raw_role.lower()), None)
        if role is None:
            print(f"[meeting] WARNING: unknown board role '{raw_role}' — valid: {BOARD_ORDER}")
            return None
        # Load calibrated profile so meeting voices match debate voices
        profiles = load_board_profiles(company)
        prompt = _board_member_prompt(role, company, profile=profiles.get(role), departments=departments)
        return (f"Board:{role}", prompt)

    # Department manager — case-insensitive name lookup
    matched = next((d for d in departments if d.name.lower() == spec.lower()), None)
    if matched is None:
        names = [d.name for d in departments]
        print(f"[meeting] WARNING: unknown participant '{spec}' — valid depts: {names}")
        return None
    prompt = build_manager_prompt(company, matched, departments)
    return (f"{matched.display_name} Manager", prompt)


def run_cross_agent_meeting(
    company: CompanyConfig,
    departments: list[DepartmentConfig],
    participants: list[str],
    topic: str,
    session_dir: Path | None = None,
) -> MeetingTranscript:
    """Convene a cross-agent meeting between any combination of department
    managers and/or board members.

    Parameters
    ----------
    company : CompanyConfig
    departments : list[DepartmentConfig]
        Pre-loaded departments (passed from Orchestrator to avoid re-scanning).
    participants : list[str]
        Participant spec strings, e.g. ["marketing", "finance", "board:Analyst"].
        Last participant in the list speaks as the closing synthesizer.
    topic : str
        The meeting topic / question.
    session_dir : Path | None
        If provided, writes `{session_dir}/cross-meeting.md`.
    """
    transcript = MeetingTranscript(topic=topic, meeting_type="cross-agent")

    # Resolve all participants, dropping unknowns
    resolved: list[tuple[str, str]] = []
    for spec in participants:
        result = _resolve_participant(spec, company, departments)
        if result:
            resolved.append(result)

    if not resolved:
        transcript.statements.append(
            MeetingStatement(
                participant="System",
                content="No valid participants could be resolved. Meeting aborted.",
            )
        )
        return transcript

    for i, (display_name, system_prompt) in enumerate(resolved):
        is_closing = i == len(resolved) - 1
        response = _invoke_participant(
            display_name,
            system_prompt,
            topic,
            transcript.statements,
            is_closing=is_closing,
        )
        transcript.statements.append(MeetingStatement(participant=display_name, content=response))

    if session_dir is not None:
        _write_transcript(transcript, session_dir, "cross-meeting.md")

    return transcript
