@echo off
title KOOK 自动回复启动器
echo ============================================
echo   KOOK 语音频道自动回复 - 一键启动
echo ============================================
echo.

rem ---------- 配置 ----------
rem KOOK 可执行文件（Squirrel 启动器会自动加载最新版本）
set "KOOK_LAUNCHER=D:\KOOK\KOOK.exe"
rem CDP 调试端口
set "CDP_PORT=9222"
rem 启动参数
set "CDP_ARGS=--remote-debugging-port=%CDP_PORT% --remote-allow-origins=*"

rem ---------- 1. 检查并准备 KOOK ----------
echo [1/3] 检查 KOOK 运行状态...
tasklist /FI "IMAGENAME eq KOOK.exe" 2>nul | findstr /i "KOOK.exe" >nul
if %errorlevel%==0 (
    echo   检测到 KOOK 已运行，检查调试端口 %CDP_PORT%...
    curl -s -m 2 http://127.0.0.1:%CDP_PORT%/json/version >nul 2>&1
    if %errorlevel%==0 (
        echo   CDP 调试端口已开启，直接复用当前 KOOK 实例（不打断语音）。
    ) else (
        echo   当前 KOOK 未开启调试端口，需要重启（语音连接会断开）...
        taskkill /IM KOOK.exe /F >nul 2>&1
        timeout /t 3 /nobreak >nul
        echo   以调试模式启动 KOOK...
        start "" "%KOOK_LAUNCHER%" %CDP_ARGS%
    )
) else (
    echo   KOOK 未运行，以调试模式启动...
    start "" "%KOOK_LAUNCHER%" %CDP_ARGS%
)

rem ---------- 2. 等待 KOOK 初始化 ----------
echo.
echo [2/3] 等待 KOOK 初始化（15 秒，请确认已登录并进入目标语音频道）...
timeout /t 15 /nobreak >nul

rem 再次确认调试端口就绪
echo   确认调试端口...
set /a tries=0
:wait_port
curl -s -m 2 http://127.0.0.1:%CDP_PORT%/json/version >nul 2>&1
if %errorlevel%==0 goto port_ok
set /a tries+=1
if %tries% GEQ 10 (
    echo   [警告] 调试端口未就绪。请确认 KOOK 已启动，并检查是否需要重新登录。
    goto port_fail
)
timeout /t 3 /nobreak >nul
goto wait_port
:port_ok
echo   调试端口就绪。
:port_fail

rem ---------- 3. 启动自动回复脚本 ----------
echo.
echo [3/3] 打开可视化配置界面...
cd /d "%~dp0"
python config_editor.py
if %errorlevel% neq 0 (
    echo.
    echo [错误] 脚本异常退出，错误码 %errorlevel%
)
echo.
pause
