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

  1) Fix formatting in chosen data files         (normalize_data.py FILE ...)
  2) Import a parish JSON file into parishes.json (import_parishes.py)
  3) Make the diocesan boundaries match the parish pins (diocese-boundaries: npm run build, then npm run check)
  q) Quit

MENU
  read -r -p "Choose: " CHOICE
}

while true; do
  menu
  case "$CHOICE" in
    1)
      need python3 "Install Python 3 from https://www.python.org/downloads/" || { pause; continue; }
      FILES=()
      while IFS= read -r line; do [ -n "$line" ] && FILES+=("$line"); done < <(pick "Choose the JSON file(s) to fix")
      if [ ${#FILES[@]} -eq 0 ]; then echo "No file chosen."; pause; continue; fi
      echo "This rewrites these file(s) wherever a format rule changes something:"
      printf '  %s\n' "${FILES[@]}"
      if yes_no "Go ahead?"; then run python3 tools/normalize_data.py "${FILES[@]}"; else echo "Nothing changed."; fi
      pause ;;
    2)
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
    3)
      need node "Install Node.js from https://nodejs.org/ first." || { pause; continue; }
      cd "$ROOT/tools/diocese-boundaries" || { pause; continue; }
      [ -d node_modules ] || run npm install
      # Redraw from dioceses.json first, so the check looks at the overlay as it now stands.
      if run npm run build; then
        run npm run check
      else
        echo "Build failed, so the check was not run."
      fi
      cd "$ROOT" || exit 1
      pause ;;
    q|Q)
      exit 0 ;;
    *)
      echo "Not an option."; pause ;;
  esac
done
