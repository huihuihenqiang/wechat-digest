@echo off
setlocal
cd /d "%~dp0"

echo ================================
echo   WeChat Digest - Fetch Once
echo ================================
echo.
echo This runs one manual desktop WeChat fetch, summarizes it with the current prompt, and sends it to you.
echo It uses OpenClaw delivery by default, so the reply appears from the bot side.
echo Default delivery is OpenClaw, so send "wd status" to the bot once before using it.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\fetch_once.ps1"
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
    echo Fetch once failed. Exit code: %EXITCODE%
) else (
    echo Fetch once finished.
)
pause
exit /b %EXITCODE%
