@echo off
setlocal
cd /d "%~dp0"

echo ================================
echo   WeChat Digest Assistant - Start
echo ================================
echo.

echo == Pre-flight checks ==

if not exist "%~dp0config.yaml" (
    echo ERROR: config.yaml not found. Copy config.example.yaml to config.yaml first.
    pause
    exit /b 1
)

if not exist "%~dp0.env" (
    echo WARNING: .env file not found.  LLM API keys may be missing.
)

if not exist "%~dp0.venv\Scripts\wechat-digest.exe" (
    echo ERROR: .venv\Scripts\wechat-digest.exe not found. Run pip install -e . first.
    pause
    exit /b 1
)

echo   config.yaml: OK
echo   .venv: OK
echo.

echo == Starting OpenClaw Gateway ==
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_openclaw_gateway.ps1"
set "GW_EXITCODE=%ERRORLEVEL%"
if not "%GW_EXITCODE%"=="0" (
    echo.
    echo ==============================================
    echo   Gateway startup FAILED (exit code: %GW_EXITCODE%)
    echo.
    echo   Troubleshooting:
    echo     1. Check gateway error log:
    echo        %%LOCALAPPDATA%%\Temp\openclaw\openclaw-gateway-task.err.log
    echo     2. Check gateway output log:
    echo        %%LOCALAPPDATA%%\Temp\openclaw\openclaw-gateway-task.out.log
    echo     3. Verify OpenClaw is installed:
    echo        %%APPDATA%%\npm\openclaw.cmd version
    echo     4. Check weixin channel is logged in:
    echo        %%APPDATA%%\npm\openclaw.cmd channels status
    echo ==============================================
    pause
    exit /b 1
)

echo.
echo == Checking Gateway status ==
if not exist "%APPDATA%\npm\openclaw.cmd" (
    echo ERROR: OpenClaw command not found: "%APPDATA%\npm\openclaw.cmd"
    pause
    exit /b 1
)
call "%APPDATA%\npm\openclaw.cmd" gateway status --require-rpc
if errorlevel 1 (
    echo WARNING: Gateway status check failed. It may still be starting -- wait a moment and retry.
)

echo.
echo == Starting digest scheduler ==
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_digest_scheduler.ps1"
if errorlevel 1 (
    echo.
    echo ERROR: Failed to start local digest scheduler.
    echo Check logs: logs\digest-scheduler.err.log
    pause
    exit /b 1
)

echo.
echo ================================
echo   Startup finished.
echo.
echo   Send these commands to WeChat:
echo     wd status
echo     wd daily yesterday
echo     wd search keyword
echo     wd group group_name
echo     wd memory 2026-05-10
echo     wd schedule 22:00
echo.
echo   To exit: double-click stop_wechat_digest.cmd
echo   Manual one-time AI summary: double-click fetch_once.cmd
echo ================================
pause
