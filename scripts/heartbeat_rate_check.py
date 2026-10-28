"""Live end-to-end check of the heartbeat rate limit against a real board DB.

Not a pytest module: a script that drives the real ``kanban_heartbeat`` tool
handler against a real (temporary) board and counts the resulting
``heartbeat`` rows in the DB. Run:

    python3 scripts/heartbeat_rate_check.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hb-rate-check-"))
    home = tmp / ".hermes"
    home.mkdir()
    workspace = tmp / "ws"
    workspace.mkdir()
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_PROFILE"] = "hb-check"
    os.environ["HERMES_KANBAN_WORKSPACE"] = str(workspace)
    os.environ["HERMES_KANBAN_ATTACHMENTS_ROOT"] = str(tmp / "att")
    # This script may be started from inside a delegate_task child; the board
    # fence would refuse the writes it is meant to measure.
    os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)

    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from tools import kanban_heartbeat_rate
    from tools import kanban_tools

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kbc.connect()
    try:
        tid = kb.create_task(
            conn, title="heartbeat rate check", assignee="hb-check",
            workspace_kind="scratch", workspace_path=str(workspace))
        claim = kb.claim_task(conn, tid)
        assert claim is not None
    finally:
        conn.close()

    os.environ["HERMES_KANBAN_TASK"] = tid
    os.environ["HERMES_KANBAN_RUN_ID"] = str(claim.id)
    os.environ["HERMES_KANBAN_CLAIM_LOCK"] = claim.claim_lock or ""

    kanban_heartbeat_rate.reset()
    results = []
    # 60 explicit heartbeat calls in a tight loop (~a "storm").
    for _ in range(60):
        results.append(json.loads(kanban_tools._handle_heartbeat({})))

    conn = kbc.connect()
    try:
        events = kb.list_events(conn, tid)
    finally:
        conn.close()

    beats = [e for e in events if e.kind == "heartbeat"]
    storms = [e for e in events if e.kind == "heartbeat_storm"]
    recorded = [r for r in results if r.get("heartbeat") == "recorded"]
    dropped = [r for r in results if r.get("heartbeat") == "dropped"]

    print(f"calls:            {len(results)}")
    print(f"recorded (tool):  {len(recorded)}")
    print(f"dropped (tool):   {len(dropped)}")
    print(f"heartbeat rows:   {len(beats)}")
    print(f"storm events:     {len(storms)}")
    print(f"all ok=true:      {all(r.get('ok') is True for r in results)}")
    print(f"reasons:          {sorted({r.get('reason') for r in dropped if r.get('reason')})}")

    ok = (
        len(beats) == 1
        and len(storms) == 1
        and len(recorded) == 1
        and len(dropped) == 59
        and all(r.get("ok") is True for r in results)
    )
    print("RESULT:", "PASS" if ok else "FAIL")
    kanban_heartbeat_rate.reset()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
