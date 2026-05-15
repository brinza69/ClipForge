#!/usr/bin/env bash
# ============================================================================
# ClipForge — Start script (Linux / WSL)
#
# Starts backend (uvicorn) and frontend (npm dev) as detached background
# processes in their own process groups via setsid. Writes PIDs to .run/,
# logs to logs/. Exits as soon as both are reachable. Stop them later with:
#   ./scripts/stop.sh
#
# Usage:
#   ./scripts/start.sh             # start both
#   ./scripts/start.sh --no-browser  # don't open the browser
#   CLIPFORGE_BACKEND_PORT=…  CLIPFORGE_FRONTEND_PORT=…   # override ports
# ============================================================================

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/logs"
BACKEND_PORT="${CLIPFORGE_BACKEND_PORT:-8420}"
FRONTEND_PORT="${CLIPFORGE_FRONTEND_PORT:-3000}"
OPEN_BROWSER=1

for arg in "$@"; do
    case "$arg" in
        --no-browser) OPEN_BROWSER=0 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

mkdir -p "$RUN_DIR" "$LOG_DIR"

if [ -t 1 ]; then
    C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_C=$'\033[36m'
    C_D=$'\033[2m'; C_X=$'\033[0m'
else C_R=""; C_G=""; C_Y=""; C_C=""; C_D=""; C_X=""; fi

ok()   { printf "  ${C_G}[OK]${C_X}   %s\n" "$*"; }
info() { printf "  ${C_D}%s${C_X}\n" "$*"; }
warn() { printf "  ${C_Y}[WARN]${C_X} %s\n" "$*"; }
err()  { printf "  ${C_R}[FAIL]${C_X} %s\n" "$*"; }

is_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

kill_port() {
    local port="$1"
    local pids=""
    if command -v ss >/dev/null 2>&1; then
        pids="$(ss -tlnp 2>/dev/null | awk -v p=":$port " '$4 ~ p { print $0 }' | grep -oP 'pid=\K[0-9]+' | sort -u || true)"
    fi
    if [ -z "$pids" ] && command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -ti tcp:"$port" 2>/dev/null | sort -u || true)"
    fi
    for p in $pids; do
        kill -TERM "$p" 2>/dev/null || true
        info "killed PID $p on :$port"
    done
    [ -n "$pids" ] && sleep 0.5
}

find_node_modules() {
    local cur="$1"
    while [ -n "$cur" ] && [ "$cur" != "/" ]; do
        if [ -d "$cur/node_modules" ]; then echo "$cur/node_modules"; return 0; fi
        cur="$(dirname "$cur")"
    done
    return 1
}

printf "\n${C_C}========================================${C_X}\n"
printf "${C_C}  ClipForge — Starting services${C_X}\n"
printf "${C_C}========================================${C_X}\n\n"

VENV_PY="$ROOT/server/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
    err "Python venv missing at server/.venv. Run ./scripts/setup.sh first."
    exit 1
fi

NM="$(find_node_modules "$ROOT" || true)"
if [ -z "$NM" ]; then
    err "node_modules not found in $ROOT or any parent. Run ./scripts/setup.sh first."
    exit 1
fi
info "node_modules: $NM"

BACKEND_ALIVE=0; FRONTEND_ALIVE=0
for svc in backend frontend; do
    pidfile="$RUN_DIR/$svc.pid"
    if [ -f "$pidfile" ]; then
        old="$(cat "$pidfile" 2>/dev/null || true)"
        if is_alive "$old"; then
            warn "$svc already running (PID $old). Use stop.sh first if you want to restart."
            [ "$svc" = "backend" ]  && BACKEND_ALIVE=1
            [ "$svc" = "frontend" ] && FRONTEND_ALIVE=1
        else
            rm -f "$pidfile"
        fi
    fi
done

