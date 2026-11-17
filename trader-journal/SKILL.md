---
name: trader-journal
description: >-
  Trading-Tagebuch in Euro. Erfasst jeden Kauf und Verkauf, den der Nutzer
  selbst (z. B. in Scalable Capital) ausgeführt und ausdrücklich gemeldet hat,
  mit Stückzahl, Kurs, Gebühren und Strategie (kurzfristig oder spekulativ),
  und speichert ihn sofort per Commit und Push im PRIVATEN Repository des
  Nutzers, nie im öffentlichen. Bestätigt eine Speicherung erst nach
  erfolgreichem Push, erkennt Git-Konflikte, nutzt nie Force-Push. Rechnet
  realisierte Gewinne und Verluste nach FIFO, offene Positionen, Gebühren und
  freies Kapital des 500-Euro-Budgets, getrennt je Strategie, und schreibt
  deutsche Auswertungen. Wird beim Sitzungsstart automatisch geladen. Use when
  the user says "ich habe gekauft", "ich habe verkauft", "Kauf ausgeführt",
  "trag den Trade ein", "Tagebuch", "wie viel Budget ist frei", "meine
  Positionen", "Auswertung", "Gewinn und Verlust", "Gebühren", "Storno".
argument-hint: "[status | kauf | verkauf | bericht | liste | storno | pruefen | sichern]"
---

# trader-journal

Das Trading-Tagebuch hält fest, was der Nutzer tatsächlich gehandelt hat. Es
platziert keine Orders und hat keine Brokeranbindung. Die Daten liegen
ausschließlich im privaten Repository (Umgebungsvariable
`TRADER_JOURNAL_REPO`, geklont nach `../trading-journal`); der Ordner
`trader/` dieses Projekts ist ein Symlink dorthin und per `.gitignore` vom
öffentlichen Repository ausgeschlossen.

`${CLAUDE_SKILL_DIR}` ist der Ordner dieser SKILL.md.

```bash
python3 "${CLAUDE_SKILL_DIR}/journal.py" status
python3 "${CLAUDE_SKILL_DIR}/journal.py" kauf --strategie kurzfristig --symbol AAPL \
    --name "Apple Inc." --isin US0378331005 --stueck 1 --kurs 318,18 --gebuehren 0,99 \
    --stop 315,00 --ziel 324,55 --zeit "2026-11-17 16:15" --handelsplatz gettex
python3 "${CLAUDE_SKILL_DIR}/journal.py" verkauf --strategie kurzfristig --symbol AAPL \
    --stueck 1 --kurs 315,00 --gebuehren 0,99 --zeit "2026-11-17 16:40"
python3 "${CLAUDE_SKILL_DIR}/journal.py" bericht                       # beide Strategien
python3 "${CLAUDE_SKILL_DIR}/journal.py" bericht --strategie spekulativ --kurs XYZ=12,34
python3 "${CLAUDE_SKILL_DIR}/journal.py" bericht --datei berichte/2026-11.md
python3 "${CLAUDE_SKILL_DIR}/journal.py" liste --anzahl 20
python3 "${CLAUDE_SKILL_DIR}/journal.py" storno --id ID --grund "falsche Stückzahl"
python3 "${CLAUDE_SKILL_DIR}/journal.py" pruefen
python3 "${CLAUDE_SKILL_DIR}/journal.py" sichern
```

Beträge in Euro, deutsche Schreibweise erlaubt (`318,18`). `--zeit` ist die
Ausführungszeit in deutscher Zeit (Standard: jetzt, MEZ/MESZ automatisch).
`--fiktiv` markiert Testeinträge; sie zählen in Auswertungen nicht mit (außer
mit `bericht --mit-fiktiven`).

## Was es liest und schreibt

| Datei (im privaten Repository) | Inhalt |
| --- | --- |
| `journal/ausfuehrungen.jsonl` | eine Zeile je Kauf, Verkauf oder Storno; nur anhängen |
| `journal/einstellungen.json` | `startkapital_eur` (500), `gewinne_erhoehen_budget` (false) |
| `trader/` | Arbeitsdateien der anderen Skills (über den Symlink) |
| `berichte/` | mit `bericht --datei` gespeicherte Auswertungen |

## Regeln

