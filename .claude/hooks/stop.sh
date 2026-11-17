#!/bin/bash
# Stop: pusht nach jeder Antwort ungesicherte Aenderungen im privaten Tagebuch.
# Still, wenn nichts zu tun ist; meldet ein Scheitern als systemMessage.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
python3 trader-journal/journal.py sichern --hook
exit 0
