# ============================================================================
# ClipForge - Autonomous Sheet Runner
# Processes the configured Google Sheet row-by-row with ZERO interaction:
#   pull next row -> run all role presets (voice + big RO subs + avatar,
#   1-min split, GPU erase) -> upload each to its Drive folder ->
#   write the AI description back to the sheet -> advance -> repeat.
#
# Prereqs (one-time): backend running on :8420, Google connected, sheet
# configured, the role presets saved, API keys set. Then just:
#   .\scripts\run-sheet.ps1
# ============================================================================
param(
  [string]$Backend = "http://127.0.0.1:8420",
  [string[]]$Presets = @("narator", "comentator", "povestitor"),
  [string]$Engine = "openai",          # transcript clean/translate engine
  [string]$Lang = "ro",                 # target subtitle/voice language
  [int]$MaxRows = 100                    # safety cap
)

$body = @{
  variant_preset_ids   = $Presets
  from_sheets          = $true
  auto_detect_zones    = $true
  erase_method         = "lama"
  transcript_engine    = $Engine
  transcript_target_lang = $Lang
} | ConvertTo-Json

Write-Host "ClipForge sheet runner — presets: $($Presets -join ', ')" -ForegroundColor Cyan
for ($i = 0; $i -lt $MaxRows; $i++) {
  $pull = Invoke-RestMethod -Method Post -Uri "$Backend/api/sheets/pull-next"
  if ($pull.empty) { Write-Host "No URL at row $($pull.row) — finished." -ForegroundColor Green; break }
  Write-Host "`n=== Row $($pull.row) (nr $($pull.number)) ===" -ForegroundColor Yellow
  Write-Host "  $($pull.url)"
  $resp = Invoke-RestMethod -Method Post -Uri "$Backend/api/auto" -ContentType "application/json" -Body $body
  $jid = $resp.job_id
  Write-Host "  job $jid"
  $lastMsg = ""
  do {
    Start-Sleep -Seconds 6
    $job = Invoke-RestMethod -Uri "$Backend/api/jobs/$jid"
    if ($job.progress_message -ne $lastMsg) { Write-Host "    $($job.progress_message)"; $lastMsg = $job.progress_message }
  } while ($job.status -eq "running" -or $job.status -eq "queued")
  if ($job.status -ne "done") { Write-Host "  FAILED: $($job.error)" -ForegroundColor Red; break }
  Write-Host "  done -> uploaded to Drive + description written." -ForegroundColor Green
}
Write-Host "`nAll done." -ForegroundColor Cyan
