"""
webapp/services — backend functions for the GUI

These wrap the core/ engine + comprehensive_demo for use by the Flask routes.
All operations are performed against ONE company at a time (selected via
session cookie / query param).

Heavy operations (department demos, board deliberations, manager dispatches)
are run on a background thread pool so the GUI stays responsive. The pool
exposes job records (id, status, started_at, completed_at, log_tail, result)
so the UI can poll for progress.
"""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.board import convene_board
from core.company import CompanyConfig, load_company
from core.env import get_vault_dir
from core.managers.base import dispatch_manager
from core.managers.loader import DepartmentConfig, load_departments


# ---------------------------------------------------------------------------
# Job runner — background tasks (demo, board, dispatch) so the UI can poll
# ---------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    kind: str           # "dept_demo" | "all_demos" | "synthesis" | "board" | "dispatch" | "meeting"
    label: str
    company_dir: str
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    completed_at: str = ""
    status: str = "running"  # "running" | "done" | "error"
    log: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    # Lock guards all access to self.log; worker thread writes while Flask
    # request threads read via to_dict() / api_job polling.
    _log_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def append_log(self, line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        with self._log_lock:
            self.log.append(f"[{ts}] {line}")
            # Keep log bounded; reassignment under the lock prevents a concurrent
            # to_dict() read from seeing an inconsistent list reference.
            if len(self.log) > 500:
                self.log = self.log[-500:]

    def to_dict(self) -> dict[str, Any]:
        # Capture a single consistent snapshot of the log list under the lock
        # so that log_tail and log_count always refer to the same list state.
        with self._log_lock:
            log_snapshot = list(self.log)
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "company_dir": self.company_dir,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "log_tail": log_snapshot[-50:],
            "log_count": len(log_snapshot),
            "result": self.result,
            "error": self.error,
        }


class JobRegistry:
    """In-memory job registry. Keeps last N jobs."""
    MAX_JOBS = 100

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Phase 14 — raised from 2 → 10 so batch-scenario runs actually
        # parallelize. Each job shells out to the SDK/Anthropic API so
        # CPU contention is negligible; API rate limits are the real
        # constraint. Tune via `COMPANY_OS_JOB_WORKERS` env override.
        import os
        max_workers = int(os.environ.get("COMPANY_OS_JOB_WORKERS", "10"))
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="cos-job",
        )

    def submit(
        self,
        kind: str,
        label: str,
        company_dir: str,
        target: Callable[[Job], dict[str, Any]],
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label, company_dir=company_dir)
        with self._lock:
            self._jobs[job.id] = job
            self._evict_if_needed()

        def _runner() -> None:
            try:
                job.append_log(f"START — {label}")
                result = target(job)
                job.result = result or {}
                job.status = "done"
                job.append_log("DONE")
            except Exception as exc:  # noqa: BLE001
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.append_log(f"ERROR: {job.error}")
                job.append_log(traceback.format_exc()[-2000:])
            finally:
                job.completed_at = datetime.now().isoformat(timespec="seconds")

        self._executor.submit(_runner)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, company_dir: str | None = None) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if company_dir:
            jobs = [j for j in jobs if j.company_dir == company_dir]
        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    def _evict_if_needed(self) -> None:
        if len(self._jobs) <= self.MAX_JOBS:
            return
        oldest = sorted(self._jobs.values(), key=lambda j: j.started_at)
        to_drop = len(self._jobs) - self.MAX_JOBS
        for j in oldest[:to_drop]:
            del self._jobs[j.id]


# Module-level singleton
JOB_REGISTRY = JobRegistry()


# ---------------------------------------------------------------------------
# Company discovery + loading
# ---------------------------------------------------------------------------
def discover_companies() -> list[dict[str, Any]]:
    """Find companies by scanning the vault for folders containing config.json."""
    try:
        vault_dir = get_vault_dir()
    except RuntimeError:
        return []
    if not vault_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(vault_dir.iterdir()):
        if not child.is_dir():
            continue
        cfg = child / "config.json"
        if not cfg.exists():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "company_name": data.get("company_name", child.name),
            "company_id": data.get("company_id", child.name),
            "industry": data.get("industry", ""),
            "company_dir": str(child),
            "active_departments": data.get("active_departments", []),
        })
    return out


def load_company_safe(company_dir: str) -> CompanyConfig | None:
    try:
        return load_company(company_dir)
    except Exception:  # noqa: BLE001
        return None