1. **Nur echte, gemeldete Ausführungen.** Einen Kauf oder Verkauf erst
   erfassen, wenn der Nutzer ausdrücklich sagt, dass die Order in Scalable
   Capital ausgeführt wurde, mit Stückzahl, Kurs und Gebühren. Fehlt etwas,
   nachfragen, nicht schätzen. Strategie immer angeben.
2. **Gespeichert heißt gepusht.** Erst nach der Zeile `Gespeichert ✓ auf
   GitHub (Commit ...)` dem Nutzer die Speicherung bestätigen. Exit 4 heißt:
   nur lokal, geht bei Sitzungsende verloren; sofort `sichern` wiederholen und
   den Nutzer warnen.
3. **Konflikt (Exit 5).** Nichts überschreiben, kein Force-Push, kein
   `reset --hard`. Den lokalen Commit und den Stand auf GitHub zeigen und
   gemeinsam mit dem Nutzer lösen.
4. **Nicht geladen (Exit 3).** Keine Positionen, Guthaben oder Trades
   annehmen oder erfinden. Erst das Tagebuch laden (siehe unten).
5. **Budget.** Ein Kauf über dem freien Kapital wird abgelehnt. Nur wenn der
   Nutzer bestätigt, dass er bereits ausgeführt ist, mit
   `--ueber-budget-bestaetigt` erfassen (das Tagebuch zeigt die Wirklichkeit)
   und deutlich warnen.
6. **Korrekturen** nur als `storno`, nie Zeilen löschen oder ändern.
7. Ergebnisse auf Deutsch zusammenfassen, Rechnung zeigen, keine
   Anlageberatung. Bei spekulativen Positionen das Totalverlustrisiko nennen.

## Rechnung

FIFO je Strategie und Wertpapier: zuerst gekaufte Stücke gelten als zuerst
verkauft. Kaufgebühren erhöhen den Einstand, Verkaufsgebühren mindern den
Erlös. Kurzfristige und spekulative Bestände werden nie miteinander
verrechnet: ein Verkauf in der falschen Strategie wird abgelehnt.

- **Gebunden**: Einstand aller offenen Positionen inklusive Kaufgebühren.
- **Frei**: Startkapital plus realisierte Verluste (Gewinne nur, wenn
  `gewinne_erhoehen_budget` true ist) minus Gebunden. Das ist die Obergrenze
  für den nächsten Kauf, über beide Strategien zusammen.
- **R** (kurzfristig, wenn ein Stop erfasst wurde): Ergebnis nach Gebühren
  geteilt durch das geplante Risiko bis zum Stop.

Steuern sind nicht berücksichtigt.

## Laden und Speichern

- **Sitzungsstart**: `.claude/hooks/session-start.sh` ruft `journal.py start`
  auf: klont oder aktualisiert das private Repository, verlinkt `trader/`,
  setzt `TRADER_JOURNAL_DIR` und gibt den Stand aus. Schlägt das fehl, steht
  `TAGEBUCH NICHT GELADEN` im Sitzungskontext. Dann `add_repo` für das
  Repository aus `TRADER_JOURNAL_REPO` (Zugriff push) aufrufen und
  `python3 trader-journal/journal.py start` erneut ausführen.
- **Jede Eintragung** wird sofort committet und gepusht (bis zu 4 Versuche,
  vorher `fetch` und `rebase`; `.gitattributes` mit `merge=union` behält
  gleichzeitig angehängte Zeilen beider Sitzungen; danach wird die Datei
  geprüft).
- **Stop-Hook**: `.claude/hooks/stop.sh` ruft nach jeder Antwort
  `journal.py sichern --hook` auf und pusht Arbeitsstände der Skills.
- **Unerwartetes Sitzungsende**: Gesichert ist, was gepusht wurde. Höchstens
  eine Eintragung, deren Push gerade lief, kann fehlen. Darum gilt Regel 2,
  und Abweichungen lassen sich mit der Transaktionshistorie in Scalable
  Capital abgleichen.

## Beispiel

```
Kauf erfasst: Kauf 1 Stück AAPL zu 318,18 €, Gebühren 0,99 €, Strategie kurzfristig, Zeit 2026-11-17 16:15 MEZ.
Frei für neue Käufe: 180,83 €.
Gespeichert ✓ auf GitHub (Commit 1a2b3c4, id 20261117-151500-a1b2c3).
```

Nur ein Tagebuch: keine Orders, keine Brokeranbindung, keine Anlageberatung.
