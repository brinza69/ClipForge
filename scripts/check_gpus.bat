@echo off
REM ClipForge — detect NVIDIA GPUs and save the report for the doodle
REM Local ComfyUI image-generation feature.
cd /d D:\clipforge
if not exist data\comfy mkdir data\comfy

nvidia-smi
nvidia-smi > data\comfy\gpu_info.txt
echo. >> data\comfy\gpu_info.txt
nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv >> data\comfy\gpu_info.txt

echo.
echo Saved report to data\comfy\gpu_info.txt
pause
