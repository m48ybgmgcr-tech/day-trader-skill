# CLAUDE.md – Vorgaben für dieses Projekt

Dieses Repository ist ein Fork des Day-Trader-Skill-Pakets. Die elf Skills
liegen unter `.claude/skills/trader-*` und werden automatisch ausgewählt und
kombiniert. Dazu kommt im Fork `trader-journal` (Trading-Tagebuch, siehe unten). Die Originalordner `trader-*/` im Wurzelverzeichnis sind die
Quelle, gegen die die Tests in `tests/` laufen. Ändert sich dort etwas, die
Kopien unter `.claude/skills/` mit aktualisieren.

## Sprache und Stil

- Alle Antworten und Aktienanalysen ausschließlich auf **Deutsch**.
- Verständlich erklären: Fachbegriffe (z. B. Opening Range, Stop-Loss, R,
  Chance-Risiko-Verhältnis) beim ersten Auftreten kurz in einfachen Worten
  erläutern.
- Zahlen nachvollziehbar zeigen: Einstieg, Stop, Ziel, Stückzahl, maximaler
  Verlust in Euro, dazu die Rechnung.
- Die Skripte geben englischen Text aus. Ergebnisse immer auf Deutsch
  zusammenfassen, nicht roh weiterreichen.
- Uhrzeiten der US-Börse (Eastern Time) zusätzlich in deutscher Zeit
  (MEZ/MESZ) angeben.
- Keine Anlageberatung: Ergebnisse sind Analysen und Rechnungen, keine
  Empfehlungen. Unsicherheiten und Risiken offen benennen.

## Die zwei Strategien

1. **Kurzfristiges Trading** (Intraday bis wenige Tage)
   Werkzeuge: `trader-scanner` → `trader-news` → `trader-entry` →
   `trader-exit` → `trader-timemachine` → `trader-budget` → `trader-risk`,
   dazu `trader-alerts` für Stop, Ziel und Glattstellung.
   Jeder Trade braucht vor dem Einstieg einen festen Stop und ein Ziel.

2. **Spekulative Aktien mit größerem Kurspotenzial** (längere Haltedauer)
   Werkzeuge: `trader-scanner daily`, `trader-news`, `trader-analyst`
   (Quartalszahlen), `trader-pelosi` (Kongress-Meldungen) als Ideenquelle,
   `trader-budget` und `trader-risk` für Größe und Limits.
   `trader-entry`, `trader-exit` und `trader-timemachine` sind für Intraday
   gebaut. Bei längerer Haltedauer darauf hinweisen, dass ihre Ergebnisse
   nur eingeschränkt passen.
   Spekulativ heißt: Ein Totalverlust der Position ist möglich und muss
   ausdrücklich genannt werden.

Bei jeder Analyse angeben, zu welcher der beiden Strategien sie gehört.

## Budget

- Das gesamte Spielbudget beträgt **500 €**, für beide Strategien zusammen.
  Es wird nie überschritten, auch nicht durch Nachkaufen.
- Kein Hebel, keine Optionen, keine Leerverkäufe, kein Margin, keine
  Derivate/CFDs/Knock-outs.
- Die Skripte rechnen in US-Dollar (Kurse von Yahoo Finance). Beträge immer
  in Euro umrechnen und den verwendeten Wechselkurs nennen. Im Zweifel
  vorsichtig rechnen, sodass das Euro-Budget sicher eingehalten wird.
- Ordergebühren und Spreads beim Broker sind bei kleinen Beträgen spürbar und
  in die Rechnung einzubeziehen bzw. als offener Punkt zu nennen.

## Ausführung: nur manuell über Scalable Capital

- Alle Trades führt der Nutzer **selbst und manuell** in Scalable Capital aus.
- **Keine automatische Brokeranbindung.** Keinen Broker-Connector oder
  MCP-Server einrichten oder aufrufen (kein Alpaca, kein Interactive
  Brokers, keine sonstige Order-Schnittstelle).
- **Keine echten Orders** – Claude platziert nie eine Order, auch nicht im
  Papierhandel und auch nicht nach einem „ja“.
- `trader-broker` nur verwenden, um ein Order-Ticket zur manuellen Eingabe
  anzuzeigen (`ticket.py show`) und vom Nutzer gemeldete Ausführungen zu
  protokollieren (`ticket.py fill`), damit `trader-risk` das Tageslimit
  verfolgen kann. Die Teile des Skills zum Einrichten von Brokern und zum
  Senden von Orders werden ignoriert.
- Ein Ticket so formulieren, dass es direkt in Scalable Capital übertragbar
  ist: Wertpapier (Name und, wenn bekannt, ISIN), Kauf/Verkauf, Stückzahl,
  Ordertyp, Limit- bzw. Stop-Preis in Euro. Darauf hinweisen, den Kurs in
  der App vor dem Abschicken zu prüfen.

## Technik

- Python 3.8+ und nur die Standardbibliothek; nichts installieren.
- Tests: `python3 -m unittest discover -s tests`
- Arbeitsdaten der Skills landen in `trader/`. Persönliche Daten (Trades,
  E-Mail für SEC) nicht ungefragt committen: das Repository ist öffentlich.
- Netzwerkfreigaben der Umgebung nie ohne Zustimmung des Nutzers ändern.

## Trading-Tagebuch (privat)

- Echte Trades, Einstellungen und Arbeitsstände liegen **nur** im privaten
  Repository aus der Umgebungsvariable `TRADER_JOURNAL_REPO`. Der
  SessionStart-Hook klont es nach `../trading-journal` und verlinkt `trader/`
  dorthin. `trader` ist hier per `.gitignore` ausgeschlossen; nie mit
  `git add -f` erzwingen.
- Zu Beginn jeder Sitzung die Hook-Ausgabe „Trading-Tagebuch (privat)“
  beachten. Steht dort `TAGEBUCH NICHT GELADEN`: keine Positionen, Guthaben
  oder Trades annehmen oder erfinden. `add_repo` für das Repository aus
  `TRADER_JOURNAL_REPO` mit Zugriff push aufrufen, dann
  `python3 trader-journal/journal.py start` ausführen. Klappt das nicht, dem
  Nutzer die Fehlermeldung zeigen.
- Kauf oder Verkauf nur mit `trader-journal` erfassen, und nur wenn der
  Nutzer ausdrücklich meldet, dass er die Order ausgeführt hat (Stückzahl,
  Kurs in Euro, Gebühren, Strategie). Fehlende Angaben erfragen.
- Eine Speicherung erst nach `Gespeichert ✓ auf GitHub` bestätigen. Bei
  Exit-Code 4 (nur lokal) oder 5 (Konflikt) den Nutzer sofort warnen. Nie
  Force-Push, nie `reset --hard`, nie Zeilen des Tagebuchs löschen oder
  ändern; Korrekturen nur per `storno`.
- Testdaten nur mit `--fiktiv` und nie auf dem Zweig `main` des privaten
  Repositorys.
- Das freie Kapital aus `journal.py status` ist die Obergrenze für neue Käufe
  über beide Strategien (Budget 500 €).
