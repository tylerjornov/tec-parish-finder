#!/bin/bash
# Double-click me. Asks which parish JSON file(s) to check, checks them against the parish websites, opens the results.
# (From Terminal you can name the files instead:  ./run.command path/to/a.json path/to/b.json)
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

# Which files to check: ones named on the command line, else a file picker (never assumes the live parishes.json).
FILES=("$@")
if [ ${#FILES[@]} -eq 0 ]; then
  PICKED=$(osascript \
    -e 'set fs to choose file with prompt "Choose the parish JSON file(s) to check" of type {"json", "public.json"} with multiple selections allowed default location (POSIX file "'"$(cd .. && pwd)"'")' \
    -e 'set o to ""' \
    -e 'repeat with f in fs' \
    -e 'set o to o & POSIX path of f & linefeed' \
    -e 'end repeat' \
    -e 'return o' 2>/dev/null)
  while IFS= read -r line; do [ -n "$line" ] && FILES+=("$line"); done <<< "$PICKED"
  [ ${#FILES[@]} -gt 0 ] || { echo "No file chosen. Nothing was checked."; finish 0; }
fi
for f in "${FILES[@]}"; do echo "Checking: $f"; done

"$PY" parish_check.py "${FILES[@]}"
RC=$?
if [ $RC -eq 0 ]; then
  OPEN=()
  for f in "${FILES[@]}"; do
    DIR=$("$PY" parish_check.py --where "$f")
    PREFIX=""
    [ "$(basename "$DIR")" != "output" ] && PREFIX="$(basename "$DIR") - "
    OPEN+=("$DIR/${PREFIX}Corrections.xlsx" "$DIR/${PREFIX}Website Problems.xlsx")
  done
  open "${OPEN[@]}"
fi
finish $RC
