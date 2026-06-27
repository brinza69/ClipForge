# ============================================================================
# ClipForge rig watchdog — keeps the dual-GPU video factory running FOREVER.
#
# Cold-starts AND supervises the whole rig:
#   - backend A : RTX 3060      (:8420, data/)
#   - backend B : GTX 1660 SUPER(:8421, data_b/)
#   - dual_dispatch.py  (drives both backends from the Google Sheet)
#   - dual_status_writer.py (live dashboard feed)
#
# Every 30s it health-checks each piece and restarts whatever has died
# (crash, sleep/resume, logoff/session-end, etc.). A backend that is bound
# but wedged for 3 min is killed and respawned. Single-instance (mutex).
#
# Meant to run as the "ClipForge-Watchdog" scheduled task (at logon), but can
# also be run by hand:  powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1
# Live dashboard: http://localhost:8420/exports/live.html   Log: data\watchdog.log
# ============================================================================
$ErrorActionPreference = "Continue"
$root = "D:\clipforge"
$py   = "$root\server\.venv\Scripts\python.exe"
$log  = "$root\data\watchdog.log"

# --- single-instance guard (don't stack watchdogs) ---
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\ClipForgeWatchdog', [ref]$createdNew)
if (-not $createdNew) { exit 0 }

# hang counters: consecutive ticks a backend was bound-but-unhealthy
$script:hang = @{ 8420 = 0; 8421 = 0 }

function Log($msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try { Add-Content -Path $log -Value $line -ErrorAction Stop } catch {}
}

function Get-Uuid($pattern) {
    $line = (& nvidia-smi -L) | Select-String $pattern | Select-Object -First 1
    if ($line) { ($line.ToString() -replace '.*UUID:\s*(GPU-[0-9a-fA-F-]+)\).*', '$1') } else { "" }
}

function Test-Health($port) {
    try { Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 3 | Out-Null; $true }
    catch { $false }
}

function Test-PortListening($port) {
    [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

function Test-ProcRunning($pattern) {
    [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -match $pattern })
}

function Start-Backend($port, $uuid, $dataDir, $name) {
    $env:CLIPFORGE_MAX_CONCURRENT_JOBS = "1"
    $env:CUDA_VISIBLE_DEVICES = $uuid
    $env:CLIPFORGE_DATA_DIR   = $dataDir
    Start-Process -WindowStyle Hidden -FilePath $py `
        -ArgumentList '-m','uvicorn','main:app','--app-dir','server','--port',"$port" `
        -WorkingDirectory $root `
        -RedirectStandardOutput (Join-Path $dataDir "backend.out.log") `
        -RedirectStandardError  (Join-Path $dataDir "backend.err.log")
    Log "started backend $name (:$port  $dataDir  gpu=$uuid)"
}

function Ensure-Backend($port, $uuid, $dataDir, $name) {
    if (Test-Health $port) { $script:hang[$port] = 0; return }
    if (Test-PortListening $port) {
        # bound but not answering health — wedged or still booting
        $script:hang[$port] = $script:hang[$port] + 1
        if ($script:hang[$port] -ge 6) {
            $owner = (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
            if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; Log "killed wedged backend $name pid=$owner (:$port)" }
            $script:hang[$port] = 0
        } else {
            Log "backend $name (:$port) bound but unhealthy ($($script:hang[$port])/6) — waiting"
        }
        return
    }
    $script:hang[$port] = 0
    if (-not $uuid) { Log "ERROR: no GPU UUID for $name — cannot start"; return }
    Start-Backend $port $uuid $dataDir $name
}

function Ensure-Proc($pattern, $scriptPath, $outLog, $errLog, $name) {
    if (Test-ProcRunning $pattern) { return }
    Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList $scriptPath `
        -WorkingDirectory $root -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Log "started $name"
}

Log "watchdog online (pid=$PID)"
$tick = 0
while ($true) {
    $u3060 = Get-Uuid "3060"
    $u1660 = Get-Uuid "1660"
    Ensure-Backend 8420 $u3060 "$root\data"   "A(3060)"
    Ensure-Backend 8421 $u1660 "$root\data_b" "B(1660)"

    $aOk = Test-Health 8420
    $bOk = Test-Health 8421
    if ($aOk -and $bOk) {
        # dispatcher stdout MUST be dispatch.log — the status writer parses it for row numbers
        Ensure-Proc 'dual_dispatch\.py'      "$root\scripts\dual_dispatch.py"      "$root\data\dispatch.log"    "$root\data\dispatch.err.log" "dispatcher"
        Ensure-Proc 'dual_status_writer\.py' "$root\scripts\dual_status_writer.py" "$root\data\status.out.log"  "$root\data\status.err.log"   "status-writer"
    }

    if ($tick % 20 -eq 0) {
        Log ("heartbeat  A={0} B={1} dispatch={2} status={3}" -f `
            $aOk, $bOk, (Test-ProcRunning 'dual_dispatch\.py'), (Test-ProcRunning 'dual_status_writer\.py'))
    }
    $tick++
    Start-Sleep -Seconds 30
}
