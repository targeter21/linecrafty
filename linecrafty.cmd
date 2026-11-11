@echo off
setlocal
if not exist "%~dp0python_path.txt" (
  echo Not set up yet: run setup.cmd first.
  exit /b 1
)
set /p PY=<"%~dp0python_path.txt"
"%PY%" -s "%~dp0linecrafty.py" %*
