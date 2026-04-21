"""
core/onboarding/premortem.py — Phase 8.3 — §5.2 / §0.5 pre-mortem injection
===========================================================================
The founder's pre-mortem answer is load-bearing per plan §5.2 (line 346):

  "The founder's answer to the pre-mortem question is injected as context
   into every cross-dept synthesis step ('Before synthesizing, check:
   does this plan accelerate the founder's named failure mode? If yes,
   surface that explicitly in the output.') and into every adversary
   activation. The pre-mortem is cheap to capture and high-signal — it
   should appear in every synthesis system prompt that could advance the
   business in a direction."

This module is a pure string primitive — no I/O, no LLM calls. Two
capabilities:

  * `load_premortem_from_profile(md)` — parse the `## Pre-mortem` section
    from a `founder_profile.md` string, strip blockquote formatting.
  * `inject_premortem_context(body, premortem, *, kind)` — prepend a
    marker-guarded block to `body` with a kind-specific guard sentence.
    Idempotent: repeat calls are a no-op when the marker is already
    present.

Two kinds:
  * `synthesis` — used on every cross-dept synthesis system prompt.
  * `adversary` — used on every §0.5 adversary activation.

Both emit the same factual block (founder's named cause) with different
guard sentences. Downstream agents parse the marker to distinguish
injected context from founder-authored brief text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PREMORTEM_MARKER = "<!-- premortem-injected -->"
PREMORTEM_END_MARKER = "<!-- /premortem-injected -->"

SYNTHESIS_GUARD_SENTENCE = (
    "Before synthesizing, check: does this plan accelerate the founder's "
    "named failure mode? If yes, surface that explicitly in the output."
)

ADVERSARY_GUARD_SENTENCE = (
    "When stress-testing the founder's direction, reference this failure "
    "mode explicitly — the founder already accepts it as plausible and "
    "expects the adversary to flag plans that drift toward it."
)

InjectionKind = Literal["synthesis", "adversary"]


@dataclass(frozen=True)
class PremortemContext:
    """The parsed pre-mortem payload. `cause` is the founder's raw text."""

    cause: str
    source_path: str | None = None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def load_premortem_from_profile(
    profile_md: str,
    *,
    source_path: str | None = None,
) -> PremortemContext | None:
    """Parse the `## Pre-mortem` section from a founder_profile.md string.

    Returns None if the section is absent or its body is empty. The
    founder_profile.md renderer writes the cause as a markdown
    blockquote (`> ...`) — this parser strips the leading `> ` on each
    line and joins the paragraph.
    """
    lines = profile_md.splitlines()
    in_section = False
    collected: list[str] = []
    for raw in lines:
        stripped = raw.rstrip()
        if stripped.lower().startswith("## pre-mortem"):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            # next top-level section — stop
            break
        if in_section:
            collected.append(raw)
    if not in_section:
        return None

    cause_lines: list[str] = []
    in_italic_block = False
    for raw in collected:
        s = raw.strip()
        if not s:
            continue
        # Italic meta-annotation may span multiple lines
        # ("_Load-bearing per §0.5: this text is injected\nand so on._").
        if in_italic_block:
            if s.endswith("_"):
                in_italic_block = False
            continue
        if s.startswith("_") and not s.endswith("_"):
            in_italic_block = True
            continue
        if s.startswith("_") and s.endswith("_") and len(s) > 1:
            # single-line italic meta-annotation — skip
            continue
        if s.startswith(">"):
            cause_lines.append(s.lstrip(">").strip())
        else:
            cause_lines.append(s)
    cause = " ".join(cause_lines).strip()
    if not cause:
        return None
    return PremortemContext(cause=cause, source_path=source_path)


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------
def is_premortem_injected(body: str) -> bool:
    """Return True iff `body` already carries an injection marker."""
    return PREMORTEM_MARKER in body


def inject_premortem_context(
    body: str,
    premortem: PremortemContext | None,
    *,
    kind: InjectionKind = "synthesis",
) -> str:
    """Prepend a pre-mortem context block to `body`.

    * `None` premortem → returns body unchanged (nothing to inject).
    * Already-injected body → returns body unchanged (idempotent).
    * Otherwise: emits a marker-guarded block followed by `body`.
    """
    if premortem is None:
        return body
    if is_premortem_injected(body):
        return body
    if kind == "synthesis":
        guard = SYNTHESIS_GUARD_SENTENCE
    elif kind == "adversary":
        guard = ADVERSARY_GUARD_SENTENCE
    else:  # pragma: no cover — Literal narrows this
        raise ValueError(f"unknown injection kind: {kind!r}")
    block = (
        f"{PREMORTEM_MARKER}\n"
        f"## Founder pre-mortem (load-bearing per §0.5)\n"
        f"The founder named this as the most likely cause of failure "
        f"12 months out:\n\n"
        f"> {premortem.cause}\n\n"
        f"{guard}\n"
        f"{PREMORTEM_END_MARKER}\n"
    )
    separator = "\n\n" if body and not body.startswith("\n") else ""
    return block + separator + body


def strip_premortem_injection(body: str) -> str:
    """Remove any injected pre-mortem block from `body`.

    If no marker is present, returns `body` unchanged. Useful when a
    caller wants to replace an old injection with an updated one.
    """
    start = body.find(PREMORTEM_MARKER)
    if start == -1:
        return body
    end_marker_idx = body.find(PREMORTEM_END_MARKER, start)
    if end_marker_idx == -1:
        return body  # malformed — leave alone
    end = end_marker_idx + len(PREMORTEM_END_MARKER)
    # Eat trailing newlines / separator introduced by inject_premortem_context
    remainder = body[end:]
    remainder_lstripped = remainder.lstrip("\n")
    return (body[:start] + remainder_lstripped).lstrip("\n")
