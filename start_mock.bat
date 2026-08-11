@echo off
chcp 65001 >nul
title WMS 立体库看板（模拟模式）
cd /d "%~dp0"
echo ============================================
echo   WMS 立体库看板 - 多库区监控（模拟数据）
echo ============================================
echo.
echo 启动服务（模拟模式）...
set WMS_MOCK_MODE=1
python start.py 2>nul
if %errorlevel% neq 0 (
    echo python 不可用，尝试 py -3 ...
    py -3 start.py
)
pause
