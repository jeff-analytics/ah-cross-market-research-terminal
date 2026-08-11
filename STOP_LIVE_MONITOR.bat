@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title Stop A-H Live Monitor
if not exist data mkdir data
>"data\live_monitor.stop" echo stop
echo Stop signal written.
timeout /t 3 /nobreak >nul
if exist "data\live_monitor.pid" (
  set /p LIVEPID=<"data\live_monitor.pid"
  if defined LIVEPID taskkill /PID !LIVEPID! /T >nul 2>&1
)
echo Live monitor has stopped or is exiting.
pause
exit /b 0
