#!/usr/bin/env bash
# ClipForge - Autonomous Sheet Runner (Linux/macOS/Git-Bash).
# Processes the configured Google Sheet row-by-row with zero interaction:
#   pull next row -> run all role presets (voice + big RO subs + avatar,
#   1-min split, GPU erase) -> upload to Drive -> write description -> advance.
# Prereqs: backend on :8420, Google connected, sheet + presets + keys configured.
# Usage:  scripts/run-sheet.sh
set -e
BACKEND="${BACKEND:-http://127.0.0.1:8420}"
PRESETS_JSON="${PRESETS_JSON:-[\"narator\",\"comentator\",\"povestitor\"]}"
ENGINE="${ENGINE:-openai}"
LANG_="${LANG_:-ro}"
MAXROWS="${MAXROWS:-100}"

body="{\"variant_preset_ids\":$PRESETS_JSON,\"from_sheets\":true,\"auto_detect_zones\":true,\"erase_method\":\"lama\",\"transcript_engine\":\"$ENGINE\",\"transcript_target_lang\":\"$LANG_\"}"

for i in $(seq 1 "$MAXROWS"); do
  pull=$(curl -s -X POST "$BACKEND/api/sheets/pull-next")
  if echo "$pull" | grep -q '"empty":true'; then echo "Finished — no more rows."; break; fi
  row=$(echo "$pull" | grep -oE '"row":[0-9]+' | head -1 | cut -d: -f2)
  echo "=== Row $row ==="
  jid=$(curl -s -X POST "$BACKEND/api/auto" -H "Content-Type: application/json" -d "$body" | grep -oE '"job_id":"[^"]*"' | cut -d'"' -f4)
  echo "  job $jid"
  last=""
  while :; do
    sleep 6
    job=$(curl -s "$BACKEND/api/jobs/$jid")
    status=$(echo "$job" | grep -oE '"status":"[a-z]+"' | head -1 | cut -d'"' -f4)
    msg=$(echo "$job" | grep -oE '"progress_message":"[^"]*"' | head -1 | cut -d'"' -f4)
    [ "$msg" != "$last" ] && { echo "    $msg"; last="$msg"; }
    case "$status" in done) echo "  done."; break;; failed|error) echo "  FAILED"; exit 1;; esac
  done
done
echo "All done."
