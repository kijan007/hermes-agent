# Worklog — t_ed455c77

## Kontext
M3 im Guardrail-Milestone: Der Signatur-Hash für den Identical-Call-Streak
(`identical_call_streak_halt` in `agent/tool_guardrails.py`) enthielt volatile
Felder (Heartbeat-/Zeit-/ID-Felder), sodass semantisch identische Aufrufe, die
sich nur in diesen Feldern unterscheiden und > 60 s auseinander liegen, den
Streak resetten und den Halt nie auslösten. Ansatz 1 dieses Tasks starb ohne
Terminal-Call (Protokollverstoß) und hinterließ die Implementierung uncommittet
im Worktree — dieser Lauf hat verifiziert, korrigiert, getestet und geliefert.

## Aktionen
- Bestehenden Worktree `/workspace/.worktrees/t_ed455c77` (Branch
  `task/t_ed455c77`, origin = kijan007/hermes-agent) übernommen; uncommittete
  Änderung des Vorlaufs geprüft (Volatile-Strip in `ToolCallSignature.from_call`
  bereits implementiert, Tests vorhanden).
- `git fetch origin main` — Branch war exakt bei origin/main (0 hinter).
- Erste Testausführung scheiterte an `tests/home_io_guard.py` (Real-HOME-Guard
  greift in dieser Sandbox, weil HOME/HERMES_HOME unter /workspace/.hermes
  liegen): Tests laufen mit isoliertem `HOME=`/`HERMES_HOME=`/`TMPDIR=` unter
  /workspace/worktrees/t_ed455c77/scratch/.
- Akzeptanztest `test_identical_call_streak_fires_with_volatile_args_60s_apart`
  war rot (halt=None): Der Streak vergleicht (Signatur, Ergebnis-Hash); der
  Test verwendete unterschiedliche Ergebnisse — fachlich falsch modelliert.
  Test korrigiert: identisches Tool-Ergebnis, pro Aufruf wechselnde
  volatile args-Felder (timestamp/last_heartbeat), Halt bei Threshold 3.
- Commit `22222b9499` auf `task/t_ed455c77`, gepusht nach origin.
- Integration: `git fetch origin dev && git rebase origin/dev` (M1-Warntext-
  Commit 29695ed1e0 konfliktfrei integriert, beide Änderungen koexistieren),
  neuer Commit `b486ff55bb`; Suite erneut grün; Push nach origin/dev.

## Geaenderte / erstellte Dateien
- agent/tool_guardrails.py — `_VOLATILE_ARGS_KEYS` (zentrale, testbare
  Frozenset-Konstante: Zeitstempel-, Heartbeat-, Laufzeit-, Session-/Request-ID-,
  Retry-Felder) + `_strip_volatile_args()` (rekursiv, dicts + Listen von dicts);
  angewendet in `ToolCallSignature.from_call` vor `canonical_tool_args`, mit
  Kommentar zur Begründung.
- tests/agent/test_tool_guardrails.py — 4 neue Tests: Signatur-Gleichheit trotz
  volatile Felder, Erhalt echter inhaltlicher Unterschiede (query/language- und
  Struktur-Unterschiede), Konstante testbar (task_id NICHT volatile),
  Akzeptanztest identical_call_streak_halt mit volatile args > 60 s auseinander.

## Tests / Verifikation
- `python3 -m pytest tests/agent/test_tool_guardrails.py tests/agent/test_agent_guardrails.py -q`
  → **60 passed** (Basis 56 + 4 neue; Akzeptanzzeile im Task erwartete 56 als
  Bestand, Suite wuchs um die 4 neuen M3-Tests).
- Einzeln: neuer Akzeptanztest grün (`1 passed`).
- Struktur-Check: `halt.code == "identical_call_streak_halt"` im Test verifiziert.

## Dev-Integration
- Commit(s): b486ff55bb auf origin/dev (kijan007/hermes-agent), Branch
  task/t_ed455c77 gepusht (https://github.com/kijan007/hermes-agent).
