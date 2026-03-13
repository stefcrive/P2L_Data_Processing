# IRMS Results Processing System - Technical Documentation

## Overview

The `IRMS_output_analyzer.py` is a comprehensive Streamlit-based application for analyzing Isotope Ratio Mass Spectrometer (IRMS) data. This system processes raw IRMS output files, performs calibration using reference standards, identifies outliers, generates diagnostic visualizations, and exports processed results.

## System Architecture

### Core Dependencies
- **Streamlit**: Web application framework
- **Pandas**: Data manipulation and analysis
- **NumPy**: Numerical computations
- **Plotly**: Interactive visualizations
- **SciPy**: Statistical analysis and linear regression
- **scikit-learn**: PCA analysis and data standardization
- **Matplotlib/Seaborn**: Additional plotting capabilities
- **ReportLab**: PDF generation capabilities

### File Structure Dependencies
- `Standards.csv`: Reference standards database containing true isotopic values
- Input: Excel files (.xls/.xlsx) with IRMS measurement data
- Output: Processed Excel files with calibrated results

## Core Functions and Modules

### 1. Data Processing Functions

#### `extract_number(text)` - Line 51
- **Purpose**: Extracts the first numerical value from a text string
- **Parameters**: `text` (string or NaN)
- **Returns**: Integer or None
- **Usage**: Helper function for parsing measurement identifiers

#### `extract_info_values(df)` - Line 58
- **Purpose**: Parses the 'Information' column to extract instrumental parameters
- **Parameters**: `df` (DataFrame with 'Information' column)
- **Returns**: DataFrame with additional columns for extracted parameters
- **Extracted Parameters**:
  - `acid_temp`: Acid temperature
  - `leak_rate`: System leak rate
  - `p_no_acid`: Pressure without acid
  - `p_gases`: Gas pressure
  - `total_co2`: Total CO2 measurement
  - `co2_after_exp`: CO2 after expansion
  - `left_mbar`/`right_mbar`: Left/right pressure readings
  - `left_pos`/`right_pos`: Left/right valve positions
  - `vm1_after_transfer`: VM1 after transfer measurement

**Regex Patterns Used**:
```python
patterns = {
    'acid_temp': r'Acid:\s*([\d.]+)',
    'leak_rate': r'LeakRate.*?:\s*([\d.]+)',
    'p_no_acid': r'P no Acid\s*:\s*([\d.]+)',
    'p_gases': r'P gases:\s*([\d.]+)',
    'total_co2': r'Total CO2\s*:\s*([\d.]+)',
    'co2_after_exp': r'CO2 after Exp\.:\s*([\d.]+)',
    'left_mbar': r'RefRe skipped: L mBar\s*([\d.]+)',
    'right_mbar': r'RefRe skipped: R mBar\s*([\d.]+)',
    'left_pos': r'L.*?Pos\s*([\d.]+)',
    'right_pos': r'R.*?Pos\s*([\d.]+)',
    'vm1_after_transfer': r'VM1 aftr Trfr\.:\s*([-\d.]+)'
}
```

### 2. Outlier Detection Functions

#### `identify_outliers(data, column, sigma_level)` - Line 99
- **Purpose**: Z-score based outlier detection
- **Algorithm**: Statistical outlier detection using standard deviation
- **Parameters**:
  - `data`: DataFrame containing the data
  - `column`: Column name to analyze
  - `sigma_level`: Number of standard deviations threshold
- **Returns**: Boolean Series indicating outliers
- **Formula**:
  ```
  outliers = (value > mean + σ_level * std) | (value < mean - σ_level * std)
  ```

#### `identify_outliers_iqr(data, column, iqr_multiplier=1.5)` - Line 125
- **Purpose**: Interquartile Range (IQR) based outlier detection
- **Algorithm**: Uses quartiles to identify outliers
- **Parameters**:
  - `data`: DataFrame containing the data
  - `column`: Column name to analyze
  - `iqr_multiplier`: Multiplier for IQR bounds (default 1.5)
- **Returns**: Boolean Series indicating outliers
- **Formula**:
  ```
  Q1 = 25th percentile
  Q3 = 75th percentile
  IQR = Q3 - Q1
  Lower bound = Q1 - (iqr_multiplier * IQR)
  Upper bound = Q3 + (iqr_multiplier * IQR)
  ```

