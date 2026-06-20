#!/usr/bin/env bash
set -e

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

mkdir -p logs

.venv/bin/gunicorn config.wsgi:application \
  --bind 0.0.0.0:8444 \
  --workers 3 \
  --timeout 120 \
  --certfile /home/support/certs/ogk-ai.local+2.pem \
  --keyfile  /home/support/certs/ogk-ai.local+2-key.pem \
  --access-logfile logs/access.log \
  --error-logfile  logs/app.log \
  --capture-output \
  --daemon \
  --pid     logs/gunicorn.pid

echo "Assessment app started — https://ogk-ai.local:8444"
echo "Logs: $APP_DIR/logs/app.log"
