#!/bin/bash
# =====================================================================================================
#  run.command  -  double-click me.
#  Sets everything up the first time, checks the AI (Ollama), shows a dry-run summary, asks before it
#  starts, runs, then opens the results folder.   (You can also add options:  ./run.command --only NAME)
# =====================================================================================================

# Always work from the folder this file lives in (works even when the folder name has spaces).
cd "$(dirname "$0")" || { echo "Could not open the project folder."; read -n 1 -s -r -p "Press any key to close."; exit 1; }
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"

say()  { printf '\n%s\n' "$1"; }
wait_for_key() { printf '\n'; read -n 1 -s -r -p "Press any key to close this window."; printf '\n'; }
stop_here() { say "$1"; wait_for_key; exit "${2:-1}"; }

trap 'say "Stopped by you. Progress is saved; double-click run.command again to continue."; wait_for_key; exit 130' INT

say "================  Parish Info Check  ================"

# ---- 1. Python 3 -------------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  stop_here "Python 3 is not installed on this Mac.
  Fix: go to https://www.python.org/downloads/ , download the macOS installer, open it, click
  through it, then double-click run.command again.
  (Another way: open Terminal and type   xcode-select --install   and press Return.)"
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  stop_here "Your Python is too old (this tool needs Python 3.9 or newer).
  Fix: install the newest Python from https://www.python.org/downloads/ and run this again."
fi

# ---- 2. Private Python environment + libraries (first run only) -----------------------------------
if [ ! -x "$PY" ]; then
  say "First run: creating a private Python environment (one time, about a minute)..."
  python3 -m venv "$ROOT/.venv" || stop_here "Could not create the Python environment. Make sure Python 3 installed correctly."
fi
REQ_SUM="$(shasum "$ROOT/requirements.txt" | awk '{print $1}')"
if [ ! -f "$ROOT/.venv/.requirements_ok" ] || [ "$(cat "$ROOT/.venv/.requirements_ok")" != "$REQ_SUM" ]; then
  say "Installing the libraries this tool needs (one time, a few minutes)..."
  "$PY" -m pip install --disable-pip-version-check -q --upgrade pip
  if "$PY" -m pip install --disable-pip-version-check -q -r "$ROOT/requirements.txt"; then
    echo "$REQ_SUM" > "$ROOT/.venv/.requirements_ok"
  else
    stop_here "Installing the libraries failed. Check your internet connection and try again."
  fi
fi

# ---- 3. Helper browser for JavaScript-heavy pages (first run only) ----------------------------------
if [ ! -f "$ROOT/.venv/.chromium_ok" ]; then
  say "Installing the small helper browser (one time, about 100 MB)..."
  if "$PY" -m playwright install chromium; then
    touch "$ROOT/.venv/.chromium_ok"
  else
    say "Note: the helper browser could not be installed. The tool still works, but a few JavaScript-only
websites may come back empty. You can try again later by double-clicking run.command."
  fi
fi

