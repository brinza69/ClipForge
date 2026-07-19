# ClipForge — pre-download the local Qwen models (one per GPU tier).
#   qwen3:8b (~5.2 GB) -> big GPU instance   :11434
#   qwen3:4b (~2.6 GB) -> small GPU instance :11435
# Requires the instances started via scripts\start_ollama_dual.ps1 (pull goes
# through the instance so each model lands ready on its own port).
$ErrorActionPreference = "Continue"
function Find-Ollama {
    $c = Get-Command ollama -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $p) { return $p }
    return $null
}
$ollama = Find-Ollama
if (-not $ollama) { Write-Host "Ollama not installed (winget install Ollama.Ollama)" -ForegroundColor Red; exit 1 }

$env:OLLAMA_HOST = "127.0.0.1:11434"
Write-Host "Pulling qwen3:8b via :11434 ..." -ForegroundColor Cyan
& $ollama pull qwen3:8b

$second = Get-NetTCPConnection -State Listen -LocalPort 11435 -ErrorAction SilentlyContinue
if ($second) {
    $env:OLLAMA_HOST = "127.0.0.1:11435"
    Write-Host "Pulling qwen3:4b via :11435 ..." -ForegroundColor Cyan
    & $ollama pull qwen3:4b
} else {
    Write-Host "No second instance on :11435 (single GPU) — skipping qwen3:4b."
}
Write-Host "Done. Test: python scripts\qwen_bug_watch.py"