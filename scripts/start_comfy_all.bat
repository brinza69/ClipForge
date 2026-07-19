@echo off
REM ClipForge — start BOTH local ComfyUI instances (GPU0 :8188, GPU1 :8189).
REM Keep these windows open while using Local ComfyUI image generation.
cd /d D:\clipforge
start "ComfyUI GPU0" scripts\start_comfy_gpu0.bat
timeout /t 5
start "ComfyUI GPU1" scripts\start_comfy_gpu1.bat
echo Both ComfyUI instances launching (8188 / 8189). Keep them running.
