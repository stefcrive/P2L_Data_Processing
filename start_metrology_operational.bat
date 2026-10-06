@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_metrology.ps1" -Operational %*
set "METROLOGY_EXIT_CODE=%ERRORLEVEL%"
if errorlevel 1 pause
endlocal & exit /b %METROLOGY_EXIT_CODE%
