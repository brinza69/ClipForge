# ============================================================================
# ClipForge — start local Qwen LLMs (Ollama), one instance per NVIDIA GPU.
#
#   biggest-VRAM GPU  -> instance A  http://127.0.0.1:11434  (model: qwen3:8b)
#   second GPU        -> instance B  http://127.0.0.1:11435  (model: qwen3:4b)
#
# GPU-count adaptive: a single-GPU PC gets only instance A. Models are pulled
# on first use; run  scripts\pull_qwen_models.ps1  once to pre-download.
# Used by scripts\qwen_bug_watch.py (bug-detection orchestrator).
# ============================================================================
$ErrorActionPreference = "Continue"

function Find-Ollama {
    $c = Get-Command ollama -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $p) { return $p }
    return $null
}

$ollama = Find-Ollama
if (-not $ollama) {
    Write-Host "Ollama not found. Install it first:  winget install Ollama.Ollama" -ForegroundColor Red
    exit 1
}

# GPUs sorted by VRAM desc -> big model goes to the big card.
$gpus = @()
foreach ($line in (& nvidia-smi --query-gpu=uuid,memory.total,name --format=csv,noheader)) {
    $parts = $line -split ",\s*"
    if ($parts.Count -ge 3) {
        $gpus += [pscustomobject]@{ Uuid = $parts[0]; Mem = [int]($parts[1] -replace "[^\d]", ""); Name = $parts[2] }
    }
}
$gpus = @($gpus | Sort-Object Mem -Descending)
if ($gpus.Count -lt 1) { Write-Host "No NVIDIA GPU detected." -ForegroundColor Red; exit 1 }

function Start-OllamaInstance($hostPort, $gpuUuid, $label) {
    $running = Get-NetTCPConnection -State Listen -LocalPort ($hostPort -split ":")[-1] -ErrorAction SilentlyContinue
    if ($running) { Write-Host "Instance on $hostPort already running — skipping."; return }
    $env:OLLAMA_HOST = $hostPort
    $env:CUDA_VISIBLE_DEVICES = $gpuUuid
    $env:OLLAMA_KEEP_ALIVE = "2m"          # unload fast — the GPUs also serve the video rig + ComfyUI
    $env:OLLAMA_MAX_LOADED_MODELS = "1"
    Start-Process -WindowStyle Hidden -FilePath $ollama -ArgumentList "serve"
    Write-Host "Started Ollama $label on $hostPort (GPU $gpuUuid)"
}

Start-OllamaInstance "127.0.0.1:11434" $gpus[0].Uuid "A/qwen3:8b  ($($gpus[0].Name))"
if ($gpus.Count -ge 2) {
    Start-OllamaInstance "127.0.0.1:11435" $gpus[1].Uuid "B/qwen3:4b  ($($gpus[1].Name))"
} else {
    Write-Host "Single GPU — only instance A started (qwen_bug_watch adapts automatically)."
}
Write-Host "Done. Health check:  curl http://127.0.0.1:11434/api/tags"