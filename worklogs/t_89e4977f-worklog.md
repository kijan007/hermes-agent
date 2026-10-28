# Worklog — t_89e4977f

## Kontext

- **Task:** Worker-Runtime: Heartbeat-Rate-Limit clientseitig (max 1 alle 30s, Dedupe)
- **Task-ID:** t_89e4977f (assignee software-development), erstellt von scrummaster
- **Vorgänger-Klärung:** t_9d2cdb94 `[Team-Klärung] Dispatcher: Heartbeat-Sturm, fehlende Runtime-Caps, Quota-gate-Flapping` (done). Entscheidung dort:
  1. Heartbeat-Rate-Limit **clientseitig**: 30s-Mindestabstand + Dedupe im Worker-Bridge → **diese Karte**.
  2. Dispatcher: Default-Runtime-Cap 4h + Sturm-Erkennung >10 HB/min → Karte an architect (nicht hier).
  3. Quota-Gate-Hysterese → Karte an devops (nicht hier).
- **Befund aus der Klärung:** Worker pid 750488 feuerte 151 Heartbeats „direkt nach Spawn" (~123/s) → frische Heartbeats hielten den Lauf „lebendig", obwohl der Worker keinen Fortschritt machte (Provider down) → kein Stale-Reclaim, 151 min Blockade bis manueller Reclaim (Lauf 59088, Task t_a6fcd671).
- **Betroffener Code:** Der Heartbeat-Bridge aus Commit bc31ee5cf8 („bridge worker runtime activity to board heartbeat", #31752) — `agent/activity_tracking.py:_touch_activity` → `tools/kanban_tools.py:heartbeat_current_worker_from_env` → `hermes_cli/kanban_db_dispatch.py:heartbeat_worker` → `tasks.last_heartbeat_at` + `task_events(kind='heartbeat')`.
- **Repo:** `/hermes-src` (Hermes Agent, Install-Methode git). Branch `task/t_89e4977f` im Worktree `/hermes-src/.worktrees/t_89e4977f`, Basis `origin/main` (kijan007/hermes-agent) = 90267d65d3.
- **Korrektur zur Aufgaben-Prämisse (belegt, nicht übernommen):** Die 151 Heartbeats von Lauf 59088 waren **nicht** in ~1,2s, sondern exakt 60s auseinander über 9007 s (143× 60 s, 7× 61 s). Ebenso Lauf 61878 (157 HBs, min. Abstand 60 s) und Lauf 61864 (1025 HBs, min. Abstand 56 s). Ein Burst im Sekundenbereich ist in `task_events` **nicht** belegt. Die Entscheidung der Team-Klärung (30s-Floor + Dedupe + Sturm-Guard) ist davon unabhängig umgesetzt; der Burst selbst war offenbar eine Fehlmessung der Analyse (Zählung der Event-Zeilen statt der Zeitabstände). Der Sturm-Guard greift trotzdem, weil er **Requests** zählt und nicht akzeptierte Writes.

## Aktionen

1. **Code-Analyse der Heartbeat-Pfade** (Worktree `task/t_89e4977f`):
   - `tools/kanban_tools.py:heartbeat_current_worker_from_env` (Auto-Bridge) hatte bereits eine **60s**-Fensterlogik (Modul-Global `_auto_heartbeat_last_attempt`) — der Auto-Pfad war also nicht der ungedrosselte Verursacher.
   - `tools/kanban_tools.py:_handle_heartbeat` (explizites `kanban_heartbeat`-Tool) hatte **gar keine** Drosselung: jeder Aufruf schrieb `tasks.last_heartbeat_at` + ein `heartbeat`-Event. Das ist der ungeschützte Pfad.
   - `hermes_cli/kanban_db_dispatch.py:heartbeat_worker` ist der DB-Schreiber und bleibt unangetastet (kein Schreib-Semantik-Wechsel).
   - Reclaim-Logik gelesen (`hermes_cli/kanban_db.py:release_stale_claims`, `DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS = 3600`, `DEFAULT_CLAIM_TTL_SECONDS = 900`): ein 30s-Floor schadet legitimen Workern nicht — sie refreshen damit weiter ~30× häufiger als der 1h-Stale-Backstop braucht.
2. **Neues Modul `tools/kanban_heartbeat_rate.py`**: threadsicherer, pro-Task clientseitiger Limiter.
   - `HEARTBEAT_MIN_INTERVAL_SECONDS = 30.0` — Mindestabstand zwischen zwei akzeptierten Writes; Writes im Fenster werden **verworfen** (Dedupe), **nicht** eingereiht (ein nachgereichter Burst würde den Zweck aufheben).
   - `HEARTBEAT_STORM_THRESHOLD_PER_MINUTE = 10` — Sturm-Guard.
   - Zwei Zähler pro Task: `requests` (wie oft **gefragt** wurde → Sturmsignal) und `recent` (akzeptierte Writes). Nötig, weil ein 30s-Floor akzeptierte Writes auf 2/min begrenzt — ein Sturm wäre sonst nie über die Write-Zahl erkennbar, nur über die Request-Zahl.
   - `first_in_burst`: der Sturm wird **einmal pro Burst** gemeldet, nicht pro verworfenem Heartbeat.
   - Verdict-API: `ALLOW` / `DEDUPE` / `STORM`, plus `retry_after`, `per_minute`.
3. **`tools/kanban_tools.py` verdrahtet** (beide Schreibpfade):
   - Auto-Bridge: zusätzlich durch den gemeinsamen Limiter geschleust; bei `DEDUPE`/`STORM` **kein** Write **und keine** Claim-Verlängerung (ein Heartbeat-Sturm darf einen festgefahrenen Lauf nicht am Leben halten).
   - Explizites `kanban_heartbeat`-Tool: ein verworfener Write ist ein **Erfolg** (`ok: true`, `heartbeat: "dropped"`, `reason`, `retry_after`) — das Modell soll für einen Heartbeat, den das Board schon hat, **keinen** Tool-Fehler sehen. Der erste Sturm-Schlag wird als Event `heartbeat_storm` auf dem Board protokolliert (best-effort, einmal pro Burst).
4. **Tests geschrieben** (18 Tests):
   - `tests/tools/test_kanban_heartbeat_rate.py` — 13 Unit-Tests am Limiter (Floor, Dedupe-Grenze 29,9 s / 30 s, Sturm-Schwelle, Fenster-Ablauf, Task-Isolation, Reset, Thread-Sicherheit).
   - `tests/tools/test_kanban_auto_heartbeat_rate.py` — 2 Integrationstests am Auto-Bridge (zweiter Write im Fenster fällt durch; 15 Bridge-Aufrufe ⇒ genau 1 Write).
   - `tests/tools/test_kanban_heartbeat_rate.py` (3 Tool-Integrationstests im selben File) — explizites Tool: erstes `recorded`, zweites `dropped`, genau **eine** heartbeat-Zeile in der DB; Drops sind Fehler-frei; genau **ein** `heartbeat_storm`-Event.
5. **Live-End-to-End-Skript** `scripts/heartbeat_rate_check.py` gegen eine echte (temporäre) Board-DB: 60 Heartbeat-Aufrufe in einer engen Schleife.
6. **Regressionsabgleich gegen Baseline** (jeweils mit `git stash`): `tests/hermes_cli/ -k kanban` ⇒ **39 failed, 480 passed** vor **und** nach der Änderung (identisch); `tests/tools/ -k "kanban or heartbeat"` ⇒ **2 failed** vor **und** nach (identisch). Alle Fehlschläge sind umgebungsbedingt (`home_io_guard`: „file I/O against the REAL hermes home") und unabhängig von dieser Änderung.

## Geaenderte / erstellte Dateien

- `tools/kanban_heartbeat_rate.py` (neu) — der clientseitige Limiter (Floor 30 s, Dedupe, Sturm-Guard >10/min), threadsicher, ohne Schreibzugriff.
- `tools/kanban_tools.py` (geändert) — beide Heartbeat-Schreibpfade laufen durch den Limiter; der Auto-Bridge verwirft auch die Claim-Verlängerung; der explizite Tool meldet Drops als Erfolg und protokolliert den Sturm als Event.
- `tests/tools/test_kanban_heartbeat_rate.py` (neu) — 16 Tests (13 Unit + 3 Tool-Integration).
- `tests/tools/test_kanban_auto_heartbeat_rate.py` (neu) — 2 Integrationstests am Auto-Bridge.
- `scripts/heartbeat_rate_check.py` (neu) — Live-Prüfskript gegen eine reale Board-DB (60 Calls ⇒ 1 Zeile).
- **Keine Datei in einem KPT-Projekt-Repo geändert** (kein Projektcode betroffen).

## Tests / Verifikation

- `python3 -m pytest tests/tools/test_kanban_heartbeat_rate.py tests/tools/test_kanban_auto_heartbeat_rate.py -q` → **18 passed** in 4,78 s.
- `python3 scripts/heartbeat_rate_check.py` → `calls: 60 · recorded (tool): 1 · dropped (tool): 59 · heartbeat rows: 1 · storm events: 1 · all ok=true: True · reasons: ['dedupe','storm'] · RESULT: PASS` (Exit 0) — **beweist live gegen die echte DB, dass 60 Heartbeat-Aufrufe genau eine Zeile erzeugen**.
- `python3 -m pytest tests/hermes_cli/ -q -k kanban` → **39 failed, 480 passed**; Baseline (ohne meine Änderung, `git stash`): **39 failed, 480 passed** → identisch, keine Regression.
- `python3 -m pytest tests/tools/ -q -k "kanban or heartbeat" --ignore=tests/tools/test_fal_sdk.py --ignore=tests/tools/test_mcp_tool.py --ignore=tests/tools/test_schema_boolean_required.py` → **2 failed, 127 passed**; Baseline: **2 failed, 109 passed** → dieselben 2 Fehlschläge, +18 passing (die neuen Tests). Die 3 ignorierten Files haben Collection-Errors wegen fehlender Optional-Deps (`fal_sdk`, `mcp`) — vorbestehend.
- `python3 -m py_compile` über alle 4 geänderten/neuen Python-Dateien → **COMPILE_OK**.
- `sqlite3 /workspace/.hermes/kanban.db "select created_at from task_events where run_id=59088 and kind='heartbeat' order by created_at"` → 151 Werte, `gaps: 60×143, 61×7`, Span 9007 s → **die Prämisse „151 Heartbeats in ~1,2 s" ist nicht durch die DB belegt** (siehe Kontext).
- `sqlite3 … run_id=61878` → 157 HBs, min. Abstand 60 s; `run_id=61864` → 1025 HBs, min. Abstand 56 s.

## Dev-Integration

- **Lieferung:** Branch `task/t_89e4977f` im Repo `/hermes-src` → `origin` (kijan007/hermes-agent), Basis `origin/main` = 90267d65d3. Das Repo hat **kein** `dev`-Branch (Remote-Branches: `main`, `bot/js-autofix`, `release/v2026.9.24` sowie Task-Branches) — der Aufgabentext „Lieferung auf origin/dev" ist hier nicht anwendbar; `origin/main` ist der Ziel-Branch (so wie die übrigen Task-Branches `task/t_162f3992`, `task/t_387eb774`).
- **Kein Deployment, keine Dev-Umgebung nötig:** der Fix ist reines Python im Worker-Runtime-Pfad; verifiziert durch pytest (isolierte Board-DB) und das Live-Skript gegen eine reale Board-DB.
- **Abgrenzung:** Die Dispatcher-Seite (Default-Cap 4h, Sturm-Verdacht >10 HB/min für die Lebendigkeit) ist laut Klärung eine **Folgekarte an architect** und nicht Teil dieser Karte; die Default-Cap-Entscheidung ist im Board-Protokoll der Klärung als übernommen vermerkt.
