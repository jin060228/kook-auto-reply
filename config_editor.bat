@echo off
rem ---------- locate python (absolute path, avoid PATH issue) ----------
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python37\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python37\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY set "PY=python"
title KOOK Auto Reply Config
cd /d "%~dp0"
"%PY%" config_editor.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to run, please make sure Python is installed.
)
pause
