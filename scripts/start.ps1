# ============================================================================
# ClipForge - Start Script
# Launches both the Next.js frontend and Python worker backend.
# Kills leftover processes, waits for backend health, handles Ctrl+C.
# ============================================================================

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Stop-PortProcess($port) {
    $pids = netstat -ano | Select-String ":$port\s" | ForEach-Object {
        ($_ -split '\s+')[-1]
    } | Sort-Object -Unique | Where-Object { $_ -ne '0' }
    foreach ($pid in $pids) {
        try {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            Write-Host "  Killed process $pid on port $port" -ForegroundColor Yellow
        } catch {}
    }
}

function Wait-ForBackend($url, $maxWait) {
    $elapsed = 0
    while ($elapsed -lt $maxWait) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Seconds 1
        $elapsed++
        Write-Host "  Waiting for backend... ($elapsed/$maxWait)" -ForegroundColor DarkGray
    }
    return $false
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ClipForge - Starting Services" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

$venvPython = "$ProjectRoot\server\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "  [ERROR] Python venv not found at $venvPython" -ForegroundColor Red
    Write-Host "  Run .\scripts\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path "$ProjectRoot\node_modules")) {
    Write-Host "  [ERROR] node_modules not found." -ForegroundColor Red
    Write-Host "  Run .\scripts\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# Kill leftover processes on our ports
# ---------------------------------------------------------------------------

Write-Host "  Cleaning up stale processes..." -ForegroundColor White
Stop-PortProcess 3000
Stop-PortProcess 8420
Start-Sleep -Seconds 1

# ---------------------------------------------------------------------------
# Start backend
# ---------------------------------------------------------------------------

Write-Host "  Starting Python backend on port 8420..." -ForegroundColor White
$workerJob = Start-Process -PassThru -NoNewWindow -FilePath $venvPython -ArgumentList "main.py" -WorkingDirectory "$ProjectRoot\server"

if (-not $workerJob) {
    Write-Host "  [ERROR] Failed to start Python backend." -ForegroundColor Red
    exit 1
}

# Wait for backend to be healthy before starting frontend
Write-Host "  Waiting for backend health check..." -ForegroundColor White
$backendReady = Wait-ForBackend "http://localhost:8420/api/health" 30

if (-not $backendReady) {
    Write-Host "  [ERROR] Backend failed to start within 30 seconds." -ForegroundColor Red
    Write-Host "  Check the backend logs for errors." -ForegroundColor Yellow
    if (-not $workerJob.HasExited) {
        Stop-Process -Id $workerJob.Id -Force -ErrorAction SilentlyContinue
    }
    exit 1
}

Write-Host "  Backend is healthy!" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Start frontend
# ---------------------------------------------------------------------------

Write-Host "  Starting Next.js frontend on port 3000..." -ForegroundColor White
$frontendJob = Start-Process -PassThru -NoNewWindow -FilePath "npm.cmd" -ArgumentList "run","dev" -WorkingDirectory "$ProjectRoot"

if (-not $frontendJob) {
    Write-Host "  [ERROR] Failed to start Next.js frontend." -ForegroundColor Red
    if (-not $workerJob.HasExited) {
        Stop-Process -Id $workerJob.Id -Force -ErrorAction SilentlyContinue
    }
    exit 1
}

# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ClipForge is running!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Frontend:  http://localhost:3000" -ForegroundColor White
Write-Host "  Backend:   http://localhost:8420" -ForegroundColor White
Write-Host "  API Docs:  http://localhost:8420/docs" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Press Ctrl+C to stop both services." -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# Monitor loop with clean shutdown on Ctrl+C
# ---------------------------------------------------------------------------

try {
    while ($true) {
        if ($workerJob.HasExited) {
            Write-Host "  [WARN] Python backend exited unexpectedly (exit code: $($workerJob.ExitCode))." -ForegroundColor Yellow
            break
        }
        if ($frontendJob.HasExited) {
            Write-Host "  [WARN] Next.js frontend exited unexpectedly (exit code: $($frontendJob.ExitCode))." -ForegroundColor Yellow
            break
        }
        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host ""
    Write-Host "  Stopping services..." -ForegroundColor Yellow
    if (-not $workerJob.HasExited) {
        Stop-Process -Id $workerJob.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  Stopped backend (PID $($workerJob.Id))" -ForegroundColor DarkGray
    }
    if ($null -ne $frontendJob -and -not $frontendJob.HasExited) {
        Stop-Process -Id $frontendJob.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  Stopped frontend (PID $($frontendJob.Id))" -ForegroundColor DarkGray
    }
    # Also clean up any child processes left on the ports
    Stop-PortProcess 3000
    Stop-PortProcess 8420
    Write-Host "  ClipForge stopped." -ForegroundColor Cyan
}
