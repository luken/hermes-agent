"""Integration tests for run-scoped Kanban worker termination."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent


def _response_with_tool(name: str):
    call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name=name, arguments="{}"),
    )
    message = SimpleNamespace(content="", tool_calls=[call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
        model="test/model",
        usage=None,
    )


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_run")
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        instance = AIAgent(
            session_id="kanban-run-test",
            api_key="test-key",
            base_url="https://example.invalid/v1",
            provider="openai-compat",
            model="test/model",
            max_iterations=3,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    instance.valid_tool_names = {"kanban_request_review"}
    instance._cached_system_prompt = "stable test prompt"
    instance._session_db = None
    instance._session_json_enabled = False
    instance.save_trajectories = False
    instance.compression_enabled = False
    instance._cleanup_task_resources = lambda *_a, **_kw: None
    instance._save_trajectory = lambda *_a, **_kw: None
    return instance


def test_successful_review_handoff_stops_before_second_provider_call(agent):
    provider_call = MagicMock(return_value=_response_with_tool(
        "kanban_request_review"
    ))
    agent._interruptible_api_call = provider_call

    with (
        patch("run_agent.handle_function_call", return_value='{"ok": true}'),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation("work the card")

    assert provider_call.call_count == 1
    assert result["turn_exit_reason"] == "kanban_run_transitioned"
    assert result["completed"] is True


def test_externally_moved_run_stops_before_provider_call(agent):
    provider_call = MagicMock()
    agent._interruptible_api_call = provider_call

    with (
        patch("agent.kanban_stop.kanban_worker_run_is_current", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation("work the stale card")

    provider_call.assert_not_called()
    assert result["api_calls"] == 0
    assert result["turn_exit_reason"] == "kanban_run_ownership_lost"


def test_ownership_change_while_model_runs_skips_returned_tools(agent):
    agent._interruptible_api_call = MagicMock(
        return_value=_response_with_tool("kanban_request_review")
    )

    with (
        patch(
            "agent.kanban_stop.kanban_worker_run_is_current",
            side_effect=[True, False],
        ),
        patch("run_agent.handle_function_call") as handler,
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation("work the card")

    handler.assert_not_called()
    assert result["turn_exit_reason"] == "kanban_run_ownership_lost_before_tools"
    assert any(
        message.get("role") == "tool"
        and "ownership changed" in str(message.get("content"))
        for message in result["messages"]
        if isinstance(message, dict)
    )
