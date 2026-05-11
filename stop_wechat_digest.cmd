@echo off
setlocal
cd /d "%~dp0"

echo ================================
echo   Stop WeChat Digest Assistant
echo ================================
echo.
echo This stops the local scheduler and OpenClaw Gateway.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_wechat_digest.ps1"
echo.
pause
