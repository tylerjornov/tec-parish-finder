#!/bin/bash
# Double-click me. A menu of the project's scripts. Pick a number, follow the prompts.
# (Nothing here is hidden: each option runs the same command you would type in Terminal, shown above it.)
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"

pause() { printf '\n'; read -n 1 -s -r -p "Press any key to return to the menu."; printf '\n'; }

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "'$1' is not installed. $2"; return 1; }
}

# Run a command, showing it first.
run() {
  printf '\n$ %s\n\n' "$*"
  "$@"
  local rc=$?
  printf '\n(finished, exit code %d)\n' "$rc"
  return $rc
}

# File picker in the same style as parish-webcrawler/run.command. Prints the chosen path(s), one per line.
pick() {
  local prompt="$1"; shift
  osascript \
    -e "set fs to choose file with prompt \"$prompt\" of type {\"json\", \"public.json\"} with multiple selections allowed default location (POSIX file \"$ROOT/data\")" \
    -e 'set o to ""' \
    -e 'repeat with f in fs' \
    -e 'set o to o & POSIX path of f & linefeed' \
    -e 'end repeat' \
    -e 'return o' 2>/dev/null
}

yes_no() { local a; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }

menu() {
  clear 2>/dev/null
  cat <<'MENU'
Tools for the parish finder

  1) Check formatting in the data files          (normalize_data.py --check)
  2) Fix formatting in the data files            (normalize_data.py)
  3) Import a parish JSON file into parishes.json (import_parishes.py)
  4) Find coordinates for one address             (geocode_address.py)
  5) Find coordinates for records in a file       (geocode_address.py --file)
  6) Rebuild the diocesan boundaries              (diocese-boundaries: npm run build)
  7) Check the diocesan boundaries                (diocese-boundaries: npm run check)
  8) Check parish websites                        (parish-webcrawler/run.command)
  q) Quit

MENU
  read -r -p "Choose: " CHOICE
}

while true; do
  menu
  case "$CHOICE" in
    1)
      need python3 "Install Python 3 from https://www.python.org/downloads/" || { pause; continue; }
      run python3 tools/normalize_data.py --check
      pause ;;
    2)
      need python3 "Install Python 3 from https://www.python.org/downloads/" || { pause; continue; }
      echo "This rewrites data/parishes.json and every file in data/wip-dioceses/ where a format rule changes something."
      if yes_no "Go ahead?"; then run python3 tools/normalize_data.py; fi
      pause ;;
    3)
      need python3 "Install Python 3 from https://www.python.org/downloads/" || { pause; continue; }
      FILES=()
      while IFS= read -r line; do [ -n "$line" ] && FILES+=("$line"); done < <(pick "Choose the parish JSON file(s) to import")
      if [ ${#FILES[@]} -eq 0 ]; then echo "No file chosen."; pause; continue; fi
      run python3 tools/import_parishes.py "${FILES[@]}" --dry-run
      if yes_no "Import these file(s) into data/parishes.json for real?"; then
        run python3 tools/import_parishes.py "${FILES[@]}"
      else
        echo "Not imported."
      fi
      pause ;;
    4)
      need python3 "Install Python 3 from https://www.python.org/downloads/" || { pause; continue; }
      read -r -p "Address (e.g. 3430 Old US Highway 70, Cleveland, NC 27013): " ADDR
      if [ -z "$ADDR" ]; then echo "No address given."; pause; continue; fi
      run python3 tools/geocode_address.py "$ADDR"
      pause ;;
    5)
      need python3 "Install Python 3 from https://www.python.org/downloads/" || { pause; continue; }
      FILE=$(pick "Choose the diocese JSON file to geocode")
      if [ -z "$FILE" ]; then echo "No file chosen."; pause; continue; fi
      read -r -p "Only one record? Paste its id, or press Return for every record in the file: " ID
      if [ -n "$ID" ]; then ID_ARGS=(--id "$ID"); else ID_ARGS=(); fi
      run python3 tools/geocode_address.py --file "$FILE" "${ID_ARGS[@]}"
      if yes_no "Save these coordinates into the file?"; then
        run python3 tools/geocode_address.py --file "$FILE" "${ID_ARGS[@]}" --write
      else
        echo "Not saved."
      fi
      pause ;;
    6)
      need node "Install Node.js from https://nodejs.org/ first." || { pause; continue; }
      cd "$ROOT/tools/diocese-boundaries" || { pause; continue; }
      [ -d node_modules ] || run npm install
      run npm run build
      cd "$ROOT" || exit 1
      pause ;;
    7)
      need node "Install Node.js from https://nodejs.org/ first." || { pause; continue; }
      cd "$ROOT/tools/diocese-boundaries" || { pause; continue; }
      [ -d node_modules ] || run npm install
      run npm run check
      cd "$ROOT" || exit 1
      pause ;;
    8)
      run "$ROOT/parish-webcrawler/run.command"
      pause ;;
    q|Q)
      exit 0 ;;
    *)
      echo "Not an option."; pause ;;
  esac
done
