"""Tests for the kanban worker turn-end stop guard."""

from __future__ import annotations

import pytest

from agent.kanban_stop import (
    build_kanban_stop_nudge,
    kanban_stop_nudge_enabled,
    kanban_worker_run_is_current,
    session_called_kanban_terminal,
)


@pytest.fixture
def clear_kanban_env(monkeypatch):
    for var in (
        "HERMES_KANBAN_TASK",
        "HERMES_KANBAN_STOP_NUDGE",
        "HERMES_KANBAN_RUN_ID",
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_CLAIM_LOCK",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch






def test_env_can_disable(clear_kanban_env):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_abc")
    clear_kanban_env.setenv("HERMES_KANBAN_STOP_NUDGE", "0")
    assert kanban_stop_nudge_enabled() is False
    assert build_kanban_stop_nudge(messages=[]) is None


def test_nudge_when_no_terminal_tool(clear_kanban_env):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_46be8aa5")
    messages = [
        {"role": "user", "content": "work kanban task"},
        {
            "role": "assistant",
            "content": "Let me write the comprehensive recipe.",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "kanban_heartbeat", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "name": "kanban_heartbeat", "tool_call_id": "1", "content": "ok"},
    ]
    nudge = build_kanban_stop_nudge(messages=messages, attempts=0)
    assert nudge is not None
    assert "kanban_complete" in nudge
    assert "kanban_block" in nudge
    assert "t_46be8aa5" in nudge
    assert "protocol violation" in nudge.lower() or "protocol" in nudge.lower()


def test_no_nudge_after_kanban_complete(clear_kanban_env):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_abc")
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "kanban_complete", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "name": "kanban_complete", "tool_call_id": "1", "content": '{"ok": true}'},
    ]
    assert session_called_kanban_terminal(messages) is True
    assert build_kanban_stop_nudge(messages=messages) is None


@pytest.mark.parametrize(
    "name",
    [
        "kanban_complete",
        "kanban_block",
        "kanban_request_review",
        "kanban_request_changes",
        "kanban_review_pass",
    ],
)
def test_each_successful_role_valid_transition_ends_the_run(name):
    messages = [{
        "role": "tool",
        "name": name,
        "tool_call_id": "1",
        "content": '{"ok": true, "status": "ready"}',
    }]
    assert session_called_kanban_terminal(messages) is True


def test_rejected_transition_does_not_end_the_run(clear_kanban_env):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_abc")
    messages = [{
        "role": "tool",
        "name": "kanban_request_review",
        "tool_call_id": "1",
        "content": '{"error": "run_id mismatch"}',
    }]
    assert session_called_kanban_terminal(messages) is False
    assert build_kanban_stop_nudge(messages=messages) is not None


def test_run_ownership_fence_detects_external_handoff(
    clear_kanban_env, tmp_path
):
    import sqlite3

    db_path = tmp_path / "kanban.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, "
        "current_run_id INTEGER, claim_lock TEXT)"
    )
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?)",
        ("t_abc", "running", 7, "lock-7"),
    )
    conn.commit()
    conn.close()

    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_abc")
    clear_kanban_env.setenv("HERMES_KANBAN_RUN_ID", "7")
    clear_kanban_env.setenv("HERMES_KANBAN_CLAIM_LOCK", "lock-7")
    clear_kanban_env.setenv("HERMES_KANBAN_DB", str(db_path))
    assert kanban_worker_run_is_current() is True

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE tasks SET current_run_id = 8, claim_lock = 'lock-8' "
        "WHERE id = 't_abc'"
    )
    conn.commit()
    conn.close()
    assert kanban_worker_run_is_current() is False


def test_run_ownership_read_failure_fails_open(clear_kanban_env, tmp_path):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_abc")
    clear_kanban_env.setenv("HERMES_KANBAN_RUN_ID", "7")
    clear_kanban_env.setenv("HERMES_KANBAN_DB", str(tmp_path / "missing.db"))
    assert kanban_worker_run_is_current() is True






# ── Integration: agent nudge + dispatcher bounded retry ──────────────
# These tests verify the two layers compose correctly: the agent-side
# nudge fires first (up to 2 attempts), and if the worker still exits
# without a terminal call, the dispatcher's bounded retry (streak of 3)
# handles it.  See also tests/hermes_cli/test_kanban_core_functionality.py
# for the dispatcher-side streak tests.



