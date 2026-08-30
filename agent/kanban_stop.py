"""Run-lifecycle guards for dispatcher-owned Kanban workers.

Each worker owns one exact ``task_runs`` row. Card-terminal transitions and
same-card review handoffs both close that run, so the process must stop before
another provider call or tool side effect. Models sometimes narrate the next step
("Let me write the report now") and stop with ``finish_reason=stop`` and no
tool calls. Hermes treats that as a clean exit → ``rc=0`` → dispatcher
``protocol_violation``.

The ownership check is deliberately read-only and fail-open. The existing
claim lease and orphan reconciler remain the recovery authority when SQLite
cannot be read; this module never invents a board transition.
"""

from __future__ import annotations

import os
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional


_RUN_TERMINAL_KANBAN_TOOLS = frozenset({
    "kanban_complete",
    "kanban_block",
    "kanban_request_review",
    "kanban_request_changes",
    "kanban_review_pass",
})

_DEFAULT_MAX_ATTEMPTS = 2


def kanban_stop_nudge_enabled() -> bool:
    """Return whether the kanban stop-guard is active for this process.

    On when ``HERMES_KANBAN_TASK`` is set (dispatcher-spawned worker), unless
    ``HERMES_KANBAN_STOP_NUDGE`` explicitly disables it.
    """
    env = os.environ.get("HERMES_KANBAN_STOP_NUDGE")
    if env is not None and env.strip().lower() in {"0", "false", "no", "off"}:
        return False
    task = (os.environ.get("HERMES_KANBAN_TASK") or "").strip()
    return bool(task)


def _tool_call_name(tc: Any) -> str:
    if isinstance(tc, dict):
        fn = tc.get("function")
        if isinstance(fn, dict):
            return str(fn.get("name") or "")
        return str(tc.get("name") or "")
    fn = getattr(tc, "function", None)
    if fn is not None:
        return str(getattr(fn, "name", "") or "")
    return str(getattr(tc, "name", "") or "")


def _successful_tool_result(content: Any) -> bool:
    """Return whether a Kanban tool result is the canonical ``{"ok": true}``."""
    if isinstance(content, dict):
        return content.get("ok") is True
    if not isinstance(content, str):
        return False
    try:
        value, _end = json.JSONDecoder().raw_decode(content.lstrip())
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(value, dict) and value.get("ok") is True


def session_called_kanban_terminal(messages: Iterable[dict] | None) -> bool:
    """True if this conversation completed a run-terminal Kanban tool.

    The tool result, not merely the assistant's requested call, is authority.
    A rejected completion or review transition leaves the run live and must not
    suppress the stop nudge.
    """
    if not messages:
        return False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            name = str(msg.get("name") or "")
            if (
                name in _RUN_TERMINAL_KANBAN_TOOLS
                and _successful_tool_result(msg.get("content"))
            ):
                return True
    return False


def kanban_worker_run_is_current() -> bool:
    """Return False only when this dispatched worker definitely lost its run.

    Processes without the dispatcher environment, malformed environment, and
    transient SQLite read failures return True (fail open). A valid lookup that
    finds a different/non-running run returns False.
    """
    task_id = (os.environ.get("HERMES_KANBAN_TASK") or "").strip()
    run_text = (os.environ.get("HERMES_KANBAN_RUN_ID") or "").strip()
    db_text = (os.environ.get("HERMES_KANBAN_DB") or "").strip()
    if not task_id or not run_text or not db_text:
        return True
    try:
        run_id = int(run_text)
        if run_id <= 0:
            return True
        db_uri = Path(db_text).expanduser().resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True, timeout=0.1)
        try:
            row = conn.execute(
                "SELECT status, current_run_id, claim_lock FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        finally:
            conn.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return True
    if row is None or row[0] != "running" or row[1] is None:
        return False
    if int(row[1]) != run_id:
        return False
    expected_lock = (os.environ.get("HERMES_KANBAN_CLAIM_LOCK") or "").strip()
    return not expected_lock or str(row[2] or "") == expected_lock


def build_kanban_stop_nudge(
    *,
    messages: Iterable[dict] | None = None,
    attempts: int = 0,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    task_id: Optional[str] = None,
) -> Optional[str]:
    """Return a synthetic follow-up when a kanban worker exits without a terminal tool.

    Returns ``None`` when the guard should not fire (not a kanban worker,
    already completed/blocked, or nudge budget exhausted).
    """
    if not kanban_stop_nudge_enabled():
        return None
    if attempts >= max_attempts:
        return None
    if session_called_kanban_terminal(messages):
        return None

    tid = (task_id or os.environ.get("HERMES_KANBAN_TASK") or "").strip() or "this task"
    return (
        "[System: You are a Hermes kanban worker. A plain-text reply is NOT a "
        "terminal state for the board.\n\n"
        f"Task `{tid}` is still `running`. Ending now without a board tool "
        "causes a protocol violation (clean exit with no "
        "`kanban_complete` / `kanban_block`).\n\n"
        "Do this immediately in your next response — do not narrate intent:\n"
        "1. Finish any remaining deliverable (write the required file(s) now).\n"
        "2. Call `kanban_complete(summary=..., artifacts=[...])` if the work "
        "is done, OR `kanban_block(reason=...)` if you are blocked.\n\n"
        "Never end a turn with only a promise of future action. Repeated "
        "protocol violations will block this task and require manual intervention.]"
    )


__all__ = [
    "build_kanban_stop_nudge",
    "kanban_stop_nudge_enabled",
    "kanban_worker_run_is_current",
    "session_called_kanban_terminal",
]
