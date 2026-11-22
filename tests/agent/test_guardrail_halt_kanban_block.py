"""M2a: a guardrail halt (identical_call_streak_halt) ends a kanban worker's turn as
``blocked`` via a terminal ``kanban_block`` call — never as a narrated stop, which the
dispatcher books ``protocol_violation``."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.kanban_stop import (
    build_guardrail_halt_block_nudge,
    guardrail_halt_redirect_enabled,
)
from agent.tool_guardrails import ToolCallGuardrailController
from tests.agent.test_tool_call_guardrail_runtime import (
    _hard_stop_config,
    _make_agent,
    _make_tool_defs,
    _mock_response,
    _mock_tool_call,
)


def _test_env(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_guardrail")
    monkeypatch.delenv("HERMES_KANBAN_STOP_NUDGE", raising=False)


def test_release_halt_clears_only_matching_codes():
    ctl = ToolCallGuardrailController()
    assert ctl.release_halt() is None  # nothing recorded
    from agent.tool_guardrails import ToolCallSignature

    decision = ctl._decide("halt", "identical_call_streak_halt", "terminal", 5, ToolCallSignature("terminal", "h"))
    assert ctl.halt_decision is decision
    # Non-matching code: no release.
    assert ctl.release_halt(only_codes={"loop_web_search_cap"}) is None
    assert ctl.halt_decision is decision
    # Matching code: releases and returns the decision.
    assert ctl.release_halt(only_codes={"identical_call_streak_halt"}) is decision
    assert ctl.halt_decision is None


def test_redirect_enabled_only_for_kanban_worker_without_terminal_call(monkeypatch):
    _test_env(monkeypatch)
    assert guardrail_halt_redirect_enabled([], attempts=0) is True
    # Budget exhausted.
    assert guardrail_halt_redirect_enabled([], attempts=1) is False
    # Terminal call already made: never redirect (and never nudge to close again).
    messages = [{
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "1", "type": "function",
                        "function": {"name": "kanban_block", "arguments": "{}"}}],
    }]
    assert guardrail_halt_redirect_enabled(messages, attempts=0) is False
    # Not a kanban worker.
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    assert guardrail_halt_redirect_enabled([], attempts=0) is False


def test_block_nudge_names_guardrail_halt_reason(monkeypatch):
    _test_env(monkeypatch)
    decision = SimpleNamespace(code="identical_call_streak_halt", tool_name="web_search", count=5)
    nudge = build_guardrail_halt_block_nudge(decision)
    assert 'reason="Guardrail-Halt: identical_call_streak_halt"' in nudge
    assert "kanban_block" in nudge
    assert "t_guardrail" in nudge
    assert "protocol violation" in nudge


def _run_halted_turn(monkeypatch, *, block_call: bool, max_iterations: int = 12):
    """Run a turn that hits the identical_call_streak_halt; the follow-up response either
    calls ``kanban_block`` (block_call=True) or just narrates.

    The streak halt fires on the 5th identical SUCCESSFUL call (observe_call,
    no_progress_block_after=5), so the mock must return a successful identical
    result — a failing call feeds ``repeated_exact_failure_block`` instead, which
    is a caller-block code that keeps ending the turn.
    """
    _test_env(monkeypatch)
    agent = _make_agent("web_search", "kanban_block", max_iterations=max_iterations,
                        config=_hard_stop_config())
    same_args = {"query": "same"}
    halted = [
        _mock_response(
            content="", finish_reason="tool_calls",
            tool_calls=[_mock_tool_call("web_search", json.dumps(same_args), f"c{i}")],
        )
        for i in range(1, 6)  # exactly 5: the halt fires on the 5th observe_call
    ]
    narrate = _mock_response(content="I'm stuck.", finish_reason="stop", tool_calls=None)
    if block_call:
        followup = [
            _mock_response(
                content="", finish_reason="tool_calls",
                tool_calls=[_mock_tool_call("kanban_block", json.dumps(
                    {"reason": "Guardrail-Halt: identical_call_streak_halt", "kind": "transient"}
                ), "c_block")],
            ),
            narrate,  # turn closes normally after the terminal board call
        ]
    else:
        # Narrating instead of blocking: the kanban stop-guard nudges twice (its own
        # budget) before the turn finally ends as a narrated stop.
        followup = [narrate] * 4
    agent.client.chat.completions.create.side_effect = halted + followup

    with (
        # Successful identical results feed the identical_call_streak_halt; a failure
        # would feed repeated_exact_failure_block instead. kanban_block also succeeds
        # (the board call is mocked — the test only asserts the message flow).
        patch("model_tools.handle_function_call", return_value=json.dumps({"ok": True})),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("search repeatedly")
    return result


def test_halt_redirects_to_kanban_block_call(monkeypatch):
    """Weg a: the halt is released, the worker is nudged, and the terminal kanban_block
    call lands — the turn ends normally (blocked on the board), not guardrail_halt."""
    result = _run_halted_turn(monkeypatch, block_call=True)
    assert result["turn_exit_reason"].startswith("text_response")
    # The guardrail cause stays reportable and the redirect is named.
    assert result["guardrail"]["code"] == "identical_call_streak_halt"
    assert result["guardrail_redirect"] == "kanban_block"
    # The instruction carried the reasoned comment for the block call.
    tool_contents = [m["content"] for m in result["messages"] if m.get("role") == "user"]
    assert any("Guardrail-Halt: identical_call_streak_halt" in c for c in tool_contents)


def test_unanswered_redirect_falls_back_to_kanban_stop_guard(monkeypatch):
    """If the worker ignores the block instruction, the ordinary kanban stop-guard takes
    over (nudge to make a terminal call) — the halt itself must not crash the turn."""
    result = _run_halted_turn(monkeypatch, block_call=False)
    # The turn ended via the narrated stop (dispatcher books the violation as before);
    # crucially the redirect did not loop forever and the guardrail cause is on record.
    user_rows = [m["content"] for m in result["messages"] if m.get("role") == "user"]
    assert any("Guardrail-Halt: identical_call_streak_halt" in c for c in user_rows)
