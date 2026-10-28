"""The auto-bridge must respect the shared client-side limiter.

The bridge (activity_tracking -> heartbeat_current_worker_from_env) is the
path that produced the heartbeat storm in Lauf 59088: it must not write more
often than the shared limiter allows, and it must not extend a claim for a
dropped heartbeat.
"""
from __future__ import annotations

from tools import kanban_heartbeat_rate
from tools import kanban_tools


def test_auto_bridge_respects_the_shared_limiter(monkeypatch, tmp_path):
    """After one accepted bridge write, a second one inside the 30 s window is
    dropped: no claim extension, no board write."""
    home = tmp_path / ".hermes"
    home.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "worker")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(workspace))
    monkeypatch.setenv("HERMES_KANBAN_ATTACHMENTS_ROOT", str(tmp_path / "att"))

    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kbc.connect()
    try:
        tid = kb.create_task(
            conn, title="bridge", assignee="worker",
            workspace_kind="scratch", workspace_path=str(workspace))
        claim = kb.claim_task(conn, tid)
        assert claim is not None
    finally:
        conn.close()

    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(claim.id))
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", claim.claim_lock or "")

    kanban_heartbeat_rate.reset()
    # The bridge has its own 60 s window; reset it so the limiter is the only
    # thing under test.
    monkeypatch.setattr(kanban_tools, "_auto_heartbeat_last_attempt", 0.0)
    try:
        assert kanban_tools.heartbeat_current_worker_from_env() is True

        # The next bridge call is inside the 30 s limiter window: the write
        # must be dropped even though the bridge's own 60 s window is open.
        monkeypatch.setattr(kanban_tools, "_auto_heartbeat_last_attempt", 0.0)
        assert kanban_tools.heartbeat_current_worker_from_env() is False

        conn = kbc.connect()
        try:
            beats = [e for e in kb.list_events(conn, tid) if e.kind == "heartbeat"]
            assert len(beats) == 1, f"expected one heartbeat row, got {len(beats)}"
        finally:
            conn.close()
    finally:
        kanban_heartbeat_rate.reset()


def test_auto_bridge_drops_a_storm(monkeypatch, tmp_path):
    """More than 10 requests per minute: the bridge stops writing."""
    home = tmp_path / ".hermes"
    home.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "worker")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(workspace))
    monkeypatch.setenv("HERMES_KANBAN_ATTACHMENTS_ROOT", str(tmp_path / "att"))

    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kbc.connect()
    try:
        tid = kb.create_task(
            conn, title="bridge storm", assignee="worker",
            workspace_kind="scratch", workspace_path=str(workspace))
        claim = kb.claim_task(conn, tid)
        assert claim is not None
    finally:
        conn.close()

    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(claim.id))
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", claim.claim_lock or "")

    kanban_heartbeat_rate.reset()
    try:
        written = 0
        for _ in range(15):
            # Reset the bridge's own window so the shared limiter decides.
            monkeypatch.setattr(kanban_tools, "_auto_heartbeat_last_attempt", 0.0)
            if kanban_tools.heartbeat_current_worker_from_env():
                written += 1
        # The limiter allows at most one write per 30 s window, so a burst of
        # 15 requests inside a second yields exactly one write.
        assert written == 1, f"expected 1 write, got {written}"
    finally:
        kanban_heartbeat_rate.reset()
