"""Dispatcher-side runtime cap and heartbeat-storm detection.

Two failure modes of a runaway task are covered here:

1. A task with NO explicit ``max_runtime_seconds`` had no upper runtime bound
   at all in the dispatcher — a wedged worker ran forever. The dispatcher now
   applies a default cap (4h unless configured otherwise) to such tasks; an
   explicit per-task value always wins.

2. A heartbeat storm (e.g. a heartbeat loop in a retry path) kept
   ``last_heartbeat_at`` fresh, so the stale reclaim never fired. A task that
   emits more than ``heartbeat_storm_threshold`` heartbeats inside a 60-second
   window is now treated as if it had NO heartbeat: it is reclaimed after
   ``dispatch_stale_timeout_seconds`` like any other inactive task.
"""

from __future__ import annotations

import os
import time

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    conn = kbc.connect(tmp_path / "kanban.db")
    try:
        yield conn
    finally:
        conn.close()


def _running_task(conn, *, max_runtime=None, age_seconds=3600) -> str:
    """A claimed, running task with a (possibly foreign) worker PID.

    The PID is the current process with a deliberately wrong fingerprint, so
    the timeout path never signals a live stranger and the reclaim path can
    release the task without a real process being killed.
    """
    tid = kb.create_task(conn, title="job", assignee="worker", max_runtime_seconds=max_runtime)
    kb.claim_task(conn, tid)
    kbd._set_worker_pid(conn, tid, os.getpid())
    old = int(time.time()) - age_seconds
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET worker_started_at = ?, started_at = ?, claim_expires = ? WHERE id = ?",
            (1, old, old, tid),
        )
        conn.execute(
            "UPDATE task_runs SET started_at = ? WHERE id = (SELECT current_run_id FROM tasks WHERE id = ?)",
            (old, tid),
        )
    return tid


def _heartbeats(conn, task_id: str, count: int, *, within_seconds: int = 60,
                fresh_column: bool = True) -> None:
    """Insert ``count`` heartbeat events inside the storm window.

    ``fresh_column`` also touches ``tasks.last_heartbeat_at`` like the real
    ``heartbeat_worker`` does — otherwise the task has NO heartbeat at all and
    is reclaimed for that reason, not because of the storm.
    """
    now = int(time.time())
    with kb.write_txn(conn):
        for i in range(count):
            conn.execute(
                "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) "
                "VALUES (?, NULL, 'heartbeat', NULL, ?)",
                (task_id, now - within_seconds + i),
            )
        if fresh_column:
            conn.execute(
                "UPDATE tasks SET last_heartbeat_at = ? WHERE id = ?", (now, task_id))


def _status(conn, task_id: str):
    """The task's current status (``None`` if the row is gone)."""
    task = kb.get_task(conn, task_id)
    return task.status if task is not None else None


# ---------------------------------------------------------------------------
# Default runtime cap
# ---------------------------------------------------------------------------


def test_task_without_explicit_cap_is_capped_by_the_default(board):
    """A task with no ``max_runtime_seconds`` gets the dispatcher's default cap."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)

    assert kbd.enforce_max_runtime(conn, default_max_runtime=1800) == [tid]
    assert _status(conn, tid) == "ready"


def test_default_cap_is_not_applied_before_it_elapsed(board):
    """The default cap must not kill a task that is still inside the window."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=60)

    assert kbd.enforce_max_runtime(conn, default_max_runtime=1800) == []
    assert _status(conn, tid) == "running"


def test_explicit_cap_wins_over_the_default(board):
    """An explicit per-task value is used verbatim — even when it is LARGER
    than the default cap (the default is a fallback, not a maximum)."""
    conn = board
    tid = _running_task(conn, max_runtime=7200, age_seconds=3600)

    assert kbd.enforce_max_runtime(conn, default_max_runtime=1800) == []
    assert _status(conn, tid) == "running"


def test_explicit_cap_shorter_than_the_default_still_applies(board):
    """An explicit cap below the default is honoured."""
    conn = board
    tid = _running_task(conn, max_runtime=60, age_seconds=3600)

    assert kbd.enforce_max_runtime(conn, default_max_runtime=14400) == [tid]
    assert _status(conn, tid) == "ready"


