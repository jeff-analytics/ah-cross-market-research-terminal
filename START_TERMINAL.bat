@echo off
REM Unified launcher delegates to scripts\start_terminal.py.
REM That launcher runs ensure_universe.py, ensure_daily_market_data.py, ensure_live_monitor.py, then uvicorn server:app.
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title A/H Cross-Market Research Terminal v5.1.8
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Virtual environment not found.
  echo Run INSTALL_WINDOWS.bat first.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" scripts\start_terminal.py
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Terminal launcher exited with error code %RC%.
pause
exit /b %RC%