def load_departments_safe(company: CompanyConfig) -> list[DepartmentConfig]:
    try:
        return load_departments(company)
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Read-only views (used by GUI pages)
# ---------------------------------------------------------------------------
def read_company_summary(company: CompanyConfig) -> dict[str, Any]:
    """Cheap structured view of the company config + on-disk artifacts."""
    cd = company.company_dir
    return {
        "name": company.name,
        "company_id": company.company_id,
        "industry": company.industry,
        "company_dir": str(cd),
        "active_departments": company.active_departments,
        "priorities": company.priorities,
        "settled_convictions": company.settled_convictions,
        "hard_constraints": company.hard_constraints,
        "delegation": company.delegation,
        "has_orchestrator_charter": (cd / "orchestrator-charter.md").exists(),
        "has_board_onboarding": (cd / "board" / "onboarding.json").exists(),
        "demo_artifacts_exist": (cd / "demo-artifacts" / "INDEX.md").exists(),
        "context_preview": company.context[:1500],
    }


def list_dept_summaries(
    company: CompanyConfig,
    departments: list[DepartmentConfig],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dept in departments:
        manager_mem_exists = dept.manager_memory_path.exists()
        onboard_marker = dept.dept_dir / "onboarding.json"
        setup_checklist = dept.dept_dir / "setup-checklist.md"
        out.append({
            "name": dept.name,
            "display_name": dept.display_name,
            "specialist_count": len(dept.specialists),
            "specialists": [
                {"name": s.name, "attribute": s.attribute, "description": s.description}
                for s in dept.specialists
            ],
            "manager_memory_exists": manager_mem_exists,
            "onboarded": onboard_marker.exists(),
            "has_setup_checklist": setup_checklist.exists(),
            "dept_dir": str(dept.dept_dir),
        })
    return out


def read_dept_detail(
    company: CompanyConfig,
    departments: list[DepartmentConfig],
    dept_name: str,
) -> dict[str, Any] | None:
    dept = next((d for d in departments if d.name == dept_name), None)
    if dept is None:
        return None
    cd = company.company_dir

    def _read_or_empty(path: Path, max_chars: int = 30000) -> str:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        return text[:max_chars]

    return {
        "name": dept.name,
        "display_name": dept.display_name,
        "dept_dir": str(dept.dept_dir),
        "charter_body": dept.prompt_body,
        "manager_memory": _read_or_empty(dept.manager_memory_path),
        "setup_checklist": _read_or_empty(dept.dept_dir / "setup-checklist.md"),
        "specialists": [
            {
                "name": s.name,
                "attribute": s.attribute,
                "description": s.description,
                "tools": s.tools,
                "is_scout": s.is_scout,
                "specialist_dir": str(s.specialist_dir),
                "memory_exists": s.memory_path.exists(),
                "memory": _read_or_empty(s.memory_path, max_chars=10000),
                "prompt_body_preview": s.prompt_body[:2000],
            }
            for s in dept.specialists
        ],
        "demo_artifact": _read_or_empty(cd / "demo-artifacts" / "depts" / f"{dept.name}-demo.md"),
        "demo_artifact_path": str(cd / "demo-artifacts" / "depts" / f"{dept.name}-demo.md"),
    }


def list_board_profiles(company: CompanyConfig) -> list[dict[str, Any]]:
    board_dir = company.company_dir / "board"
    out: list[dict[str, Any]] = []
    if not board_dir.exists():
        return out
    from core.board import ORDER as _BOARD_ORDER  # single source of truth — chunk 1a.6
    for role in _BOARD_ORDER:
        path = board_dir / f"{role.lower()}-profile.md"
        body = path.read_text(encoding="utf-8") if path.exists() else ""
        out.append({
            "role": role,
            "exists": path.exists(),
            "body": body,
            "path": str(path),
        })
    return out


def list_board_meetings(company: CompanyConfig) -> list[dict[str, Any]]:
    meetings_dir = company.company_dir / "board" / "meetings"
    if not meetings_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(meetings_dir.glob("*.md"), reverse=True):
        try:
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        except OSError:
            mtime = ""
        # Pull the first heading + first ~300 chars
        text = path.read_text(encoding="utf-8")
        first_line = next((l for l in text.splitlines() if l.startswith("#")), path.name)
        snippet = text[:500].strip()
        out.append({
            "name": path.name,
            "path": str(path),
            "rel_path": str(path.relative_to(company.company_dir)),
            "title": first_line.lstrip("# ").strip(),
            "snippet": snippet,
            "mtime": mtime,
        })
    return out


def list_sessions(company: CompanyConfig) -> list[dict[str, Any]]:
    sessions_dir = company.company_dir / "sessions"
    if not sessions_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            mtime = ""
        files = [p.name for p in path.iterdir() if p.is_file()][:10]
        out.append({
            "name": path.name,
            "path": str(path),
            "mtime": mtime,
            "file_count": sum(1 for _ in path.iterdir() if _.is_file()),
            "sample_files": files,
        })
    return out


def list_decisions(company: CompanyConfig) -> list[dict[str, Any]]:
    decisions_dir = company.company_dir / "decisions"
    if not decisions_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(decisions_dir.glob("*.md"), reverse=True):
        text = path.read_text(encoding="utf-8")
        first_line = next((l for l in text.splitlines() if l.startswith("#")), path.name)
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            mtime = ""
        out.append({
            "name": path.name,
            "path": str(path),
            "rel_path": str(path.relative_to(company.company_dir)),
            "title": first_line.lstrip("# ").strip(),
            "mtime": mtime,
        })
    return out


def list_demo_artifacts(company: CompanyConfig) -> dict[str, Any]:
    """Structured view of demo-artifacts/."""
    root = company.company_dir / "demo-artifacts"
    if not root.exists():
        return {"exists": False, "files": []}
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.md")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            mtime = ""
        rel = path.relative_to(company.company_dir)
        files.append({
            "name": path.name,
            "rel_path": str(rel),
            "path": str(path),
            "mtime": mtime,
            "size": path.stat().st_size,
        })
    summary_path = root / "_run-summary.json"
    summary: dict[str, Any] | None = None
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            summary = None
    return {
        "exists": True,
        "root": str(root),
        "files": files,
        "run_summary": summary,
    }


def read_artifact_safe(
    company: CompanyConfig,
    rel_path: str,
) -> dict[str, Any] | None:
    """Read a markdown artifact under the company dir. Sandboxed."""
    candidate = (company.company_dir / rel_path).resolve()
    try:
        candidate.relative_to(company.company_dir.resolve())
    except ValueError:
        return None  # path escapes sandbox
    if not candidate.exists() or not candidate.is_file():
        return None
    text = candidate.read_text(encoding="utf-8", errors="replace")
    try:
        mtime = datetime.fromtimestamp(candidate.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        mtime = ""
    return {
        "name": candidate.name,
        "rel_path": str(candidate.relative_to(company.company_dir)),
        "abs_path": str(candidate),
        "size": candidate.stat().st_size,
        "mtime": mtime,
        "text": text,
    }


# ---------------------------------------------------------------------------
# Action runners (return results to be stored as Job.result)
# ---------------------------------------------------------------------------
def run_dispatch_action(
    job: Job,
    company_dir: str,
    dept_name: str,
    brief: str,
) -> dict[str, Any]:
    company = load_company(company_dir)
    departments = load_departments(company)
    dept = next((d for d in departments if d.name == dept_name), None)
    if dept is None:
        raise ValueError(f"Department '{dept_name}' not found")
    job.append_log(f"Dispatching {dept.display_name} manager...")
    job.append_log(f"Specialists in dept: {[s.name for s in dept.specialists]}")
    result = dispatch_manager(dept_name, brief, company, departments=departments)
    job.append_log(f"Manager returned. Specialists called: {result.specialists_called}")
    return {
        "dept": dept_name,
        # Coerce SpecialistResult → str at the JSON boundary. Chunk 1b.4.
        "specialists_called": [str(s) for s in result.specialists_called],
        "tool_call_count": len(result.tool_calls),
        "raw_messages_count": result.raw_messages_count,
        "final_text": result.final_text,
    }


def _safe_relpath(abs_path: str | Path | None, company_dir: Path) -> str:
    """Return the path relative to company_dir using forward slashes, or '' if
    the path is empty or escapes the company sandbox. Cross-platform safe."""
    if not abs_path:
        return ""
    try:
        p = Path(abs_path).resolve()
        rel = p.relative_to(company_dir.resolve())
        return rel.as_posix()
    except (ValueError, OSError):
        return ""


def run_board_action(
    job: Job,
    company_dir: str,
    topic: str,
    include_dossier: bool = False,
) -> dict[str, Any]:
    company = load_company(company_dir)
    departments = load_departments(company)
    if include_dossier:
        from comprehensive_demo import run_board_deliberation
        job.append_log("Running board with dept-dossier injection...")
        result = run_board_deliberation(
            company, departments, topic=topic, include_dept_dossier=True
        )
        result["summary_rel_path"] = _safe_relpath(result.get("summary_path"), company.company_dir)
        result["transcript_rel_path"] = _safe_relpath(result.get("transcript_path"), company.company_dir)
        return result
    job.append_log("Convening board (silent observer enabled)...")
    debate = convene_board(
        topic=topic,
        company=company,
        departments=departments,
        observer_summary=True,
        write_to_company=True,
    )
    return {
        "topic": topic,
        "summary_path": str(debate.summary_path) if debate.summary_path else "",
        "transcript_path": str(debate.transcript_path) if debate.transcript_path else "",
        "summary_rel_path": _safe_relpath(debate.summary_path, company.company_dir),
        "transcript_rel_path": _safe_relpath(debate.transcript_path, company.company_dir),
        "observer_summary_preview": debate.observer_summary[:1500],
        "statements_count": len(debate.statements),
        "queries_made_total": sum(len(s.queries_made) for s in debate.statements),
    }


def run_full_demo_action(
    job: Job,
    company_dir: str,
    only_depts: list[str] | None,
    force: bool,
    skip_synthesis: bool,
    skip_board: bool,
) -> dict[str, Any]:
    """Equivalent to the CLI runner — used by the GUI's 'Run full demo' button."""
    from comprehensive_demo import (
        run_all_department_demos,
        run_orchestrator_synthesis,
        run_board_deliberation,
        write_index,
    )

    company = load_company(company_dir)
    departments = load_departments(company)

    job.append_log(f"Phase 1 — department demos (only={only_depts}, force={force})")
    dept_results = run_all_department_demos(company, departments, only=only_depts, force=force)
    for r in dept_results:
        job.append_log(f"  {r['dept']}: {r.get('status')}")

    synthesis_result = None
    if not skip_synthesis and any(r.get("status") in ("generated", "skipped") for r in dept_results):
        job.append_log("Phase 2 — orchestrator synthesis")
        synthesis_result = run_orchestrator_synthesis(company, dept_results)

    board_result = None
    if not skip_board:
        job.append_log("Phase 3 — board deliberation (silent observer)")
        board_result = run_board_deliberation(company, departments, include_dept_dossier=True)

    index_path = write_index(company, dept_results, synthesis_result, board_result)

    return {
        "dept_count": len(dept_results),
        "dept_results": [{k: v for k, v in r.items() if k != "summary"} for r in dept_results],
        "synthesis_path": synthesis_result.get("path") if synthesis_result else None,
        "synthesis_rel_path": _safe_relpath(
            synthesis_result.get("path") if synthesis_result else None, company.company_dir
        ),
        "board_summary_path": board_result.get("summary_path") if board_result else None,
        "board_summary_rel_path": _safe_relpath(
            board_result.get("summary_path") if board_result else None, company.company_dir
        ),
        "index_path": str(index_path),
        "index_rel_path": _safe_relpath(index_path, company.company_dir),
    }


# ---------------------------------------------------------------------------
# Cost log reader (chunk 1a.9)
# ---------------------------------------------------------------------------
def cost_log_reader(company: CompanyConfig) -> dict[str, Any]:
    """Read `<company_dir>/cost-log.jsonl` and return a structured summary.

    Returned shape:
        {
          "entries": [ {timestamp, session_id, cost_tag, model, input_tokens,
                         output_tokens, ...}, ... ],
          "by_session": { session_id: {entry_count, input, output, total} },
          "by_tag":     { cost_tag:   {entry_count, input, output, total} },
          "totals":     {input, output, total, call_count},
          "log_path":   str | None,
          "missing":    bool,
        }

    A missing log file is not an error — it means no LLM calls have been
    recorded yet. The template renders a friendly empty state in that case.
    """
    log_path = company.company_dir / "cost-log.jsonl"
    entries: list[dict[str, Any]] = []
    if not log_path.exists():
        return {
            "entries": entries,
            "by_session": {},
            "by_tag": {},
            "totals": {"input": 0, "output": 0, "total": 0, "call_count": 0},
            "log_path": str(log_path),
            "missing": True,
        }

    try:
        for raw in log_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                # One bad line should not sink the dashboard; skip silently.
                continue
    except OSError:
        # Unreadable file surfaces as missing=True — caller shows the empty state.
        return {
            "entries": entries,
            "by_session": {},
            "by_tag": {},
            "totals": {"input": 0, "output": 0, "total": 0, "call_count": 0},
            "log_path": str(log_path),
            "missing": True,
        }

    def _bucket() -> dict[str, int]:
        return {"entry_count": 0, "input": 0, "output": 0, "total": 0}

    by_session: dict[str, dict[str, int]] = {}
    by_tag: dict[str, dict[str, int]] = {}
    tot = {"input": 0, "output": 0, "total": 0, "call_count": 0}

    for e in entries:
        inp = int(e.get("input_tokens", 0) or 0)
        out = int(e.get("output_tokens", 0) or 0)
        tot["input"] += inp
        tot["output"] += out
        tot["total"] += inp + out
        tot["call_count"] += 1

        sid = str(e.get("session_id") or "(unknown)")
        tag = str(e.get("cost_tag") or "(untagged)")
        b = by_session.setdefault(sid, _bucket())
        b["entry_count"] += 1
        b["input"] += inp
        b["output"] += out
        b["total"] += inp + out
        b = by_tag.setdefault(tag, _bucket())
        b["entry_count"] += 1
        b["input"] += inp
        b["output"] += out
        b["total"] += inp + out

    return {
        "entries": entries,
        "by_session": by_session,
        "by_tag": by_tag,
        "totals": tot,
        "log_path": str(log_path),
        "missing": False,
    }
