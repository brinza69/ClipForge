# ============================================================================
# ClipForge — Setup Script
# Prepares the full development environment for ClipForge.
# ============================================================================

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ClipForge — Environment Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Step 1: Run Preflight ---
Write-Host "[1/6] Running preflight checks..." -ForegroundColor White
$preflight = & "$PSScriptRoot\preflight.ps1"
Write-Host $preflight
Write-Host ""

# --- Step 2: Create data directories ---
Write-Host "[2/6] Creating data directories..." -ForegroundColor White

$dirs = @(
    "$ProjectRoot\data",
    "$ProjectRoot\data\media",
    "$ProjectRoot\data\exports",
    "$ProjectRoot\data\cache",
    "$ProjectRoot\data\temp",
    "$ProjectRoot\data\thumbnails",
    "$ProjectRoot\data\db"
)

foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "  Created: $dir" -ForegroundColor Green
    } else {
        Write-Host "  Exists:  $dir" -ForegroundColor DarkGray
    }
}
Write-Host ""

# --- Step 3: Install Node.js dependencies ---
Write-Host "[3/6] Installing Node.js dependencies..." -ForegroundColor White
Push-Location $ProjectRoot
try {
    npm install 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    Write-Host "  Node.js dependencies installed." -ForegroundColor Green
} catch {
    Write-Host "  ERROR: npm install failed. Please run 'npm install' manually." -ForegroundColor Red
}
Pop-Location
Write-Host ""

# --- Step 4: Setup Python 3.12 virtual environment ---
Write-Host "[4/6] Setting up Python virtual environment..." -ForegroundColor White

$venvPath = "$ProjectRoot\server\.venv"
$py312Available = $false

# Try py -3.12 first
try {
    $pyVer = & py -3.12 --version 2>$null
    if ($pyVer -match "3\.12") {
        $py312Available = $true
        Write-Host "  Found: $pyVer" -ForegroundColor Green
    }
} catch {}

if ($py312Available) {
    if (-not (Test-Path "$venvPath\Scripts\python.exe")) {
        Write-Host "  Creating virtual environment with Python 3.12..." -ForegroundColor White
        & py -3.12 -m venv $venvPath
        Write-Host "  Virtual environment created at: $venvPath" -ForegroundColor Green
    } else {
        Write-Host "  Virtual environment already exists." -ForegroundColor DarkGray
    }

    # Install Python dependencies
    Write-Host "  Installing Python dependencies (this may take a few minutes)..." -ForegroundColor White
    & "$venvPath\Scripts\python.exe" -m pip install --upgrade pip 2>&1 | Select-Object -Last 1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    & "$venvPath\Scripts\python.exe" -m pip install -r "$ProjectRoot\server\requirements.txt" 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    Write-Host "  Python dependencies installed." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  ============================================" -ForegroundColor Yellow
    Write-Host "  Python 3.12 is required but was not found." -ForegroundColor Yellow
    Write-Host "  ============================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  To install Python 3.12:" -ForegroundColor White
    Write-Host "  1. Go to: https://www.python.org/downloads/release/python-3129/" -ForegroundColor White
    Write-Host "  2. Download 'Windows installer (64-bit)'" -ForegroundColor White
    Write-Host "  3. Run the installer" -ForegroundColor White
    Write-Host "  4. IMPORTANT: Check 'Add Python 3.12 to PATH'" -ForegroundColor Yellow
    Write-Host "  5. Verify the 'py launcher' option is checked" -ForegroundColor White
    Write-Host "  6. Re-run this setup script after installation" -ForegroundColor White
    Write-Host ""
    Write-Host "  Note: This will NOT affect your existing Python 3.14 installation." -ForegroundColor DarkGray
    Write-Host ""
}
Write-Host ""

# --- Step 5: Create .env.local if missing ---
Write-Host "[5/6] Checking .env.local..." -ForegroundColor White

$envFile = "$ProjectRoot\.env.local"
if (-not (Test-Path $envFile)) {
    @"
# ClipForge Environment Configuration
# ====================================

# Python Worker URL (default: http://localhost:8420)
NEXT_PUBLIC_WORKER_URL=http://localhost:8420

# Data Directories (defaults to ./data/)
CLIPFORGE_DATA_DIR=./data
CLIPFORGE_MEDIA_DIR=./data/media
CLIPFORGE_EXPORTS_DIR=./data/exports
CLIPFORGE_CACHE_DIR=./data/cache
CLIPFORGE_TEMP_DIR=./data/temp
CLIPFORGE_DB_PATH=./data/db/clipforge.db

# Transcription Settings
WHISPER_MODEL=base
WHISPER_DEVICE=auto
WHISPER_COMPUTE_TYPE=float16

# Export Settings
DEFAULT_EXPORT_WIDTH=1080
DEFAULT_EXPORT_HEIGHT=1920
DEFAULT_EXPORT_FPS=30
DEFAULT_EXPORT_BITRATE=8M
"@ | Set-Content -Path $envFile -Encoding UTF8
    Write-Host "  Created .env.local with defaults." -ForegroundColor Green
} else {
    Write-Host "  .env.local already exists." -ForegroundColor DarkGray
}
Write-Host ""

# --- Step 6: Add data/ to .gitignore ---
Write-Host "[6/6] Updating .gitignore..." -ForegroundColor White

$gitignore = "$ProjectRoot\.gitignore"
$additions = @("data/", "server/.venv/", "*.db", "*.db-journal")

if (Test-Path $gitignore) {
    $content = Get-Content $gitignore -Raw
    foreach ($entry in $additions) {
        if ($content -notmatch [regex]::Escape($entry)) {
            Add-Content -Path $gitignore -Value "`n$entry"
            Write-Host "  Added to .gitignore: $entry" -ForegroundColor Green
        }
    }
} else {
    $additions -join "`n" | Set-Content -Path $gitignore
}
Write-Host ""

# --- Done ---
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
if (-not $py312Available) {
    Write-Host "  1. Install Python 3.12 (see instructions above)" -ForegroundColor Yellow
    Write-Host "  2. Re-run: .\scripts\setup.ps1" -ForegroundColor Yellow
    Write-Host "  3. Start with: .\scripts\start.ps1" -ForegroundColor White
} else {
    Write-Host "  Start with: .\scripts\start.ps1" -ForegroundColor White
}
Write-Host ""
