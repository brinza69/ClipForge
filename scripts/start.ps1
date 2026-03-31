# ============================================================================
# ClipForge — Start Script
# Launches both the Next.js frontend and Python worker backend.
# ============================================================================

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ClipForge — Starting Services" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if Python venv exists
$venvPython = "$ProjectRoot\server\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "  [ERROR] Python virtual environment not found." -ForegroundColor Red
    Write-Host "  Run .\scripts\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}

# Check if node_modules exists
if (-not (Test-Path "$ProjectRoot\node_modules")) {
    Write-Host "  [ERROR] Node modules not found." -ForegroundColor Red
    Write-Host "  Run .\scripts\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}

Write-Host "  Starting Python worker on port 8420..." -ForegroundColor White
$workerJob = Start-Process -PassThru -NoNewWindow -FilePath $venvPython -ArgumentList "$ProjectRoot\server\main.py" -WorkingDirectory "$ProjectRoot\server"

Start-Sleep -Seconds 2

Write-Host "  Starting Next.js frontend on port 3000..." -ForegroundColor White
$frontendJob = Start-Process -PassThru -NoNewWindow -FilePath "npm" -ArgumentList "run","dev" -WorkingDirectory $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ClipForge is running!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Frontend:  http://localhost:3000" -ForegroundColor White
Write-Host "  Worker:    http://localhost:8420" -ForegroundColor White
Write-Host "  API Docs:  http://localhost:8420/docs" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Press Ctrl+C to stop both services." -ForegroundColor DarkGray
Write-Host ""

# Wait for either process to exit
try {
    while ($true) {
        if ($workerJob.HasExited) {
            Write-Host "  [WARN] Python worker exited with code $($workerJob.ExitCode)" -ForegroundColor Yellow
            break
        }
        if ($frontendJob.HasExited) {
            Write-Host "  [WARN] Next.js frontend exited with code $($frontendJob.ExitCode)" -ForegroundColor Yellow
            break
        }
        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host ""
    Write-Host "  Stopping services..." -ForegroundColor Yellow
    
    if (-not $workerJob.HasExited) {
        Stop-Process -Id $workerJob.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  Python worker stopped." -ForegroundColor DarkGray
    }
    if (-not $frontendJob.HasExited) {
        Stop-Process -Id $frontendJob.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  Next.js frontend stopped." -ForegroundColor DarkGray
    }
    
    Write-Host "  ClipForge stopped." -ForegroundColor Cyan
}
