# ============================================================================
# ClipForge - GPU stack setup (Windows)
# Installs the pieces NOT in requirements.txt: torch+torchvision (CUDA build),
# simple-lama-inpainting (neural eraser), and audioop-lts (pydub on Py 3.13).
# Run AFTER scripts/setup.ps1 has created server/.venv.
# Works on ANY NVIDIA card — the LaMa batch size auto-tunes to the VRAM at runtime.
# ============================================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = "$root\server\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[ERROR] venv missing. Run scripts\setup.ps1 first." -ForegroundColor Red; exit 1
}

Write-Host "[1/3] torch + torchvision (CUDA 12.4)..." -ForegroundColor Cyan
& $py -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

Write-Host "[2/3] simple-lama-inpainting (wheel only)..." -ForegroundColor Cyan
& $py -m pip install simple-lama-inpainting --only-binary=:all:

Write-Host "[3/3] audioop-lts (only needed on Python 3.13+)..." -ForegroundColor Cyan
$needs = (& $py -c "import sys; print(1 if sys.version_info[:2] >= (3,13) else 0)").Trim()
if ($needs -eq "1") { & $py -m pip install audioop-lts } else { Write-Host "  skipped (Python <= 3.12 has audioop built in)" -ForegroundColor DarkGray }

Write-Host ""
& $py -c "import torch; print('CUDA available:', torch.cuda.is_available(), '|', (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only'))"
Write-Host "GPU stack ready. LaMa batch auto-tunes to the card at runtime." -ForegroundColor Green