# ---- 4. Settings we need from config.yaml ---------------------------------------------------------
read_setting() {   # prints the value of a top-level setting from config.yaml
  "$PY" - "$1" <<'PYEOF'
import sys, yaml
try:
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8")) or {}
except Exception as exc:
    print("ERROR: config.yaml cannot be read: %s" % exc, file=sys.stderr); sys.exit(3)
print(cfg.get(sys.argv[1], ""))
PYEOF
}
MODEL="$(read_setting model)"        || stop_here "config.yaml has a typing mistake (see the message above). Fix it and run again."
INPUT_JSON="$(read_setting input_json)"
OUTPUT_DIR="$(read_setting output_dir)"
[ -z "$MODEL" ] && MODEL="qwen2.5:3b"
[ -z "$INPUT_JSON" ] && INPUT_JSON="parishes.json"
[ -z "$OUTPUT_DIR" ] && OUTPUT_DIR="output"
case "$INPUT_JSON" in /*) INPUT_PATH="$INPUT_JSON" ;; *) INPUT_PATH="$ROOT/$INPUT_JSON" ;; esac
case "$OUTPUT_DIR" in /*) OUTPUT_PATH="$OUTPUT_DIR" ;; *) OUTPUT_PATH="$ROOT/$OUTPUT_DIR" ;; esac

# ---- 5. Is Ollama (the AI) running? -----------------------------------------------------------------
ollama_up() { curl -s -m 3 http://localhost:11434/api/tags >/dev/null 2>&1; }
if ! ollama_up; then
  say "Ollama (the AI program) is not running. Trying to start it..."
  open -a Ollama >/dev/null 2>&1
  for i in $(seq 1 15); do
    ollama_up && break
    sleep 2
  done
fi
if ! ollama_up; then
  stop_here "I could not reach Ollama.
  1. If you have not installed it: download it from https://ollama.com/download , open it once.
  2. If it is installed: open the Ollama app yourself (look for the llama icon in the menu bar at the top
     of the screen), wait a few seconds,
  3. then double-click run.command again."
fi

# ---- 6. Is the model downloaded? ------------------------------------------------------------------------
OLLAMA_BIN="$(command -v ollama || true)"
[ -z "$OLLAMA_BIN" ] && [ -x /usr/local/bin/ollama ] && OLLAMA_BIN=/usr/local/bin/ollama
[ -z "$OLLAMA_BIN" ] && [ -x /opt/homebrew/bin/ollama ] && OLLAMA_BIN=/opt/homebrew/bin/ollama
[ -z "$OLLAMA_BIN" ] && [ -x /Applications/Ollama.app/Contents/Resources/ollama ] && OLLAMA_BIN=/Applications/Ollama.app/Contents/Resources/ollama

has_model() {
  curl -s -m 5 http://localhost:11434/api/tags | "$PY" -c '
import json, sys
want = sys.argv[1]
names = [m.get("name", "") for m in json.load(sys.stdin).get("models", [])]
sys.exit(0 if any(n == want or n == want + ":latest" for n in names) else 1)' "$MODEL"
}
if ! has_model; then
  say "The AI model '$MODEL' is not on this Mac yet. Downloading it (one time, about 2 GB)..."
  if [ -z "$OLLAMA_BIN" ]; then
    stop_here "I could not find the 'ollama' command. Open Terminal and type:   ollama pull $MODEL
  then double-click run.command again."
  fi
  "$OLLAMA_BIN" pull "$MODEL" || stop_here "The model download failed. Check your internet connection and try again."
fi

# ---- 7. Your data file --------------------------------------------------------------------------------
if [ ! -f "$INPUT_PATH" ]; then
  stop_here "I can't find your parish file. I looked here:
      $INPUT_PATH
  Open config.yaml (in this folder), fix the 'input_json' line so it points to your parishes.json,
  then double-click run.command again."
fi

# ---- 8. Dry run: show the plan, then ask ------------------------------------------------------------------
say "Step 1 of 2: a dry run. This lists what I would do and estimates the time. Nothing is extracted yet."
"$PY" "$ROOT/scraper.py" --config "$ROOT/config.yaml" --dry-run "$@"
RC=$?
if [ $RC -ne 0 ]; then
  stop_here "The dry run stopped with a problem (see the message above)." $RC
fi

printf '\nProceed with the full run? (y/n) '
read -r ANSWER
case "$ANSWER" in
  y|Y|yes|YES|Yes) ;;
  *) stop_here "OK - nothing was run. Double-click run.command whenever you are ready." 0 ;;
esac

# ---- 9. The real run ------------------------------------------------------------------------------------------
say "Step 2 of 2: the full run. You can press Ctrl+C at any time; progress is saved."
"$PY" "$ROOT/scraper.py" --config "$ROOT/config.yaml" "$@"
RC=$?

if [ $RC -eq 130 ]; then
  say "Stopped. Progress is saved; double-click run.command again to continue where it left off."
  wait_for_key
  exit 130
fi
if [ $RC -ne 0 ]; then
  say "The run ended with a problem (see the message above). Details are in: $OUTPUT_PATH/run.log"
  wait_for_key
  exit $RC
fi

# ---- 10. Show the results ----------------------------------------------------------------------------------
say "Finished! Opening the results folder. Start with  needs_review.csv"
open "$OUTPUT_PATH" >/dev/null 2>&1
wait_for_key
exit 0
