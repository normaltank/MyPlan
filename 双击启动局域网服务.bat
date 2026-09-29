@echo off
title Faculty Harvest Web Console
cd /d "%~dp0"
if not exist "%~dp0scripts\start_local_services.ps1" (
    echo Missing scripts\start_local_services.ps1.
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local_services.ps1"
set "launcher_exit=%errorlevel%"
if not "%launcher_exit%"=="0" (
    pause
    exit /b %launcher_exit%
)
