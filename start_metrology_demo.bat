@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_metrology.ps1" -Demo %*
if errorlevel 1 pause
endlocal