### 3. Calibration System

#### `get_true_value(standard_name, isotopic_type)` - Line 173
- **Purpose**: Retrieves reference values from standards database
- **Parameters**:
  - `standard_name`: Name of reference standard
  - `isotopic_type`: 'δVPDB(13C)' or 'δVSMOW(18O)'
- **Returns**: True isotopic value for calibration
- **Data Source**: `Standards.csv` file

#### `single_point_calibration(raw_sample, raw_std, true_std)` - Line 184
- **Purpose**: Applies single-point calibration correction
- **Algorithm**: Linear transformation using one reference standard
- **Parameters**:
  - `raw_sample`: Raw measured value
  - `raw_std`: Raw standard measurement
  - `true_std`: True standard value
- **Formula**:
  ```
  calibrated = ((raw_sample + 1000) * (true_std + 1000)) / (raw_std + 1000) - 1000
  ```

#### `double_point_calibration(raw_sample, raw_rm1, true_rm1, raw_rm2, true_rm2)` - Line 189
- **Purpose**: Applies two-point linear calibration
- **Algorithm**: Linear regression using two reference standards
- **Parameters**:
  - `raw_sample`: Raw measured value
  - `raw_rm1`, `raw_rm2`: Raw measurements for standards 1 and 2
  - `true_rm1`, `true_rm2`: True values for standards 1 and 2
- **Formula**:
  ```
  slope = (true_rm2 - true_rm1) / (raw_rm2 - raw_rm1)
  intercept = true_rm1 - slope * raw_rm1
  calibrated = slope * raw_sample + intercept
  ```

#### `calibrate_results(standards_df, full_df, selected_standards)` - Line 196
- **Purpose**: Main calibration function for both δ13C and δ18O
- **Process**:
  1. Creates calibrated columns (`d13C_calibrated`, `d18O_calibrated`)
  2. Applies single or double-point calibration based on number of standards
  3. Handles both isotopic types simultaneously
- **Parameters**:
  - `standards_df`: Filtered standards data (outliers removed)
  - `full_df`: Complete dataset to calibrate
  - `selected_standards`: List of 1-2 reference standards

### 4. Visualization Functions

#### `create_calibration_plots(standards_reference_df, measurement_df, selected_standards, color_param)` - Line 247
- **Purpose**: Generates interactive calibration plots using Plotly
- **Features**:
  - Scatter plots for measured vs. true values
  - Color coding by user-selected parameter
  - Calibration lines (offset for single-point, regression for double-point)
  - Interactive hover information
  - Separate plots for δ13C and δ18O
- **Returns**: Dictionary containing Plotly figures for both isotopes

**Visualization Elements**:
- Marker colors: Viridis colorscale mapped to selected parameter
- Marker symbols: Different for standards vs. samples
- Calibration lines: Orange dashed (single-point) or blue solid (double-point)
- Annotations: Calibration equations and statistics

#### `create_diagnostic_plots(df, color_param)` - Line 406
- **Purpose**: Comprehensive diagnostic visualization suite
- **Layout**: 7 rows × 3 columns subplot grid (21 total plots)
- **Plot Types**:
  - **Scatter plots**: Parameter correlations
  - **Box plots**: Distribution analysis by instrument line
  - **PCA plot**: Principal component analysis
  - **Polynomial fitting**: Quadratic curve fitting for Signal vs CO2

**Diagnostic Plot Matrix**:
```
Row 1: Leak Rate vs δ13C | P no Acid vs δ13C | Total CO2 vs δ13C
Row 2: Leak Rate vs δ18O | P no Acid vs δ18O | Total CO2 vs δ18O
Row 3: Leak Rate vs Line | Signal vs pCO2 (w/ fit) | Signal vs δ13C
Row 4: Signal vs δ18O | δ13C vs Line (box) | δ18O vs Line (box)
Row 5: Leak vs pCO2 | δ13C vs δ18O | Total CO2 vs Line (box)
Row 6: Leak vs Signal | P no Acid vs Leak | P gases vs Leak
Row 7: PCA Components | (empty) | (empty)
```

