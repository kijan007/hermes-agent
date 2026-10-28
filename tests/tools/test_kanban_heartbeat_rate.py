"""Client-side heartbeat rate limit (Team-Klärung t_9d2cdb94).

A worker that fires heartbeats far more often than the board needs keeps a
stalled run looking alive, so the dispatcher never reclaims it (Lauf 59088:
the task blocked for 151 minutes until a manual reclaim). The limiter drops
the excess writes client-side: at least 30 s between two heartbeat writes for
the same task, and more than 10 requests per minute is a storm.
"""
from __future__ import annotations

import json

from tools import kanban_heartbeat_rate as hbr


def test_first_heartbeat_is_allowed():
    limiter = hbr.HeartbeatRateLimiter()
    decision = limiter.check("t_1", now=1000.0)
    assert decision.allowed
    assert decision.verdict == hbr.ALLOW


def test_second_heartbeat_inside_the_window_is_dropped():
    """The 30 s floor: a write 1 s after the last one is dropped, not queued."""
    limiter = hbr.HeartbeatRateLimiter()
    assert limiter.check("t_1", now=1000.0).allowed
    decision = limiter.check("t_1", now=1001.0)
    assert decision.verdict == hbr.DEDUPE
    assert not decision.allowed
    # The drop is not deferred: the next write is allowed again once the
    # window has passed, not "one queued write per dropped heartbeat".
    assert limiter.check("t_1", now=1030.0).allowed


def test_dedupe_window_boundary():
    limiter = hbr.HeartbeatRateLimiter()
    assert limiter.check("t_1", now=1000.0).allowed
    # 29.9 s later: still inside the window.
    assert limiter.check("t_1", now=1029.9).verdict == hbr.DEDUPE
    # 30 s later: the window has passed.
    assert limiter.check("t_1", now=1030.0).allowed


def test_minimum_interval_is_thirty_seconds():
    assert hbr.HEARTBEAT_MIN_INTERVAL_SECONDS == 30.0


def test_storm_threshold_is_ten_per_minute():
    assert hbr.HEARTBEAT_STORM_THRESHOLD_PER_MINUTE == 10


def test_more_than_ten_requests_per_minute_is_a_storm():
    """Requests are counted, not accepted writes: a tight loop asks far more
    often than the 30 s floor lets through."""
    limiter = hbr.HeartbeatRateLimiter()
    now = 1000.0
    verdicts = []
    firsts = []
    for _ in range(12):
        decision = limiter.check("t_1", now=now)
        verdicts.append(decision.verdict)
        firsts.append(decision.first_in_burst)
        now += 0.1
    assert hbr.STORM in verdicts
    # The storm is reported once per burst, not once per dropped heartbeat.
    assert firsts.count(True) == 1
    # The first request was the only accepted write (the 30 s floor blocks
    # the rest) — the storm verdict is what catches the loop.
    assert verdicts[0] == hbr.ALLOW
    assert verdicts.count(hbr.DEDUPE) >= 1


def test_storm_drops_the_write():
    limiter = hbr.HeartbeatRateLimiter()
    now = 1000.0
    for _ in range(11):
        limiter.check("t_1", now=now)
        now += 0.1
    decision = limiter.check("t_1", now=now)
    assert decision.verdict == hbr.STORM
    assert not decision.allowed
    assert decision.per_minute >= hbr.HEARTBEAT_STORM_THRESHOLD_PER_MINUTE


def test_storm_window_expires():
    """After a quiet minute the worker is trusted again."""
    limiter = hbr.HeartbeatRateLimiter()
    now = 1000.0
    for _ in range(12):
        limiter.check("t_1", now=now)
        now += 0.1
    assert limiter.check("t_1", now=now).verdict == hbr.STORM
    # 61 s later: the request window is empty again, so the write is allowed
    # (the 30 s floor has passed too).
    assert limiter.check("t_1", now=now + 61.0).allowed
    # ... and the next heartbeat inside the new 30 s window is dropped again.
    assert limiter.check("t_1", now=now + 62.0).verdict == hbr.DEDUPE


def test_tasks_are_limited_independently():
    """One noisy task must not throttle another task's heartbeats."""
    limiter = hbr.HeartbeatRateLimiter()
    assert limiter.check("t_1", now=1000.0).allowed
    assert limiter.check("t_1", now=1001.0).verdict == hbr.DEDUPE
    assert limiter.check("t_2", now=1001.0).allowed


def test_reset_clears_the_state():
    limiter = hbr.HeartbeatRateLimiter()
    assert limiter.check("t_1", now=1000.0).allowed
    assert limiter.check("t_1", now=1001.0).verdict == hbr.DEDUPE
    limiter.reset("t_1")
    assert limiter.check("t_1", now=1001.0).allowed


