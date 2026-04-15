#!/usr/bin/env bash
# ClipForge dev runner — start/stop the backend + frontend in the background.
#
# Usage:
#   ./dev.sh start      # start both services (idempotent)
#   ./dev.sh stop       # stop both services
#   ./dev.sh restart    # stop then start
#   ./dev.sh status     # show PID + port state
#   ./dev.sh logs [backend|frontend]  # tail the log (Ctrl-C to exit)
#
# Requirements (one-time):
#   - server/.venv created with requirements installed
#   - node_modules installed (npm install)
#
# Designed for WSL / Linux. Uses process groups so child processes are cleaned
# up properly — no orphaned node/uvicorn after `stop`.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/logs"
VENV="$ROOT/server/.venv"

BACKEND_PORT="${CLIPFORGE_BACKEND_PORT:-8420}"
FRONTEND_PORT="${CLIPFORGE_FRONTEND_PORT:-3000}"

# Whisper model: "small" OOM-kills under WSL2's default memory cap; "base"
# fits comfortably. Override with: CLIPFORGE_WHISPER_MODEL=small ./dev.sh start
: "${CLIPFORGE_WHISPER_MODEL:=base}"
export CLIPFORGE_WHISPER_MODEL

mkdir -p "$RUN_DIR" "$LOG_DIR"

c_red()   { printf '\033[31m%s\033[0m\n' "$*"; }
c_green() { printf '\033[32m%s\033[0m\n' "$*"; }
c_yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
c_dim()   { printf '\033[2m%s\033[0m\n' "$*"; }

# Is the process identified by $1 (PID) still alive?
is_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

# Port $1 currently bound by anything?
port_in_use() {
  if command -v ss >/dev/null 2>&1; then
    ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}$"
  else
    netstat -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}$"
  fi
}

# Wait up to $2 seconds for port $1 to start listening.
wait_for_port() {
  local port="$1" timeout="$2" i=0
  while [ "$i" -lt "$timeout" ]; do
    if port_in_use "$port"; then return 0; fi
    sleep 1; i=$((i+1))
  done
  return 1
}

read_pid() { [ -f "$1" ] && cat "$1" 2>/dev/null || true; }

# Kill a whole process group given a leader PID, escalating SIGTERM→SIGKILL.
kill_group() {
  local pid="$1"
  is_alive "$pid" || return 0
  local pgid
  pgid="$(ps -o pgid= "$pid" 2>/dev/null | tr -d ' ' || true)"
  [ -z "$pgid" ] && pgid="$pid"
  kill -TERM "-$pgid" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    is_alive "$pid" || return 0
    sleep 0.5
  done
  kill -KILL "-$pgid" 2>/dev/null || true
  sleep 0.3
}

# --- service launchers --------------------------------------------------------

start_backend() {
  local pidfile="$RUN_DIR/backend.pid"
  local pid; pid="$(read_pid "$pidfile")"
  if is_alive "${pid:-}"; then
    c_yellow "backend already running (pid $pid)"
    return 0
  fi

  if [ ! -x "$VENV/bin/uvicorn" ]; then
    c_red "missing venv at $VENV"
    c_dim  "  create with: python3 -m venv server/.venv && server/.venv/bin/pip install -r server/requirements.txt"
    return 1
  fi

  if port_in_use "$BACKEND_PORT"; then
    c_red "port $BACKEND_PORT already in use"
    return 1
  fi

  echo "starting backend on :$BACKEND_PORT..."
  # setsid puts the child in a fresh process group so we can kill the whole tree.
  setsid bash -c "
    cd '$ROOT/server'
    exec '$VENV/bin/uvicorn' main:app --host 0.0.0.0 --port '$BACKEND_PORT'
  " >"$LOG_DIR/backend.log" 2>&1 < /dev/null &
  echo $! > "$pidfile"

  if wait_for_port "$BACKEND_PORT" 20; then
    c_green "backend up   (pid $(cat "$pidfile"))  http://localhost:$BACKEND_PORT"
  else
    c_red "backend failed to bind :$BACKEND_PORT within 20s — see $LOG_DIR/backend.log"
    return 1
  fi
}

start_frontend() {
  local pidfile="$RUN_DIR/frontend.pid"
  local pid; pid="$(read_pid "$pidfile")"
  if is_alive "${pid:-}"; then
    c_yellow "frontend already running (pid $pid)"
    return 0
  fi

  if [ ! -d "$ROOT/node_modules" ]; then
    c_red "missing node_modules — run: npm install"
    return 1
  fi

  if port_in_use "$FRONTEND_PORT"; then
    c_red "port $FRONTEND_PORT already in use"
    return 1
  fi

  echo "starting frontend on :$FRONTEND_PORT..."
  setsid bash -c "
    cd '$ROOT'
    exec npm run dev -- --port '$FRONTEND_PORT'
  " >"$LOG_DIR/frontend.log" 2>&1 < /dev/null &
  echo $! > "$pidfile"

  if wait_for_port "$FRONTEND_PORT" 60; then
    c_green "frontend up  (pid $(cat "$pidfile"))  http://localhost:$FRONTEND_PORT"
  else
    c_red "frontend failed to bind :$FRONTEND_PORT within 60s — see $LOG_DIR/frontend.log"
    return 1
  fi
}

stop_one() {
  local name="$1" pidfile="$RUN_DIR/$1.pid"
  local pid; pid="$(read_pid "$pidfile")"
  if is_alive "${pid:-}"; then
    echo "stopping $name (pid $pid)..."
    kill_group "$pid"
    if is_alive "$pid"; then
      c_red "$name (pid $pid) did not die"
    else
      c_green "$name stopped"
    fi
  else
    c_dim "$name not running"
  fi
  rm -f "$pidfile"
}

# --- subcommands --------------------------------------------------------------

cmd_start() {
  echo "whisper model: $CLIPFORGE_WHISPER_MODEL"
  start_backend
  start_frontend
  echo
  c_dim "logs:  $LOG_DIR/{backend,frontend}.log"
  c_dim "stop:  ./dev.sh stop"
}

cmd_stop() {
  stop_one backend
  stop_one frontend
}

cmd_status() {
  for name in backend frontend; do
    local pidfile="$RUN_DIR/$name.pid"
    local pid; pid="$(read_pid "$pidfile")"
    local port; [ "$name" = backend ] && port="$BACKEND_PORT" || port="$FRONTEND_PORT"
    if is_alive "${pid:-}"; then
      c_green "$name  running  pid=$pid  port=$port"
    elif port_in_use "$port"; then
      c_yellow "$name  port $port busy but no tracked pid"
    else
      c_dim    "$name  stopped"
    fi
  done
}

cmd_logs() {
  local which="${1:-backend}"
  local f="$LOG_DIR/$which.log"
  [ -f "$f" ] || { c_red "no log at $f"; exit 1; }
  exec tail -n 100 -f "$f"
}

case "${1:-start}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  logs)    shift || true; cmd_logs "${1:-backend}" ;;
  *)
    cat >&2 <<EOF
usage: $0 {start|stop|restart|status|logs [backend|frontend]}
EOF
    exit 2
    ;;
esac