**Advanced Features**:
- **Standards differentiation**: Open circles for standards, filled for samples
- **Interactive coloring**: User-selectable parameter for color mapping
- **Quadratic fitting**: Polynomial regression with R² calculation
- **PCA analysis**:
  - Standardized feature scaling
  - Loading vector arrows
  - Feature contribution labels

### 5. Data Export System

#### `download_excel(df, outliers, filename, selected_standards)` - Line 624
- **Purpose**: Multi-sheet Excel export with comprehensive metadata
- **Features**:
  - Calibration status validation
  - Multiple worksheets (data, outliers, statistics)
  - Formatted tables with styling
  - Statistical summaries
- **Sheets Generated**:
  - **Main Data**: Processed measurements with calibration
  - **Outliers**: Identified outliers with reasons
  - **Statistics**: Summary statistics and method parameters

## Application Flow and User Interface

### Session State Management
The application maintains persistent state across user interactions:
```python
# Core state variables
if 'df' not in st.session_state:
    st.session_state.df = None
if 'file_processed' not in st.session_state:
    st.session_state.file_processed = False
if 'include_outliers' not in st.session_state:
    st.session_state.include_outliers = "No"

# Range filters with conservative defaults
if 'signal_range' not in st.session_state:
    st.session_state.signal_range = (1000.0, 10000.0)
if 'leak_range' not in st.session_state:
    st.session_state.leak_range = (0.0, 1000.0)
```

### Main Application Structure - `main()` Function (Line 758)

#### 1. File Upload and Processing
- **File Support**: Excel files (.xls, .xlsx) with multiple engine support
- **Data Preprocessing**:
  - Type standardization with `convert_dtypes()`
  - Date parsing with format '%m/%d/%y'
  - Ordinal date conversion for plotting
  - Information column parsing via `extract_info_values()`

**File Processing Code Flow**:
```python
try:
    # Try openpyxl engine first
    df = pd.read_excel(uploaded_file, engine='openpyxl')
except Exception:
    # Fallback to xlrd engine
    df = pd.read_excel(uploaded_file, engine='xlrd')

# Data standardization
df = df.convert_dtypes()
df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%y', errors='coerce')
df['Date_ordinal'] = pd.to_numeric(df['Date'].map(lambda x: x.toordinal() if pd.notnull(x) else None))
df = extract_info_values(df)
```

#### 2. Tab-Based Interface Structure

### Tab 1: Diagnostics (Line 874)
- **Purpose**: Exploratory data analysis and quality assessment
- **Controls**:
  - **Sample Statistics Table**: Count analysis with percentages
  - **Parameter Selection**: Color coding dropdown
  - **Identifier Filtering**: Multi-select sample filtering
  - **Range Selectors**: δ13C and δ18O range sliders

**Statistical Calculations**:
```python
sample_counts = st.session_state.df.groupby('Identifier 1').agg({
    'Identifier 2': 'nunique',  # Unique samples
    'Identifier 1': 'count'     # Total measurements
})
```

**Data Filtering Pipeline**:
```python
# Filter by identifier
if identifier_filter:
    filtered_df = filtered_df[filtered_df['Identifier 1'].isin(identifier_filter)]

# Apply range filters
filtered_df = filtered_df[
    (filtered_df['d 13C/12C  Mean'] >= min_d13C) &
    (filtered_df['d 13C/12C  Mean'] <= max_d13C) &
    (filtered_df['d 18O/16O  Mean'] >= min_d18O) &
    (filtered_df['d 18O/16O  Mean'] <= max_d18O)
]
```

### Tab 2: Calibration (Line 985)
- **Purpose**: Standards-based isotopic calibration
- **Process Flow**:
  1. **Standards Selection**: 1-2 reference standards
  2. **Outlier Method**: Z-score or IQR selection
  3. **Parameter Tuning**: Sigma level and IQR multiplier
  4. **Visualization**: Color parameter selection
  5. **Calibration Execution**: Outlier removal → calibration → plotting

