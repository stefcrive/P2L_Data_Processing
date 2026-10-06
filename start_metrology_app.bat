@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_metrology.ps1" %*
set "METROLOGY_EXIT_CODE=%ERRORLEVEL%"
if errorlevel 1 (
  echo Metrology startup failed. Review the message above.
  pause
)
endlocal & exit /b %METROLOGY_EXIT_CODE%
