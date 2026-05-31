@echo off
setlocal

echo Setting up IRMS Output Analyzer environment...
echo.

set "VENV_DIR=.venv"
if exist "ven\\Scripts\\activate.bat" if not exist ".venv\\Scripts\\activate.bat" set "VENV_DIR=ven"
set "VENV_ACTIVATE=%VENV_DIR%\\Scripts\\activate.bat"
set "VENV_PYTHON=%VENV_DIR%\\Scripts\\python.exe"

if exist "%VENV_PYTHON%" (
  call :is_python_usable "%VENV_PYTHON%"
  if errorlevel 1 (
    if exist "ven\\Scripts\\python.exe" (
      call :is_python_usable "ven\\Scripts\\python.exe"
      if not errorlevel 1 (
        set "VENV_DIR=ven"
        set "VENV_ACTIVATE=ven\\Scripts\\activate.bat"
        set "VENV_PYTHON=ven\\Scripts\\python.exe"
      )
    ) else (
      set "VENV_DIR=ven"
      set "VENV_ACTIVATE=ven\\Scripts\\activate.bat"
      set "VENV_PYTHON=ven\\Scripts\\python.exe"
    )
  )
)

call :resolve_python
if errorlevel 1 exit /b 1

rem Create virtual environment if it doesn't exist.
if not exist "%VENV_ACTIVATE%" (
  echo Creating virtual environment in .\\%VENV_DIR% ...
  %PYTHON_CMD% -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo Failed to create virtual environment.
    exit /b 1
  )
)

if not exist "%VENV_PYTHON%" (
  echo Virtual environment Python executable not found at %VENV_PYTHON%.
  exit /b 1
)

rem Upgrade pip and install dependencies.
"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 (
  echo Failed to upgrade pip.
  exit /b 1
)

"%VENV_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install requirements.
  exit /b 1
)

echo.
echo Setup complete.
echo You can now run start_app.bat to start the app.
pause
exit /b 0

:resolve_python
set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    exit /b 0
  )
)

where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
    exit /b 0
  )
)

echo Python interpreter not found. Install Python 3 and make sure `py` or `python` works in PATH.
exit /b 1

:is_python_usable
if "%~1"=="" exit /b 1
if not exist "%~1" exit /b 1
"%~1" -c "import sys" >nul 2>&1
exit /b %ERRORLEVEL%
