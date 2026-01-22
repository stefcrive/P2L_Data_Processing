.PHONY: analyzer pangea

analyzer:
\tstreamlit run IRMS_output_analyzer.py

pangea:
\tstreamlit run Pangea_paleorecord_visualizer.py
