#!/usr/bin/env bash
# ============================================================================
# ClipForge - Start Script (Bash / Git Bash on Windows)
# Launches both the Next.js frontend and Python worker backend.
# Kills leftover processes, waits for backend health, handles Ctrl+C.
# ============================================================================
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo ""
echo "========================================"
echo "  ClipForge - Starting Services"
echo "========================================"
echo ""

# ---------------------------------------------------------------------------
# Kill existing processes on our ports
# ---------------------------------------------------------------------------
echo "  Cleaning up stale processes..."
for port in 3000 8420; do
  pids=$(netstat -ano 2>/dev/null | grep ":${port} " | awk '{print $NF}' | sort -u | grep -v '^0$' || true)
  for pid in $pids; do
    echo "  Killing process $pid on port $port"
    taskkill //PID "$pid" //F 2>/dev/null || true
  done
done
sleep 1

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
VENV_PYTHON="$PROJECT_ROOT/server/.venv/Scripts/python.exe"
if [ ! -f "$VENV_PYTHON" ]; then
  echo "  [ERROR] Python venv not found at $VENV_PYTHON"
  echo "  Run ./scripts/setup.ps1 first."
  exit 1
fi

if [ ! -d "$PROJECT_ROOT/node_modules" ]; then
  echo "  [ERROR] node_modules not found."
  echo "  Run ./scripts/setup.ps1 first."
  exit 1
fi

# ---------------------------------------------------------------------------
# Start backend
# ---------------------------------------------------------------------------
echo "  Starting Python backend on port 8420..."
cd "$PROJECT_ROOT/server"
"$VENV_PYTHON" main.py &
BACKEND_PID=$!

# Wait for backend health
echo "  Waiting for backend health check..."
READY=false
for i in $(seq 1 30); do
  if curl -s http://localhost:8420/api/health >/dev/null 2>&1; then
    READY=true
    break
  fi
  echo "  Waiting for backend... ($i/30)"
  sleep 1
done

if [ "$READY" != "true" ]; then
  echo "  [ERROR] Backend failed to start within 30 seconds."
  echo "  Check the backend logs for errors."
  kill $BACKEND_PID 2>/dev/null || true
  exit 1
fi

echo "  Backend is healthy!"

# ---------------------------------------------------------------------------
# Start frontend
# ---------------------------------------------------------------------------
echo "  Starting Next.js frontend on port 3000..."
cd "$PROJECT_ROOT"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "========================================"
echo "  ClipForge is running!"
echo "========================================"
echo ""
echo "  Frontend:  http://localhost:3000"
echo "  Backend:   http://localhost:8420"
echo "  API Docs:  http://localhost:8420/docs"
echo ""
echo "  Press Ctrl+C to stop both services."
echo ""

# ---------------------------------------------------------------------------
# Clean shutdown on Ctrl+C
# ---------------------------------------------------------------------------
cleanup() {
  echo ""
  echo "  Stopping services..."
  kill $BACKEND_PID 2>/dev/null || true
  kill $FRONTEND_PID 2>/dev/null || true
  # Also clean up any child processes left on the ports
  for port in 3000 8420; do
    pids=$(netstat -ano 2>/dev/null | grep ":${port} " | awk '{print $NF}' | sort -u | grep -v '^0$' || true)
    for pid in $pids; do
      taskkill //PID "$pid" //F 2>/dev/null || true
    done
  done
  echo "  ClipForge stopped."
  exit 0
}

trap cleanup INT TERM

# Wait for either process to exit
wait
