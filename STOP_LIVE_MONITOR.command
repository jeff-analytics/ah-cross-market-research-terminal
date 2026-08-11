#!/bin/zsh
cd "$(dirname "$0")"
mkdir -p data
print -r -- stop > data/live_monitor.stop
echo "Stop signal written."
sleep 2
if [ -f data/live_monitor.pid ]; then
  PID=$(cat data/live_monitor.pid 2>/dev/null || true)
  if [[ "$PID" == <-> ]]; then kill "$PID" 2>/dev/null || true; fi
fi
echo "Live monitor has stopped or is exiting."
read "?Press Enter to close..."