**Calibration Workflow**:
```python
# For each selected standard
for standard in selected_standards:
    mask = filtered_df['Identifier 1'] == standard
    standard_data = filtered_df[mask]

    # Outlier detection
    if calibration_type == "Z-Score":
        d13c_outliers = identify_outliers(standard_data, 'd 13C/12C  Mean', sigma_level)
        d18o_outliers = identify_outliers(standard_data, 'd 18O/16O  Mean', sigma_level)
    elif calibration_type == "IQR":
        d13c_outliers = identify_outliers_iqr(standard_data, 'd 13C/12C  Mean', irq_multiplier)
        d18o_outliers = identify_outliers_iqr(standard_data, 'd 18O/16O  Mean', irq_multiplier)

    # Remove outliers
    keep_mask = ~(d13c_outliers | d18o_outliers)
    filtered_df.loc[mask] = standard_data[keep_mask]

# Apply calibration
calibrated_df = calibrate_results(filtered_df, st.session_state.df, selected_standards)
```

**Standards Visualization**:
- Individual standard analysis with outlier highlighting
- Box plots with statistical boundaries (Q1, Q3, IQR limits)
- Color-coded outlier identification

### Tab 3: Data Processing (Line 1316)
- **Purpose**: Final data preparation and export
- **Features**:
  - **Range Filtering**: Signal intensity, leak rate, isotope ranges
  - **Statistical Outlier Detection**: Group-wise outlier identification
  - **Export Options**: Excel download with multiple sheets

**Advanced Filtering Logic**:
```python
# Apply range filters
signal_mask = (df_copy['1  Cycle Int  Samp  44'] >= signal_range[0]) & (df_copy['1  Cycle Int  Samp  44'] <= signal_range[1])
leak_mask = (df_copy['leak_rate'] >= leak_range[0]) & (df_copy['leak_rate'] <= leak_range[1])
d13c_mask = (df_copy['d 13C/12C  Mean'] >= d13c_range[0]) & (df_copy['d 13C/12C  Mean'] <= d13c_range[1])
d18o_mask = (df_copy['d 18O/16O  Mean'] >= d18o_range[0]) & (df_copy['d 18O/16O  Mean'] <= d18o_range[1])

# Group-wise statistical outlier detection
for identifier in data_to_process['Identifier 1'].unique():
    for comment in data_to_process[data_to_process['Identifier 1'] == identifier]['Comment'].unique():
        group_mask = (data_to_process['Identifier 1'] == identifier) & (data_to_process['Comment'] == comment)
        group_data = data_to_process[group_mask]

        if len(group_data) > 1:
            mean_d13C = group_data['d 13C/12C  Mean'].mean()
            std_d13C = group_data['d 13C/12C  Mean'].std()
            # ... statistical outlier calculation per group
```

## Data Column Specifications

### Input Data Requirements
- **Row**: Sequential row number
- **Method**: Analysis method identifier
- **Date**: Measurement date (MM/DD/YY format)
- **Time**: Measurement time
- **Identifier 1**: Sample/standard identifier
- **Identifier 2**: Sample sequence/replicate identifier
- **Comment**: Analysis comment
- **d 13C/12C Mean**: Raw δ13C measurement
- **d 13C/12C Std Dev**: δ13C standard deviation
- **d 18O/16O Mean**: Raw δ18O measurement
- **d 18O/16O Std Dev**: δ18O standard deviation
- **1 Cycle Int Samp 44**: Signal intensity (mass 44)
- **Line**: Instrument line identifier
- **Information**: Instrumental parameters text

### Generated Columns
- **Date_ordinal**: Numeric date for plotting
- **acid_temp**: Extracted acid temperature
- **leak_rate**: Extracted leak rate
- **p_no_acid**: Pressure without acid
- **p_gases**: Gas pressure
- **total_co2**: Total CO2 measurement
- **co2_after_exp**: CO2 after expansion
- **left_mbar/right_mbar**: Pressure readings
- **left_pos/right_pos**: Valve positions
- **vm1_after_transfer**: VM1 after transfer
- **d13C_calibrated**: Calibrated δ13C values
- **d18O_calibrated**: Calibrated δ18O values
- **Sequence**: Extracted sequence number from Identifier 2

## Calculation Strategies

### 1. Isotope Ratio Calibration
**Single-Point Calibration** (VPDB/VSMOW scale correction):
```
δ_calibrated = ((δ_raw + 1000) × (δ_true_std + 1000)) / (δ_raw_std + 1000) - 1000
```

