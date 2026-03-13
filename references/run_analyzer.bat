@echo off
echo Starting IRMS Output Analyzer...
echo.

rem Ensure virtual environment exists
if not exist "ven\\Scripts\\activate.bat" (
  echo Virtual environment not found. Running setup...
  call setup.bat
  if errorlevel 1 (
    echo Setup failed. Exiting.
    exit /b 1
  )
)

rem Activate virtual environment
call ven\\Scripts\\activate.bat
if errorlevel 1 (
  echo Failed to activate virtual environment.
  exit /b 1
)

rem Run the analyzer using streamlit
streamlit run IRMS_output_analyzer.py

rem Keep window open
pause
