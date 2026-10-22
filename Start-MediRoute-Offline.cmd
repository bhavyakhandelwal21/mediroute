@echo off
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
  echo Python environment is missing. Follow the setup instructions in README.md first.
  pause
  exit /b 1
)
"backend\.venv\Scripts\python.exe" run_offline.py
if errorlevel 1 pause
