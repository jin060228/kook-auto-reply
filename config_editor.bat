@echo off
title KOOK Auto Reply Config
cd /d "%~dp0"
python config_editor.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to run, please make sure Python is installed.
)
pause
