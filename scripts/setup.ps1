# ============================================================================
# ClipForge - Setup Script
# Prepares the full development environment.
# ============================================================================

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ClipForge - Environment Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Step 1: Create data directories ---
Write-Host "[1/5] Creating data directories..." -ForegroundColor White

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

# --- Step 2: Copy .env.example to .env.local if missing ---
Write-Host "[2/5] Checking .env.local..." -ForegroundColor White

$envExample = "$ProjectRoot\.env.example"
$envLocal = "$ProjectRoot\.env.local"

if (-not (Test-Path $envLocal)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envLocal
        Write-Host "  Created .env.local from .env.example" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: .env.example not found, skipping" -ForegroundColor Yellow
    }
} else {
    Write-Host "  .env.local already exists" -ForegroundColor DarkGray
}
Write-Host ""

# --- Step 3: Install Node.js dependencies ---
Write-Host "[3/5] Installing Node.js dependencies..." -ForegroundColor White
Push-Location $ProjectRoot
try {
    npm install 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    Write-Host "  Node.js dependencies installed." -ForegroundColor Green
} catch {
    Write-Host "  ERROR: npm install failed." -ForegroundColor Red
}
Pop-Location
Write-Host ""

# --- Step 4: Setup Python 3.12 venv ---
Write-Host "[4/5] Setting up Python virtual environment..." -ForegroundColor White

$venvPath = "$ProjectRoot\server\.venv"
$py312Available = $false

try {
    $pyVer = & py -3.12 --version 2>$null
    if ($pyVer -match "3\.12") {
        $py312Available = $true
        Write-Host "  Found: $pyVer" -ForegroundColor Green
    }
} catch {}

if ($py312Available) {
    if (-not (Test-Path "$venvPath\Scripts\python.exe")) {
        Write-Host "  Creating venv with Python 3.12..." -ForegroundColor White
        & py -3.12 -m venv $venvPath
        Write-Host "  Virtual environment created." -ForegroundColor Green
    } else {
        Write-Host "  Virtual environment already exists." -ForegroundColor DarkGray
    }

    Write-Host "  Installing Python dependencies..." -ForegroundColor White
    & "$venvPath\Scripts\python.exe" -m pip install --upgrade pip -q 2>&1 | Out-Null
    & "$venvPath\Scripts\python.exe" -m pip install -r "$ProjectRoot\server\requirements.txt" 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    Write-Host "  Python dependencies installed." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  Python 3.12 is required but was not found." -ForegroundColor Yellow
    Write-Host "  Install from: https://www.python.org/downloads/release/python-3129/" -ForegroundColor White
    Write-Host "  Check 'Add to PATH' and 'py launcher' during install." -ForegroundColor White
    Write-Host "  Re-run this script after installation." -ForegroundColor White
    Write-Host ""
}
Write-Host ""

# --- Step 5: Update .gitignore ---
Write-Host "[5/5] Updating .gitignore..." -ForegroundColor White

$gitignore = "$ProjectRoot\.gitignore"
$additions = @("data/", "server/.venv/", "*.db", "*.db-journal", ".env.local")

if (Test-Path $gitignore) {
    $content = Get-Content $gitignore -Raw
    foreach ($entry in $additions) {
        if ($content -notmatch [regex]::Escape($entry)) {
            Add-Content -Path $gitignore -Value "`n$entry"
            Write-Host "  Added: $entry" -ForegroundColor Green
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
if (-not $py312Available) {
    Write-Host "  Next: Install Python 3.12, then re-run setup." -ForegroundColor Yellow
} else {
    Write-Host "  Next: .\scripts\start.ps1" -ForegroundColor White
}
Write-Host ""
