@echo off
REM ClipForge — ComfyUI instance for GPU 1 (RTX 3060, 12GB) on port 8189.
title ComfyUI GPU1 :8189
cd /d D:\clipforge\tools\ComfyUI
call venv\Scripts\activate
python main.py --listen 127.0.0.1 --port 8189 --cuda-device 1 --output-directory D:\clipforge\data\comfy\outputs\gpu1 --input-directory D:\clipforge\data\comfy\input --temp-directory D:\clipforge\data\comfy\temp\gpu1
pause
