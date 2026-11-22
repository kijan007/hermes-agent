# Worklog — t_e875e1ac

## Kontext
Repo: kijan007/hermes-agent, Branch main (kein dev-Branch). Ziel (M2, Weg b): Der
Dispatcher wertet das Ausbleiben eines terminalen Calls nach einem Guardrail-Halt als
blocked statt als crashed/protocol_violation. Kein Re-Dispatch; retry_status bleibt blocked.

Ist-Zustand: hermes_cli/kanban_db_dispatch.py `_classify_dead_worker_exit` booked
clean_exit (rc=0) als protocol_violation=True → retry_status="ready" → Karte wird neu
dispatched → Guardrail feuert erneut → Infinite Loop. M2a (t_7dbbb2e8) hatte den
Worker-Weg implementiert (kanban_block-Nudge, nicht auf main gemergt); M2b ist der
Fallback, wenn der Worker nach einem Halt trotzdem ohne terminalen Board-Call endet.

## Aktionen
1. hermes_cli/kanban_db.py: neue Konstante `KANBAN_GUARDRAIL_HALT_EXIT_CODE = 76`
   (BSD EX_UNAVAILABLE — politischer Stop, der Worker wird nicht selbst retryen).
2. hermes_cli/turn_exit.py: `turn_exit_code()` liefert Exit 76 wenn das Turn-Ergebnis
   `guardrail`-Metadata traegt (oder `failure_reason == "guardrail_halt"`) — statt 0.
   Das verhindert, dass ein Halt als protocol_violation (rc=0) gelesen wird.
3. hermes_cli/kanban_db_dispatch.py:
   - `_DeadWorker`: `guardrail_halt`-Flag; `run_outcome` → "blocked".
   - `_exit_code_kind`: rc=76 → kind "guardrail_halt".
   - `_classify_dead_worker_exit`: guardrail_halt → controlled stop (never crashed).
   - `_reclaim_dead_workers`: bei guardrail_halt `retry_status = "blocked"` (statt ready),
     zusaetzlich ein `blocked`-Event (reason=guardrail_halt) schreiben, damit
     `_has_sticky_block` die Karte haelt und `recompute_ready` sie NICHT zurueck nach
     ready foerdert (kein Re-Dispatch). `last_failure_error` wird gesetzt (Board-Diagnose).
   - `_account_crashes`: guardrail_halt → pass (kein breaker-Trip, keine
     consecutive_failures-Erhoehung, protocol_violation_streak bleibt 0).
4. tests/hermes_cli/test_kanban_worker_exit_trailer.py: neuer Test
   `test_guardrail_halt_books_blocked_no_redispatch` (rc=76 → outcome=blocked,
   event.kind=blocked, status=blocked haelt recompute_ready aus, nur unblock_task loest).
   5 → 6 Tests.

Echter Crash-Pfad (nonzero_exit/signaled/unknown) und protocol_violation-Pfad bleiben
unveraendert — nur der KANBAN_GUARDRAIL_HALT_EXIT_CODE-Fall wird zu blocked. Zustands-
uebergaenge sind im Code und unten kommentiert (blocked-Event + retry_status=blocked).

## Geaenderte / erstellte Dateien
hermes_cli/kanban_db.py           |  +9 | KANBAN_GUARDRAIL_HALT_EXIT_CODE = 76
hermes_cli/kanban_db_dispatch.py  | +55 | guardrail_halt-Track im Dispatcher-Reclaim
hermes_cli/turn_exit.py           | +11 | Exit-Code-Mapping 76
tests/hermes_cli/test_kanban_worker_exit_trailer.py | +36 | neuer blocked-Test
worklogs/t_e875e1ac-worklog.md    | 57  | dieses Worklog

## Tests / Verifikation
python3 -m pytest tests/agent/test_tool_guardrails.py tests/agent/test_agent_guardrails.py -q
  → 56 passed (Acceptance-Kriterium, vorher 56).
python3 -m pytest tests/hermes_cli/test_kanban_worker_exit_trailer.py -q
  → 6 passed (inkl. test_guardrail_halt_books_blocked_no_redispatch).
python3 -m pytest tests/hermes_cli/test_kanban_blocked_sticky.py
   tests/hermes_cli/test_single_query_exit_contract.py
   tests/hermes_cli/test_kanban_core_functionality.py
   tests/hermes_cli/test_kanban_managed_exit_reconciliation.py
   tests/hermes_cli/test_kanban_managed_worker_loss.py
   tests/hermes_cli/test_kanban_owner_receipt_reclaim.py
   tests/hermes_cli/test_kanban_reclaim_claim_lock_guard.py
   tests/gateway/test_kanban_reconcile_orphans.py -q
  → 94 passed (Crash-/Reclaim-/Exit-Pfade unveraendert gruen).

## Dev-Integration
Branch task/t_e875e1ac pushed auf origin (kijan007/hermes-agent), Commits: c90bd29621
(Implementierung), 4b22c22a0c (Worklog). Kein dev-Branch vorhanden (Task-Vorgabe: Branch
main). Lieferung mit kpt-deliver.sh bzw. Merge via PR nach main.
