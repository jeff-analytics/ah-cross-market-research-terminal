@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title A/H Cross-Market Research Terminal v5.1.8 - Install
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "LOG=%~dp0install_windows.log"
>"%LOG%" echo Installation started

echo ============================================================
echo A/H Cross-Market Research Terminal v5.1.8
echo Windows installer
echo ============================================================
where py >nul 2>&1
if not errorlevel 1 (set "BASE_PY=py"& set "BASE_ARGS=-3") else (where python >nul 2>&1 || goto :no_python& set "BASE_PY=python"& set "BASE_ARGS=")
%BASE_PY% %BASE_ARGS% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >>"%LOG%" 2>&1 || goto :bad_python
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment...
  %BASE_PY% %BASE_ARGS% -m venv .venv >>"%LOG%" 2>&1 || goto :fail
) else (echo [1/4] Existing virtual environment found.)
set "PY=%CD%\.venv\Scripts\python.exe"
echo [2/4] Updating pip...
"%PY%" -m pip install --upgrade pip >>"%LOG%" 2>&1 || goto :fail
echo [3/4] Installing dependencies...
"%PY%" -m pip install -r requirements.txt >>"%LOG%" 2>&1 || goto :fail
echo [4/4] Initializing local live database...
"%PY%" scripts\init_live_db.py >>"%LOG%" 2>&1 || goto :fail
echo.
echo Installation completed. Run START_TERMINAL.bat.
echo Log: %LOG%
pause
exit /b 0
:no_python
echo ERROR: Python 3.10+ was not found. Install Python and enable Add Python to PATH.
goto :endfail
:bad_python
echo ERROR: Python 3.10+ is required.
goto :endfail
:fail
echo ERROR: Installation failed. See %LOG%.
:endfail
pause
exit /b 1
