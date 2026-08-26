# ============================================================================
# ClipForge - Dual-GPU launcher (run after a reboot)
# Starts: backend A on the RTX 3060 (:8420, data/), backend B on the GTX 1660
# SUPER (:8421, data_b/), the dual dispatcher, and the live status-writer.
# GPU UUIDs are auto-detected, so it survives PCI re-ordering.
# Live dashboard: http://localhost:8420/exports/live.html
# (Run only when nothing is already running on 8420/8421.)
# ============================================================================
$ErrorActionPreference = "Continue"
$root = "D:\clipforge"
$py = "$root\server\.venv\Scripts\python.exe"

# --- detect the two GPUs' UUIDs (order-independent) ---
$gpus = & nvidia-smi -L
function Uuid($pattern) {
    $line = $gpus | Select-String $pattern | Select-Object -First 1
    if ($line) { ($line.ToString() -replace '.*UUID:\s*(GPU-[0-9a-fA-F-]+)\).*', '$1') } else { "" }
}
$u3060 = Uuid "3060"
$u1660 = Uuid "1660"
Write-Host "3060 = $u3060" -ForegroundColor Cyan
Write-Host "1660 = $u1660" -ForegroundColor Cyan
if (-not $u3060 -or -not $u1660) { Write-Host "Could not detect both GPUs — aborting." -ForegroundColor Red; exit 1 }

# ATENTIE: scriptul asta leaga placile dupa MODEL (A=3060, B=1660), pe cand
# `watchdog.ps1` — cel care porneste rigul in mod normal — le leaga dupa INDEX,
# ceea ce pe rigul asta da A=1660, B=3060. Deci maparea depinde de CU CE ai
# pornit. Daca folosesti scriptul asta, `stare.py` si pagina live vor eticheta
# placile invers, iar un lot trimis pe "1660" va ajunge pe 3060.

# --- backend A on the 3060 (main data/) ---
$env:CLIPFORGE_MAX_CONCURRENT_JOBS = "1"
$env:CUDA_VISIBLE_DEVICES = $u3060
$env:CLIPFORGE_DATA_DIR = "$root\data"
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList '-m','uvicorn','main:app','--app-dir','server','--port','8420' -WorkingDirectory $root -RedirectStandardOutput "$root\data\backendA.out.log" -RedirectStandardError "$root\data\backendA.err.log"

# --- backend B on the 1660 (data_b/) ---
$env:CUDA_VISIBLE_DEVICES = $u1660
$env:CLIPFORGE_DATA_DIR = "$root\data_b"
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList '-m','uvicorn','main:app','--app-dir','server','--port','8421' -WorkingDirectory $root -RedirectStandardOutput "$root\data_b\backendB.out.log" -RedirectStandardError "$root\data_b\backendB.err.log"

# --- wait for both to be healthy ---
foreach ($port in 8420, 8421) {
    for ($i = 0; $i -lt 45; $i++) {
        try { Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 2 | Out-Null; Write-Host "backend :$port up" -ForegroundColor Green; break }
        catch { Start-Sleep -Seconds 1 }
    }
}

# --- dispatcher (drives both backends) + live status-writer ---
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList "$root\scripts\dual_dispatch.py" -WorkingDirectory $root -RedirectStandardOutput "$root\data\dispatch.log" -RedirectStandardError "$root\data\dispatch.err.log"
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList "$root\scripts\dual_status_writer.py" -WorkingDirectory $root

Write-Host ""
Write-Host "Dual-GPU rig started." -ForegroundColor Green
Write-Host "  Live: http://localhost:8420/exports/live.html" -ForegroundColor White
Write-Host "  (frontend UI optional: npm run dev)" -ForegroundColor DarkGray
