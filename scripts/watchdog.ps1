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
# Local mutex stops same-session stacking. A PID lock file stops a SECOND
# watchdog from a DIFFERENT session (logon task + a manual run) — the original
# cause of TWO of every rig process. NOTE: a broad command-line scan is WRONG
# here — it ALSO matches the shell that LAUNCHES this script (its args contain
# this path), which made the watchdog exit immediately on every manual launch.
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\ClipForgeWatchdog', [ref]$createdNew)
if (-not $createdNew) { exit 0 }
$lock = "$root\data\watchdog.lock"
if (Test-Path $lock) {
    $oldPid = (Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($oldPid) {
        $op = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue
        if ($op -and $op.CommandLine -match 'watchdog\.ps1' -and [int]$oldPid -ne $PID) { exit 0 }
    }
}
Set-Content -Path $lock -Value $PID -Encoding ascii -Force

# hang counters: consecutive ticks a backend was bound-but-unhealthy
$script:hang = @{ 8420 = 0; 8421 = 0 }
# last (re)start time per port — give a fresh backend time to import torch +
# bind its port (60-90s on this box) before spawning ANOTHER. Without this the
# 30s tick starts duplicate backends that each load torch and caused the
# OOM / orphaned-whisper-worker pile-up.
$script:started = @{ 8420 = (Get-Date).AddSeconds(-999); 8421 = (Get-Date).AddSeconds(-999) }

function Log($msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try { Add-Content -Path $log -Value $line -ErrorAction Stop } catch {}
}

function Get-GpuUuids {
    # All NVIDIA GPU UUIDs in index order (GPU 0, GPU 1, ...). Model-agnostic so
    # the rig works on ANY NVIDIA box — a single card or the dual-GPU rig. (The
    # old version matched hardcoded model names "3060"/"1660" and started ZERO
    # backends on any other machine.)
    $uuids = @()
    foreach ($line in (& nvidia-smi -L)) {
        if ($line -match 'UUID:\s*(GPU-[0-9a-fA-F-]+)') { $uuids += $Matches[1] }
    }
    return ,$uuids
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
    # Healthy = the real backend is bound on this port. The venv launcher's
    # second python.exe is harmless (it does not bind the port); do NOT hunt it
    # down — killing it takes the real backend with it.
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
    if (((Get-Date) - $script:started[$port]).TotalSeconds -lt 90) {
        Log "backend $name (:$port) still booting (<90s since last start) — not respawning"
        return
    }
    $script:started[$port] = Get-Date
    Start-Backend $port $uuid $dataDir $name
}

function Ensure-Proc($pattern, $scriptPath, $outLog, $errLog, $name) {
    # NOTE: the venv python.exe is a LAUNCHER that spawns a real child, so ONE
    # logical process (dispatcher / status writer / backend) shows up as TWO
    # python.exe. Do NOT "dedupe" by killing the extra — that kills the launcher
    # and takes the real process down with it, which caused the restart churn +
    # OOM earlier. Just make sure at least one is running.
    if (Test-ProcRunning $pattern) { return }
    Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList $scriptPath `
        -WorkingDirectory $root -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Log "started $name"
}

Log "watchdog online (pid=$PID)"
$tick = 0
while ($true) {
    # Detect GPUs each tick (a card can drop/return). GPU 0 -> backend A
    # (:8420, data/); GPU 1 -> backend B (:8421, data_b/). A single-GPU PC runs
    # ONLY backend A; capped at 2 backends.
    $gpus = @(Get-GpuUuids)
    $nb = [Math]::Min(2, $gpus.Count)
    if ($nb -lt 1) {
        if ($tick % 20 -eq 0) { Log "no NVIDIA GPU detected by nvidia-smi — cannot start backends" }
        $tick++; Start-Sleep -Seconds 30; continue
    }
    $oks = @()
    for ($i = 0; $i -lt $nb; $i++) {
        $port = 8420 + $i
        $data = if ($i -eq 0) { "$root\data" } else { "$root\data_b" }
        $nm   = [string][char](65 + $i)   # A, B
        Ensure-Backend $port $gpus[$i] $data $nm
        $oks += [bool](Test-Health $port)
    }

    # Start dispatcher + status writer once ALL detected backends are healthy.
    if (($oks.Count -gt 0) -and (-not ($oks -contains $false))) {
        # dispatcher stdout MUST be dispatch.log — the status writer parses it for row numbers
        Ensure-Proc 'dual_dispatch\.py'      "$root\scripts\dual_dispatch.py"      "$root\data\dispatch.log"   "$root\data\dispatch.err.log" "dispatcher"
        Ensure-Proc 'dual_status_writer\.py' "$root\scripts\dual_status_writer.py" "$root\data\status.out.log" "$root\data\status.err.log"   "status-writer"
    }

    if ($tick % 20 -eq 0) {
        Log ("heartbeat  gpus={0} backendsOk=[{1}] dispatch={2} status={3}" -f `
            $gpus.Count, ($oks -join ','), (Test-ProcRunning 'dual_dispatch\.py'), (Test-ProcRunning 'dual_status_writer\.py'))
    }
    $tick++
    Start-Sleep -Seconds 30
}
