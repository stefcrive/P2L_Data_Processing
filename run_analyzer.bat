@echo off
echo Starting IRMS Output Analyzer...
echo.

rem Activate virtual environment
call ven\Scripts\activate.bat

rem Run the analyzer using streamlit
streamlit run IRMS_output_analyzer.py

rem Keep window open
pause