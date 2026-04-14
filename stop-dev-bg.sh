#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.run"

stop_one() {
  local name="$1"
  local pidfile="$2"

  if [ -f "$pidfile" ]; then
    local PID
    PID="$(cat "$pidfile")"
    if kill -0 "$PID" 2>/dev/null; then
      echo "Opresc $name (PID $PID)..."
      kill "$PID" || true
    else
      echo "$name nu mai rulează."
    fi
    rm -f "$pidfile"
  else
    echo "Nu există PID file pentru $name."
  fi
}

stop_one "backend" "$RUN_DIR/backend.pid"
stop_one "frontend" "$RUN_DIR/frontend.pid"
