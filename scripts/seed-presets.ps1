# Copy the committed (redacted) role presets from seeds/ into the live data/ store.
# Idempotent. After seeding, re-add your Drive folder link + avatar videos in the UI.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = "$root\seeds\variant_presets"
$dst = "$root\data\variant_presets"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Get-ChildItem "$src\*.json" | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $dst $_.Name) -Force
    Write-Host "  seeded $($_.Name)" -ForegroundColor Green
}
Write-Host "Done. NOTE: presets have NO Drive folder (redacted) — set it per role in the UI," -ForegroundColor Yellow
Write-Host "and re-upload the avatar videos under Commentators." -ForegroundColor Yellow
