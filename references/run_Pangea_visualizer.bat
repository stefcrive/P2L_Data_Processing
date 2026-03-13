@echo off
echo Starting Pangea visualizer...
echo.

rem Activate virtual environment
call ven\Scripts\activate.bat

rem Run the analyzer using streamlit
streamlit run Pangea_paleorecord_visualizer.py

rem Keep window open
pause