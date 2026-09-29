@echo off
chcp 65001 >nul
title 高校师资工单巡检器
echo =====================================================================
echo  高校师资工单巡检器
echo  - 目标服务: http://127.0.0.1:8000
echo  - 只监测待认领工单，不执行旧脚本采集
echo  - 请在 IDE 按 faculty-browser-harvest 技能派发学院 Subagent
echo =====================================================================
echo.
python "%~dp0server\auto_dispatcher.py" --interval 5
pause
