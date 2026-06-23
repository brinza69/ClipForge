# ============================================================================
# ClipForge - Continuous Sheet Watcher
# Like run-sheet, but NEVER stops on its own: it processes every available row,
# and when the sheet is exhausted it WAITS and re-checks, so any new rows you
# add later get picked up automatically. Leave it running. Ctrl+C to stop.
#
# On a row FAILURE it stops (so real problems surface) rather than silently
# skipping — a failed row keeps its place (next_row doesn't advance), so after
# you fix the cause you can just restart and it resumes there.
# ============================================================================
param(
  [string]$Backend = "http://127.0.0.1:8420",
  [string[]]$Presets = @("narator", "comentator", "povestitor"),
  [string]$Engine = "openai",
  [string]$Lang = "ro",
  [int]$IdleWaitSec = 300        # how long to wait when the sheet has no new rows
)
$body = @{
  variant_preset_ids     = $Presets
  from_sheets            = $true
  auto_detect_zones      = $true
  erase_method           = "lama"
  transcript_engine      = $Engine
  transcript_target_lang = $Lang
} | ConvertTo-Json

Write-Host "ClipForge continuous watcher — presets: $($Presets -join ', '). Ctrl+C to stop." -ForegroundColor Cyan
while ($true) {
  try { $pull = Invoke-RestMethod -Method Post -Uri "$Backend/api/sheets/pull-next" }
  catch { Write-Host "$(Get-Date -Format HH:mm:ss) backend unreachable — retry in 30s" -ForegroundColor DarkYellow; Start-Sleep 30; continue }

  if ($pull.empty) {
    Write-Host "$(Get-Date -Format HH:mm:ss) no new rows — waiting $IdleWaitSec s…" -ForegroundColor DarkGray
    Start-Sleep -Seconds $IdleWaitSec; continue
  }

  Write-Host "`n=== Row $($pull.row) (nr $($pull.number)) ===" -ForegroundColor Yellow
  $resp = Invoke-RestMethod -Method Post -Uri "$Backend/api/auto" -ContentType "application/json" -Body $body
  $jid = $resp.job_id
  $last = ""
  do {
    Start-Sleep -Seconds 6
    $job = Invoke-RestMethod -Uri "$Backend/api/jobs/$jid"
    if ($job.progress_message -ne $last) { Write-Host "    $($job.progress_message)"; $last = $job.progress_message }
  } while ($job.status -in @("running", "queued"))

  if ($job.status -eq "done") { Write-Host "  done -> Drive + description written." -ForegroundColor Green }
  else { Write-Host "  STOPPED on failure: $($job.error)" -ForegroundColor Red; Write-Host "  (row kept its place; fix the cause and restart to resume here)"; break }
}
