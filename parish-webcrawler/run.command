#!/bin/bash
# Double-click me. Checks ../data/parishes.json against the parish websites and opens the results.
# (From Terminal you can name another file:  ./run.command path/to/file.json)
cd "$(dirname "$0")" || exit 1
PY=".venv/bin/python"
LIBS="httpx>=0.27 beautifulsoup4>=4.12 openpyxl>=3.1"
finish() { printf '\n'; read -n 1 -s -r -p "Press any key to close this window."; printf '\n'; exit "$1"; }

command -v python3 >/dev/null 2>&1 || { echo "Install Python 3.10 or newer from https://www.python.org/downloads/ first."; finish 1; }
[ -x "$PY" ] || { echo "First run: setting up (a minute)..."; python3 -m venv .venv || finish 1; }
if [ "$(cat .venv/.libs 2>/dev/null)" != "$LIBS" ]; then
  "$PY" -m pip install -q --disable-pip-version-check $LIBS && echo "$LIBS" > .venv/.libs \
    || { echo "Installing libraries failed. Check your internet connection."; finish 1; }
fi

"$PY" parish_check.py "${1:-../data/parishes.json}"
RC=$?
[ $RC -eq 0 ] && open "output/Corrections.xlsx" "output/Website Problems.xlsx"
finish $RC
