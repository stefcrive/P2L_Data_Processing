@echo off
setlocal

powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\kill_app.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %EXIT_CODE%