def test_module_level_limiter_is_shared():
    hbr.reset()
    assert hbr.check("t_1", now=1000.0).allowed
    assert hbr.check("t_1", now=1001.0).verdict == hbr.DEDUPE
    hbr.reset()


def test_state_snapshot_reports_the_counters():
    limiter = hbr.HeartbeatRateLimiter()
    limiter.check("t_1", now=1000.0)
    snap = limiter.state_snapshot("t_1")
    assert snap["recent"] == 1.0
    assert snap["requests"] == 1.0
    assert snap["storm_reported"] == 0.0


def test_limiter_is_thread_safe():
    """Concurrent callers must not corrupt the state (the agent loop touches
    activity from several threads)."""
    import threading

    limiter = hbr.HeartbeatRateLimiter()
    verdicts = []
    lock = threading.Lock()

    def hammer():
        local = [limiter.check("t_1").verdict for _ in range(50)]
        with lock:
            verdicts.extend(local)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(verdicts) == 200
    # At most one write per 30 s window can be accepted.
    assert verdicts.count(hbr.ALLOW) == 1


# --- integration: the explicit ``kanban_heartbeat`` tool ------------------------


def _make_running_kanban_task(monkeypatch, tmp_path):
    """A claimed task with a running run, in an isolated board."""
    home = tmp_path / ".hermes"
    home.mkdir()
    attachments_root = tmp_path / "attachments"
    workspace = tmp_path / "worker-workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "worker")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(workspace))
    monkeypatch.setenv("HERMES_KANBAN_ATTACHMENTS_ROOT", str(attachments_root))

    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kbc.connect()
    try:
        tid = kb.create_task(
            conn,
            title="heartbeat worker",
            assignee="worker",
            workspace_kind="scratch",
            workspace_path=str(workspace),
        )
        claim = kb.claim_task(conn, tid)
        assert claim is not None
        run_id = claim.id
    finally:
        conn.close()

    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return kb, tid, run_id


def test_heartbeat_tool_writes_once_and_then_drops(monkeypatch, tmp_path):
    """The explicit tool is rate-limited too: the second call inside the 30 s
    window must not append a second heartbeat row to the board."""
    kb, tid, _run_id = _make_running_kanban_task(monkeypatch, tmp_path)
    from hermes_cli import kanban_db_connect as kbc
    from tools import kanban_heartbeat_rate
    from tools import kanban_tools

    kanban_heartbeat_rate.reset()

    first = json.loads(kanban_tools._handle_heartbeat({"note": "alive"}))
    assert first["ok"] is True
    assert first["heartbeat"] == "recorded"

    second = json.loads(kanban_tools._handle_heartbeat({"note": "alive again"}))
    assert second["ok"] is True
    assert second["heartbeat"] == "dropped"
    assert second["reason"] == "dedupe"

    conn = kbc.connect()
    try:
        beats = [e for e in kb.list_events(conn, tid) if e.kind == "heartbeat"]
        assert len(beats) == 1, f"expected exactly one heartbeat row, got {len(beats)}"
    finally:
        conn.close()
        kanban_heartbeat_rate.reset()


def test_heartbeat_tool_drops_are_not_errors(monkeypatch, tmp_path):
    """A dropped heartbeat is a success: the model must not see a tool error
    for a heartbeat the board already has."""
    kb, tid, _run_id = _make_running_kanban_task(monkeypatch, tmp_path)
    from tools import kanban_heartbeat_rate
    from tools import kanban_tools

    kanban_heartbeat_rate.reset()
    try:
        json.loads(kanban_tools._handle_heartbeat({}))
        payload = json.loads(kanban_tools._handle_heartbeat({}))
        assert payload["ok"] is True
        assert payload["heartbeat"] == "dropped"
        assert payload["retry_after"] > 0
    finally:
        kanban_heartbeat_rate.reset()


def test_heartbeat_storm_is_recorded_once_on_the_board(monkeypatch, tmp_path):
    """A storm leaves a ``heartbeat_storm`` event on the board, so the
    suspicion is visible to the dispatcher and to the operator."""
    kb, tid, _run_id = _make_running_kanban_task(monkeypatch, tmp_path)
    from hermes_cli import kanban_db_connect as kbc
    from tools import kanban_heartbeat_rate
    from tools import kanban_tools

    kanban_heartbeat_rate.reset()
    try:
        # The 30 s floor already drops the second call; drive the storm by
        # asking for heartbeats faster than the storm threshold.
        for _ in range(12):
            kanban_tools._handle_heartbeat({})
        conn = kbc.connect()
        try:
            storms = [e for e in kb.list_events(conn, tid) if e.kind == "heartbeat_storm"]
            assert len(storms) == 1, f"expected one storm event, got {len(storms)}"
        finally:
            conn.close()
    finally:
        kanban_heartbeat_rate.reset()
