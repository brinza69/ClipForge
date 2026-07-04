#!/usr/bin/env bash
# One-shot: ensure backend up, run a parallel job (XTTS, no Sheets), poll to
# done, then probe the output for 1080x1920 @ 60fps. Runs entirely inside one
# WSL session so the VM stays alive for the whole ~10 min.
set -u
cd /mnt/f/ClipForge

# 1. ensure backend
if ! curl -s -o /dev/null -m 3 http://localhost:8420/api/parallel/recent; then
  echo "backend down -> starting"
  ./dev.sh start >/dev/null 2>&1
fi
for i in $(seq 1 30); do
  curl -s -o /dev/null -m 3 http://localhost:8420/api/parallel/recent && break
  sleep 2
done
echo "backend: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8420/api/parallel/recent)"

# 2. submit
cat > /tmp/tp.json <<'EOF'
{
  "url": "https://vm.tiktok.com/ZNRwB492A/",
  "title": "S5.12 1080p+60fps test",
  "erase_zone": {"x":100,"y":100,"w":880,"h":150,"src_w":1080,"src_h":1920},
  "caption_zone": {"x":100,"y":1550,"w":880,"h":200,"src_w":1080,"src_h":1920},
  "erase_mode": "blur",
  "erase_auto_detect": false,
  "transcript_engine": "ollama",
  "variants": [
    {"name":"A","tts_engine":"xtts","tts_voice_id":"Daniel_Mihai.mp3","tts_language":"en","caption_template_id":"bold_impact","caption_words_per_chunk":1},
    {"name":"B","tts_engine":"xtts","tts_voice_id":"Daniel_Mihai.mp3","tts_language":"en","caption_template_id":"bold_impact","caption_words_per_chunk":1}
  ]
}
EOF
RESP=$(curl -s -X POST http://localhost:8420/api/parallel/start -H "Content-Type: application/json" -d @/tmp/tp.json)
echo "start: $RESP"
JOB=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
PROJ=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['project_id'])" 2>/dev/null)
[ -z "$JOB" ] && { echo "NO JOB ID"; exit 1; }
echo "job=$JOB proj=$PROJ"

# 3. poll
for i in $(seq 1 220); do
  R=$(curl -s http://localhost:8420/api/jobs/$JOB)
  ST=$(echo "$R" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status'),round((d.get('progress') or 0)*100),d.get('progress_message',''))" 2>/dev/null)
  echo "[$i] $ST"
  case "$ST" in
    done*) break;;
    failed*|cancelled*) echo "TERMINAL-BAD"; echo "$R"; exit 1;;
  esac
  sleep 6
done

# 4. probe outputs
echo "===== PROBE OUTPUTS ====="
echo "--- source ---"
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -of default=noprint_wrappers=1 "data/media/$PROJ/video.mp4"
for d in v0 v1; do
  F=$(ls data/media/$PROJ/$d/*.mp4 2>/dev/null | grep -v -E 'video_|voice' | head -1)
  echo "--- $d: $(basename "$F") ---"
  ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,nb_read_frames -of default=noprint_wrappers=1 "$F" 2>/dev/null
  DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$F")
  echo "duration=$DUR"
done
echo "===== DONE ====="
