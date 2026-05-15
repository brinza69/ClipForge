#!/usr/bin/env bash
# ============================================================================
# ClipForge — Stop script (Linux / WSL)
#
# Reads .run/{backend,frontend}.pid, kills those processes plus their entire
# process group (setsid-launched in start.sh), then verifies ports 8420 and
# 3000 are clear. Idempotent.
#
# Usage:
#   ./scripts/stop.sh              # stop both
#   ./scripts/stop.sh --clean-temp # also wipe data/temp/erase/* (old eraser leftovers)
# ============================================================================

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
BACKEND_PORT="${CLIPFORGE_BACKEND_PORT:-8420}"
FRONTEND_PORT="${CLIPFORGE_FRONTEND_PORT:-3000}"
CLEAN_TEMP=0

for arg in "$@"; do
    case "$arg" in
        --clean-temp) CLEAN_TEMP=1 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

if [ -t 1 ]; then
    C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_C=$'\033[36m'
    C_D=$'\033[2m'; C_X=$'\033[0m'
else C_R=""; C_G=""; C_Y=""; C_C=""; C_D=""; C_X=""; fi

ok()   { printf "  ${C_G}[OK]${C_X}   %s\n" "$*"; }
info() { printf "  ${C_D}%s${C_X}\n" "$*"; }
warn() { printf "  ${C_Y}[WARN]${C_X} %s\n" "$*"; }

is_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

port_in_use() {
    if command -v ss >/dev/null 2>&1; then
        ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}$"
    else
        netstat -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}$"
    fi
}

# Kill an entire process group given a leader PID. Escalates TERM → KILL.
kill_tree() {
    local pid="$1"
    local label="$2"
    is_alive "$pid" || { info "$label PID $pid not running."; return 0; }
    local pgid
    pgid="$(ps -o pgid= "$pid" 2>/dev/null | tr -d ' ' || true)"
    [ -z "$pgid" ] && pgid="$pid"
    kill -TERM "-$pgid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        is_alive "$pid" || { ok "$label stopped (PID $pid)"; return 0; }
        sleep 0.3
    done
    kill -KILL "-$pgid" 2>/dev/null || true
    sleep 0.3
    if is_alive "$pid"; then
        warn "$label PID $pid did not die"
    else
        ok "$label stopped (PID $pid, force)"
    fi
}

kill_port() {
    local port="$1"
    local label="$2"
    local pids=""
    if command -v ss >/dev/null 2>&1; then
        pids="$(ss -tlnp 2>/dev/null | awk -v p=":$port " '$4 ~ p { print $0 }' | grep -oP 'pid=\K[0-9]+' | sort -u || true)"
    fi
    if [ -z "$pids" ] && command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -ti tcp:"$port" 2>/dev/null | sort -u || true)"
    fi
    for p in $pids; do
        kill -TERM "$p" 2>/dev/null || true
        info "killed leftover PID $p on :$port ($label)"
    done
    [ -n "$pids" ] && sleep 0.5
    port_in_use "$port" && return 1 || return 0
}

printf "\n${C_C}========================================${C_X}\n"
printf "${C_C}  ClipForge — Stopping services${C_X}\n"
printf "${C_C}========================================${C_X}\n\n"

for svc in backend frontend; do
    pidfile="$RUN_DIR/$svc.pid"
    if [ -f "$pidfile" ]; then
        val="$(cat "$pidfile" 2>/dev/null || true)"
        [ -n "$val" ] && kill_tree "$val" "$svc"
        rm -f "$pidfile"
    else
        info "$svc: no pidfile, will check port directly"
    fi
done

printf "\n"
info "Verifying ports are clear…"

BE_FREE=0; FE_FREE=0
if kill_port "$BACKEND_PORT" "backend"; then BE_FREE=1; fi
if kill_port "$FRONTEND_PORT" "frontend"; then FE_FREE=1; fi
if [ "$BE_FREE" -eq 1 ]; then ok ":$BACKEND_PORT  free"; else warn ":$BACKEND_PORT  still in use"; fi
if [ "$FE_FREE" -eq 1 ]; then ok ":$FRONTEND_PORT free"; else warn ":$FRONTEND_PORT still in use"; fi

if [ "$CLEAN_TEMP" -eq 1 ]; then
    printf "\n"
    info "Cleaning data/temp/erase/*…"
    ERASE_TMP="$ROOT/data/temp/erase"
    if [ -d "$ERASE_TMP" ]; then
        count="$(find "$ERASE_TMP" -mindepth 1 -maxdepth 1 -type d | wc -l)"
        find "$ERASE_TMP" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
        ok "removed $count erase workdir(s)"
    else
        info "(nothing to clean — $ERASE_TMP doesn't exist)"
    fi
fi

printf "\n"
if [ "$BE_FREE" -eq 1 ] && [ "$FE_FREE" -eq 1 ]; then
    printf "${C_G}  ClipForge stopped cleanly.${C_X}\n\n"
else
    printf "${C_Y}  Stop completed with warnings — see above.${C_X}\n\n"
fi
