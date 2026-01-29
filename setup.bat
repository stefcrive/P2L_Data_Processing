@echo off
setlocal

echo Setting up IRMS Output Analyzer environment...
echo.

rem Create virtual environment if it doesn't exist
if not exist "ven\\Scripts\\activate.bat" (
  echo Creating virtual environment in .\\ven ...
  python -m venv ven
  if errorlevel 1 (
    echo Failed to create virtual environment.
    exit /b 1
  )
)

rem Activate virtual environment
call ven\\Scripts\\activate.bat
if errorlevel 1 (
  echo Failed to activate virtual environment.
  exit /b 1
)

rem Upgrade pip and install dependencies
python -m pip install --upgrade pip
if errorlevel 1 (
  echo Failed to upgrade pip.
  exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install requirements.
  exit /b 1
)

echo.
echo Setup complete.
echo You can now run run_analyzer.bat to start the app.
pause