def test_default_cap_disabled_keeps_tasks_unbounded(board):
    """``default_max_runtime=None`` keeps the old behaviour: no cap for tasks
    without an explicit ``max_runtime_seconds``."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=100_000)

    assert kbd.enforce_max_runtime(conn, default_max_runtime=None) == []
    assert _status(conn, tid) == "running"


def test_default_cap_comes_from_the_config(board, monkeypatch):
    """``kanban.dispatch_default_max_runtime_seconds`` is read from config."""
    monkeypatch.setattr(
        kbd, "resolve_default_max_runtime_seconds", lambda: 1800, raising=True)
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)

    assert kbd.enforce_max_runtime(conn) == [tid]
    assert _status(conn, tid) == "ready"


def test_timeout_of_a_default_capped_task_is_booked(board):
    """The timeout is booked like any other timeout: ``timed_out`` event plus
    a run outcome, so the breaker sees the failed attempt."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)

    kbd.enforce_max_runtime(conn, default_max_runtime=1800)
    kinds = [e.kind for e in kb.list_events(conn, tid)]
    assert "timed_out" in kinds
    run = conn.execute(
        "SELECT outcome FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (tid,),
    ).fetchone()
    assert run["outcome"] == "timed_out"


# ---------------------------------------------------------------------------
# Heartbeat storm
# ---------------------------------------------------------------------------


def test_heartbeat_storm_task_is_reclaimed(board):
    """A storming task is reclaimed like a task without any heartbeat."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)
    _heartbeats(conn, tid, 30)

    assert kbd.detect_stale_running(
        conn, stale_timeout_seconds=1800, heartbeat_storm_threshold=10) == [tid]
    assert _status(conn, tid) == "ready"


def test_heartbeat_storm_is_recorded_in_the_event_payload(board):
    """The reclaim event documents WHY the task was considered inactive."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)
    _heartbeats(conn, tid, 30)

    kbd.detect_stale_running(
        conn, stale_timeout_seconds=1800, heartbeat_storm_threshold=10)
    events = [e for e in kb.list_events(conn, tid) if e.kind == "stale"]
    assert events, "expected a stale event"
    payload = events[-1].payload or {}
    assert payload.get("heartbeat_storm") is True
    assert payload.get("heartbeat_count_window") == 30
    assert payload.get("heartbeat_storm_threshold") == 10


def test_heartbeats_below_the_threshold_keep_the_task_alive(board):
    """Normal heartbeat traffic (below the threshold) stays a liveness signal."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)
    _heartbeats(conn, tid, 5)

    assert kbd.detect_stale_running(
        conn, stale_timeout_seconds=1800, heartbeat_storm_threshold=10) == []
    assert _status(conn, tid) == "running"


def test_storm_detection_disabled_keeps_the_task_alive(board):
    """``heartbeat_storm_threshold=0`` disables storm detection."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)
    _heartbeats(conn, tid, 30)

    assert kbd.detect_stale_running(
        conn, stale_timeout_seconds=1800, heartbeat_storm_threshold=0) == []
    assert _status(conn, tid) == "running"


