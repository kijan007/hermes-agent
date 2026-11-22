# Worklog — t_d971ebee

## Kontext
M1: Der Warntext der Identical-Call-Notice in kijan007/hermes-agent (agent/tool_guardrails.py, _IDENTICAL_CALL_NOTICE und _IDENTICAL_CYCLE_NOTICE) konnte als Stopp/Halt missverstanden werden. Ziel: Text praezisieren — explizit WARNUNG, kein Stopp, Lauf laeuft weiter; keine Logik-Aenderung.

## Aktionen
- Bestehenden Worktree /workspace/.worktrees/t_d971ebee (Branch task/t_d971ebee, Basis origin/main) uebernommen; Commit ed0e174af0 des Vorlaufs verifiziert.
- Nutzungsstellen geprueft: Notices werden nur in agent/tool_guardrails.py definiert, in run_agent.py (Zeile ~1307) an das Tool-Ergebnis angehaengt und via record_guardrail_warnings (hermes_cli/observability/shared_metrics_harness.py) als Warn-Metrik erfasst; Kanban-Kommentar-Pfad existiert nicht.
- Dokumentation geprueft: website/docs/user-guide/configuration.md beschreibt die Notice bereits korrekt als Warnung ("never blocks the call") — keine Doku-Aenderung noetig.
- Tests bestanden, Commit verifiziert, mit kpt-deliver.sh auf origin/dev geliefert (Task-Ziel-Branch laut Done-Gate-Policy; origin/main hat keinen dev-Vorgaenger).

## Geaenderte / erstellte Dateien
- agent/tool_guardrails.py — _IDENTICAL_CALL_NOTICE und _IDENTICAL_CYCLE_NOTICE: Prefix "[hermes note: WARNING (not a stop — execution continues): ...]" statt "[hermes note: ...]"; "Do not repeat it/the batch" -> "Do not repeat the call/batch unchanged". Nur Text, keine Logik.

## Tests / Verifikation
- `python3 -m pytest tests/agent/test_tool_guardrails.py tests/agent/test_agent_guardrails.py -q` → 56 passed (wie gefordert)
- `python3 -m pytest tests/agent/test_stall_guards.py -q` → 39 passed (Zusatzabsicherung: Deckt die Notices direkt ab)
- `git merge-base --is-ancestor HEAD origin/dev` → nach Lieferung integriert

## Dev-Integration
- Commit(s): ed0e174af0 (+ Worklog-Commit) auf origin/dev (Push via kpt-deliver.sh)
