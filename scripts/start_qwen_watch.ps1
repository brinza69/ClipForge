# ClipForge — run the local-LLM bug watch on a loop (default: every 30 min).
# Findings accumulate in data\qwen_findings.md. Free/offline (local Qwen).
param([int]$Minutes = 30)
$py = "D:\clipforge\server\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Start-Process -WindowStyle Hidden -FilePath $py `
    -ArgumentList "D:\clipforge\scripts\qwen_bug_watch.py", "--loop", "$Minutes" `
    -WorkingDirectory "D:\clipforge" `
    -RedirectStandardOutput "D:\clipforge\data\qwen_watch.out.log" `
    -RedirectStandardError  "D:\clipforge\data\qwen_watch.err.log"
Write-Host "Qwen bug watch running every $Minutes min -> data\qwen_findings.md"