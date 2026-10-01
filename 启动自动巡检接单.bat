@echo off
chcp 65001 >nul
title 高校师资工单巡检器
echo =====================================================================
echo  高校师资工单巡检器
echo  - 本机 SQLite 只读总数，前台地址读取本机端口配置
echo  - 不读取教师记录，不认领或采集
echo  - 采集由常驻 python -m server.agent_worker 执行
echo =====================================================================
echo.
if exist "%~dp0.runtime\worker-venv\Scripts\python.exe" (
    "%~dp0.runtime\worker-venv\Scripts\python.exe" "%~dp0server\auto_dispatcher.py" --interval 5
) else (
    python "%~dp0server\auto_dispatcher.py" --interval 5
)
pause
