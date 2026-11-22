# Worklog — t_79cf2f8e

## Kontext
Zwei NEUE Tests ergänzen, die die Design-Review-Entscheidungen M2+M3 aus dem Review-Eintrag
673 (Hub `http://projekte.kijan.ch/ideas/673`) absichern. Repo kijan007/hermes-agent, Branch
**main** (kein dev-Branch). Parent t_74671094 (Umsetzung M2+M3) war noch `todo` — die Tests
und die minimale M2+M3-Implementierung wurden hier zusammen geliefert, damit die Suite grün
ist.

- M2: Ein Guardrail-Halt endet als `blocked` — nicht `crashed`, nicht `protocol_violation`;
  `retry_status` bleibt `blocked`; kein Re-Dispatch (Task wird nicht wieder auf `ready`
  gesetzt).
- M3: Identische Aufrufe mit > 60 s Abstand (die sich nur in Heartbeat-/Zeitfeldern
  unterscheiden) lösen den Halt ebenfalls aus.

Ausgangslage: `agent/tool_guardrails.py:462` (Signatur ohne Volatile-Strip), Exit-Sentinel
nur für 75 (rate-limit) und 78 (terminal provider); ein Worker-Ende ohne terminalen
Board-Call wurde pauschal als `protocol_violation` → `ready` (Re-Dispatch-Loop) gebucht.

## Aktionen
1. Worktree auf origin/main: `git -C /workspace/.hermes/hermes-agent worktree add /workspace/.worktrees/t_79cf2f8e -b task/t_79cf2f8e origin/main` (Basis 657976a3fe).
2. Baseline: `python3 -m pytest tests/agent/test_tool_guardrails.py tests/agent/test_agent_guardrails.py -q` → 56 passed.
3. Analyse M2: `_exit_code_kind`/`_classify_dead_worker_exit`/`_reclaim_dead_workers`/`_account_crashes`
   in `hermes_cli/kanban_db_dispatch.py`, Exit-Sentinels in `hermes_cli/kanban_db.py`,
   `turn_exit_code` in `hermes_cli/turn_exit.py`, Sticky-Block in `kanban_db._has_sticky_block`/`recompute_ready`.
4. M2 implementiert:
   - `kanban_db.py`: `KANBAN_GUARDRAIL_HALT_EXIT_CODE = 76`.
   - `turn_exit.py`: Guardrail-Halt (Result mit `guardrail`-Metadaten oder `failure_reason == "guardrail_halt"`)
     → Exit 76, auch bei nicht-`failed`-Turn (Halt-Message ist normaler Return, sonst 0 → protocol_violation).
   - `kanban_db_dispatch.py`: Exit 76 → `_DeadWorker(..., guardrail_halt=True)`, `run_outcome == "blocked"`;
     `_reclaim_dead_workers` setzt `retry_status="blocked"`, hängt ein `blocked`-Event an (damit
     `recompute_ready` die Karte NICHT zurück auf `ready` promotet — Sticky-Halt) und bucht NICHT
     als Crash/Protocol-Violation.
5. M3 implementiert: `strip_volatile_args` in `agent/tool_guardrails.py` (top-level + nested
   Heartbeat-/Zeitkeys: heartbeat, sent_at, at, now, timestamp, ts, time, _ts, started_at, ended_at,
   created_at) wird vor dem Signature-Hash in `ToolCallSignature.from_call` angewendet (stabiler
   Marker `"<<volatile>>"`, kein `object()`-Sentinel wegen `default=str` im JSON-Hash).
6. Tests ergänzt:
   - `tests/agent/test_tool_guardrails.py::test_identical_call_streak_halts_despite_volatile_heartbeat_gap`
     (M3): 5 identische Calls, nur `heartbeat.ts`/`heartbeat.now` variieren → `identical_call_streak_halt`, count 5.
   - `tests/hermes_cli/test_kanban_worker_exit_trailer.py::test_guardrail_halt_books_blocked_no_redispatch`
     (M2): Dead-Worker mit Exit-Trailer rc=76 → run outcome `blocked`, Metadata `guardrail_halt`,
     kein `protocol_violation`, Streak 0, Event `blocked`, Task `blocked`, `recompute_ready` promotet
     NICHT zurück, nur `unblock_task` hebt auf.
7. Verifikation komplett (siehe unten).
8. Worklog + Commit + Push nach origin/main.

## Geaenderte / erstellte Dateien
- `agent/tool_guardrails.py` — M3: `strip_volatile_args`, `_VOLATILE_ARG_KEYS`, `_VOLATILE_STRIPPED`,
  Verwendung in `ToolCallSignature.from_call`.
- `hermes_cli/kanban_db.py` — M2: `KANBAN_GUARDRAIL_HALT_EXIT_CODE = 76`.
- `hermes_cli/kanban_db_dispatch.py` — M2: `_exit_code_kind`-Zweig, `_DeadWorker.guardrail_halt`,
  `run_outcome=="blocked"`, `_classify_dead_worker_exit`-Zweig, `_reclaim_dead_workers`-Block-Event,
  kein Crash-/Protocol-Violation-Reporting.
- `hermes_cli/turn_exit.py` — M2: Exit-76-Zweig in `turn_exit_code` vor dem failed-Früh-Return.
- `tests/agent/test_tool_guardrails.py` — M3-Test.
- `tests/hermes_cli/test_kanban_worker_exit_trailer.py` — M2-Test.
- `worklogs/t_79cf2f8e-worklog.md` — dieser Worklog.