def test_storm_does_not_reclaim_before_the_stale_timeout(board):
    """A storming task is only reclaimed once it exceeded the stale timeout."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=60)
    _heartbeats(conn, tid, 30)

    assert kbd.detect_stale_running(
        conn, stale_timeout_seconds=1800, heartbeat_storm_threshold=10) == []
    assert _status(conn, tid) == "running"


def test_storm_counts_only_the_current_window(board):
    """Old heartbeats outside the 60-second window do not make a storm."""
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)
    _heartbeats(conn, tid, 30, within_seconds=3600, fresh_column=False)
    # The task's last heartbeat is recent, so it stays alive.
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET last_heartbeat_at = ? WHERE id = ?",
            (int(time.time()) - 5, tid),
        )

    assert kbd.detect_stale_running(
        conn, stale_timeout_seconds=1800, heartbeat_storm_threshold=10) == []
    assert _status(conn, tid) == "running"


def test_storm_threshold_comes_from_the_config(board, monkeypatch):
    """``kanban.heartbeat_storm_threshold`` is read from config."""
    monkeypatch.setattr(
        kbd, "resolve_heartbeat_storm_threshold", lambda: 10, raising=True)
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)
    _heartbeats(conn, tid, 30)

    assert kbd.detect_stale_running(conn, stale_timeout_seconds=1800) == [tid]


def test_storm_affects_only_the_storming_task(board):
    """A storm on one task must not reclaim a healthy sibling."""
    conn = board
    storming = _running_task(conn, max_runtime=None, age_seconds=3600)
    healthy = _running_task(conn, max_runtime=None, age_seconds=3600)
    _heartbeats(conn, storming, 30)
    # The sibling has a single, normal heartbeat — no storm, so it stays alive.
    _heartbeats(conn, healthy, 1)

    reclaimed = kbd.detect_stale_running(
        conn, stale_timeout_seconds=1800, heartbeat_storm_threshold=10)
    assert reclaimed == [storming]
    assert _status(conn, healthy) == "running"


def test_storm_count_only_inspects_the_candidate_tasks(board):
    """The storm counter must not scan the whole event table.

    ``task_events`` grows with every heartbeat of every task on the board. The
    counter therefore takes the task ids it needs — the running tasks that are
    already past the stale timeout — instead of aggregating the full history.
    """
    conn = board
    tid = _running_task(conn, max_runtime=None, age_seconds=3600)
    _heartbeats(conn, tid, 30)

    counts = kbd._heartbeat_storm_counts(conn, int(time.time()), [tid])
    assert counts == {tid: 30}
    # An unrelated id is not counted — the query is scoped to the given ids.
    assert kbd._heartbeat_storm_counts(conn, int(time.time()), []) == {}
    assert kbd._heartbeat_storm_counts(conn, int(time.time()), ["t_nope"]) == {}


# ---------------------------------------------------------------------------
# Gateway settings resolution (tri-state: value / disabled / default)
# ---------------------------------------------------------------------------


def test_settings_fall_back_to_the_builtin_default():
    """A missing config key uses the shipped default (4h cap / threshold 10)."""
    from gateway.kanban_watchers_dispatcher import _resolve_dispatcher_settings
    from hermes_cli import kanban_db as kb_mod

    settings = _resolve_dispatcher_settings({}, kb_mod)
    assert settings.default_max_runtime_seconds == 14400
    assert settings.heartbeat_storm_threshold == 10


def test_settings_can_be_disabled_with_zero():
    """``0`` switches the feature off — it does not fall back to the default."""
    from gateway.kanban_watchers_dispatcher import _resolve_dispatcher_settings
    from hermes_cli import kanban_db as kb_mod

    settings = _resolve_dispatcher_settings(
        {"dispatch_default_max_runtime_seconds": 0, "heartbeat_storm_threshold": 0},
        kb_mod,
    )
    assert settings.default_max_runtime_seconds is None
    assert settings.heartbeat_storm_threshold is None


def test_settings_use_the_configured_value():
    """A configured value wins over the built-in default."""
    from gateway.kanban_watchers_dispatcher import _resolve_dispatcher_settings
    from hermes_cli import kanban_db as kb_mod

    settings = _resolve_dispatcher_settings(
        {"dispatch_default_max_runtime_seconds": 1800, "heartbeat_storm_threshold": 25},
        kb_mod,
    )
    assert settings.default_max_runtime_seconds == 1800
    assert settings.heartbeat_storm_threshold == 25


def test_settings_ignore_an_invalid_value():
    """A malformed value falls back to the default instead of disabling."""
    from gateway.kanban_watchers_dispatcher import _resolve_dispatcher_settings
    from hermes_cli import kanban_db as kb_mod

    settings = _resolve_dispatcher_settings(
        {"dispatch_default_max_runtime_seconds": "nope", "heartbeat_storm_threshold": -5},
        kb_mod,
    )
    assert settings.default_max_runtime_seconds == 14400
    assert settings.heartbeat_storm_threshold == 10
