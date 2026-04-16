@echo off
setlocal

if "%~4"=="" (
  echo Usage: launch_backend.bat ROOT_DIR VENV_PYTHON BACKEND_PORT RUN_MODE
  exit /b 1
)

set "ROOT_DIR=%~1"
set "VENV_PYTHON=%~2"
set "BACKEND_PORT=%~3"
set "RUN_MODE=%~4"

if not exist "%VENV_PYTHON%" (
  echo Virtualenv python executable was not found: %VENV_PYTHON%
  exit /b 1
)

cd /d "%ROOT_DIR%" || (
  echo Could not switch to repository root: %ROOT_DIR%
  exit /b 1
)

echo Starting IRMS backend in %RUN_MODE% mode on http://127.0.0.1:%BACKEND_PORT%
echo Press Ctrl+C to stop the backend.
echo.

if /I "%RUN_MODE%"=="prod" (
  "%VENV_PYTHON%" -m uvicorn services.irms_api.api.main:app --host 127.0.0.1 --port %BACKEND_PORT%
) else (
  "%VENV_PYTHON%" -m uvicorn services.irms_api.api.main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload
)

set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Backend exited with code %EXIT_CODE%.
  echo.
)

exit /b %EXIT_CODE%
