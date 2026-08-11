@echo off
chcp 65001 >nul
title WMS 立体库看板（多库区）
cd /d "%~dp0"
echo ============================================
echo   WMS 立体库看板 - 多库区监控
echo   F区 + H区
echo ============================================
echo.
echo 启动服务中...
echo 尝试使用 python 启动...
python start.py 2>nul
if %errorlevel% neq 0 (
    echo python 不可用，尝试 py -3 ...
    py -3 start.py
)
pause
