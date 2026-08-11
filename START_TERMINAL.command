#!/bin/zsh
set -u
cd "$(dirname "$0")"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1
if [ ! -x .venv/bin/python ]; then
  echo "ERROR: virtual environment not found. Run INSTALL_MAC.command first."
  read "?Press Enter to close..."
  exit 1
fi
.venv/bin/python scripts/start_terminal.py
RC=$?
echo
[ "$RC" -eq 0 ] || echo "Terminal launcher exited with code $RC."
read "?Press Enter to close..."
exit "$RC"
