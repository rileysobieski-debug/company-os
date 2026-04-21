"""Dispatch layer — handshake, evaluator, memory-updater, drift-guard.

Per plan §3 (file tree) and §13 Phase 7. The modules in this package
compose into the dispatch-manager pre/post hook payloads — the hook
*signatures* were shipped in Phase 1a; Phase 7 ships the payloads.
"""
from __future__ import annotations

from core.dispatch.drift_guard import (
    DriftGuardReport,
    evaluate_dispatch,
)
from core.dispatch.hooks import (
    DispatchPostState,
    make_evaluate_post_hook,
    make_handshake_pre_hook,
)
from core.dispatch.handshake_runner import (
    Handshake,
    handshake_to_claim,
    iter_session_handshakes,
    load_handshake,
    write_handshake,
)
from core.dispatch.memory_updater import (
    MemoryEntry,
    RouteResult,
    append_manager_memory,
    append_specialist_memory,
    record_dispatch_outcome,
    route_output_dir,
    write_output_artifact,
)

__all__ = [
    "DispatchPostState",
    "DriftGuardReport",
    "Handshake",
    "MemoryEntry",
    "RouteResult",
    "append_manager_memory",
    "append_specialist_memory",
    "evaluate_dispatch",
    "handshake_to_claim",
    "iter_session_handshakes",
    "load_handshake",
    "make_evaluate_post_hook",
    "make_handshake_pre_hook",
    "record_dispatch_outcome",
    "route_output_dir",
    "write_handshake",
    "write_output_artifact",
]