if [ "$BACKEND_ALIVE" -eq 1 ] && [ "$FRONTEND_ALIVE" -eq 1 ]; then
    printf "\n  Already running.\n"
    printf "  Frontend: http://localhost:%s\n  Backend:  http://localhost:%s\n" "$FRONTEND_PORT" "$BACKEND_PORT"
    exit 0
fi

[ "$BACKEND_ALIVE"  -eq 0 ] && kill_port "$BACKEND_PORT"
[ "$FRONTEND_ALIVE" -eq 0 ] && kill_port "$FRONTEND_PORT"

if command -v ffmpeg >/dev/null 2>&1; then
    FFMPEG_DIR="$(dirname "$(command -v ffmpeg)")"
    info "ffmpeg dir: $FFMPEG_DIR"
else
    warn "ffmpeg not on PATH — downloads/eraser/exports will fail. Run setup.sh."
    FFMPEG_DIR=""
fi

# ── start backend ─────────────────────────────────────────────────────────
if [ "$BACKEND_ALIVE" -eq 0 ]; then
    info "Starting backend on :$BACKEND_PORT…"
    : > "$LOG_DIR/backend.log"
    setsid bash -c "
        cd '$ROOT/server'
        ${FFMPEG_DIR:+export CLIPFORGE_FFMPEG_PATH='$FFMPEG_DIR'}
        ${FFMPEG_DIR:+export PATH='$FFMPEG_DIR':\$PATH}
        exec '$VENV_PY' -m uvicorn main:app --host 127.0.0.1 --port '$BACKEND_PORT'
    " > "$LOG_DIR/backend.log" 2>&1 < /dev/null &
    echo $! > "$RUN_DIR/backend.pid"
    info "backend PID $(cat "$RUN_DIR/backend.pid"), log → $LOG_DIR/backend.log"

    HEALTHY=0
    for _ in $(seq 1 120); do
        if curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/health" -o /dev/null 2>/dev/null; then
            HEALTHY=1; break
        fi
        sleep 0.5
    done
    if [ "$HEALTHY" -eq 1 ]; then
        ok "backend healthy at http://127.0.0.1:$BACKEND_PORT"
    else
        err "backend did not become healthy within 60 s. Tail $LOG_DIR/backend.log for the error."
        exit 1
    fi
fi

# ── start frontend ────────────────────────────────────────────────────────
if [ "$FRONTEND_ALIVE" -eq 0 ]; then
    info "Starting frontend on :$FRONTEND_PORT…"
    : > "$LOG_DIR/frontend.log"
    setsid bash -c "
        cd '$ROOT'
        exec npm run dev -- --port '$FRONTEND_PORT'
    " > "$LOG_DIR/frontend.log" 2>&1 < /dev/null &
    echo $! > "$RUN_DIR/frontend.pid"
    info "frontend PID $(cat "$RUN_DIR/frontend.pid"), log → $LOG_DIR/frontend.log"

    READY=0
    for _ in $(seq 1 120); do
        if grep -q "Ready in" "$LOG_DIR/frontend.log" 2>/dev/null; then
            READY=1; break
        fi
        sleep 0.5
    done
    if [ "$READY" -eq 1 ]; then
        ok "frontend ready at http://localhost:$FRONTEND_PORT"
    else
        warn "frontend didn't print 'Ready in' within 60 s. Check $LOG_DIR/frontend.log."
    fi
fi

printf "\n${C_C}========================================${C_X}\n"
printf "${C_G}  Running${C_X}\n"
printf "${C_C}========================================${C_X}\n"
printf "  Frontend: http://localhost:%s\n" "$FRONTEND_PORT"
printf "  Backend:  http://localhost:%s\n"  "$BACKEND_PORT"
printf "  ${C_D}Logs:     %s/{backend,frontend}.log${C_X}\n" "$LOG_DIR"
printf "  ${C_D}Stop:     ./scripts/stop.sh${C_X}\n\n"

if [ "$OPEN_BROWSER" -eq 1 ]; then
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then
        open "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1 &
    elif command -v wslview >/dev/null 2>&1; then
        wslview "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1 &
    fi
fi
