@echo off
rem Double-click to set up (or re-run to repair). Optional: setup.cmd "D:\path\to\ComfyUI"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
