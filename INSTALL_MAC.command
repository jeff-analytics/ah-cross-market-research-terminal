#!/bin/zsh
set -u
cd "$(dirname "$0")"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
LOG="$PWD/install_mac.log"
: > "$LOG"
echo "============================================================"
echo "A/H Cross-Market Research Terminal v5.1.8 - macOS installer"
echo "============================================================"
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 was not found. Install Python 3.10+ first."
  read "?Press Enter to close..."; exit 1
fi
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || { echo "ERROR: Python 3.10+ is required."; read "?Press Enter to close..."; exit 1; }
if [ ! -x .venv/bin/python ]; then echo "[1/4] Creating virtual environment..."; python3 -m venv .venv >>"$LOG" 2>&1 || exit 1; else echo "[1/4] Existing virtual environment found."; fi
PY="$PWD/.venv/bin/python"
echo "[2/4] Updating pip..."; "$PY" -m pip install --upgrade pip >>"$LOG" 2>&1 || { tail -40 "$LOG"; read "?Press Enter to close..."; exit 1; }
echo "[3/4] Installing dependencies..."; "$PY" -m pip install -r requirements.txt >>"$LOG" 2>&1 || { tail -40 "$LOG"; read "?Press Enter to close..."; exit 1; }
echo "[4/4] Initializing local live database..."; "$PY" scripts/init_live_db.py >>"$LOG" 2>&1 || { tail -40 "$LOG"; read "?Press Enter to close..."; exit 1; }
echo; echo "Installation completed. Run START_TERMINAL.command."; read "?Press Enter to close..."
