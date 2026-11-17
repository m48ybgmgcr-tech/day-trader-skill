#!/bin/bash
# SessionStart: laedt das PRIVATE Trading-Tagebuch (TRADER_JOURNAL_REPO) nach
# ../trading-journal und verlinkt ./trader dorthin. Enthaelt keine Handelsdaten.
# Laeuft nur in Claude-Code-Cloud-Sitzungen. Endet immer mit 0, Fehler stehen im Text.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
python3 trader-journal/journal.py start || echo "TAGEBUCH NICHT GELADEN: journal.py start ist abgebrochen."
exit 0
