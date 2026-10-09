#!/bin/bash
# =====================================================================================================
#  run.command  -  double-click me. Choose the parish JSON file; the results open in Excel when done.
#  (First run sets everything up. From Terminal you can also add:  --only NAME   or   --fresh)
# =====================================================================================================

cd "$(dirname "$0")" || { echo "Could not open the project folder."; read -n 1 -s -r -p "Press any key to close."; exit 1; }
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"

say()  { printf '%s\n' "$1"; }
wait_for_key() { printf '\n'; read -n 1 -s -r -p "Press any key to close this window."; printf '\n'; }
stop_here() { printf '\n%s\n' "$1"; wait_for_key; exit "${2:-1}"; }

trap 'stop_here "Stopped. Progress is saved; double-click run.command again to continue." 130' INT

say "================  Parish Info Check  ================"

# ---- 1. Python + libraries (set up once) ------------------------------------------------------------
command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
  || stop_here "Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/ , then run this again."
if [ ! -x "$PY" ]; then
  say "First run: setting up (a few minutes)..."
  python3 -m venv "$ROOT/.venv" || stop_here "Could not set up Python. Reinstall Python from python.org and try again."
fi
REQ_SUM="$(shasum "$ROOT/requirements.txt" | awk '{print $1}')"
if [ "$(cat "$ROOT/.venv/.requirements_ok" 2>/dev/null)" != "$REQ_SUM" ]; then
  say "Installing libraries (one time)..."
  "$PY" -m pip install --disable-pip-version-check -q --upgrade pip
  "$PY" -m pip install --disable-pip-version-check -q -r "$ROOT/requirements.txt" \
    && echo "$REQ_SUM" > "$ROOT/.venv/.requirements_ok" \
    || stop_here "Installing libraries failed. Check your internet connection and try again."
fi
if [ ! -f "$ROOT/.venv/.chromium_ok" ]; then
  say "Installing the helper browser (one time, about 100 MB)..."
  "$PY" -m playwright install chromium >/dev/null 2>&1 && touch "$ROOT/.venv/.chromium_ok" \
    || say "(Helper browser not installed; a few JavaScript-only sites may come back empty.)"
fi

# ---- 2. Ollama (the AI) running, with the model downloaded ------------------------------------------
MODEL="$("$PY" -c 'import yaml; print((yaml.safe_load(open("config.yaml")) or {}).get("model") or "qwen2.5:3b")' 2>/dev/null)"
[ -z "$MODEL" ] && stop_here "config.yaml has a typing mistake. Undo your last change to it and run this again."
ollama_up() { curl -s -m 3 http://localhost:11434/api/tags >/dev/null 2>&1; }
if ! ollama_up; then
  open -a Ollama >/dev/null 2>&1
  for _ in $(seq 1 15); do ollama_up && break; sleep 2; done
fi
ollama_up || stop_here "Ollama is not running. Install it from https://ollama.com/download (or open the Ollama app), then run this again."
has_model() {
  curl -s -m 5 http://localhost:11434/api/tags | "$PY" -c 'import json, sys; w = sys.argv[1]
names = [m.get("name", "") for m in json.load(sys.stdin).get("models", [])]
sys.exit(0 if w in names or w + ":latest" in names else 1)' "$MODEL"
}
if ! has_model; then
  OLLAMA_BIN="$(command -v ollama || ls /opt/homebrew/bin/ollama /usr/local/bin/ollama /Applications/Ollama.app/Contents/Resources/ollama 2>/dev/null | head -1)"
  [ -z "$OLLAMA_BIN" ] && stop_here "Could not find Ollama. Open Terminal, type:  ollama pull $MODEL  then run this again."
  say "Downloading the AI model (one time, about 2 GB)..."
  "$OLLAMA_BIN" pull "$MODEL" || stop_here "The model download failed. Check your internet connection and try again."
fi

# ---- 3. Choose the parish file ------------------------------------------------------------------------
START_DIR="$ROOT"; [ -d "$ROOT/../data" ] && START_DIR="$(cd "$ROOT/../data" && pwd)"
JSON="$(osascript -e "POSIX path of (choose file with prompt \"Choose the parish JSON file to check\" of type {\"public.json\"} default location POSIX file \"$START_DIR\")" 2>/dev/null)"
[ -z "$JSON" ] && stop_here "No file chosen, so nothing was run." 0
say "File: $JSON"
say ""

# ---- 4. Run, then open the results ----------------------------------------------------------------------
"$PY" "$ROOT/scraper.py" --input "$JSON" "$@"
RC=$?
[ $RC -eq 130 ] && stop_here "Double-click run.command again to continue where it left off." 130
[ $RC -ne 0 ] && stop_here "The run stopped with a problem (see above). Details: output/.work/run.log" $RC

RESULT="$(ls -t "$ROOT/output/Parish Check Results"*.xlsx 2>/dev/null | head -1)"
[ -n "$RESULT" ] && open "$RESULT"
wait_for_key
exit 0
