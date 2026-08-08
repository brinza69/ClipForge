# Porneste rigul la logon si continua exact de unde a ramas.
#
# Citeste data/rig_state.json (scris de scripts/rig_state.py), pune valorile in
# mediul PROCESULUI si abia apoi porneste watchdog-ul — watchdog-ul isi lanseaza
# backend-urile si dispecerul din propriul mediu, iar o variabila scrisa doar la
# nivel de utilizator NU ajunge la un proces deja pornit. Asta a facut o data ca
# outro-ul sa lipseasca din doua randari fara ca nimic sa se planga.
#
# Ce s-a terminat deja NU se tine minte aici: sheet-ul si Drive-ul sunt evidenta,
# iar deduplicarea sare peste ce exista deja. Reluarea nu re-randeaza.
#
# Instalare ca sarcina la logon (o singura data, din PowerShell ca administrator
# nu e necesar — sarcina ruleaza in contul curent):
#   schtasks /Create /TN ClipForgeBoot /SC ONLOGON /RL HIGHEST /F ^
#     /TR "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File D:\clipforge\scripts\rig_boot.ps1"

$ErrorActionPreference = 'Continue'
$root = 'D:\clipforge'
$py   = "$root\server\.venv\Scripts\python.exe"
$log  = "$root\data\rig_boot.log"

function Log($m) {
  $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
  $line | Tee-Object -FilePath $log -Append
}

Log "rig_boot pornit"

# 1) starea salvata -> mediul acestui proces
$stateJson = & $py "$root\scripts\rig_state.py" show 2>$null
$state = @{}
if (Test-Path "$root\data\rig_state.json") {
  try {
    $o = Get-Content "$root\data\rig_state.json" -Raw | ConvertFrom-Json
    foreach ($p in $o.PSObject.Properties) { $state[$p.Name] = [string]$p.Value }
  } catch { Log "nu pot citi rig_state.json: $_" }
}
$implicite = @{
  CLIPFORGE_DISPATCHER       = 'dual_dispatch.py'
  CLIPFORGE_PRESETS          = 'narator,comentator'
  CLIPFORGE_DISPATCH_MIN_ROW = '2'
}
foreach ($k in $implicite.Keys) { if (-not $state.ContainsKey($k)) { $state[$k] = $implicite[$k] } }
foreach ($k in $state.Keys) {
  if ($state[$k]) { Set-Item -Path "env:$k" -Value $state[$k] }
}
Log ("config: dispecer={0} roluri={1} minRow={2}" -f `
     $env:CLIPFORGE_DISPATCHER, $env:CLIPFORGE_PRESETS, $env:CLIPFORGE_DISPATCH_MIN_ROW)

# 2) UN SINGUR watchdog, si acela pornit de aici.
#    Sarcina veche ClipForgeAutoStart lanseaza watchdog.ps1 direct, fara sa
#    incarce rig_state.json — deci ar rula cu configuratia gresita, tacut. Daca
#    a apucat sa porneasca inaintea noastra, il inlocuim. La logon nu randeaza
#    nimic inca, deci oprirea nu pierde nicio lucrare.
$existent = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and ($_.CommandLine -match 'watchdog\.ps1|dual_dispatch|herstory_dispatch')
}
foreach ($p in $existent) {
  try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Log "oprit $($p.ProcessId) (pornit fara stare)" } catch {}
}
if ($existent) { Start-Sleep -Seconds 4 }

Start-Process powershell -ArgumentList '-NoProfile','-WindowStyle','Hidden',
  '-ExecutionPolicy','Bypass','-File',"$root\scripts\watchdog.ps1"
Log "watchdog pornit"
