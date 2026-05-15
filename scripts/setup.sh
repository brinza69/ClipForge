#!/usr/bin/env bash
# ============================================================================
# ClipForge — Setup / check script (Linux / WSL)
#
# Idempotent: re-runnable any time. Verifies every dependency, installs what
# it can without sudo, prints clear instructions for what needs root, and
# ends with a summary table.
#
# Usage:
#   ./scripts/setup.sh              # full setup (installs what's missing)
#   ./scripts/setup.sh --check      # report only, install nothing
#   ./scripts/setup.sh --gpu        # also install torch + LaMa (~3 GB)
#   ./scripts/setup.sh --skip-gpu   # never prompt; never install torch
# ============================================================================

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CHECK=0
WANT_GPU=""    # "" = ask, "1" = yes, "0" = no
for arg in "$@"; do
    case "$arg" in
        --check)    CHECK=1 ;;
        --gpu)      WANT_GPU=1 ;;
        --skip-gpu) WANT_GPU=0 ;;
        -h|--help)
            sed -n '2,15p' "$0"
            exit 0
            ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

# ── colors ────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_C=$'\033[36m'
    C_D=$'\033[2m'; C_X=$'\033[0m'
else
    C_R=""; C_G=""; C_Y=""; C_C=""; C_D=""; C_X=""
fi

ok()   { printf "  ${C_G}[OK]${C_X}   %s\n" "$*"; }
info() { printf "  ${C_D}[..]${C_X}   %s\n" "$*"; }
warn() { printf "  ${C_Y}[WARN]${C_X} %s\n" "$*"; }
err()  { printf "  ${C_R}[FAIL]${C_X} %s\n" "$*"; }
skip() { printf "  ${C_D}[SKIP] %s${C_X}\n" "$*"; }
section() { printf "\n${C_C}── %s ──${C_X}\n" "$*"; }

declare -A STATUS DETAIL
record() { STATUS["$1"]="$2"; DETAIL["$1"]="$3"; }

# ── detect package manager for ffmpeg suggestion ──────────────────────────
PKG=""
if command -v apt-get >/dev/null 2>&1; then PKG="apt";
elif command -v dnf     >/dev/null 2>&1; then PKG="dnf";
elif command -v pacman  >/dev/null 2>&1; then PKG="pacman";
elif command -v apk     >/dev/null 2>&1; then PKG="apk";
fi

install_hint_ffmpeg() {
    case "$PKG" in
        apt)    echo "sudo apt-get update && sudo apt-get install -y ffmpeg" ;;
        dnf)    echo "sudo dnf install -y ffmpeg" ;;
        pacman) echo "sudo pacman -S --noconfirm ffmpeg" ;;
        apk)    echo "sudo apk add ffmpeg" ;;
        *)      echo "Install ffmpeg from https://ffmpeg.org/download.html" ;;
    esac
}

# ── banner ────────────────────────────────────────────────────────────────
printf "\n${C_C}========================================${C_X}\n"
if [ "$CHECK" -eq 1 ]; then
    printf "${C_C}  ClipForge — System check (read-only)${C_X}\n"
else
    printf "${C_C}  ClipForge — Setup${C_X}\n"
fi
printf "${C_C}========================================${C_X}\n"

# ── Python (3.10–3.13) ────────────────────────────────────────────────────
section "Python"

PYTHON=""
PYVER=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        v="$("$cmd" --version 2>&1 | awk '{print $2}')"
        if [[ "$v" =~ ^3\.(10|11|12|13)\.[0-9]+$ ]]; then
            PYTHON="$cmd"
            PYVER="$v"
            break
        fi
    fi
done

if [ -n "$PYTHON" ]; then
    ok "$PYVER ($PYTHON)"
    record "Python" "ok" "$PYVER"
else
    err "Python 3.10–3.13 not found. faster-whisper does not support 3.14+."
    case "$PKG" in
        apt)    err "Install: sudo apt-get install -y python3.12 python3.12-venv" ;;
        dnf)    err "Install: sudo dnf install -y python3.12" ;;
        pacman) err "Install: sudo pacman -S --noconfirm python" ;;
        *)      err "Install from https://www.python.org/downloads/" ;;
    esac
    record "Python" "missing" "Install 3.10–3.13 manually."
fi

# ── Node.js + npm ─────────────────────────────────────────────────────────
section "Node.js"

if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    NODE_VER="$(node --version 2>/dev/null)"
    NPM_VER="$(npm --version 2>/dev/null)"
    ok "node $NODE_VER / npm $NPM_VER"
    record "Node.js" "ok" "$NODE_VER (npm $NPM_VER)"