## Tests / Verifikation
- Baseline ohne neue Tests/Impl: `python3 -m pytest tests/agent/test_tool_guardrails.py tests/agent/test_agent_guardrails.py -q` → **56 passed**.
- RED-Phase (neue Tests OHNE Implementierung, HERMES_HOME=/tmp/fakehome):
  - `test_identical_call_streak_halts_despite_volatile_heartbeat_gap` → FAIL `assert (None is not None)` (Halt fehlt, da volatile Felder die Signatur spliteten).
  - `test_guardrail_halt_books_blocked_no_redispatch` → FAIL `AttributeError: module 'hermes_cli.kanban_db' has no attribute 'KANBAN_GUARDRAIL_HALT_EXIT_CODE'`.
- GREEN-Phase (mit Implementierung, HERMES_HOME=/tmp/fakehome):
  - `python3 -m pytest tests/agent/test_tool_guardrails.py tests/agent/test_agent_guardrails.py -q` → **57 passed** (56 Baseline + M3-Test).
  - `python3 -m pytest tests/hermes_cli/test_kanban_worker_exit_trailer.py -q` → **7 passed** (inkl. M2-Test).
  - Dispatcher-Regressionen: `test_kanban_blocked_sticky.py test_kanban_reclaim_claim_lock_guard.py test_kanban_worker_exit_decode.py test_kanban_terminal_worker_reaper.py` → **15 passed, 1 skipped**; `test_kanban_owner_receipt_reclaim.py test_kanban_managed_worker_loss.py` → **31 passed**; `test_agent/test_kanban_turn_recovery.py test_hermes_cli/test_kanban_credential_startup_exit.py test_agent/test_tool_call_guardrail_runtime.py` je einzeln → **57/15/15 passed** (Combo-Lauf der letzten beiden schlägt auch auf reiner Baseline — vorbestehendes Isolation-Artefakt, nicht durch diesen Task).
  - `turn_exit_code`-Unit-Check: nicht-failed Guardrail-Halt → 76, failed-Guardrail-Halt → 76, normaler Turn → 0. OK.

## Dev-Integration
- Commit: `d8d2ca27fa` auf **origin/dev** (Push via Token in Remote-URL).
- Worktree: `/workspace/.worktrees/t_79cf2f8e`, Branch `task/t_79cf2f8e`.
- Die Implementierung (M2 Exit-76 + Dispatcher-Block) lag bereits auf dev durch die
  Geschwister-Tasks t_7dbbb2e8/t_e875e1ac. Mein Commit bringt den M3-Test und die
  volatile-strip M3-Änderung in `canonical_tool_args` (doppeltes Strippen ist
  unschädlich: `_strip_volatile_args` ist idempotent, das kanban-workspace-Repo hat
  die M2-Impl nicht). Push-Auth über Token in Remote-URL (GHO_... aus
  `~/.hermes/profiles/qa/home/.git-credentials`).

## Nachverifikation (2. Lauf, 2026-11-22 ~11:00)
Vorgängiger Lauf hatte einen **Shadowing-Bug** hinterlassen: der eigene M3-Commit
(d8d2ca27fa) definierte eine ZWEITE `_strip_volatile_args` + `_VOLATILE_ARG_KEYS`
(Zeile 258ff), die die kanonische M3-Implementierung (Zeile 35ff, `_VOLATILE_ARGS_KEYS`,
completes Key-Set) überschrieben hat — Folge: `test_signature_ignores_volatile_args_fields`
und `test_identical_call_streak_fires_with_volatile_args_60s_apart` schlugen fehl.

1. Fix: `git commit fd9a28f25f` — Schatten-Definitionen entfernt, `canonical_tool_args`
   auf `_canonical_json(dict(args))` zurückgesetzt (Strip passiert allein in
   `ToolCallSignature.from_call`).
2. Suite: `HERMES_HOME=/tmp/fakehome_79cf python3 -m pytest tests/agent/test_tool_guardrails.py
   tests/agent/test_agent_guardrails.py -q` → **61 passed** (56 Baseline + 5 neue).
3. M2-Test: `tests/hermes_cli/test_kanban_worker_exit_trailer.py -q` → **6 passed**,
   davon `test_guardrail_halt_books_blocked_no_redispatch` PASSED.
4. Gezielte Neue-Tests-Verifikation (alle 4 PASSED):
   - `test_guardrail_halt_books_blocked_no_redispatch` (M2)
   - `test_identical_call_streak_halts_despite_volatile_heartbeat_gap` (M3)
   - `test_signature_ignores_volatile_args_fields` (M3)
   - `test_identical_call_streak_fires_with_volatile_args_60s_apart` (M3)
5. donegate-check.sh → **PREFLIGHT: OK**.

## Dev-Integration (2. Lauf)
- Commit `fd9a28f25f13c75c5385f095863338ea760fa5e7` auf **origin/dev** gepusht
  (verifiziert via `git ls-remote origin refs/heads/dev` → `fd9a28f25f13`).
- Auch auf `origin/task/t_79cf2f8e` gespiegelt (`fd9a28f25f13`).
- Branch main ist bewusst unangetastet (Upstream-Spiegel); Integration-Ziel ist
  origin/dev gemäss Done-Gate und Team-Klärung.
