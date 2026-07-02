#!/usr/bin/env bash
# ClipForge - GPU stack setup (Linux/macOS/Git-Bash).
# Installs what's NOT in requirements.txt: torch+torchvision (CUDA), the LaMa
# neural eraser, and audioop-lts (pydub on Python 3.13). Run AFTER setup.sh.
# The LaMa batch size auto-tunes to the card's VRAM at runtime — works on any GPU.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/server/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/server/.venv/bin/python"
if [ ! -x "$PY" ]; then echo "venv missing — run scripts/setup.sh first"; exit 1; fi

echo "[1/3] torch + torchvision (CUDA 12.4)..."
"$PY" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
echo "[2/3] simple-lama-inpainting (wheel only)..."
"$PY" -m pip install simple-lama-inpainting --only-binary=:all:
echo "[3/3] audioop-lts (Python 3.13+ only)..."
if "$PY" -c "import sys; exit(0 if sys.version_info[:2] >= (3,13) else 1)"; then
  "$PY" -m pip install audioop-lts
else
  echo "  skipped (Python <= 3.12 has audioop built in)"
fi
"$PY" -c "import torch; print('CUDA available:', torch.cuda.is_available(), '|', (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only'))"
echo "GPU stack ready."