else
    err "Node.js not found."
    case "$PKG" in
        apt)    err "Quick install: 'curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs'" ;;
        dnf)    err "Install: sudo dnf install -y nodejs" ;;
        pacman) err "Install: sudo pacman -S --noconfirm nodejs npm" ;;
        *)      err "Install from https://nodejs.org/en/download" ;;
    esac
    record "Node.js" "missing" "Install Node.js LTS."
fi

# ── ffmpeg / ffprobe ──────────────────────────────────────────────────────
section "FFmpeg"

if command -v ffmpeg >/dev/null 2>&1; then
    FFMPEG_PATH="$(command -v ffmpeg)"
    FFMPEG_VER="$("$FFMPEG_PATH" -version 2>/dev/null | head -1)"
    ok "$FFMPEG_PATH"
    ok "$FFMPEG_VER"
    record "FFmpeg" "ok" "$FFMPEG_PATH"
else
    err "ffmpeg not found."
    err "Install: $(install_hint_ffmpeg)"
    record "FFmpeg" "missing" "$(install_hint_ffmpeg)"
fi

# ── NVIDIA GPU info (controls GPU stack prompt) ───────────────────────────
section "GPU"

GPU_NAME=""
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_LINE="$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1)"
    if [ -n "$GPU_LINE" ]; then
        GPU_NAME="$GPU_LINE"
        ok "$GPU_NAME"
        record "GPU" "ok" "$GPU_NAME"
    fi
fi
if [ -z "$GPU_NAME" ]; then
    warn "No NVIDIA GPU detected — LaMa GPU inpainting and NVENC will be unavailable."
    record "GPU" "warn" "No NVIDIA GPU; eraser uses CPU."
fi

# ── Data directories ──────────────────────────────────────────────────────
section "Data directories"

DATA_DIRS=(
    "$ROOT/data"
    "$ROOT/data/media"
    "$ROOT/data/exports"
    "$ROOT/data/thumbnails"
    "$ROOT/data/temp"
    "$ROOT/data/previews"
    "$ROOT/data/cache"
    "$ROOT/data/knowledge"
    "$ROOT/data/db"
    "$ROOT/logs"
    "$ROOT/.run"
)
for d in "${DATA_DIRS[@]}"; do
    if [ -d "$d" ]; then
        skip "$d"
    elif [ "$CHECK" -eq 1 ]; then
        warn "missing: $d"
    else
        mkdir -p "$d" && ok "created $d"
    fi
done
record "Data dirs" "ok" "ensured under data/"

# ── Python venv + base requirements ───────────────────────────────────────
section "Python venv (server/.venv)"

VENV="$ROOT/server/.venv"
VENV_PY="$VENV/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$VENV/Scripts/python.exe"  # WSL with Windows venv

VENV_EXISTS=0
if [ -x "$VENV_PY" ]; then
    ok "venv exists at server/.venv"
    VENV_EXISTS=1
else
    if [ "$CHECK" -eq 1 ]; then
        err "venv missing at server/.venv"
        record "venv" "missing" "Run ./scripts/setup.sh"
    elif [ -n "$PYTHON" ]; then
        info "Creating venv with $PYTHON…"
        if "$PYTHON" -m venv "$VENV"; then
            VENV_PY="$VENV/bin/python"
            ok "venv created"
            VENV_EXISTS=1
        else
            err "venv creation failed (apt: install python3-venv)"
            record "venv" "fail" "python -m venv failed"
        fi
    else
        skip "no Python available, skipping venv creation"
    fi
fi

if [ "$VENV_EXISTS" -eq 1 ]; then
    if [ "$CHECK" -eq 1 ]; then
        if "$VENV_PY" -c "import fastapi, uvicorn, faster_whisper" 2>/dev/null; then
            ok "base requirements importable"
            record "venv deps" "ok" "fastapi/uvicorn/faster-whisper present"
        else
            warn "base requirements not importable — run setup without --check"
            record "venv deps" "missing" "run setup to install requirements.txt"
        fi
    else
        info "Upgrading pip…"
        "$VENV_PY" -m pip install --upgrade pip --quiet
        info "Installing server/requirements.txt + python-multipart…"
        if "$VENV_PY" -m pip install -r "$ROOT/server/requirements.txt" --quiet \
            && "$VENV_PY" -m pip install python-multipart --quiet; then
            ok "base Python deps installed"
            record "venv deps" "ok" "requirements.txt + python-multipart"
        else
            err "pip install failed (see output above)"
            record "venv deps" "fail" "see pip output"
        fi
    fi
fi

# ── GPU stack (torch + simple-lama-inpainting), ~3 GB ─────────────────────
section "GPU stack (torch + LaMa)"

