# ============================================================================
# ClipForge — START EVERYTHING with one command.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
#
# Starts (idempotent — anything already running is skipped):
#   1. Backend A  :8420  (GPU 0)          — FastAPI worker, data\
#   2. Backend B  :8421  (GPU 1, if any)  — FastAPI worker, data_b\
#   3. Frontend   :3000  (Next.js dev)
#   4. ComfyUI    :8188 / :8189           — local image generation (per GPU)
#   5. Ollama     :11434 / :11435         — local Qwen LLMs (one per GPU)
#
# Optional switches:
#   -Narators    also start the narrator video-factory watchdog (sheet rig)
#   -QwenWatch   also start the 30-min local-LLM bug-watch loop
#   -NoComfy / -NoLlm / -NoFrontend   skip that piece
# ============================================================================
param(
    [switch]$Narators,
    [switch]$QwenWatch,
    [switch]$NoComfy,
    [switch]$NoLlm,
    [switch]$NoFrontend
)
$ErrorActionPreference = "Continue"
$root = "D:\clipforge"
$py   = "$root\server\.venv\Scripts\python.exe"

function Test-Port($port) {
    [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

# GPUs in index order (GPU 0 -> backend A / comfy :8188, GPU 1 -> B / :8189).
$gpus = @()
foreach ($line in (& nvidia-smi -L 2>$null)) {
    if ($line -match 'UUID:\s*(GPU-[0-9a-fA-F-]+)') { $gpus += $Matches[1] }
}
Write-Host ("GPUs detected: {0}" -f $gpus.Count) -ForegroundColor Cyan

# --- 1+2. Backends ----------------------------------------------------------
$backends = @(
    @{ Port = 8420; Data = "$root\data";   Name = "A" },
    @{ Port = 8421; Data = "$root\data_b"; Name = "B" }
)
$nb = [Math]::Min([Math]::Max(1, $gpus.Count), 2)
for ($i = 0; $i -lt $nb; $i++) {
    $b = $backends[$i]
    if (Test-Port $b.Port) { Write-Host "Backend $($b.Name) :$($b.Port) already running" -ForegroundColor DarkGray; continue }
    $env:CLIPFORGE_MAX_CONCURRENT_JOBS = "1"
    $env:CLIPFORGE_DATA_DIR = $b.Data
    if ($gpus.Count -gt $i) { $env:CUDA_VISIBLE_DEVICES = $gpus[$i] }
    Start-Process -WindowStyle Hidden -FilePath $py `
        -ArgumentList '-m','uvicorn','main:app','--app-dir','server','--port',"$($b.Port)" `
        -WorkingDirectory $root `
        -RedirectStandardOutput (Join-Path $b.Data "backend.out.log") `
        -RedirectStandardError  (Join-Path $b.Data "backend.err.log")
    Write-Host "Started backend $($b.Name) :$($b.Port)" -ForegroundColor Green
}

# --- 3. Frontend ------------------------------------------------------------
if (-not $NoFrontend) {
    if (Test-Port 3000) { Write-Host "Frontend :3000 already running" -ForegroundColor DarkGray }
    else {
        Start-Process -WindowStyle Hidden -FilePath "cmd.exe" `
            -ArgumentList "/c", "npm run dev" -WorkingDirectory $root `
            -RedirectStandardOutput "$root\data\frontend.out.log" `
            -RedirectStandardError  "$root\data\frontend.err.log"
        Write-Host "Started frontend :3000 (Next.js)" -ForegroundColor Green
    }
}

# --- 4. ComfyUI (image generation) -----------------------------------------
if (-not $NoComfy) {
    $comfy = @( @{ Port = 8188; Bat = "start_comfy_gpu0.bat" }, @{ Port = 8189; Bat = "start_comfy_gpu1.bat" } )
    for ($i = 0; $i -lt [Math]::Min($gpus.Count, 2); $i++) {
        $c = $comfy[$i]
        if (Test-Port $c.Port) { Write-Host "ComfyUI :$($c.Port) already running" -ForegroundColor DarkGray; continue }
        if (Test-Path "$root\scripts\$($c.Bat)") {
            Start-Process -WindowStyle Minimized -FilePath "$root\scripts\$($c.Bat)" -WorkingDirectory $root
            Write-Host "Started ComfyUI :$($c.Port)" -ForegroundColor Green
        }
    }
}

# --- 5. Local Qwen LLMs (Ollama, one per GPU) ------------------------------
if (-not $NoLlm) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$root\scripts\start_ollama_dual.ps1"
}

# --- Optional extras --------------------------------------------------------
if ($QwenWatch) { & powershell -NoProfile -ExecutionPolicy Bypass -File "$root\scripts\start_qwen_watch.ps1" }
if ($Narators)  {
    Start-Process -WindowStyle Hidden -FilePath 'powershell.exe' `
        -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"$root\scripts\watchdog.ps1"
    Write-Host "Started narrator watchdog (sheet video factory)" -ForegroundColor Green
}

# --- Summary ----------------------------------------------------------------
Start-Sleep -Seconds 3
Write-Host "`n=== ClipForge stack ===" -ForegroundColor Cyan
$rows = @(
    @{ N = "Backend A";  P = 8420 },  @{ N = "Backend B";  P = 8421 },
    @{ N = "Frontend";   P = 3000 },  @{ N = "ComfyUI-0";  P = 8188 },
    @{ N = "ComfyUI-1";  P = 8189 },  @{ N = "Qwen 8b";    P = 11434 },
    @{ N = "Qwen 4b";    P = 11435 }
)
foreach ($r in $rows) {
    $up = if (Test-Port $r.P) { "UP  " } else { "down" }
    Write-Host ("  {0,-10} :{1,-6} {2}" -f $r.N, $r.P, $up)
}
Write-Host "`nApp: http://localhost:3000   (backends warm up ~60-90s)"
Write-Host "Narators OFF by default - resume: scheduled task 09:00, or -Narators switch."