# IRMS Output Analyzer (Streamlit)

Streamlit-based tools for IRMS data processing and visualization.

Key scripts:

- `IRMS_output_analyzer.py`: main Streamlit app for processing IRMS outputs.
- `interpolate_outliers.py`: helper routines for outlier interpolation.
- `Pangea_paleorecord_visualizer.py`: separate Streamlit visualizer.

Quick start:

1. Create/activate a virtual environment (optional but recommended).
2. Install dependencies: `pip install -r requirements.txt`.
3. Run the analyzer:
   - Windows: `run_analyzer.bat`
   - Or directly: `streamlit run IRMS_output_analyzer.py`

Notes:

- Keep large input files (e.g., `all_physical.tab`) outside git as configured in `.gitignore`.