if [ "$VENV_EXISTS" -eq 1 ]; then
    HAS_TORCH=0; HAS_LAMA=0
    "$VENV_PY" -c "import torch" 2>/dev/null && HAS_TORCH=1
    "$VENV_PY" -c "import simple_lama_inpainting" 2>/dev/null && HAS_LAMA=1

    if [ "$HAS_TORCH" -eq 1 ] && [ "$HAS_LAMA" -eq 1 ]; then
        CUDA_OK="$("$VENV_PY" -c 'import torch; print(int(torch.cuda.is_available()))' 2>/dev/null || echo "?")"
        ok "torch + simple-lama-inpainting installed (CUDA available: $CUDA_OK)"
        record "GPU stack" "ok" "torch + LaMa (CUDA=$CUDA_OK)"
    elif [ "$CHECK" -eq 1 ]; then
        warn "torch=$HAS_TORCH, lama=$HAS_LAMA — eraser falls back to OpenCV CPU"
        record "GPU stack" "missing" "Optional. Re-run setup with --gpu."
    else
        WANT="$WANT_GPU"
        if [ -z "$WANT" ]; then
            if [ -n "$GPU_NAME" ]; then
                printf "  ${C_Y}NVIDIA GPU detected. Install torch+CUDA + LaMa for GPU inpainting?${C_X}\n"
                printf "  ${C_D}Downloads ~3 GB. Skip if disk is tight.${C_X}\n"
                read -rp "  [y/N] " reply
                [[ "$reply" =~ ^[Yy]$ ]] && WANT=1 || WANT=0
            else
                WANT=0
            fi
        fi
        if [ "$WANT" -eq 1 ]; then
            info "Installing torch + torchvision (CUDA 12.1) — slow step…"
            if "$VENV_PY" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121; then
                info "Installing simple-lama-inpainting…"
                if "$VENV_PY" -m pip install simple-lama-inpainting --quiet; then
                    ok "GPU stack installed"
                    record "GPU stack" "installed" "torch + LaMa"
                else
                    err "simple-lama-inpainting failed"
                    record "GPU stack" "fail" "torch ok, LaMa failed"
                fi
            else
                err "torch install failed (often disk space; need ~5 GB free)"
                record "GPU stack" "fail" "pip install torch failed"
            fi
        else
            skip "GPU stack not requested (eraser uses cv2 CPU)"
            record "GPU stack" "skipped" "Re-run with --gpu to install."
        fi
    fi
fi

# ── Node modules (walk up for monorepo-style installs) ────────────────────
section "Node modules"

find_node_modules() {
    local cur="$1"
    while [ -n "$cur" ] && [ "$cur" != "/" ]; do
        if [ -d "$cur/node_modules" ]; then echo "$cur/node_modules"; return 0; fi
        cur="$(dirname "$cur")"
    done
    return 1
}

NM="$(find_node_modules "$ROOT" || true)"
if [ -n "$NM" ]; then
    ok "node_modules: $NM"
    record "Frontend deps" "ok" "$NM"
else
    if [ "$CHECK" -eq 1 ]; then
        err "node_modules missing in $ROOT or any parent"
        record "Frontend deps" "missing" "run npm install"
    elif command -v npm >/dev/null 2>&1; then
        info "Running npm install…"
        if ( cd "$ROOT" && npm install ); then
            ok "frontend deps installed"
            record "Frontend deps" "ok" "npm install completed"
        else
            err "npm install failed"
            record "Frontend deps" "fail" "see npm output"
        fi
    else
        skip "Node.js not available, skipping npm install"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────
printf "\n${C_C}========================================${C_X}\n"
printf "${C_C}  Summary${C_X}\n"
printf "${C_C}========================================${C_X}\n"
printf "  %-15s  %-10s  %s\n" "NAME" "STATUS" "DETAIL"
printf "  %-15s  %-10s  %s\n" "----" "------" "------"
for k in "Python" "Node.js" "FFmpeg" "GPU" "Data dirs" "venv" "venv deps" "GPU stack" "Frontend deps"; do
    s="${STATUS[$k]:-}"; d="${DETAIL[$k]:-}"
    [ -z "$s" ] && continue
    case "$s" in
        ok|installed) color="$C_G" ;;
        warn|skipped) color="$C_Y" ;;
        missing|fail) color="$C_R" ;;
        *)            color="$C_X" ;;
    esac
    printf "  %-15s  ${color}%-10s${C_X}  %s\n" "$k" "$s" "$d"
done

# Exit 1 if anything is missing/failed.
for k in "${!STATUS[@]}"; do
    case "${STATUS[$k]}" in missing|fail)
        printf "\n${C_Y}  Action items above. Re-run after fixing.${C_X}\n\n"
        exit 1
        ;;
    esac
done

printf "\n${C_G}  Ready. Start the app with: ./scripts/start.sh${C_X}\n\n"
