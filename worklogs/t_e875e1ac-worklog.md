# Worklog t_e875e1ac — M2b: Dispatcher wertet fehlenden Terminal-Call nach Halt als blocked

## Ziel
Nach Guardrail-Halt (identical_call_streak_halt / identical_cycle_halt / same_tool_failure_halt):
der Dispatcher erkennt, dass kein terminaler Board-Call kam, und parkt die Karte als `blocked`
statt als `crashed`/`protocol_violation`. Kein Re-Dispatch; `retry_status` bleibt `blocked`.

## Analyse (Ist-Zustand vor M2b)
`hermes_cli/kanban_db_dispatch.py`:
- `_classify_dead_worker_exit`: clean_exit (rc=0) → protocol_violation=True → retry_status=ready
- `_reclaim_dead_workers`: karte wird auf retry_status (→ready) gesetzt → re-dispatch → loop.
M2a (task/t_7dbbb2e8) hat den Worker-Weg (kanban_block-Nudge); M2b ist der Fallback.

## Implementierung

### 1. hermes_cli/kanban_db.py
Exit-Code 76 = BSD EX_UNAVAILABLE:
```python
KANBAN_GUARDRAIL_HALT_EXIT_CODE = 76
```

### 2. hermes_cli/turn_exit.py
turn_exit_code(): guardrail-Ergebnis (M2a oder M2a-ignoriert) → Exit 76 statt 0:
```python
if kanban_worker:
    if result.get("guardrail") or result.get("failure_reason") == "guardrail_halt":
        return KANBAN_GUARDRAIL_HALT_EXIT_CODE
```

### 3. hermes_cli/kanban_db_dispatch.py
- `_DeadWorker`: guardrail_halt-Flag, run_outcome="blocked"
- `_exit_code_kind`: rc=76 → kind="guardrail_halt"
- `_classify_dead_worker_exit`: guardrail_halt → controlled stop
- `_reclaim_dead_workers`: guardrail_halt → retry_status="blocked" + blocked-Event schreiben
  (blocked-Event hält die Karte via _has_sticky_block; recompute_ready promote sie NICHT)
- `_account_crashes`: guardrail_halt → pass (kein breaker-Trip)

### 4. tests/hermes_cli/test_kanban_worker_exit_trailer.py
test_guardrail_halt_books_blocked_no_redispatch: 6 tests (vorher 5 → nachher 6)

## Zustandsübergang
| Vorher | Auslöser | Nachher | retry_status |
|--------|----------|---------|-------------|
| running | guardrail halt, kein Terminal-Call | blocked | blocked |

consecutive_failures unverändert, protocol_violation_streak=0, nur unblock_task befreit.

## Verification
- tests/agent/test_tool_guardrails.py + test_agent_guardrails.py: 56 passed ✓
- tests/hermes_cli/test_kanban_worker_exit_trailer.py: 6 passed ✓
- tests/hermes_cli/test_kanban_blocked_sticky.py + test_kanban_core_functionality.py + test_single_query_exit_contract.py + test_kanban_managed_exit_reconciliation.py: 94 passed ✓

## Geänderte Dateien
- hermes_cli/kanban_db.py (+9 Zeilen): Exit-Code 76
- hermes_cli/kanban_db_dispatch.py (+55 Zeilen): guardrail_halt im Dispatcher
- hermes_cli/turn_exit.py (+11 Zeilen): Exit-Code-Mapping
- tests/hermes_cli/test_kanban_worker_exit_trailer.py (+36 Zeilen): neuer Test