**Two-Point Linear Calibration**:
```
m = (δ_true_std2 - δ_true_std1) / (δ_raw_std2 - δ_raw_std1)
b = δ_true_std1 - m × δ_raw_std1
δ_calibrated = m × δ_raw + b
```

### 2. Quality Control Metrics
**Z-Score Outlier Detection**:
```
z = (x - μ) / σ
outlier if |z| > threshold
```

**IQR Outlier Detection**:
```
IQR = Q3 - Q1
Lower bound = Q1 - 1.5 × IQR
Upper bound = Q3 + 1.5 × IQR
```

### 3. Principal Component Analysis
- **Standardization**: Z-score normalization before PCA
- **Features**: `['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'total_co2', 'd 18O/16O  Mean', 'Line', '1  Cycle Int  Samp  44']`
- **Components**: Maximum 2 components for 2D visualization
- **Loadings**: Component vectors scaled by explained variance

### 4. Statistical Analysis
**Polynomial Fitting** (Signal Intensity vs Total CO2):
```python
# Quadratic fit: y = ax² + bx + c
coeffs = np.polyfit(x_data_clean, y_data_clean, 2)
quadratic_curve = np.polyval(coeffs, x_data_clean)
```

## Chart Visualization Strategies

### 1. Interactive Plotly Implementation
- **Framework**: Plotly with Streamlit integration
- **Color Schemes**: Viridis colorscale for continuous variables
- **Interactivity**: Hover information, zoom, pan, selection
- **Responsive Design**: Container width adaptation

### 2. Multi-Panel Diagnostic Dashboard
- **Grid Layout**: 7×3 subplot configuration
- **Plot Types**: Scatter, box, polynomial regression, PCA
- **Visual Encoding**:
  - Color: User-selected parameter mapping
  - Shape: Standards (open circles) vs samples (filled circles)
  - Size: Consistent marker sizing
  - Lines: Calibration/regression lines with statistical annotations

### 3. Calibration Plot Features
- **Dual Isotope Display**: Side-by-side δ13C and δ18O plots
- **Reference Lines**: True vs measured value relationships
- **Statistical Annotations**: Offset values, regression equations
- **Quality Indicators**: R² values, calibration uncertainties

### 4. Box Plot Analysis
- **Grouping**: By instrument line
- **Statistical Elements**: Q1, Q3, median, whiskers, outliers
- **IQR Visualization**: ±1.5 IQR boundary lines
- **Percentile Annotations**: 25th and 75th percentile markers

## Error Handling and Validation

### 1. File Processing
- **Engine Fallback**: openpyxl → xlrd engine switching
- **Data Type Validation**: Automatic type conversion with error handling
- **Missing Data**: NaN handling throughout processing pipeline

### 2. Calibration Validation
- **Standards Availability**: Verification against standards database
- **Outlier Method Validation**: Z-score vs IQR parameter validation
- **Calibration Requirements**: 1-2 standards validation for method selection

### 3. Visualization Safety
- **Data Completeness**: NaN filtering before plotting
- **Statistical Requirements**: Minimum sample sizes for regression
- **Color Parameter Validation**: Column existence verification

## Technical Implementation Notes

### 1. Performance Optimizations
- **Pandas Copy-on-Write**: Enabled for memory efficiency
- **Streamlit Caching**: Session state management for large datasets
- **Vectorized Operations**: NumPy/Pandas vectorization for calculations

### 2. Standards Database Integration
- **Format**: CSV with columns: Standard, Isotopic_Value_Type, Value
- **Supported Types**: 'δVPDB(13C)', 'δVSMOW(18O)'
- **Error Handling**: Missing standard value validation

### 3. Export Capabilities
- **Multi-Sheet Excel**: Separate sheets for data, outliers, statistics
- **Metadata Preservation**: Calibration status, parameters, timestamps
- **Statistical Summaries**: Automated calculation of key metrics

This comprehensive system provides a complete workflow from raw IRMS data import through calibrated result export, with extensive quality control, visualization, and validation capabilities suitable for analytical chemistry laboratories requiring high precision isotope ratio measurements.