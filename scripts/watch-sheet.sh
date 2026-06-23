#!/usr/bin/env bash
# ClipForge - Continuous Sheet Watcher (Linux/macOS/Git-Bash).
# Processes every available row, then WAITS and re-checks for new rows you add
# later — runs until you stop it (Ctrl+C). Stops on a row failure (so real
# problems surface); the failed row keeps its place so a restart resumes there.
set -u
BACKEND="${BACKEND:-http://127.0.0.1:8420}"
PRESETS_JSON="${PRESETS_JSON:-[\"narator\",\"comentator\",\"povestitor\"]}"
ENGINE="${ENGINE:-openai}"; LANG_="${LANG_:-ro}"; IDLE="${IDLE:-300}"
body="{\"variant_preset_ids\":$PRESETS_JSON,\"from_sheets\":true,\"auto_detect_zones\":true,\"erase_method\":\"lama\",\"transcript_engine\":\"$ENGINE\",\"transcript_target_lang\":\"$LANG_\"}"

echo "ClipForge continuous watcher — Ctrl+C to stop."
while :; do
  pull=$(curl -s -X POST "$BACKEND/api/sheets/pull-next" || true)
  if [ -z "$pull" ]; then echo "$(date +%H:%M:%S) backend unreachable — retry 30s"; sleep 30; continue; fi
  if echo "$pull" | grep -q '"empty":true'; then echo "$(date +%H:%M:%S) no new rows — waiting ${IDLE}s"; sleep "$IDLE"; continue; fi
  row=$(echo "$pull" | grep -oE '"row":[0-9]+' | head -1 | cut -d: -f2)
  echo "=== Row $row ==="
  jid=$(curl -s -X POST "$BACKEND/api/auto" -H "Content-Type: application/json" -d "$body" | grep -oE '"job_id":"[^"]*"' | cut -d'"' -f4)
  last=""
  while :; do
    sleep 6
    job=$(curl -s "$BACKEND/api/jobs/$jid")
    st=$(echo "$job" | grep -oE '"status":"[a-z]+"' | head -1 | cut -d'"' -f4)
    msg=$(echo "$job" | grep -oE '"progress_message":"[^"]*"' | head -1 | cut -d'"' -f4)
    [ "$msg" != "$last" ] && { echo "    $msg"; last="$msg"; }
    case "$st" in done) echo "  done."; break;; failed|error|cancelled) echo "  STOPPED on failure"; exit 1;; esac
  done
done
