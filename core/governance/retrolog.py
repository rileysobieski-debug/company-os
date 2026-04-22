"""Retro-logging of founder-initiated dispatches.

Every human click that fires a background dispatch in the webapp
writes a row to the governance `decisions` table with source="human".
This gives Phase 1 observability into what the founder has been doing
without changing any existing flow.

Two entry points:

  record_human_action(...):
      The low-level helper. Builds a DecisionRecord and persists it.
      Fail-safe: exceptions are logged and swallowed so a retrolog
      failure can never break a real dispatch.

  retrolog_dispatch(action_type, agent_resolver=None):
      Flask decorator. Apply to any founder-initiated route. The
      decorator calls record_human_action after the view returns.
      Using the decorator instead of scattered one-liners avoids the
      maintenance time-bomb of missing a new route during a refactor.
"""
from __future__ import annotations

import datetime
import functools
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from core.governance.models import DecisionRecord
from core.governance.storage import (
    open_db, persist_decision, most_recent_decision_at,
)


_logger = logging.getLogger("governance.retrolog")


# Founder-initiated dispatch routes redirect to `/c/<slug>/j/<job_id>`
# (and sometimes `/c/<slug>/j/<job_id>/...`). This pattern extracts the
# job id segment from such a Location header or URL. Not part of the
# public API surface; used by the retrolog decorator so the logged
# decisions row can be joined back to the job that was actually
# dispatched rather than just the route that was clicked.
_JOB_ID_RE = re.compile(r"/j/([A-Za-z0-9_\-]+)")


def _extract_job_id_from_response(resp: Any) -> str | None:
    """Pull a job id out of a Flask view's return value.

    Accepts three shapes: (a) a `flask.wrappers.Response` whose
    `location` header points at a dispatch URL, (b) a tuple whose
    first element is a redirect Response, or (c) a string that is
    itself a URL or path. Returns `None` for anything else. Never
    raises: a retrolog helper must not break a real dispatch.
    """
    if resp is None:
        return None
    # Tuple forms: (body, status) or (Response, status) etc.
    if isinstance(resp, tuple) and resp:
        resp = resp[0]
    # Flask Response-like: look for Location header.
    location = None
    try:
        headers = getattr(resp, "headers", None)
        if headers is not None:
            location = headers.get("Location")
    except Exception:
        location = None
    if location is None:
        location = getattr(resp, "location", None)
    if not location and isinstance(resp, str):
        location = resp
    if not location:
        return None
    try:
        match = _JOB_ID_RE.search(str(location))
    except Exception:
        return None
    return match.group(1) if match else None


def _now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def record_human_action(
    company_dir: Path,
    *,
    action_type: str,
    agent_id: str | None,
    action_summary: str = "",
    outcome: str = "dispatched",
    founder_trigger_route: str | None = None,
    job_id: str | None = None,
    notes: str = "",
) -> None:
    """Write one decisions row with source='human'. Safe to call from
    any request handler. If the insert fails, log with exc_info and
    return normally; observability must never break production."""
    rec = DecisionRecord(
        decision_id=uuid.uuid4().hex,
        source="human",
        agent_id=agent_id,
        action_type=action_type,
        action_summary=action_summary,
        outcome=outcome,
        decided_at=_now_iso(),
        founder_trigger_route=founder_trigger_route,
        job_id=job_id,
        notes=notes,
    )
    try:
        conn = open_db(company_dir)
        try:
            persist_decision(conn, rec)
        finally:
            conn.close()
    except Exception:
        _logger.error(
            "retrolog write failed for action_type=%s agent=%s route=%s",
            action_type, agent_id, founder_trigger_route, exc_info=True,
        )


def last_successful_retrolog_write(company_dir: Path) -> str | None:
    """Most recent decisions.decided_at of any row. Surfaced on the
    governance UI so a silently broken retrolog is immediately
    visible to the founder."""
    try:
        conn = open_db(company_dir)
        try:
            return most_recent_decision_at(conn)
        finally:
            conn.close()
    except Exception:
        _logger.error("last_successful_retrolog_write probe failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Flask decorator
# ---------------------------------------------------------------------------
def retrolog_dispatch(
    action_type: str,
    *,
    agent_resolver: Callable[[dict], str | None] | None = None,
    summary_resolver: Callable[[dict], str] | None = None,
):
    """Wrap a Flask view function so successful returns also write a
    decisions row with source='human'. The wrapped view must accept
    `slug` as its first argument (companies are slug-addressed).

    Positional and keyword args from the request are forwarded to
    `agent_resolver` and `summary_resolver` as a kwargs dict so they
    can extract dept, role_slug, etc. without pulling from Flask globals.
    """
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(*args, **kwargs):
            # Import here to avoid circular imports at module load time.
            from flask import request as flask_request
            # Resolve the company_dir without parsing the slug ourselves.
            # webapp.app._company_or_404 is the authority but calling it
            # twice would duplicate work. Safer: import on demand and
            # only use if the view did not raise.
            try:
                resp = view_func(*args, **kwargs)
            except Exception:
                raise
            try:
                from webapp.app import _company_or_404
                slug = kwargs.get("slug") or (args[0] if args else None)
                if not slug:
                    return resp
                company, _ = _company_or_404(slug)
                merged = dict(kwargs)
                agent_id = (
                    agent_resolver(merged) if agent_resolver is not None else None
                )
                action_summary = (
                    summary_resolver(merged)
                    if summary_resolver is not None
                    else _default_summary(action_type, merged)
                )
                founder_trigger_route = getattr(flask_request, "path", None)
                job_id = _extract_job_id_from_response(resp)
                record_human_action(
                    company.company_dir,
                    action_type=action_type,
                    agent_id=agent_id,
                    action_summary=action_summary,
                    outcome="dispatched",
                    founder_trigger_route=founder_trigger_route,
                    job_id=job_id,
                )
            except Exception:
                _logger.error(
                    "retrolog decorator failed for %s", action_type, exc_info=True,
                )
            return resp
        return wrapper
    return decorator


def _default_summary(action_type: str, kwargs: dict[str, Any]) -> str:
    parts = [action_type]
    if "dept" in kwargs:
        parts.append(f"dept={kwargs['dept']}")
    if "role_slug" in kwargs:
        parts.append(f"role={kwargs['role_slug']}")
    if "candidate_id" in kwargs:
        parts.append(f"candidate={kwargs['candidate_id']}")
    return " | ".join(parts)
