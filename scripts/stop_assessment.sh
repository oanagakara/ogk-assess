#!/usr/bin/env bash
PID_FILE="$(cd "$(dirname "$0")/.." && pwd)/logs/gunicorn.pid"
if [ -f "$PID_FILE" ]; then
  kill "$(cat "$PID_FILE")" && echo "Assessment app stopped." || echo "Process already gone."
  rm -f "$PID_FILE"
else
  echo "No PID file found — trying pkill..."
  pkill -f 'config.wsgi:application' || echo "Nothing to kill."
fi
