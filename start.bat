@echo off
title my-agent Web UI
cd /d "%~dp0"

echo ============================================
echo   my-agent - Multi-Agent Framework
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo   [Error] Python not found
    pause
    exit /b 1
)

echo   Starting server...
echo.

python launch.py
pause
