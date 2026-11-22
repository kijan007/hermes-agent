# Worklog — t_7dbbb2e8

## Kontext
Repo: kijan007/hermes-agent, Basis main (kein dev-Branch). Ziel M2a: Ein Guardrail-Halt (identical_call_streak_halt) endet als blocked, NICHT als crashed/protocol_violation. Der Worker wird nach dem Halt per Synthetic-Nudge zu kanban_block (reason="Guardrail-Halt: <code>") aufgefordert, damit der Turn als terminaler blocked-Call endet.

## Aktionen
- Vorheriger Attempt hatte Implementierung im Worktree hinterlassen (uncommittet): release_halt() in tool_guardrails.py, Redirect-Logik in turn_tool_round.py, Nudge-Builder in kanban_stop.py, Metadata in turn_finalizer.py, Turn-State-Reset in turn_context.py — geprüft und für inhaltlich korrekt befunden.
- Neue Testdatei tests/agent/test_guardrail_halt_kanban_block.py: 2 der 5 Tests rot. Ursache: der handle_function_call-Mock gab immer {"error": "boom"} zurück — damit fütterte jeder Call repeated_exact_failure_block (exact_failure_block_after=2), bevor identical_call_streak_halt (no_progress_block_after=5, nur bei identischen ERFOLGREICHEN Calls) überhaupt feuern konnte.
- Fix im Test-Setup: Mock auf {"ok": True} (erfolgreiche identische Ergebnisse) umgestellt, Responses exakt auf 5 begrenzt (Halt feuert auf dem 5. observe_call), Follow-up-Ketten für block_call=True (kanban_block-Call + Narration) und block_call=False (4x Narration, kanban stop-guard läuft in sein Budget).
- Verifikation: env -u HERMES_HOME python3 -m pytest … → 61 passed (HERMES_HOME unset, damit die Session-Sandbox greift und der home_io_guard die Tracebacks nicht zerschießt).
- Commit 1ae6f95ece (Code+Tests) und Push auf origin/task/t_7dbbb2e8.

## Geaenderte / erstellte Dateien
- agent/tool_guardrails.py — release_halt(only_codes=...): Halt freigeben, damit der Turn zum terminalen Board-Call weiterlaufen kann
- agent/turn_context.py — 3 neue Turn-State-Felder in _PER_TURN_RESET_STATE (_guardrail_halt_redirects, _guardrail_halt_released, _guardrail_block_nudge)
- agent/turn_tool_round.py — Redirect-Zweig: Halt freigeben, Nudge als user-Row (_kanban_stop_synthetic) anhängen, Turn weiterführen statt guardrail_halt-Exit
- agent/turn_finalizer.py — result["guardrail"] (freigegebener Halt) + result["guardrail_redirect"]="kanban_block" im Turn-Ergebnis
- agent/kanban_stop.py — _GUARDRAIL_HALT_REDIRECT_CODES (identical_call_streak_halt / identical_cycle_halt / same_tool_failure_halt), guardrail_halt_redirect_enabled(), build_guardrail_halt_block_nudge()
- tests/agent/test_guardrail_halt_kanban_block.py — 5 Tests: release_halt-Semantik, Redirect-Gate (kanban worker, Budget, Terminal-Call), Nudge-Text, End-to-End-Redirect zum kanban_block-Call, Fallback auf kanban stop-guard

## Tests / Verifikation
- `env -u HERMES_HOME python3 -m pytest tests/agent/test_tool_guardrails.py tests/agent/test_agent_guardrails.py tests/agent/test_guardrail_halt_kanban_block.py -q` → 61 passed (56 bestehende + 5 neue)

## Dev-Integration
- Commit(s): 1ae6f95ece, 761b0848ab auf origin/task/t_7dbbb2e8 (kijan007/hermes-agent)
