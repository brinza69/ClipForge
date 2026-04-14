#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/logs"

mkdir -p "$RUN_DIR" "$LOG_DIR"
mkdir -p "$ROOT/data"/{media,exports,thumbnails,temp,cache,knowledge,db}

if [ ! -d "$ROOT/.venv" ]; then
  echo "Lipsește .venv. Creează mai întâi mediul virtual."
  exit 1
fi

# Whisper model default — "small" OOM-kills under WSL2's memory cap.
# "base" fits comfortably and still transcribes short-form content well.
# Override with: CLIPFORGE_WHISPER_MODEL=small ./start-dev-bg.sh
: "${CLIPFORGE_WHISPER_MODEL:=base}"
export CLIPFORGE_WHISPER_MODEL
echo "Whisper model: $CLIPFORGE_WHISPER_MODEL"

if [ -f "$RUN_DIR/backend.pid" ] && kill -0 "$(cat "$RUN_DIR/backend.pid")" 2>/dev/null; then
  echo "Backend deja rulează cu PID $(cat "$RUN_DIR/backend.pid")"
else
  echo "Pornesc backend-ul..."
  nohup bash -lc "
    cd \"$ROOT/server\" &&
    source \"$ROOT/.venv/bin/activate\" &&
    python -m uvicorn main:app --host 0.0.0.0 --port 8420
  " > "$LOG_DIR/backend.log" 2>&1 &
  echo $! > "$RUN_DIR/backend.pid"
fi

if [ -f "$RUN_DIR/frontend.pid" ] && kill -0 "$(cat "$RUN_DIR/frontend.pid")" 2>/dev/null; then
  echo "Frontend deja rulează cu PID $(cat "$RUN_DIR/frontend.pid")"
else
  echo "Pornesc frontend-ul..."
  nohup bash -lc "
    cd \"$ROOT\" &&
    npm run dev
  " > "$LOG_DIR/frontend.log" 2>&1 &
  echo $! > "$RUN_DIR/frontend.pid"
fi

sleep 2

echo
echo "Backend PID:  $(cat "$RUN_DIR/backend.pid")"
echo "Frontend PID: $(cat "$RUN_DIR/frontend.pid")"
echo
echo "Frontend: http://localhost:3000"
echo "Backend:  http://localhost:8420"
echo "Docs:     http://localhost:8420/docs"
echo
echo "Log backend:  $LOG_DIR/backend.log"
echo "Log frontend: $LOG_DIR/frontend.log"
