# QA-Notiz: Messregel Board-Deckel — M4 zu t_673

**Datum:** 2026-11-22
**Task:** t_024da4f1 (QA-Profil: Quirin QA)
**Bezug:** t_673 Design-Review · Befund B4 · Maßnahme M4
**Design-Review:** `docs/reviews/qa/2026-10-29-t_673-guardrail-hard-stop-design-review.md`
  (KijanPersonalTracker-Repo, Branch dev; Commit 621000aa0)

---

## Messregel für den Board-Deckel

**Regel:** Mess-Tasks erhalten Priorität 999/1000 und warten auf einen freien Slot;
`kanban.max_in_progress` wird **nicht** geändert.

**Kontext (B4):** Im Nachweis-Lauf für den Guardrail-Hard-Stop (t_7a6211ae) war der
Board-Deckel (`kanban.max_in_progress`) voll; der Test-Task wartete ~11 min auf einen
Slot. Dies ist kein Defekt — die Konsequenz ist, dass künftige Messläufe Priorität
setzen und den Slot abwarten müssen.

**Umsetzung:** Kein Code. Reine Konfigurations-/Prozess-Notiz.

---

## kanban.max_in_progress — unveränderter Wert

| Eigenschaft | Wert / Quelle |
|---|---|
| **Repo-Default** | `hermes_cli/config_defaults.py`, Zeile 1903: `"max_in_progress": None` |
| **Config-Auflösung** | `hermes_cli/kanban_db_dispatch.py:1877` → `resolve_max_in_progress()` |
| **Effective Default** | Memory-Derived: `derive_default_max_in_progress()` → FLOOR=2, CEILING=8 |
| **Runtime-Config (dieser Container)** | `/workspace/.hermes/config.yaml` enthält **keinen** `kanban`-Block → kein expliziter Override |
| **Operationale Beobachtung (t_673/B4)** | Deckel = 7 auf dem Gateway-Host zum Mess-Zeitpunkt (Betriebs-Konfiguration; aus dem Container nicht re-verifizierbar) |
| **Config-Änderung in diesem Task** | **Keine** |

Die Konfigurationsdatei `hermes_cli/config_defaults.py` (oder eine explizite
`kanban.max_in_progress`-Setzung in der Runtime-Konfiguration) bleibt unverändert.
Es gibt keinen Commit, der diesen Wert modifiziert.

---

## Warum keine Config-Änderung

- Die Beobachtung (Deckel voll) betraf ausschließlich den Mess-Host (isolierte Umgebung).
- Ein generisches Anheben des Deckels würde auf produktiven Hosts mehr Worker
  ermöglichen, die dort nicht nötig sind und Ressourcen verbrauchen.
- Mess-Tasks sind transient und können Priorität 999/1000 setzen; der Dispatcher
  bedient sie bevorzugsweise, sobald ein Slot frei wird.
- M2a/M2b (t_7dbbb2e8, t_e875e1ac) sind bereits auf `origin/dev` integriert
  und lösen das zugrunde liegende Guardrail-Halt-Problem, das den Messlauf
  verzögert hat.