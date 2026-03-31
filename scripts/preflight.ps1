# ============================================================================
# ClipForge — Preflight Check Script
# Checks all system dependencies and reports readiness status.
# ============================================================================

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ClipForge — Preflight System Check" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$issues = @()
$warnings = @()
$ok = @()

# --- Node.js ---
try {
    $nodeVer = (node --version 2>$null)
    if ($nodeVer) {
        $ok += "Node.js: $nodeVer"
    } else {
        $issues += "Node.js: NOT FOUND — Install from https://nodejs.org"
    }
} catch {
    $issues += "Node.js: NOT FOUND — Install from https://nodejs.org"
}

# --- npm ---
try {
    $npmVer = (npm --version 2>$null)
    if ($npmVer) {
        $ok += "npm: v$npmVer"
    } else {
        $issues += "npm: NOT FOUND — Should come with Node.js"
    }
} catch {
    $issues += "npm: NOT FOUND — Should come with Node.js"
}

# --- Python 3.12 ---
$py312Found = $false
try {
    $py312Ver = (py -3.12 --version 2>$null)
    if ($py312Ver) {
        $ok += "Python 3.12: $py312Ver"
        $py312Found = $true
    }
} catch {}

if (-not $py312Found) {
    # Check if python3.12 is on PATH
    try {
        $py312Ver = (python3.12 --version 2>$null)
        if ($py312Ver) {
            $ok += "Python 3.12: $py312Ver (via python3.12)"
            $py312Found = $true
        }
    } catch {}
}

if (-not $py312Found) {
    # List available Python versions
    $pyList = ""
    try { $pyList = (py --list 2>$null) | Out-String } catch {}
    
    $issues += @(
        "Python 3.12: NOT FOUND",
        "  -> faster-whisper requires Python 3.10-3.12 (3.14 is NOT compatible)",
        "  -> Install Python 3.12 from: https://www.python.org/downloads/release/python-3129/",
        "  -> During install, check 'Add to PATH' and ensure 'py launcher' is selected",
        "  -> Available Python versions: $($pyList.Trim())"
    )
}

# --- FFmpeg ---
try {
    $ffmpegVer = (ffmpeg -version 2>$null) | Select-Object -First 1
    if ($ffmpegVer) {
        $ok += "FFmpeg: $($ffmpegVer.Substring(0, [Math]::Min(60, $ffmpegVer.Length)))..."
    } else {
        $issues += "FFmpeg: NOT FOUND — Install from https://ffmpeg.org/download.html"
    }
} catch {
    $issues += "FFmpeg: NOT FOUND — Install from https://ffmpeg.org/download.html"
}

# --- yt-dlp ---
try {
    $ytdlpVer = (yt-dlp --version 2>$null)
    if ($ytdlpVer) {
        $ok += "yt-dlp: v$ytdlpVer"
    } else {
        $issues += "yt-dlp: NOT FOUND — Install with: pip install yt-dlp"
    }
} catch {
    $issues += "yt-dlp: NOT FOUND — Install with: pip install yt-dlp"
}

# --- GPU / CUDA ---
try {
    $gpuInfo = (nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader 2>$null)
    if ($gpuInfo) {
        $ok += "GPU: $($gpuInfo.Trim())"
        
        # Check CUDA availability
        try {
            $cudaVer = (nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>$null)
            $ok += "CUDA Driver: $($cudaVer.Trim())"
        } catch {}
    } else {
        $warnings += "GPU: No NVIDIA GPU detected — transcription will use CPU (slower but works)"
    }
} catch {
    $warnings += "GPU: nvidia-smi not found — transcription will use CPU (slower but works)"
}

# --- Disk Space ---
try {
    $drive = (Get-PSDrive -Name D -ErrorAction SilentlyContinue)
    if ($drive) {
        $freeGB = [math]::Round($drive.Free / 1GB, 1)
        if ($freeGB -lt 10) {
            $warnings += "Disk Space (D:): ${freeGB}GB free — recommend at least 20GB for video processing"
        } else {
            $ok += "Disk Space (D:): ${freeGB}GB free"
        }
    } else {
        $drive = (Get-PSDrive -Name C -ErrorAction SilentlyContinue)
        if ($drive) {
            $freeGB = [math]::Round($drive.Free / 1GB, 1)
            $ok += "Disk Space (C:): ${freeGB}GB free"
        }
    }
} catch {}

# --- Report ---
Write-Host "--- PASSED ---" -ForegroundColor Green
foreach ($item in $ok) {
    Write-Host "  [OK] $item" -ForegroundColor Green
}
Write-Host ""

if ($warnings.Count -gt 0) {
    Write-Host "--- WARNINGS ---" -ForegroundColor Yellow
    foreach ($item in $warnings) {
        Write-Host "  [!!] $item" -ForegroundColor Yellow
    }
    Write-Host ""
}

if ($issues.Count -gt 0) {
    Write-Host "--- MISSING / FAILED ---" -ForegroundColor Red
    foreach ($item in $issues) {
        Write-Host "  [XX] $item" -ForegroundColor Red
    }
    Write-Host ""
}

# --- Final Verdict ---
Write-Host "========================================" -ForegroundColor Cyan
if ($issues.Count -eq 0 -and $warnings.Count -eq 0) {
    Write-Host "  READY — All dependencies found!" -ForegroundColor Green
    $exitCode = 0
} elseif ($issues.Count -eq 0) {
    Write-Host "  READY WITH WARNINGS" -ForegroundColor Yellow
    Write-Host "  Core features will work. See warnings above." -ForegroundColor Yellow
    $exitCode = 0
} else {
    Write-Host "  MISSING REQUIREMENTS" -ForegroundColor Red
    Write-Host "  Please install missing dependencies above." -ForegroundColor Red
    $exitCode = 1
}
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

exit $exitCode
