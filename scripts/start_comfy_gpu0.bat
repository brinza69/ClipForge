@echo off
REM ClipForge — ComfyUI instance for GPU 0 (GTX 1660 SUPER, 6GB) on port 8188.
title ComfyUI GPU0 :8188
cd /d D:\clipforge\tools\ComfyUI
call venv\Scripts\activate
python main.py --listen 127.0.0.1 --port 8188 --cuda-device 0 --output-directory D:\clipforge\data\comfy\outputs\gpu0 --input-directory D:\clipforge\data\comfy\input --temp-directory D:\clipforge\data\comfy\temp\gpu0
pause
