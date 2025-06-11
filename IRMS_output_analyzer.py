import streamlit as st
import pandas as pd
import numpy as np
import io

# Enable pandas copy-on-write mode to prevent SettingWithCopyWarning
pd.options.mode.copy_on_write = True
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
import re
import plotly.express as px
from plotly.subplots import make_subplots
import plotly.graph_objects as go
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from scipy.stats import linregress
from io import BytesIO
from reportlab.lib.styles import *
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, Image
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages


st.set_page_config(layout="wide")

# Initialize session state variables if they don't exist
if 'df' not in st.session_state:
    st.session_state.df = None
if 'file_processed' not in st.session_state:
    st.session_state.file_processed = False
if 'include_outliers' not in st.session_state:
    st.session_state.include_outliers = "No"
if 'selected_ids' not in st.session_state:
    st.session_state.selected_ids = ["All"]

# Initialize range variables in session state with safe defaults
if 'signal_range' not in st.session_state:
    st.session_state.signal_range = (1000.0, 10000.0)  # Conservative default range
if 'leak_range' not in st.session_state:
    st.session_state.leak_range = (0.0, 1000.0)  # Conservative default range
if 'd13c_range' not in st.session_state:
    st.session_state.d13c_range = (-50.0, 50.0)  # Wide default range
if 'd18o_range' not in st.session_state:
    st.session_state.d18o_range = (-50.0, 50.0)  # Wide default range


def extract_number(text):
    """Extract the first number from a string."""
    if pd.isna(text):
        return None
    matches = re.findall(r'\d+', str(text))
    return int(matches[0]) if matches else None

def extract_info_values(df):
    """Extract values from Information column with the specific format provided."""
    # Initialize new columns
    df['acid_temp'] = np.nan
    df['leak_rate'] = np.nan
    df['p_no_acid'] = np.nan
    df['p_gases'] = np.nan
    df['total_co2'] = np.nan
    df['co2_after_exp'] = np.nan
    df['left_mbar'] = np.nan
    df['right_mbar'] = np.nan
    df['left_pos'] = np.nan
    df['right_pos'] = np.nan
    df['vm1_after_transfer'] = np.nan

    # Regular expressions for extracting values
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

    # Extract values using regex
    for idx, row in df.iterrows():
        info = str(row['Information'])

        for col, pattern in patterns.items():
            match = re.search(pattern, info)
            if match:
                df.at[idx, col] = float(match.group(1))

    return df

def identify_outliers(data, column, sigma_level):
    """
    Identify outliers in the specified column based on the sigma level.

    Parameters:
    - data: DataFrame containing the data.
    - column: The column name to check for outliers.
    - sigma_level: The number of standard deviations (sigma) for identifying outliers.

    Returns:
    - A boolean Series indicating True for outliers and False otherwise.
    """
    # Calculate mean and standard deviation
    column_mean = data[column].mean()
    column_std = data[column].std()

    # print(f"Number of rows: {len(data)}")
    # Define the threshold for outliers
    upper_threshold = column_mean + sigma_level * column_std
    lower_threshold = column_mean - sigma_level * column_std

    # Identify outliers by comparing values to the thresholds
    outliers = (data[column] > upper_threshold) | (data[column] < lower_threshold)

    return outliers

def identify_outliers_iqr(data, column, iqr_multiplier=1.5):
    """
    Identify outliers in the specified column using the IQR method with a customizable multiplier.

    Parameters:
    - data: DataFrame containing the data.
    - column: The column name to check for outliers.
    - iqr_multiplier: Multiplier for the IQR to define the bounds for outliers.

    Returns:
    - A boolean Series indicating True for outliers and False otherwise.
    """
    # Calculate Q1, Q3, and IQR for the column
    q1 = data[column].quantile(0.25)
    q3 = data[column].quantile(0.75)
    iqr = q3 - q1

    # Define the upper and lower bounds for outliers using the provided multiplier
    upper_bound = q3 + iqr_multiplier * iqr
    lower_bound = q1 - iqr_multiplier * iqr

    # Identify outliers (values outside the upper and lower bounds)
    outliers = (data[column] > upper_bound) | (data[column] < lower_bound)

    # Align with the original data's index
    outliers_full_index = pd.Series(False, index=data.index)
    outliers_full_index.loc[data[column].dropna().index] = outliers

    return outliers_full_index

# def calibrate_results(df):
#     """Calibrate results based on SHP2L standards."""
#     # Get SHP2L measurements (excluding outliers)
#     shp2l_data = df[df['Identifier 1'] == 'SHP2L'].copy()
#
#     # Calculate correction factors
#     d13c_correction = -0.7 - shp2l_data['d 13C/12C  Mean'].mean()
#     d18o_correction = -5.7 - shp2l_data['d 18O/16O  Mean'].mean()
#
#     # Create calibrated columns
#     df['d13C_calibrated'] = df['d 13C/12C  Mean'] + d13c_correction
#     df['d18O_calibrated'] = df['d 18O/16O  Mean'] + d18o_correction
#
#     return df


standards_df = pd.read_csv("Standards.csv")

def get_true_value(standard_name, isotopic_type):
    """Fetch the true isotopic value for a given standard and isotopic type."""
    match = standards_df[(standards_df['Standard'] == standard_name) &
                         (standards_df['Isotopic_Value_Type'] == isotopic_type)]
    if not match.empty:
        value = match['Value'].values[0]
        print(f"Found true value for {standard_name} ({isotopic_type}): {value}")
        return value
    else:
        raise ValueError(f"True value not found for {standard_name} with type {isotopic_type}")

def single_point_calibration(raw_sample, raw_std, true_std):
    """Apply single-point calibration formula."""
    calibrated_value = ((raw_sample + 1000) * (true_std + 1000)) / (raw_std + 1000) - 1000
    return calibrated_value

def double_point_calibration(raw_sample, raw_rm1, true_rm1, raw_rm2, true_rm2):
    """Apply double-point calibration formula."""
    m = (true_rm2 - true_rm1) / (raw_rm2 - raw_rm1)
    b = true_rm1 - m * raw_rm1
    calibrated_value = m * raw_sample + b
    return calibrated_value

def calibrate_results(standards_df, full_df, selected_standards):
    """
    Calibrate results based on single or double standards for both d13C and d18O.

    Parameters:
    - standards_df: DataFrame containing filtered standards data (without outliers)
    - full_df: DataFrame containing all raw sample data to be calibrated
    - selected_standards: List of selected standards (1 or 2)

    Returns:
    - DataFrame with both d13C_calibrated and d18O_calibrated columns added
    """
    # Create a copy of the full dataframe to avoid modifying the original
    calibrated_df = full_df.copy()

    # Define isotopic types and corresponding column names
    isotopic_types = {
        'δVPDB(13C)': ('d 13C/12C  Mean', 'd13C_calibrated'),
        'δVSMOW(18O)': ('d 18O/16O  Mean', 'd18O_calibrated')
    }

    for isotopic_type, (raw_column, calibrated_column) in isotopic_types.items():
        if len(selected_standards) == 1:
            # Single Point Calibration
            standard = selected_standards[0]
            # Use the mean value from filtered standards data
            raw_std = standards_df.loc[standards_df['Identifier 1'] == standard, raw_column].mean()
            true_std = get_true_value(standard, isotopic_type)
            calibrated_df[calibrated_column] = calibrated_df[raw_column].apply(
                lambda raw_sample: single_point_calibration(raw_sample, raw_std, true_std)
            )

        elif len(selected_standards) == 2:
            # Double Point Calibration
            standard1, standard2 = selected_standards
            # Use mean values from filtered standards data
            raw_rm1 = standards_df.loc[standards_df['Identifier 1'] == standard1, raw_column].mean()
            true_rm1 = get_true_value(standard1, isotopic_type)
            raw_rm2 = standards_df.loc[standards_df['Identifier 1'] == standard2, raw_column].mean()
            true_rm2 = get_true_value(standard2, isotopic_type)
            calibrated_df[calibrated_column] = calibrated_df[raw_column].apply(
                lambda raw_sample: double_point_calibration(raw_sample, raw_rm1, true_rm1, raw_rm2, true_rm2)
            )

        else:
            raise ValueError("Please select either one or two standards for calibration.")

    # print(calibrated_df.columns.tolist())

    return calibrated_df

def create_calibration_plots(standards_reference_df, measurement_df, selected_standards, color_param):
    """
    Create calibration plots for δ13C and δ18O using Plotly.

    Parameters:
    standards_reference_df (pd.DataFrame): DataFrame containing the reference standards data.
    measurement_df (pd.DataFrame): DataFrame containing the measured values.
    selected_standards (list): List of selected standard names.
    color_param (str): Column name in measurement_df to use for point coloring.

    Returns:
    dict: Dictionary containing calibration plots for δ13C and δ18O.
    """
    # Initialize dictionary for storing plots
    figs = {}

    # Define isotope mappings for processing
    isotopes = {
        'δVPDB(13C)': {
            'y_label': 'δ13C',
            'measurement_col': 'd 13C/12C  Mean'
        },
        'δVSMOW(18O)': {
            'y_label': 'δ18O',
            'measurement_col': 'd 18O/16O  Mean'
        }
    }

    for isotope_type, isotope_data in isotopes.items():
        fig = go.Figure()
        true_values = []
        measured_values = []
        color_values = []

        for standard in selected_standards:



            # Get true value for the standard
            try:
                true_value = standards_reference_df[
                    (standards_reference_df['Standard'] == standard) &
                    (standards_reference_df['Isotopic_Value_Type'] == isotope_type)
                ]['Value'].iloc[0]
            except IndexError:
                st.warning(f"No true value found for standard {standard} and isotope {isotope_type}.")
                continue

            # Get measured values and color parameter
            measured_values_for_standard = measurement_df[
                measurement_df['Identifier 1'] == standard
                ][isotope_data['measurement_col']].values

            color_values_for_standard = measurement_df[
                measurement_df['Identifier 1'] == standard
                ][color_param].values

            print(f"Standard: {standard}")
            print(f"Measured values for {isotope_data['y_label']}: {measured_values_for_standard}")
            print(f"Color values: {color_values_for_standard}")

            # Handle missing or NaN color values
            if len(measured_values_for_standard) != len(color_values_for_standard) or any(
                    pd.isnull(color_values_for_standard)):
                st.warning(f"Color parameter values missing for standard {standard}. Skipping.")
                continue

            # Append values for calibration processing
            true_values.extend([true_value] * len(measured_values_for_standard))
            measured_values.extend(measured_values_for_standard)
            color_values.extend(color_values_for_standard)

            # Add scatter points for this standard
            fig.add_trace(go.Scatter(
                x=[true_value] * len(measured_values_for_standard),
                y=measured_values_for_standard,
                mode='markers',
                name=f'{standard}',
                marker=dict(
                    size=10,
                    color=color_values_for_standard,
                    colorscale='Viridis',
                    colorbar=dict(
                        title=color_param,
                        thickness=20,
                        len=0.75,    # Longer colorbar
                        y=0.5,       # Center vertically
                        yanchor='middle',
                        x=1.15,      # Move further right
                        xanchor='right'
                    ),
                    showscale=standard == selected_standards[0]  # Show colorbar only for first standard
                )
            ))

        # Determine calibration method (single or double anchor)
        if len(selected_standards) == 1:
            # Single anchor calibration
            offset = np.mean(np.array(measured_values) - np.array(true_values))
            annotation_text = f"Offset = {offset:.3f}"
            x_min, x_max = min(true_values) - 1, max(true_values) + 1
            y_range = [x_min + offset, x_max + offset]

            # Add offset line
            fig.add_trace(go.Scatter(
                x=[x_min, x_max],
                y=y_range,
                mode='lines',
                name='Offset Line',
                line=dict(color='orange', dash='dash')
            ))
        else:
            # Double anchor calibration
            try:
                slope, intercept, _, _, _ = linregress(true_values, measured_values)
                annotation_text = f"y = {slope:.3f}x + {intercept:.3f}"
                x_min, x_max = min(true_values) - 1, max(true_values) + 1
                x_range = [x_min, x_max]
                y_range = [slope * x + intercept for x in x_range]

                # Add calibration line
                fig.add_trace(go.Scatter(
                    x=x_range,
                    y=y_range,
                    mode='lines',
                    name='Calibration Line',
                    line=dict(color='blue')
                ))
            except ValueError:
                st.warning("Insufficient data for linear regression.")

        # Update layout with annotation and axis labels
        fig.update_layout(
            title=f"{'Single' if len(selected_standards) == 1 else 'Double'} Anchor Calibration for {isotope_type}",
            xaxis_title=f"True {isotope_data['y_label']} value",
            yaxis_title=f"Raw/Measured {isotope_data['y_label']} value",
            showlegend=True,
            width=900,   # Increased width to accommodate colorbar
            height=600,
            margin=dict(r=150),  # Add right margin for colorbar
            annotations=[
                dict(
                    x=0.05, y=0.85, xref="paper", yref="paper",  # Adjusted y position for annotation
                    text=annotation_text,
                    showarrow=False,
                    font=dict(size=12, color="black"),
                    align="left",
                    bordercolor="black",
                    borderwidth=1,
                    borderpad=4,
                    bgcolor="white"
                )
            ]
        )

        figs[isotope_type] = fig

    return figs

def create_diagnostic_plots(df, color_param, standards_file='standards.csv'):
    """
    Create diagnostic plots for analysis with the option to color points by a selected parameter.
    Parameters:
        - df (pd.DataFrame): DataFrame containing the data.
        - color_param (str): The column name to use for coloring the scatter plot markers.
    """

    # Load standards from CSV
    try:
        standards_df = pd.read_csv(standards_file)
        standards_list = standards_df['Standard'].unique()
    except Exception as e:
        raise ValueError(f"Error loading standards from {standards_file}: {e}")


    # Create a subplot with 5 rows and 3 columns
    fig = make_subplots(
        rows=7, cols=3,
        subplot_titles=(
            'Leak Rate vs δ13C', 'P no Acid vs δ13C', 'Total CO2 vs δ13C',
            'Leak Rate vs δ18O', 'P no Acid vs δ18O', 'Total CO2 vs δ18O',
            'Leak Rate vs Line', 'Signal Intensity vs pCO2', 'Signal Intensity vs δ13C',
            'Signal Intensity vs δ18O', 'δ13C vs Line', 'δ18O vs Line',
            'Leak Rate vs pCO2', 'δ13C vs δ18O', 'Total CO2 vs Line',
            'Leak Rate vs Signal Intensity', 'P no Acid vs Leak Rate', 'P Gasses vs Leak Rate',
            'PCA: Principal Components'
        ),
        vertical_spacing=0.03,
        specs=[[{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'box'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'box'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}]]
    )

    # Ensure the required columns are present in the DataFrame
    required_columns = ['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'total_co2', 'd 18O/16O  Mean', 'Line',
                        '1  Cycle Int  Samp  44', 'p_gases', 'Identifier 1']
    if color_param not in df.columns:
        raise ValueError(f"Selected color parameter '{color_param}' is missing from the DataFrame.")
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    # Set marker styles based on whether Identifier 1 is in the standards list
    marker_symbols = ['circle-open' if id in standards_list else 'circle' for id in df['Identifier 1']]
    hover_text = df['Identifier 2']

    # Scatter plots with coloring by selected parameter
    # First trace with the colorbar
    fig.add_trace(go.Scatter(
        x=df['leak_rate'],
        y=df['d 13C/12C  Mean'],
        mode='markers',
        marker=dict(
            color=df[color_param],
            colorscale='Viridis',
            symbol=marker_symbols,
            colorbar=dict(
                title=color_param,
                thickness=20,
                len=0.75,  # Longer colorbar
                y=0.5,     # Center vertically
                yanchor='middle',
                x=1.15,    # Move further right
                xanchor='right'
            ),
            showscale=True
        ),
        text=hover_text,
        hoverinfo='text+x+y'
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['p_no_acid'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=1, col=2)
    fig.add_trace(go.Scatter(x=df['total_co2'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=1, col=3)

    fig.add_trace(go.Scatter(x=df['leak_rate'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['p_no_acid'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=2)
    fig.add_trace(go.Scatter(x=df['total_co2'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=3)

    fig.add_trace(go.Box(x=df['Line'], y=df['leak_rate']), row=3, col=1)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['total_co2'], mode='markers', marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=3, col=2)

    # Prepare x_data and y_data with valid (non-NaN, non-inf) values for fitting
    x_data = df['1  Cycle Int  Samp  44']
    y_data = df['total_co2']

    # Remove NaN and infinite values from x_data and y_data
    valid_data = np.isfinite(x_data) & np.isfinite(y_data)
    x_data_clean = x_data[valid_data]
    y_data_clean = y_data[valid_data]

    # Check if there is sufficient data after cleaning for a quadratic fit
    if len(x_data_clean) >= 3:
        # Fit quadratic polynomial (2nd degree)
        coeffs = np.polyfit(x_data_clean, y_data_clean, 2)  # coeffs = [a, b, c]
        quadratic_curve = np.polyval(coeffs, x_data_clean)  # Evaluate polynomial at cleaned x_data points

        # Sort x_data_clean and quadratic_curve to ensure the line is smooth
        sorted_indices = np.argsort(x_data_clean)
        x_data_sorted = x_data_clean.iloc[sorted_indices]
        quadratic_curve_sorted = quadratic_curve[sorted_indices]

    # Plot the sorted quadratic fit as a line
    fig.add_trace(go.Scatter(
        x=x_data_sorted, y=quadratic_curve_sorted, mode='lines', name='Quadratic Fit',
        line=dict(color='red', dash='dash')
    ), row=3, col=2)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=3, col=3)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=4, col=1)
    fig.add_trace(go.Box(x=df['Line'], y=df['d 13C/12C  Mean']), row=4, col=2)
    fig.add_trace(go.Box(x=df['Line'], y=df['d 18O/16O  Mean']), row=4, col=3)

    fig.add_trace(go.Scatter(x=df['leak_rate'], y=df['total_co2'], mode='markers', marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=5, col=1)
    fig.add_trace(go.Scatter(x=df['d 13C/12C  Mean'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=df[color_param], symbol=marker_symbols, colorscale='Viridis', showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=5, col=2)
    fig.add_trace(go.Box(x=df['Line'], y=df['total_co2']), row=5, col=3)



    # Add scatter plots with coloring by selected parameter, adjusting marker style for standards
    fig.add_trace(go.Scatter(
        x=df['leak_rate'], y=df['1  Cycle Int  Samp  44'], mode='markers',
        marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=6, col=1)

    fig.add_trace(go.Scatter(
        x=df['p_no_acid'], y=df['leak_rate'], mode='markers',
        marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=6, col=2)

    fig.add_trace(go.Scatter(
        x=df['p_gases'], y=df['leak_rate'], mode='markers',
        marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=6, col=3)

    # Perform PCA
    features = ['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'total_co2', 'd 18O/16O  Mean', 'Line',
                '1  Cycle Int  Samp  44']
    X = df[features].dropna()

    # Standardize the data
    X_scaled = StandardScaler().fit_transform(X)

    # Adjust n_components based on the data
    n_samples, n_features = X_scaled.shape
    n_components = min(2, n_samples, n_features)  # Ensure n_components <= min(n_samples, n_features)

    # Apply PCA
    pca = PCA(n_components=n_components)
    components = pca.fit_transform(X_scaled)

    # Calculate loadings
    loadings = pca.components_.T * np.sqrt(pca.explained_variance_)

    # Scatter plot for PCA components
    if n_components == 2:
        fig.add_trace(go.Scatter(
            x=components[:, 0], y=components[:, 1], mode='markers',
            marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False),
            text=hover_text, hoverinfo='text+x+y'
        ), row=7, col=1)

        # Add loadings as annotations
        for i, feature in enumerate(features):
            fig.add_annotation(
                x=loadings[i, 0],  # Loading for the first component (x)
                y=loadings[i, 1],  # Loading for the second component (y)
                ax=0, ay=0,  # Starting point for the arrow (origin)
                axref="x", ayref="y",  # Reference the x and y axes for arrow positioning
                showarrow=True,  # Display the arrow
                arrowsize=2,  # Set arrow size
                arrowhead=2,  # Set arrowhead style
                xanchor="right",  # Anchor the x-axis to the right side
                yanchor="top",  # Anchor the y-axis to the top side
                row=7, col=1
            )
            fig.add_annotation(
                x=loadings[i, 0],  # Loading for the first component (x)
                y=loadings[i, 1],  # Loading for the second component (y)
                xanchor="center",  # Center the x-axis label
                yanchor="bottom",  # Bottom-align the y-axis label
                text=feature,  # The feature name as annotation text
                yshift=5,  # Adjust the y-position to avoid overlap
                row=7, col=1
            )

    # # Position the color scale only on the first subplot, adjusting its height to match one row
    # fig.update_traces(marker=dict(colorbar=dict(len=0.2, y=0.2, yanchor="bottom")), selector=dict(row=1, col=1))

    # Update layout with right margin for colorbar
    fig.update_layout(
        title_text='Diagnostic Plots',
        height=2000,
        showlegend=False,
        margin=dict(r=150)  # Add right margin for colorbar
    )

    return fig


def download_excel(df, outliers=None, filename="data.xlsx", selected_standards=None):
    """
    Creates a download button for exporting DataFrames as an Excel file with multiple sheets.

    Parameters:
    - df (DataFrame): The main DataFrame to be downloaded.
    - outliers (DataFrame): Optional DataFrame containing outliers data.
    - filename (str): The filename for the download. Default is "data.xlsx".
    - selected_standards (list): List of selected standards for calibration.
    """
    if not any(col in df.columns for col in ['d13C_calibrated', 'd18O_calibrated']):
        if not st.warning("Data has not been calibrated. Do you want to continue downloading without calibration data?") or not st.button("Continue", key=f"continue_btn_{filename}"):
            return
    
    # Convert the DataFrame to Excel format in memory
    towrite = io.BytesIO()
    
    with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
        # Split data into standards and non-standards
        standards_mask = df['Identifier 1'].isin(selected_standards) if selected_standards else pd.Series(False, index=df.index)
        main_data = df[~standards_mask].copy()

        # Calculate statistics
        total_samples = len(df)
        outliers_stats = {}
        if outliers is not None and not outliers.empty:
            outliers_by_category = outliers.groupby('Category').size()
            outliers_stats = {
                cat: {'count': count, 'percentage': (count/total_samples)*100}
                for cat, count in outliers_by_category.items()
            }
        
        final_analyses = total_samples
        if outliers is not None:
            final_analyses -= len(outliers)
        if selected_standards:
            final_analyses -= len(df[standards_mask])

        # Create Statistics sheet
        stats_data = [
            ['Total Samples', total_samples],
            ['Final Analyses', final_analyses],
            ['', ''],
            ['Outliers Statistics:', '']
        ]
        
        if outliers_stats:
            for category, stat in outliers_stats.items():
                stats_data.append([
                    f'{category} Outliers',
                    f'{stat["count"]} ({stat["percentage"]:.1f}%)'
                ])
        
        stats_df = pd.DataFrame(stats_data, columns=['Metric', 'Value'])
        stats_df.to_excel(writer, index=False, sheet_name='Statistics')

        # Write main data to Data sheet
        main_data.to_excel(writer, index=False, sheet_name="Data")
        
        # Write outliers to second sheet only if they exist and we want to exclude them
        if outliers is not None and not outliers.empty and df is not None:
            filtered_outliers = outliers[~outliers['Identifier 1'].isin(selected_standards)] if selected_standards else outliers
            if not filtered_outliers.empty:
                # Add Category column if it doesn't exist
                if 'Category' not in filtered_outliers.columns:
                    filtered_outliers['Category'] = 'Statistical'  # Default category for legacy outliers
                
                # Create category-wise sheets
                for category in filtered_outliers['Category'].unique():
                    category_outliers = filtered_outliers[filtered_outliers['Category'] == category]
                    if not category_outliers.empty:
                        sheet_name = f"Outliers - {category}"
                        if len(sheet_name) > 31:  # Excel sheet name length limit
                            sheet_name = sheet_name[:31]
                        category_outliers.to_excel(writer, index=False, sheet_name=sheet_name)
            
        # Create standards sheet if standards are selected
        if selected_standards:
            standards_data = []
            
            # Create a separate sheet for standards measurements
            standards_measurements = df[standards_mask].copy()
            if not standards_measurements.empty:
                standards_measurements.to_excel(writer, index=False, sheet_name="Standards Measurements")
                
            for standard in selected_standards:
                standard_df = df[df['Identifier 1'] == standard].copy()
                if not standard_df.empty:
                    # Calculate precision and averages
                    d13c_precision = standard_df['d 13C/12C  Mean'].std()
                    d13c_average = standard_df['d 13C/12C  Mean'].mean()
                    d18o_precision = standard_df['d 18O/16O  Mean'].std()
                    d18o_average = standard_df['d 18O/16O  Mean'].mean()
                    
                    standards_data.append({
                        'Standard': standard,
                        'δ13C Precision': d13c_precision,
                        'δ13C Average': d13c_average,
                        'δ18O Precision': d18o_precision,
                        'δ18O Average': d18o_average,
                        'Sample Count': len(standard_df),
                        'Calibration Type': 'Single Anchor' if len(selected_standards) == 1 else 'Double Anchor'
                    })
                    
            if standards_data:
                # Create summary DataFrame
                standards_summary = pd.DataFrame(standards_data)
                standards_summary.to_excel(writer, index=False, sheet_name="Standards Results")
                
                # Get workbook and worksheet
                workbook = writer.book
                worksheet = writer.sheets["Standards Results"]
                
                # Add text description of calibration plots
                row_offset = len(standards_data) + 3
                worksheet.write(row_offset, 0, "Calibration plots are available in the Calibration tab of the application.")
                worksheet.write(row_offset + 1, 0, f"Calibration type: {'Single' if len(selected_standards) == 1 else 'Double'} Anchor")
                worksheet.write(row_offset + 2, 0, f"Standards used: {', '.join(selected_standards)}")
    
    towrite.seek(0)

    # Create the download button
    st.download_button(
        label="Download Excel File",
        data=towrite,
        file_name=filename,
        mime="application/vnd.ms-excel",
        key=f"download_btn_{filename}"
    )


if "df" not in st.session_state:
    st.session_state.df = None

def main():
    st.title('Isotope Ratio Mass Spectrometer Data Analyzer')

    # Initialize session state variables if they don't exist
    if 'df' not in st.session_state:
        st.session_state.df = None
    if 'file_processed' not in st.session_state:
        st.session_state.file_processed = False
    if 'confirm_reset' not in st.session_state:
        st.session_state.confirm_reset = False

    # File uploader
    uploaded_file = st.file_uploader("Choose an XLS file", type=['xls', 'xlsx'])

    # Reset file processing with confirmation
    if st.button("Load a New File", key="load_new_file_btn"):
        st.session_state.confirm_reset = True  # Trigger confirmation prompt

    # Confirmation prompt
    if st.session_state.confirm_reset:
        st.warning("Are you sure you want to load a new file? This will overwrite the current data.")
        col1, col2 = st.columns(2)
        if col1.button("Yes, load new file", key="confirm_load_btn"):
            # Reset session state to allow a new file upload
            st.session_state.file_processed = False
            st.session_state.df = None
            st.session_state.confirm_reset = False  # Reset confirmation state
        elif col2.button("Cancel", key="cancel_load_btn"):
            st.session_state.confirm_reset = False  # Cancel reset and close prompt

    # Only load the file if it hasn't been processed yet
    if uploaded_file is not None and not st.session_state.file_processed:
        try:
            try:
                # First try with openpyxl engine
                df = pd.read_excel(uploaded_file, engine='openpyxl')
            except Exception as e:
                try:
                    # If openpyxl fails, try with xlrd engine
                    df = pd.read_excel(uploaded_file, engine='xlrd')
                except Exception as e:
                    st.error(f"Failed to read Excel file: {str(e)}")
                    return
            
            # Standardize types and create a clean copy
            df = df.convert_dtypes()
            df.reset_index(drop=True, inplace=True)
            df = df.map(lambda x: None if pd.isna(x) else x)

            # Convert the DataFrame 'Date' column to datetime with explicit format
            df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%y', errors='coerce')
            df['Date_ordinal'] = pd.to_numeric(df['Date'].map(lambda x: x.toordinal() if pd.notnull(x) else None))

            # Save original columns for reference
            original_columns = df.columns.tolist()

            # Extract values from Information column
            df = extract_info_values(df)

            # Ensure all original columns are included
            for col in original_columns:
                if col not in df.columns:
                    df[col] = None

            # Save df to session_state
            st.session_state.df = df
            st.session_state.file_processed = True

        except Exception as e:
            st.error(f"Error loading file: {e}")

    # Display a warning if no file is uploaded
    if st.session_state.df is None:
        st.warning("Please upload a file to begin analysis.")
        return

    # Display data preview if available
    if st.session_state.df is not None:
        with st.expander("Data Table", expanded=True):
            # Display the DataFrame using Streamlit's native table component
            st.dataframe(
                st.session_state.df,
                height=400,  # Set table height for vertical scroll
                use_container_width=True  # Use full width of the container
            )

    # Sidebar for user-selected sigma level
    # with st.sidebar:
    #     sigma_level = st.number_input("Set Sigma Level for Outlier Exclusion",
    #                                   min_value=0.1,
    #                                   max_value=5.0,
    #                                   value=1.0,
    #                                   step=0.1)

    # Create tabs for different views
    tab1, tab2, tab3 = st.tabs([
        'Diagnostics',
        'Calibration',
        'Data Processing'
    ])

    color_options = {
        'Line': 'Line',
        'Signal Intensity': '1  Cycle Int  Samp  44',
        'd18O values': 'd 18O/16O  Mean',
        'd13C values': 'd 13C/12C  Mean',
        'Leak Rate': 'leak_rate',
        'Total CO2': 'total_co2',
        'P gasses': 'p_gases',
        'P no acid': 'p_no_acid',
        'Date': 'Date_ordinal'
    }

    # Get list of friendly names for dropdown
    color_param_names = list(color_options.keys())

    with tab1:
        st.header('Diagnostic Plots')
        
        # Create three columns for controls
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.subheader('Sample Statistics')
            # Display sample counts as a table with percentage
            # Count samples considering duplicates (Identifier 1 and 2 combinations)
            sample_counts = st.session_state.df.groupby('Identifier 1').agg({
                'Identifier 2': 'nunique',
                'Identifier 1': 'count'
            }).rename(columns={
                'Identifier 2': 'Unique Samples',
                'Identifier 1': 'Total Measurements'
            })
            total_unique = sample_counts['Unique Samples'].sum()
            total_measurements = sample_counts['Total Measurements'].sum()
            
            # Create DataFrame with percentages
            count_df = pd.DataFrame({
                'Identifier': sample_counts.index,
                'Unique Samples': sample_counts['Unique Samples'],
                'Total Measurements': sample_counts['Total Measurements'],
                'Measurements %': (sample_counts['Total Measurements'] / total_measurements * 100).round(1)
            })
            # Format the percentage column
            count_df['Measurements %'] = count_df['Measurements %'].map('{:,.1f}%'.format)
            st.dataframe(count_df, hide_index=True)
            # Display metrics
            metrics_col1, metrics_col2 = st.columns(2)
            metrics_col1.metric("Total Unique Samples", total_unique)
            metrics_col2.metric("Total Measurements", total_measurements)

        with col2:
            st.subheader('Parameter Selection')
            # Dropdown for selecting color parameter
            default_color_param = 'd18O values'
            default_index = color_param_names.index(default_color_param) if default_color_param in color_param_names else 0
            selected_color_param = st.selectbox(
                "Choose a parameter to color the dots:",
                color_param_names,
                index=default_index,
                key="diagnostic_param"
            )
            
            # Filter by Identifier 1
            identifier_filter = st.multiselect(
                "Filter by Identifier 1:",
                options=st.session_state.df['Identifier 1'].unique().tolist(),
                default=None
            )
            
        with col3:
            st.subheader('Value Ranges')
            # δ13C/12C Mean range selector
            d13c_min = float(st.session_state.df['d 13C/12C  Mean'].min())
            d13c_max = float(st.session_state.df['d 13C/12C  Mean'].max())
            d13c_range = st.slider(
                "Select min and max δ13C/12C Mean",
                min_value=d13c_min,
                max_value=d13c_max,
                value=(d13c_min, d13c_max),
                step=0.1
            )
            
            # δ18O/16O Mean range selector
            d18o_min = float(st.session_state.df['d 18O/16O  Mean'].min())
            d18o_max = float(st.session_state.df['d 18O/16O  Mean'].max())
            d18o_range = st.slider(
                "Select min and max δ18O/16O Mean",
                min_value=d18o_min,
                max_value=d18o_max,
                value=(d18o_min, d18o_max),
                step=0.1
            )

        st.divider()
        # Map the selected friendly name to the actual column name
        color_param = color_options[selected_color_param]

        # Get filter values from the three-column controls
        min_d13C, max_d13C = d13c_range
        min_d18O, max_d18O = d18o_range

        # Ensure that there are no NaN values in the columns before filtering
        filtered_df = st.session_state.df.dropna(subset=['d 13C/12C  Mean', 'd 18O/16O  Mean', 'Identifier 1'])

        # Apply identifier filter if any identifiers are selected
        if identifier_filter:
            filtered_df = filtered_df[filtered_df['Identifier 1'].isin(identifier_filter)]

        # Ensure the columns are of the correct type (float) for comparison
        filtered_df['d 13C/12C  Mean'] = filtered_df['d 13C/12C  Mean'].astype(float)
        filtered_df['d 18O/16O  Mean'] = filtered_df['d 18O/16O  Mean'].astype(float)

        # Filter the DataFrame based on the selected min and max values
        filtered_df = filtered_df[
            (filtered_df['d 13C/12C  Mean'] >= min_d13C) &
            (filtered_df['d 13C/12C  Mean'] <= max_d13C) &
            (filtered_df['d 18O/16O  Mean'] >= min_d18O) &
            (filtered_df['d 18O/16O  Mean'] <= max_d18O)
        ]

        # Generate the figure using the filtered DataFrame and selected color parameter
        fig = create_diagnostic_plots(filtered_df, color_param)

        # Display the plot
        st.plotly_chart(fig)

    with tab2:
            st.header("Calibration")

            # Load standards reference data
            standards_reference = pd.read_csv('standards.csv')

            # Create a list of unique standards
            standards_list = standards_reference['Standard'].unique().tolist()

            # Create three columns for the controls
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown("#### Standard Selection")
                # Dropdown for user to select standards (multiple selection)
                selected_standards = st.multiselect(
                    "Select Standards to Filter Data:",
                    standards_list,
                    help="Select 1 standard for single-point calibration or 2 standards for double-point calibration"
                )

            with col2:
                st.markdown("#### Outlier Detection")
                sigma_level = st.number_input("Set Sigma Level for standard´s Outlier Exclusion",
                                            min_value=0.1,
                                            max_value=5.0,
                                            value=1.0,
                                            step=0.1)

                irq_multiplier = st.number_input("Set IQR Multiplier for standard´s Outlier Exclusion",
                                                min_value=1.0,
                                                max_value=10.0,
                                                value=1.5,
                                                step=0.1)

                # User selects the calibration method
                calibration_type = st.selectbox("Choose Outlier Detection Method", options=["Z-Score", "IQR"])

            with col3:
                st.markdown("#### Visualization")
                # Dropdown for selecting color parameter
                # Ensure the default value exists in the list
                default_color_param = 'd18O values'
                default_index = color_param_names.index(
                    default_color_param) if default_color_param in color_param_names else 0

                # Dropdown for selecting color parameter with a default value
                selected_color_param = st.selectbox(
                    "Choose a parameter to color the dots:",
                    color_param_names,
                    index=default_index,
                    key="calibration_param"
                )

                # Map the selected friendly name to the actual column name
                color_param = color_options[selected_color_param]

                # Add some vertical spacing
                st.write("")
                st.write("")
                
            if st.button("Calibrate Results", use_container_width=True):
                if selected_standards:
                    # Check if the selected standards are 1 or 2
                    if len(selected_standards) in [1, 2]:
                        method_type = "single-point" if len(selected_standards) == 1 else "double-point"
                        st.info(
                            f"Performing {method_type} calibration for {', '.join(selected_standards)} using {calibration_type} method.")

                        # Create a copy of the original dataframe to avoid modifying it directly
                        filtered_df = st.session_state.df.copy()

                        # Filter out outliers for each standard
                        for standard in selected_standards:
                            # Filter data for the current standard
                            mask = filtered_df['Identifier 1'] == standard
                            standard_data = filtered_df[mask]

                            if not standard_data.empty:
                                if calibration_type == "Z-Score":
                                    # Identify outliers for d13C and d18O using Z-Score method
                                    d13c_outliers = identify_outliers(standard_data, 'd 13C/12C  Mean', sigma_level)
                                    d18o_outliers = identify_outliers(standard_data, 'd 18O/16O  Mean', sigma_level)

                                elif calibration_type == "IQR":
                                    # Identify outliers for d13C and d18O using IQR method
                                    d13c_outliers = identify_outliers_iqr(standard_data, 'd 13C/12C  Mean',
                                                                            irq_multiplier)
                                    d18o_outliers = identify_outliers_iqr(standard_data, 'd 18O/16O  Mean',
                                                                            irq_multiplier)

                                # Create combined mask for rows to keep (non-outliers)
                                keep_mask = ~(d13c_outliers | d18o_outliers)

                                # Update the filtered dataframe to exclude outliers for this standard
                                filtered_df.loc[mask] = standard_data[keep_mask]

                        # Create and display the calibration plots using filtered data
                        figs = create_calibration_plots(standards_reference, filtered_df, selected_standards, color_param)

                        # Display plots in columns
                        col1, col2 = st.columns(2)
                        with col1:
                            st.plotly_chart(figs['δVPDB(13C)'], use_container_width=True)
                        with col2:
                            st.plotly_chart(figs['δVSMOW(18O)'], use_container_width=True)

                        # Perform calibration for both isotopic types in a single function call
                        calibrated_df = calibrate_results(
                            standards_df=filtered_df,  # The filtered standards dataframe (without outliers)
                            full_df=st.session_state.df,  # The complete dataframe to be calibrated
                            selected_standards=selected_standards
                        )

                        st.success("Calibration completed for both isotopic types.")
                        st.session_state.df = calibrated_df  # Save the updated filtered df to session_state
                    else:
                        st.warning("Please select either 1 or 2 standards for calibration.")
                else:
                    st.warning("Please select at least one standard to proceed with calibration.")



            # print(calibration_type)
            if selected_standards:
                for standard in selected_standards:
                    established_values = standards_reference[standards_reference['Standard'] == standard]

                    if established_values.empty:
                        st.warning(f"No established values found for the standard: {standard}")
                        continue

                    d13c_established = established_values.loc[
                        established_values['Isotopic_Value_Type'] == 'δVPDB(13C)', 'Value'].values[0]
                    d18o_established = established_values.loc[
                        established_values['Isotopic_Value_Type'] == 'δVSMOW(18O)', 'Value'].values[0]

                    shp2l_filtered_data = st.session_state.df[
                        st.session_state.df['Identifier 1'] == standard]

                    # print(f"Number of rows: {len(shp2l_filtered_data)}")

                    if shp2l_filtered_data.empty:
                        st.warning(f"No data available for the standard: {standard}")
                        continue

                    # Initialize outliers variables to ensure they exist
                    d13c_outliers = None
                    d18o_outliers = None

                    if calibration_type == "Z-Score":
                        d13c_outliers = identify_outliers(shp2l_filtered_data, 'd 13C/12C  Mean', sigma_level)
                        d18o_outliers = identify_outliers(shp2l_filtered_data, 'd 18O/16O  Mean', sigma_level)
                    else:  # IQR
                        d13c_outliers = identify_outliers_iqr(shp2l_filtered_data, 'd 13C/12C  Mean', irq_multiplier)
                        d18o_outliers = identify_outliers_iqr(shp2l_filtered_data, 'd 18O/16O  Mean', irq_multiplier)

                    # Display outliers information
                    st.subheader(f"Identified Outliers for {standard}")

                    if d13c_outliers is not None and d18o_outliers is not None and (
                            d13c_outliers.any() or d18o_outliers.any()):
                        col1, col2 = st.columns(2)

                        with col1:
                            st.markdown("### δ13C Outliers:")
                            d13c_outliers_data = shp2l_filtered_data.loc[d13c_outliers, ['d 13C/12C  Mean']]
                            if not d13c_outliers_data.empty:
                                st.dataframe(d13c_outliers_data.style.highlight_max(axis=0))
                            else:
                                st.write("No δ13C outliers found.")

                        with col2:
                            st.markdown("### δ18O Outliers:")
                            d18o_outliers_data = shp2l_filtered_data.loc[d18o_outliers, ['d 18O/16O  Mean']]
                            if not d18o_outliers_data.empty:
                                st.dataframe(d18o_outliers_data.style.highlight_max(axis=0))
                            else:
                                st.write("No δ18O outliers found.")
                    else:
                        st.write("No outliers identified at this sigma level.")

                    # Filter out outliers for precision and average calculations
                    shp2l_clean = shp2l_filtered_data.loc[~(d13c_outliers | d18o_outliers)]

                    # Display precision (standard deviation) and averages
                    # Calculate the number of standards and percentage
                    total_standards = len(shp2l_filtered_data)
                    included_standards = len(shp2l_clean)
                    standards_percentage = (included_standards / total_standards) * 100 if total_standards > 0 else 0

                    # Calculate precision values
                    d13c_precision = shp2l_clean['d 13C/12C  Mean'].std()
                    d18o_precision = shp2l_clean['d 18O/16O  Mean'].std()

                    # Determine colors based on precision values and standards percentage
                    d13c_precision_color = '#ff4444' if d13c_precision > 0.1 else '#2ecc71'
                    d18o_precision_color = '#ff4444' if d18o_precision > 0.1 else '#2ecc71'
                    standards_percentage_color = '#2ecc71' if standards_percentage >= 75 else '#666666'
                    
                    st.markdown(f"""
                    <div style='background-color: #f0f2f6; padding: 20px; border-radius: 10px; margin: 10px 0;'>
                        <h3 style='color: #1f77b4; margin-bottom: 15px;'>Precision and Averages for {standard} (Excluding Outliers)</h3>
                        <div style='display: flex; justify-content: space-between;'>
                            <div style='flex: 1; margin-right: 20px;'>
                                <p style='font-size: 18px; margin: 5px 0;'><b>δ13C Precision:</b> <span style='color: {d13c_precision_color}'>{d13c_precision:.3f}‰</span></p>
                                <p style='font-size: 18px; margin: 5px 0;'><b>δ13C Average:</b> <span style='color: #000000'>{shp2l_clean['d 13C/12C  Mean'].mean():.3f}‰</span></p>
                            </div>
                            <div style='flex: 1;'>
                                <p style='font-size: 18px; margin: 5px 0;'><b>δ18O Precision:</b> <span style='color: {d18o_precision_color}'>{d18o_precision:.3f}‰</span></p>
                                <p style='font-size: 18px; margin: 5px 0;'><b>δ18O Average:</b> <span style='color: #000000'>{shp2l_clean['d 18O/16O  Mean'].mean():.3f}‰</span></p>
                            </div>
                        </div>
                        <div style='margin-top: 15px; padding-top: 10px; border-top: 1px solid #ddd;'>
                            <p style='font-size: 16px; color: {standards_percentage_color};'>Standards included: {included_standards} out of {total_standards} ({standards_percentage:.1f}%)</p>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    # Calculate statistics for both methods
                    d13c_mean = shp2l_filtered_data['d 13C/12C  Mean'].mean()
                    d13c_std = shp2l_filtered_data['d 13C/12C  Mean'].std()
                    d18o_mean = shp2l_filtered_data['d 18O/16O  Mean'].mean()
                    d18o_std = shp2l_filtered_data['d 18O/16O  Mean'].std()

                    # Sigma level lines (for Z-Score method)
                    sigma_level_d13c_plus = d13c_mean + sigma_level * d13c_std
                    sigma_level_d13c_minus = d13c_mean - sigma_level * d13c_std
                    sigma_level_d18o_plus = d18o_mean + sigma_level * d18o_std
                    sigma_level_d18o_minus = d18o_mean - sigma_level * d18o_std

                    # IQR statistics with the irq_multiplier instead of hardcoded 1.5
                    q1_d13c = shp2l_filtered_data['d 13C/12C  Mean'].quantile(0.25)
                    q3_d13c = shp2l_filtered_data['d 13C/12C  Mean'].quantile(0.75)
                    iqr_d13c = q3_d13c - q1_d13c
                    iqr_level_d13c_plus = q3_d13c + irq_multiplier * iqr_d13c
                    iqr_level_d13c_minus = q1_d13c - irq_multiplier * iqr_d13c

                    q1_d18o = shp2l_filtered_data['d 18O/16O  Mean'].quantile(0.25)
                    q3_d18o = shp2l_filtered_data['d 18O/16O  Mean'].quantile(0.75)
                    iqr_d18o = q3_d18o - q1_d18o
                    iqr_level_d18o_plus = q3_d18o + irq_multiplier * iqr_d18o
                    iqr_level_d18o_minus = q1_d18o - irq_multiplier * iqr_d18o

                    # Define equally spaced x-values for plots
                    x_values_d13c = range(1, len(shp2l_filtered_data) + 1)
                    x_values_d18o = range(1, len(shp2l_filtered_data) + 1)

                    # Generate plots based on user choice
                    if calibration_type == "Z-Score":
                        # Plot for δ13C with Z-Score thresholds
                        fig_d13c = px.scatter(
                            x=x_values_d13c,
                            y=shp2l_filtered_data['d 13C/12C  Mean'],
                            color=shp2l_filtered_data[color_param],  # Add color parameter
                            title=f'SHP2L δ13C Calibration Values (Z-Score Method)',
                            labels={'y': 'δ13C (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d13c.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        fig_d13c.add_hline(y=sigma_level_d13c_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{sigma_level}σ')
                        fig_d13c.add_hline(y=sigma_level_d13c_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{sigma_level}σ')
                        fig_d13c.add_hline(y=d13c_mean, line_color='purple', line_dash='solid',
                                           annotation_text='Mean Value')

                        # Plot for δ18O with Z-Score thresholds
                        fig_d18o = px.scatter(
                            x=x_values_d18o,
                            y=shp2l_filtered_data['d 18O/16O  Mean'],
                            color=shp2l_filtered_data[color_param],  # Add color parameter
                            title=f'SHP2L δ18O Calibration Values (Z-Score Method)',
                            labels={'y': 'δ18O (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d18o.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        fig_d18o.add_hline(y=sigma_level_d18o_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{sigma_level}σ')
                        fig_d18o.add_hline(y=sigma_level_d18o_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{sigma_level}σ')
                        fig_d18o.add_hline(y=d18o_mean, line_color='purple', line_dash='solid',
                                           annotation_text='Mean Value')

                    elif calibration_type == "IQR":
                        # Plot for δ13C with IQR thresholds
                        fig_d13c = px.scatter(
                            x=x_values_d13c,
                            y=shp2l_filtered_data['d 13C/12C  Mean'],
                            color=shp2l_filtered_data[color_param],  # Add color parameter
                            title=f'SHP2L δ13C Calibration Values (IQR Method)',
                            labels={'y': 'δ13C (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d13c.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        fig_d13c.add_hline(y=iqr_level_d13c_plus, line_color='green', line_dash='dot',
                                           annotation_text='+1.5 IQR')
                        fig_d13c.add_hline(y=iqr_level_d13c_minus, line_color='green', line_dash='dot',
                                           annotation_text='-1.5 IQR')
                        fig_d13c.add_hline(y=q3_d13c, line_color='purple', line_dash='solid',
                                           annotation_text='Q3 (75th Percentile)')
                        fig_d13c.add_hline(y=q1_d13c, line_color='purple', line_dash='solid',
                                           annotation_text='Q1 (25th Percentile)')

                        # Plot for δ18O with IQR thresholds
                        fig_d18o = px.scatter(
                            x=x_values_d18o,
                            y=shp2l_filtered_data['d 18O/16O  Mean'],
                            color=shp2l_filtered_data[color_param],  # Add color parameter
                            title=f'SHP2L δ18O Calibration Values (IQR Method)',
                            labels={'y': 'δ18O (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d18o.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        fig_d18o.add_hline(y=iqr_level_d18o_plus, line_color='green', line_dash='dot',
                                           annotation_text='+1.5 IQR')
                        fig_d18o.add_hline(y=iqr_level_d18o_minus, line_color='green', line_dash='dot',
                                           annotation_text='-1.5 IQR')
                        fig_d18o.add_hline(y=q3_d18o, line_color='purple', line_dash='solid',
                                           annotation_text='Q3 (75th Percentile)')
                        fig_d18o.add_hline(y=q1_d18o, line_color='purple', line_dash='solid',
                                           annotation_text='Q1 (25th Percentile)')

                    # Display the plots
                    st.plotly_chart(fig_d13c, use_container_width=True)
                    st.plotly_chart(fig_d18o, use_container_width=True)


            else:
                st.write("Please select at least one standard.")

    with tab3:
        st.header('Data Processing')

        # Initialize the DataFrame copy at the start
        df_copy = st.session_state.df.copy()

        # Initialize session state for download options if not already set
        if 'include_outliers' not in st.session_state:
            st.session_state.include_outliers = "No"
        if 'selected_ids' not in st.session_state:
            st.session_state.selected_ids = ["All"]

        # Initialize the DataFrame and add Sequence column
        df_copy = st.session_state.df.copy()
        df_copy['Sequence'] = df_copy['Identifier 2'].apply(
            lambda x: int(re.search(r'\d+', str(x)).group()) if pd.notnull(x) and isinstance(x, (
            str, float, int)) and re.search(r'\d+', str(x)) else None
        )

        # Filter ranges for data processing
        st.subheader("Range Filter Outliers Settings")
        col1, col2 = st.columns(2)
        
        with col1:
            # Signal Intensity filter
            signal_min = float(df_copy['1  Cycle Int  Samp  44'].min())
            signal_max = float(df_copy['1  Cycle Int  Samp  44'].max())
            # Store ranges in session state to make them available throughout the app
            st.session_state.signal_range = st.slider(
                'Filter by Signal Intensity',
                min_value=signal_min,
                max_value=signal_max,
                value=(float(1000), signal_max)
            )

            # Leak Rate filter
            leak_min = float(df_copy['leak_rate'].min())
            leak_max = float(df_copy['leak_rate'].max())
            st.session_state.leak_range = st.slider(
                'Filter by Leak Rate',
                min_value=leak_min,
                max_value=leak_max,
                value=(leak_min, float(1000))
            )

        with col2:
            # δ13C filter
            d13c_min = float(df_copy['d 13C/12C  Mean'].min())
            d13c_max = float(df_copy['d 13C/12C  Mean'].max())
            st.session_state.d13c_range = st.slider(
                'Filter by δ13C',
                min_value=d13c_min,
                max_value=d13c_max,
                value=(float(-10), float(10))
            )

            # δ18O filter
            d18o_min = float(df_copy['d 18O/16O  Mean'].min())
            d18o_max = float(df_copy['d 18O/16O  Mean'].max())
            st.session_state.d18o_range = st.slider(
                'Filter by δ18O',
                min_value=d18o_min,
                max_value=d18o_max,
                value=(float(-10), float(10))
            )

        # Apply identifier filter if any identifiers are selected
        if identifier_filter:
            df_copy = df_copy[df_copy['Identifier 1'].isin(identifier_filter)]
            
        # Calculate total samples before filtering
        total_samples = len(df_copy)
        
        # Create masks for each filter
        signal_mask = (df_copy['1  Cycle Int  Samp  44'] >= st.session_state.signal_range[0]) & (df_copy['1  Cycle Int  Samp  44'] <= st.session_state.signal_range[1])
        leak_mask = (df_copy['leak_rate'] >= st.session_state.leak_range[0]) & (df_copy['leak_rate'] <= st.session_state.leak_range[1])
        d13c_mask = (df_copy['d 13C/12C  Mean'] >= st.session_state.d13c_range[0]) & (df_copy['d 13C/12C  Mean'] <= st.session_state.d13c_range[1])
        d18o_mask = (df_copy['d 18O/16O  Mean'] >= st.session_state.d18o_range[0]) & (df_copy['d 18O/16O  Mean'] <= st.session_state.d18o_range[1])
        
        # Calculate excluded samples for each filter individually
        excluded_by_signal = sum(~signal_mask)
        excluded_by_leak = sum(~leak_mask)
        excluded_by_d13c = sum(~d13c_mask)
        excluded_by_d18o = sum(~d18o_mask)
        
        # Keep an unfiltered copy for outlier detection
        df_unfiltered = df_copy.copy()
        
        # Apply all filters to a filtered copy for plotting
        df_filtered = df_copy.loc[signal_mask & leak_mask & d13c_mask & d18o_mask]
        
        # Calculate total excluded after applying all filters
        total_excluded = total_samples - len(df_copy)
        
        # # Display excluded samples information
        # st.markdown("#### Samples Excluded by Filters")
        # col1, col2 = st.columns(2)
        # with col1:
        #     st.write(f"Signal Intensity: {excluded_by_signal:,d} samples")
        #     st.write(f"Leak Rate: {excluded_by_leak:,d} samples")
        # with col2:
        #     st.write(f"δ13C Range: {excluded_by_d13c:,d} samples")
        #     st.write(f"δ18O Range: {excluded_by_d18o:,d} samples")
        # st.markdown(f"**Total Samples Excluded: {total_excluded:,d} of {total_samples:,d}**")

        st.subheader("Statistical Outlier Settings")
        sigma_level_data = st.number_input("Set Sigma Level for data Outlier Exclusion",
                                         min_value=0.1,
                                         max_value=6.0,
                                         value=4.0,
                                         step=0.1)

        

        # Create a subheader and expander to show active filters

        # with st.expander("Active Filters"):
        #     st.write("Signal Intensity Range:", f"{signal_range[0]:.2f} to {signal_range[1]:.2f}")
        #     st.write("Leak Rate Range:", f"{leak_range[0]:.2f} to {leak_range[1]:.2f}")
        #     st.write("δ13C Range:", f"{d13c_range[0]:.2f} to {d13c_range[1]:.2f}")
        #     st.write("δ18O Range:", f"{d18o_range[0]:.2f} to {d18o_range[1]:.2f}")

        # Prepare main dataset based on user selections
        data_to_process = df_copy.copy()
        
        # Filter by selected Identifier 1 values if not "All"
        if "All" not in st.session_state.selected_ids:
            data_to_process = data_to_process[data_to_process['Identifier 1'].isin(st.session_state.selected_ids)]

        # Initialize mask for statistical outliers
        statistical_mask = pd.Series(False, index=data_to_process.index)
        
        # Calculate statistical outliers separately for each identifier and comment group
        for identifier in data_to_process['Identifier 1'].unique():
            for comment in data_to_process[data_to_process['Identifier 1'] == identifier]['Comment'].unique():
                group_mask = (data_to_process['Identifier 1'] == identifier) & (data_to_process['Comment'] == comment)
                group_data = data_to_process[group_mask]
                
                if len(group_data) > 1:  # Only process groups with more than one sample
                    # Calculate thresholds for this group
                    mean_d13C = group_data['d 13C/12C  Mean'].mean()
                    std_d13C = group_data['d 13C/12C  Mean'].std()
                    mean_d18O = group_data['d 18O/16O  Mean'].mean()
                    std_d18O = group_data['d 18O/16O  Mean'].std()

                    # Identify statistical outliers in this group
                    group_stat_outliers = (
                        (group_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                        (group_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                        (group_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                        (group_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
                    )
                    statistical_mask[group_mask] = group_stat_outliers
                    
        # Get standards from calibration table
        try:
            standards_df = pd.read_csv("standards.csv")
            calibration_standards = standards_df['Standard'].unique().tolist()
        except Exception:
            calibration_standards = []
        
        # Add any selected standards from the calibration tab
        all_standards = calibration_standards + (selected_standards if selected_standards else [])
        
        # Now invert the mask to get within_statistical
        within_statistical = ~statistical_mask

        # Create mask for data within all ranges
        within_ranges = (
            (data_to_process['d 13C/12C  Mean'] >= st.session_state.d13c_range[0]) &
            (data_to_process['d 13C/12C  Mean'] <= st.session_state.d13c_range[1]) &
            (data_to_process['d 18O/16O  Mean'] >= st.session_state.d18o_range[0]) &
            (data_to_process['d 18O/16O  Mean'] <= st.session_state.d18o_range[1]) &
            (data_to_process['1  Cycle Int  Samp  44'] >= st.session_state.signal_range[0]) &
            (data_to_process['1  Cycle Int  Samp  44'] <= st.session_state.signal_range[1]) &
            (data_to_process['leak_rate'] >= st.session_state.leak_range[0]) &
            (data_to_process['leak_rate'] <= st.session_state.leak_range[1])
        )

        # Combine range and statistical masks
        within_all = within_ranges & within_statistical

        # Filter out standards from the data before calculating statistics
        non_standards_mask = ~data_to_process['Identifier 1'].isin(all_standards)
        data_without_standards = data_to_process[non_standards_mask].copy()

        # Calculate total samples (excluding standards)
        # Count unique samples and total measurements
        unique_samples = data_without_standards.groupby(['Identifier 1', 'Identifier 2']).size().reset_index().shape[0]
        total_measurements = len(data_without_standards)

        # Calculate outliers using data_without_standards
        stat_outliers = sum(statistical_mask[non_standards_mask])
        d13c_mask = (data_without_standards['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (data_without_standards['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
        d18o_mask = (data_without_standards['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (data_without_standards['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
        signal_mask = (data_without_standards['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (data_without_standards['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
        leak_mask = (data_without_standards['leak_rate'] < st.session_state.leak_range[0]) | (data_without_standards['leak_rate'] > st.session_state.leak_range[1])

        # Count outliers
        d13c_outliers = sum(d13c_mask)
        d18o_outliers = sum(d18o_mask)
        signal_outliers = sum(signal_mask)
        leak_outliers = sum(leak_mask)

        # Calculate final analyses (total samples minus all outliers)
        total_outliers = stat_outliers + d13c_outliers + d18o_outliers + signal_outliers + leak_outliers
        final_analyses = total_samples - total_outliers

        # Create a DataFrame for displaying statistics
        stats_data = []

        # Add total samples and final analyses
        stats_data.append({
            'Metric': 'Total Unique Samples',
            'Value': unique_samples,
            'Details': '(excluding standards)'
        })
        stats_data.append({
            'Metric': 'Total Measurements',
            'Value': total_measurements,
            'Details': '(excluding standards)'
        })

        # Add outliers by category
        if stat_outliers > 0:
            stats_data.append({
                'Metric': 'Statistical Outliers',
                'Value': stat_outliers,
                'Details': f'({(stat_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        if d13c_outliers > 0:
            stats_data.append({
                'Metric': 'δ13C Range Outliers',
                'Value': d13c_outliers,
                'Details': f'({(d13c_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        if d18o_outliers > 0:
            stats_data.append({
                'Metric': 'δ18O Range Outliers',
                'Value': d18o_outliers,
                'Details': f'({(d18o_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        if signal_outliers > 0:
            stats_data.append({
                'Metric': 'Signal Intensity Outliers',
                'Value': signal_outliers,
                'Details': f'({(signal_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        if leak_outliers > 0:
            stats_data.append({
                'Metric': 'Leak Rate Outliers',
                'Value': leak_outliers,
                'Details': f'({(leak_outliers/total_measurements)*100:.1f}% of measurements)'
            })

        stats_data.append({
            'Metric': 'Final Analyses',
            'Value': final_analyses,
            'Details': f'(Total Measurements - Outliers)'
        })

        # Convert to DataFrame
        stats_df = pd.DataFrame(stats_data)

        # Place the Download Dataset section
        st.subheader("Download Dataset")
        st.write("Configure your dataset download options below:")
        col1, col2, col3 = st.columns(3)
        with col1:
            include_outliers = st.radio(
                "Include outliers in dataset?",
                ["Yes", "No"],
                index=0 if st.session_state.include_outliers == "Yes" else 1,  # Match session state
                help="Choose whether to include outliers in the downloaded dataset",
                key="include_outliers_widget"
            )
            # Update session state
            st.session_state.include_outliers = include_outliers

        with col2:
            selected_ids = st.multiselect(
                "Select Identifier 1 values to include:",
                options=["All"] + list(df_copy['Identifier 1'].unique()),
                default=st.session_state.selected_ids,  # Match session state
                help="Choose specific Identifier 1 values to include in the download. Select 'All' to include everything.",
                key="selected_ids_widget"
            )
            # Update session state
            st.session_state.selected_ids = selected_ids

        with col3:
            st.dataframe(
                stats_df,
                hide_index=True,
                column_config={
                    "Metric": st.column_config.TextColumn("Metric", width=200),
                    "Value": st.column_config.NumberColumn("Value", width=100),
                    "Details": st.column_config.TextColumn("Details", width=150)
                }
            )
        st.markdown("---")

        # Combine range and statistical masks
        within_all = within_ranges & within_statistical

        # Separate data into main_data and outliers_df
        main_data = data_to_process[within_all].copy() if st.session_state.include_outliers == "No" else data_to_process.copy()
        if st.session_state.include_outliers == "No":
            # Collect outliers with their categories
            outliers_df = pd.DataFrame()
            
            # Statistical outliers - making sure to use the correct index
            statistical_mask = pd.Series(False, index=data_to_process.index)
            # Calculate statistical outliers by group
            for identifier in data_to_process['Identifier 1'].unique():
                for comment in data_to_process[data_to_process['Identifier 1'] == identifier]['Comment'].unique():
                    group_mask = (data_to_process['Identifier 1'] == identifier) & (data_to_process['Comment'] == comment)
                    group_data = data_to_process[group_mask]
                    
                    if len(group_data) > 1:  # Only process groups with more than one sample
                        # Calculate thresholds for this group
                        mean_d13C = group_data['d 13C/12C  Mean'].mean()
                        std_d13C = group_data['d 13C/12C  Mean'].std()
                        mean_d18O = group_data['d 18O/16O  Mean'].mean()
                        std_d18O = group_data['d 18O/16O  Mean'].std()

                        # Identify statistical outliers in this group
                        group_stat_outliers = (
                            (group_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                            (group_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                            (group_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                            (group_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
                        )
                        statistical_mask[group_mask] = group_stat_outliers
            
            statistical_outliers = data_to_process[statistical_mask].copy()
            if not statistical_outliers.empty:
                statistical_outliers['Category'] = 'Statistical'
                outliers_df = pd.concat([outliers_df, statistical_outliers])
            
            # Range outliers by category
            d13c_outliers = data_to_process[
                (data_to_process['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (data_to_process['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
            ].copy()
            if not d13c_outliers.empty:
                d13c_outliers['Category'] = 'δ13C Range'
                outliers_df = pd.concat([outliers_df, d13c_outliers])
            
            d18o_outliers = data_to_process[
                (data_to_process['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (data_to_process['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
            ].copy()
            if not d18o_outliers.empty:
                d18o_outliers['Category'] = 'δ18O Range'
                outliers_df = pd.concat([outliers_df, d18o_outliers])
            
            signal_outliers = data_to_process[
                (data_to_process['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (data_to_process['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
            ].copy()
            if not signal_outliers.empty:
                signal_outliers['Category'] = 'Signal Intensity'
                outliers_df = pd.concat([outliers_df, signal_outliers])
            
            leak_outliers = data_to_process[
                (data_to_process['leak_rate'] < st.session_state.leak_range[0]) |
                (data_to_process['leak_rate'] > st.session_state.leak_range[1])
            ].copy()
            if not leak_outliers.empty:
                leak_outliers['Category'] = 'Leak Rate'
                outliers_df = pd.concat([outliers_df, leak_outliers])
            
            # Remove duplicates (in case a sample is an outlier in multiple categories)
            if not outliers_df.empty:
                outliers_df = outliers_df.drop_duplicates(subset=['Identifier 1', 'Identifier 2'])
        else:
            outliers_df = pd.DataFrame()
        # Generate descriptive filename
        filename_parts = []
        if "All" not in selected_ids:
            if len(selected_ids) <= 3:
                filename_parts.append(f"ID{'_'.join(selected_ids)}")
            else:
                filename_parts.append(f"ID{len(selected_ids)}selected")
        filename_parts.append(f"{'with' if include_outliers == 'Yes' else 'without'}_outliers")
        filename = f"dataset_{'_'.join(filename_parts)}.xlsx"

        # For "Include outliers = Yes", add outliers to main data and clear outliers_df
        if st.session_state.include_outliers == "Yes":
            main_data = pd.concat([main_data, outliers_df], ignore_index=True)
            outliers_df = pd.DataFrame()
            
        download_excel(main_data, outliers=outliers_df, filename=filename, selected_standards=selected_standards)

        # Read the standards.csv file
        standards_df = pd.read_csv("standards.csv")
        standard_identifiers = standards_df['Standard'].unique()

        # Get unique identifiers excluding those in the standards file
        unique_identifiers = [
            identifier for identifier in df_copy['Identifier 1'].unique()
            if pd.notna(identifier) and identifier not in standard_identifiers
        ]

        # Add 'All' option to the unique_identifiers list (this will allow the user to select all identifiers)
        unique_identifiers.insert(0, 'All')

        # Charts Settings section
        st.subheader("Charts Settings")
        
        col1, col2 = st.columns(2)
        with col1:
            selected_identifier = st.selectbox("Select Identifier 1:", options=unique_identifiers)
            x_axis_option = st.selectbox(
                "Choose X-Axis Display Option:",
                options=["By Identifier 2", "By Sequence"]
            )
            
        with col2:
            # New dropdown selector in Tab 3 for color parameter
            selected_color_param_tab3 = st.selectbox("Choose a parameter to color the dots in Tab 3:", color_param_names, index='Date' in color_param_names)
            color_param_tab3 = color_options[selected_color_param_tab3]

            show_statistical_outliers = st.checkbox("Show statistical outliers on chart", value=False, key="show_statistical_outliers")
            show_range_outliers = st.checkbox("Show range outliers on chart", value=False, key="show_range_outliers")

        # If 'All' is selected, include data for all identifiers
        if selected_identifier == 'All':
            subset_data = df_filtered
            subset_data_unfiltered = df_unfiltered
            
            # Get the actual data range for the selected parameter
            param_min = df_filtered[color_param_tab3].min()
            param_max = df_filtered[color_param_tab3].max()
            
            # Create a shared colorbar figure
            colorbar_fig = go.Figure(go.Scatter(
                x=[0],  # Dummy data
                y=[0],
                mode='markers',
                marker=dict(
                    size=1,
                    color=[param_min, param_max],  # Use actual data range
                    cmin=param_min,
                    cmax=param_max,
                    colorscale="Viridis",
                    showscale=True,
                    colorbar=dict(
                        title=dict(
                            text=selected_color_param_tab3,
                            side='top'  # Move title above the colorbar
                        ),
                        len=0.6,  # Make colorbar wider
                        thickness=20,  # Make colorbar taller
                        x=0.5,  # Center horizontally
                        xanchor='center',
                        y=0.5,  # Center vertically
                        yanchor='middle',
                        orientation='h'  # Horizontal orientation
                    )
                ),
                showlegend=False
            ))
            colorbar_fig.update_layout(
                margin=dict(t=30, b=0, l=50, r=50),  # Adjust margins for better spacing
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                yaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                height=100,  # Taller height for better visibility
                width=None  # Let width be determined by container
            )
            with col2:
                st.plotly_chart(colorbar_fig, use_container_width=True)
        else:
            subset_data = df_filtered[df_filtered['Identifier 1'] == selected_identifier]
            subset_data_unfiltered = df_unfiltered[df_unfiltered['Identifier 1'] == selected_identifier]




        # Replace NaN values in the 'Comment' column with a placeholder
        # Ensure 'Comment' column is of type object (string) to allow text values
        subset_data['Comment'] = subset_data['Comment'].astype(str).fillna("No Comment")

        # Iterate through unique comments (including the placeholder)
        unique_comments = subset_data['Comment'].unique()

        # Create x_axis values
        subset_data['x_axis'] = np.nan
        if x_axis_option == "By Identifier 2":
            subset_data['x_axis'] = subset_data['Identifier 2'].apply(
                lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                    r'\d+\.?\d*', str(x)) else None
            )
        else:
            subset_data['x_axis'] = range(len(subset_data))

        # Summary Charts
        st.subheader("Summary Charts")
        
        # Create summary chart for d13C
        d13c_summary = go.Figure()
        for species in unique_comments:
            if species == "No Comment":
                continue
            
            species_data = subset_data[subset_data['Comment'] == species]
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered['Comment'] == species]
            
            # Calculate statistical outliers
            mean_d13C = species_data['d 13C/12C  Mean'].mean()
            std_d13C = species_data['d 13C/12C  Mean'].std()
            mean_d18O = species_data['d 18O/16O  Mean'].mean()
            std_d18O = species_data['d 18O/16O  Mean'].std()
            
            outlier_mask = (
                (species_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                (species_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                (species_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                (species_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
            )
            # Store statistical outliers
            statistical_outliers = species_data[outlier_mask].copy()

            # Calculate range outliers mask (always calculate to filter data)
            range_mask = (
                (species_data['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (species_data['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                (species_data['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (species_data['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                (species_data['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (species_data['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                (species_data['leak_rate'] < st.session_state.leak_range[0]) |
                (species_data['leak_rate'] > st.session_state.leak_range[1])
            )

            # Store range outliers if showing them
            if show_range_outliers:
                range_outliers = species_data[range_mask].copy()
                # Add x_axis values to range outliers
                if x_axis_option == "By Identifier 2":
                    range_outliers['x_axis'] = range_outliers['Identifier 2'].apply(
                        lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                            r'\d+\.?\d*', str(x)) else None
                    )
                else:
                    range_outliers['x_axis'] = range(len(range_outliers))
            else:
                range_outliers = pd.DataFrame(columns=species_data.columns)
                
            # Filter data to plot - exclude both statistical and range outliers
            data_to_plot = species_data[~(outlier_mask | range_mask)].copy()

            # Plot main data
            # Generate unique color based on species/comment
            species_color = f'rgb({hash(species) % 255}, {(hash(species) >> 8) % 255}, {(hash(species) >> 16) % 255})'
            
            d13c_summary.add_trace(go.Scatter(
                x=data_to_plot['x_axis'],
                y=data_to_plot['d 13C/12C  Mean'],
                mode='lines+markers',
                name=species,
                marker=dict(
                    size=8,
                    color=data_to_plot[color_param_tab3],
                    colorscale="Viridis",
                    showscale=False
                ),
                line=dict(width=1, color=species_color),
                legendgroup=species
            ))

            # Plot statistical outliers if enabled
            if show_statistical_outliers and not statistical_outliers.empty:
                d13c_summary.add_trace(go.Scatter(
                    x=statistical_outliers['x_axis'],
                    y=statistical_outliers['d 13C/12C  Mean'],
                    mode='markers',
                    name='Statistical Outliers',
                    marker=dict(
                        size=12,
                        symbol='x',
                        color=species_color,
                        line=dict(width=2, color=species_color)
                    ),
                    showlegend=True,
                    legendgroup='outliers'
                ))

            # Plot range outliers by type if enabled
            if show_range_outliers and not range_outliers.empty:
                # Signal intensity outliers
                signal_mask = (range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                if signal_mask.any():
                    d13c_summary.add_trace(go.Scatter(
                        x=range_outliers[signal_mask]['x_axis'],
                        y=range_outliers[signal_mask]['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='diamond',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Signal Intensity Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # Leak rate outliers
                leak_mask = (range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (range_outliers['leak_rate'] > st.session_state.leak_range[1])
                if leak_mask.any():
                    d13c_summary.add_trace(go.Scatter(
                        x=range_outliers[leak_mask]['x_axis'],
                        y=range_outliers[leak_mask]['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='star',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Leak Rate Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # δ13C range outliers
                d13c_mask = (range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                if d13c_mask.any():
                    d13c_summary.add_trace(go.Scatter(
                        x=range_outliers[d13c_mask]['x_axis'],
                        y=range_outliers[d13c_mask]['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='cross',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='δ13C Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # δ18O range outliers
                d18o_mask = (range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
                if d18o_mask.any():
                    d13c_summary.add_trace(go.Scatter(
                        x=range_outliers[d18o_mask]['x_axis'],
                        y=range_outliers[d18o_mask]['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='x',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='δ18O Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))
        d13c_summary.update_layout(
            title="δ13C Summary by Species",
            xaxis_title="Sample Number" if x_axis_option == "By Sequence" else "Identifier 2",
            yaxis_title="δ13C",
            showlegend=True,
            height=500
        )
        st.plotly_chart(d13c_summary, use_container_width=True)
        
        # Create summary chart for d18O
        d18o_summary = go.Figure()
        for species in unique_comments:
            if species == "No Comment":
                continue
            
            species_data = subset_data[subset_data['Comment'] == species]
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered['Comment'] == species]
            
            # Calculate statistical outliers
            mean_d13C = species_data['d 13C/12C  Mean'].mean()
            std_d13C = species_data['d 13C/12C  Mean'].std()
            mean_d18O = species_data['d 18O/16O  Mean'].mean()
            std_d18O = species_data['d 18O/16O  Mean'].std()
            
            outlier_mask = (
                (species_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                (species_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                (species_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                (species_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
            )
            statistical_outliers = species_data[outlier_mask].copy()
            data_to_plot = species_data[~outlier_mask].copy()
            
            # Calculate range outliers
            if show_range_outliers:
                range_mask = (
                    (species_data_unfiltered['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                    (species_data_unfiltered['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                    (species_data_unfiltered['leak_rate'] < st.session_state.leak_range[0]) |
                    (species_data_unfiltered['leak_rate'] > st.session_state.leak_range[1])
                )
                range_outliers = species_data_unfiltered[range_mask].copy()
                # Add x_axis values to range outliers
                if x_axis_option == "By Identifier 2":
                    range_outliers['x_axis'] = range_outliers['Identifier 2'].apply(
                        lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                            r'\d+\.?\d*', str(x)) else None
                    )
                else:
                    range_outliers['x_axis'] = range(len(range_outliers))
            else:
                range_outliers = pd.DataFrame(columns=species_data.columns)

            # Plot main data
            # Generate unique color for this species
            species_color = f'rgb({hash(species) % 255}, {(hash(species) >> 8) % 255}, {(hash(species) >> 16) % 255})'
            
            # Plot main data with consistent color
            d18o_summary.add_trace(go.Scatter(
                x=data_to_plot['x_axis'],
                y=data_to_plot['d 18O/16O  Mean'],
                mode='lines+markers',
                name=species,
                marker=dict(
                    size=8,
                    color=data_to_plot[color_param_tab3],
                    colorscale="Viridis",
                    showscale=False
                ),
                line=dict(width=1, color=species_color),
                legendgroup=species
            ))

            # Plot statistical outliers if enabled
            if show_statistical_outliers and not statistical_outliers.empty:
                d18o_summary.add_trace(go.Scatter(
                    x=statistical_outliers['x_axis'],
                    y=statistical_outliers['d 18O/16O  Mean'],
                    mode='markers',
                    name='Statistical Outliers',
                    marker=dict(
                        size=12,
                        symbol='x',
                        color=species_color,
                        line=dict(width=2, color=species_color)
                    ),
                    showlegend=True,
                    legendgroup='outliers'
                ))

            # Plot range outliers by type if enabled
            if show_range_outliers and not range_outliers.empty:
                # Signal intensity outliers
                signal_mask = (range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                if signal_mask.any():
                    d18o_summary.add_trace(go.Scatter(
                        x=range_outliers[signal_mask]['x_axis'],
                        y=range_outliers[signal_mask]['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='diamond',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Signal Intensity Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # Leak rate outliers
                leak_mask = (range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (range_outliers['leak_rate'] > st.session_state.leak_range[1])
                if leak_mask.any():
                    d18o_summary.add_trace(go.Scatter(
                        x=range_outliers[leak_mask]['x_axis'],
                        y=range_outliers[leak_mask]['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='star',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Leak Rate Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # δ13C range outliers
                d13c_mask = (range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                if d13c_mask.any():
                    d18o_summary.add_trace(go.Scatter(
                        x=range_outliers[d13c_mask]['x_axis'],
                        y=range_outliers[d13c_mask]['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='cross',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='δ13C Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # δ18O range outliers
                d18o_mask = (range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
                if d18o_mask.any():
                    d18o_summary.add_trace(go.Scatter(
                        x=range_outliers[d18o_mask]['x_axis'],
                        y=range_outliers[d18o_mask]['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='x',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='δ18O Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))
        d18o_summary.update_layout(
            title="δ18O Summary by Species",
            xaxis_title="Sample Number" if x_axis_option == "By Sequence" else "Identifier 2",
            yaxis_title="δ18O",
            showlegend=True,
            height=500
        )
        st.plotly_chart(d18o_summary, use_container_width=True)

        # Process individual species
        for comment in unique_comments:
            # Filter data for this specific comment
            comment_data = subset_data[subset_data['Comment'] == comment]
            
            # Skip if Identifier 2 is empty
            if comment_data['Identifier 2'].isna().all():
                continue

            col1, col2 = st.columns([3, 1])
            with col1:
                st.subheader(f'Species: {comment}')

            # Calculate thresholds for outliers for each comment subset
            mean_d13C = comment_data['d 13C/12C  Mean'].mean()
            std_d13C = comment_data['d 13C/12C  Mean'].std()
            mean_d18O = comment_data['d 18O/16O  Mean'].mean()
            std_d18O = comment_data['d 18O/16O  Mean'].std()

            lower_threshold_d13C = mean_d13C - (sigma_level_data * std_d13C)
            upper_threshold_d13C = mean_d13C + (sigma_level_data * std_d13C)
            lower_threshold_d18O = mean_d18O - (sigma_level_data * std_d18O)
            upper_threshold_d18O = mean_d18O + (sigma_level_data * std_d18O)

            # Create x_axis values first for all data
            comment_data['x_axis'] = np.nan
            if x_axis_option == "By Identifier 2":
                comment_data['x_axis'] = comment_data['Identifier 2'].apply(
                    lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                        r'\d+\.?\d*', str(x)) else None
                )
            else:
                comment_data['x_axis'] = range(len(comment_data))

            # Now identify statistical outliers (after x_axis is created)
            outlier_mask = (
                (comment_data['d 13C/12C  Mean'] < lower_threshold_d13C) |
                (comment_data['d 13C/12C  Mean'] > upper_threshold_d13C) |
                (comment_data['d 18O/16O  Mean'] < lower_threshold_d18O) |
                (comment_data['d 18O/16O  Mean'] > upper_threshold_d18O)
            )
            # Apply mask and include necessary columns (including x_axis)
            statistical_outliers = comment_data[outlier_mask].copy()

            # Remove statistical outliers from data_to_plot
            data_to_plot = comment_data[~outlier_mask].copy()

            # Identify range bar outliers from unfiltered data
            # Identify and process range outliers if enabled
            if show_range_outliers:
                species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered['Comment'] == comment]
                # Create mask for range outliers
                range_mask = (
                    (species_data_unfiltered['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                    (species_data_unfiltered['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                    (species_data_unfiltered['leak_rate'] < st.session_state.leak_range[0]) |
                    (species_data_unfiltered['leak_rate'] > st.session_state.leak_range[1])
                )
                # Apply mask and include necessary columns
                range_bar_outliers = species_data_unfiltered[range_mask].copy()

                # Add x_axis values to range outliers if any were found
                if not range_bar_outliers.empty:
                    if x_axis_option == "By Identifier 2":
                        range_bar_outliers['x_axis'] = range_bar_outliers['Identifier 2'].apply(
                            lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                                r'\d+\.?\d*', str(x)) else None
                        )
                    else:
                        range_bar_outliers['x_axis'] = range(len(range_bar_outliers))
            else:
                # Create empty DataFrame with required columns
                range_bar_outliers = pd.DataFrame(columns=['Identifier 1', 'Identifier 2', 'd 13C/12C  Mean', 'd 18O/16O  Mean', 'Comment', 'x_axis'])

            # Combine both types of outliers
            outliers = pd.concat([statistical_outliers, range_bar_outliers]).drop_duplicates()

            # Handle range outliers
            if not show_range_outliers:
                data_to_plot = data_to_plot[~data_to_plot.index.isin(range_bar_outliers.index)]
            
            # Create a DataFrame for displaying points, always excluding outliers for the main curve
            display_data = data_to_plot.copy()
                
            # Sort the data by x_axis to ensure proper line connections
            display_data = display_data.sort_values(by='x_axis', na_position='last')

            chart_height = 500


            # x_axis values are already created and sorted earlier

            # Loop through all identifiers to plot data for each identifier
            for identifier in unique_identifiers:
                if identifier == 'All':
                    continue  # Skip the 'All' selection here to avoid combined plotting

                # Filter data for the current identifier
                data_for_identifier = data_to_plot[data_to_plot['Identifier 1'] == identifier]

                if data_for_identifier.empty:
                    continue  # Skip if there is no data to plot for this identifier

                # Plot δ13C data for this identifier and comment
                # Create figure for δ13C
                fig_d13C = go.Figure()

                # Add statistical outliers as markers if enabled
                if show_statistical_outliers and not statistical_outliers.empty:
                    identifier_stat_outliers = statistical_outliers[statistical_outliers['Identifier 1'] == identifier]
                    if not identifier_stat_outliers.empty:
                        fig_d13C.add_trace(go.Scatter(
                            x=identifier_stat_outliers['x_axis'],
                            y=identifier_stat_outliers['d 13C/12C  Mean'],
                            mode='markers',
                            marker=dict(
                                color='red',
                                symbol='x',
                                size=12,
                                line=dict(width=2)
                            ),
                            name='Statistical Outliers'
                        ))
                        # Add them to display_data if checkbox is checked - no need to add here since they're already in display_data

                # Add range outliers if enabled
                if show_range_outliers:
                    identifier_range_outliers = range_bar_outliers[range_bar_outliers['Identifier 1'] == identifier]
                    if not identifier_range_outliers.empty:
                        # Identify outlier types
                        signal_range_mask = (identifier_range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (identifier_range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                        leak_range_mask = (identifier_range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (identifier_range_outliers['leak_rate'] > st.session_state.leak_range[1])
                        d13c_filter_mask = (identifier_range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (identifier_range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                        d18o_filter_mask = (identifier_range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (identifier_range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])

                        # Plot each type with different symbol but same red color
                        if signal_range_mask.any():
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_range_outliers[signal_range_mask]['x_axis'],
                                y=identifier_range_outliers[signal_range_mask]['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='diamond', size=12, line=dict(width=2)),
                                name='Signal Intensity Range'
                            ))
                        if leak_range_mask.any():
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_range_outliers[leak_range_mask]['x_axis'],
                                y=identifier_range_outliers[leak_range_mask]['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='star', size=12, line=dict(width=2)),
                                name='Leak Rate Range'
                            ))
                        if d13c_filter_mask.any():
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_range_outliers[d13c_filter_mask]['x_axis'],
                                y=identifier_range_outliers[d13c_filter_mask]['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='cross', size=12, line=dict(width=2)),
                                name='δ13C Range'
                            ))
                        if d18o_filter_mask.any():
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_range_outliers[d18o_filter_mask]['x_axis'],
                                y=identifier_range_outliers[d18o_filter_mask]['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='x', size=12, line=dict(width=2)),
                                name='δ18O Range'
                            ))

                fig_d13C.add_trace(go.Scatter(
                    x=display_data[display_data['Identifier 1'] == identifier]['x_axis'],
                    y=display_data[display_data['Identifier 1'] == identifier]['d 13C/12C  Mean'],
                    mode='lines+markers',
                    line=dict(color='blue', dash='dot', width=2),
                    marker=dict(
                        color=display_data[display_data['Identifier 1'] == identifier][color_param_tab3],
                        colorscale="Viridis",
                        symbol='circle',
                        size=8,
                        showscale=False  # Hide individual colorbar
                    ),
                    name=f'Raw δ13C - {identifier}'
                ))

                if 'd13C_calibrated' in data_for_identifier.columns:
                    fig_d13C.add_trace(go.Scatter(
                        x=display_data[display_data['Identifier 1'] == identifier]['x_axis'],
                        y=display_data[display_data['Identifier 1'] == identifier]['d13C_calibrated'],
                        mode='lines+markers',
                        line=dict(color='orange', dash='dot', width=2),
                        marker=dict(
                            color=display_data[display_data['Identifier 1'] == identifier][color_param_tab3],
                            colorscale="Viridis",
                            symbol='square',
                            size=8,
                            showscale=False  # Hide individual colorbar
                        ),
                        name=f'Calibrated δ13C - {identifier}'
                    ))

                fig_d13C.update_layout(
                    title=f'{identifier} - δ13C for Species: {comment}',
                    xaxis_title='X Axis',
                    yaxis_title='δ13C (‰)',
                    legend_title='Data Type',
                    margin=dict(r=100, t=100),  # Reduced right margin
                    xaxis=dict(
                        # Show ~10 ticks across the axis
                        nticks=10,
                        tickmode='auto'
                    ),
                    legend=dict(
                        x=1.05,  # Move legend closer to chart
                        xanchor='left',
                        y=0.8,  # Keep consistent position
                        yanchor='middle'
                    )
                )

                st.plotly_chart(fig_d13C, use_container_width=True, height=chart_height)

                # Plot δ18O data for this identifier and comment
                # Create figure for δ18O
                fig_d18O = go.Figure()

                # Add statistical outliers if enabled
                if show_statistical_outliers:
                    identifier_stat_outliers = statistical_outliers[statistical_outliers['Identifier 1'] == identifier]
                    if not identifier_stat_outliers.empty:
                        fig_d18O.add_trace(go.Scatter(
                            x=identifier_stat_outliers['x_axis'],
                            y=identifier_stat_outliers['d 18O/16O  Mean'],
                            mode='markers',
                            marker=dict(
                                color='red',
                                symbol='x',
                                size=12,
                                line=dict(width=2)
                            ),
                            name='Statistical Outliers'
                        ))

                # Add range outliers if enabled
                # Initialize filter masks with default values
                signal_range_mask = pd.Series(False)
                leak_range_mask = pd.Series(False)
                d13c_filter_mask = pd.Series(False)
                d18o_filter_mask = pd.Series(False)

                if show_range_outliers:
                    identifier_range_outliers = range_bar_outliers[range_bar_outliers['Identifier 1'] == identifier]
                    if not identifier_range_outliers.empty:
                        # Identify outlier types
                        signal_range_mask = (identifier_range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (identifier_range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                        leak_range_mask = (identifier_range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (identifier_range_outliers['leak_rate'] > st.session_state.leak_range[1])
                        d13c_filter_mask = (identifier_range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (identifier_range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                        d18o_filter_mask = (identifier_range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (identifier_range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])

                        # Plot each type with different symbol but same red color
                        if signal_range_mask.any():
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_range_outliers[signal_range_mask]['x_axis'],
                                y=identifier_range_outliers[signal_range_mask]['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='diamond', size=12, line=dict(width=2)),
                                name='Signal Intensity Range'
                            ))
                        if leak_range_mask.any():
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_range_outliers[leak_range_mask]['x_axis'],
                                y=identifier_range_outliers[leak_range_mask]['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='star', size=12, line=dict(width=2)),
                                name='Leak Rate Range'
                            ))
    
                    # Add main data trace using display_data
                    fig_d18O.add_trace(go.Scatter(
                        x=display_data[display_data['Identifier 1'] == identifier]['x_axis'],
                        y=display_data[display_data['Identifier 1'] == identifier]['d 18O/16O  Mean'],
                        mode='lines+markers',
                        line=dict(color='blue', dash='dot', width=2),
                        marker=dict(
                            color=display_data[display_data['Identifier 1'] == identifier][color_param_tab3],
                            colorscale="Viridis",
                            symbol='circle',
                            size=8,
                            showscale=False  # Hide individual colorbar
                        ),
                        name=f'Raw δ18O - {identifier}'
                    ))
    
                    if 'd18O_calibrated' in display_data.columns:
                        fig_d18O.add_trace(go.Scatter(
                            x=display_data[display_data['Identifier 1'] == identifier]['x_axis'],
                            y=display_data[display_data['Identifier 1'] == identifier]['d18O_calibrated'],
                            mode='lines+markers',
                            line=dict(color='orange', dash='dot', width=2),
                            marker=dict(
                                color=display_data[display_data['Identifier 1'] == identifier][color_param_tab3],
                                colorscale="Viridis",
                                symbol='square',
                                size=8
                            ),
                            name=f'Calibrated δ18O - {identifier}'
                        ))
                        if d13c_filter_mask.any():
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_range_outliers[d13c_filter_mask]['x_axis'],
                                y=identifier_range_outliers[d18o_filter_mask]['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='cross', size=12, line=dict(width=2)),
                                name='δ13C Range'
                            ))
                        if d18o_filter_mask.any():
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_range_outliers[d18o_filter_mask]['x_axis'],
                                y=identifier_range_outliers[d18o_filter_mask]['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='x', size=12, line=dict(width=2)),
                                name='δ18O Range'
                            ))

                # Plot main data trace with correct sorting
                sorted_data = data_for_identifier.sort_values(by='x_axis')
                fig_d18O.add_trace(go.Scatter(
                    x=sorted_data['x_axis'],
                    y=sorted_data['d 18O/16O  Mean'],
                    mode='lines+markers',
                    line=dict(color='blue', dash='dot', width=2),
                    marker=dict(
                        color=sorted_data[color_param_tab3],
                        colorscale="Viridis",
                        symbol='circle',
                        size=8,
                        showscale=False  # Hide individual colorbar
                    ),
                    name=f'Raw δ18O - {identifier}'
                ))

                if 'd18O_calibrated' in data_for_identifier.columns:
                    fig_d18O.add_trace(go.Scatter(
                        x=sorted_data['x_axis'],
                        y=sorted_data['d18O_calibrated'],
                        mode='lines+markers',
                        line=dict(color='orange', dash='dot', width=2),
                        marker=dict(
                            color=sorted_data[color_param_tab3],
                            colorscale="Viridis",
                            symbol='square',
                            size=8
                        ),
                        name=f'Calibrated δ18O - {identifier}'
                    ))

                fig_d18O.update_layout(
                    title=f'{identifier} - δ18O for Species: {comment}',
                    xaxis_title='X Axis',
                    yaxis_title='δ18O (‰)',
                    legend_title='Data Type',
                    margin=dict(r=100, t=100),  # Reduced right margin
                    xaxis=dict(
                        # Show ~10 ticks across the axis
                        nticks=10,
                        tickmode='auto'
                    ),
                    legend=dict(
                        x=1.05,  # Move legend closer to chart
                        xanchor='left',
                        y=0.8,  # Keep consistent position
                        yanchor='middle'
                    )
                )

                st.plotly_chart(fig_d18O, use_container_width=True, height=chart_height)

            # Display outliers header for each comment if detected
            if not comment_data['Identifier 2'].isna().all():
                st.subheader(f'Outliers Detected for Species: {comment}')
            
            # Get outliers data
            stat_outliers_only = statistical_outliers[statistical_outliers['Comment'] == comment]
            
            # Get original data for this species before any filtering
            species_data = subset_data_unfiltered[subset_data_unfiltered['Comment'] == comment]
            
            # Create masks for each range category
            d13c_outliers = species_data[
                (species_data['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (species_data['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
            ]
            
            d18o_outliers = species_data[
                (species_data['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (species_data['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
            ]
            
            signal_outliers = species_data[
                (species_data['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (species_data['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
            ]
            
            leak_outliers = species_data[
                (species_data['leak_rate'] < st.session_state.leak_range[0]) |
                (species_data['leak_rate'] > st.session_state.leak_range[1])
            ]
        
            # Create two columns for outlier information
            col1, col2 = st.columns(2)

            # Column 1: Isotope Outliers
            with col1:
                st.markdown("### 📊 Isotope Outliers")
                st.markdown("---")
                
                # Statistical Outliers
                with st.expander("Statistical Outliers (Sigma-Based)", expanded=True):
                    if not stat_outliers_only.empty:
                        st.markdown("**Based on statistical deviation from the mean**")
                        styled_stats = stat_outliers_only[['Identifier 2', 'Comment', 'd 13C/12C  Mean', 'd 18O/16O  Mean']].copy()
                        styled_stats = styled_stats.rename(columns={
                            'd 13C/12C  Mean': 'δ13C Value (‰)',
                            'd 18O/16O  Mean': 'δ18O Value (‰)'
                        })
                        st.dataframe(styled_stats, use_container_width=True)
                    else:
                        st.info("No statistical outliers detected")

                # δ13C Outliers
                with st.expander("δ13C Range Outliers", expanded=True):
                    if not d13c_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.d13c_range[0]:.2f} to {st.session_state.d13c_range[1]:.2f} ‰")
                        styled_d13c = d13c_outliers[['Identifier 2', 'Comment', 'd 13C/12C  Mean']].copy()
                        styled_d13c = styled_d13c.rename(columns={'d 13C/12C  Mean': 'δ13C Value (‰)'})
                        st.dataframe(styled_d13c, use_container_width=True)
                    else:
                        st.info("No δ13C outliers detected")

                # δ18O Outliers
                with st.expander("δ18O Range Outliers", expanded=True):
                    if not d18o_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.d18o_range[0]:.2f} to {st.session_state.d18o_range[1]:.2f} ‰")
                        styled_d18o = d18o_outliers[['Identifier 2', 'Comment', 'd 18O/16O  Mean']].copy()
                        styled_d18o = styled_d18o.rename(columns={'d 18O/16O  Mean': 'δ18O Value (‰)'})
                        st.dataframe(styled_d18o, use_container_width=True)
                    else:
                        st.info("No δ18O outliers detected")

            # Column 2: Technical Outliers
            with col2:
                st.markdown("### 🔧 Technical Outliers")
                st.markdown("---")
                
                # Signal Intensity Outliers
                with st.expander("Signal Intensity Outliers", expanded=True):
                    if not signal_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.signal_range[0]:.2f} to {st.session_state.signal_range[1]:.2f}")
                        styled_signal = signal_outliers[['Identifier 2', 'Comment', '1  Cycle Int  Samp  44']].copy()
                        styled_signal = styled_signal.rename(columns={'1  Cycle Int  Samp  44': 'Signal Intensity'})
                        st.dataframe(styled_signal, use_container_width=True)
                    else:
                        st.info("No signal intensity outliers detected")
                
                # Leak Rate Outliers
                with st.expander("Leak Rate Outliers", expanded=True):
                    if not leak_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.leak_range[0]:.2f} to {st.session_state.leak_range[1]:.2f}")
                        styled_leak = leak_outliers[['Identifier 2', 'Comment', 'leak_rate']].copy()
                        styled_leak = styled_leak.rename(columns={'leak_rate': 'Leak Rate'})
                        st.dataframe(styled_leak, use_container_width=True)
                    else:
                        st.info("No leak rate outliers detected")

            # with st.expander("Leak Rate Outliers", expanded=True):
            #     if not leak_outliers.empty:
            #         st.markdown(f"Range: {st.session_state.leak_range[0]:.2f} to {st.session_state.leak_range[1]:.2f}")
            #         st.dataframe(leak_outliers[['Identifier 2', 'Comment', 'leak_rate']])
            #     else:
            #         st.write("No leak rate outliers detected")

        #     # Check if the required columns are present
        #     calibrated_columns = ['d18O_calibrated', 'd13C_calibrated']
        #     calibration_status = all(col in data_to_plot.columns for col in calibrated_columns)

        #     # Determine the calibration status and set the filename
        #     if calibration_status:
        #         calibration_label = "Calibration performed"
        #         filename_suffix = "calibrated"
        #         label_color = "green"
        #         columns_to_export = [
        #             'Row', 'Method', 'Date', 'Time', 'Identifier 1', 'Identifier 2', 'Comment',
        #             'd 13C/12C  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Mean', 'd 18O/16O  Std Dev',
        #             'd13C_calibrated', 'd18O_calibrated'
        #         ]
        #     else:
        #         calibration_label = "Calibration not performed"
        #         filename_suffix = "uncalibrated"
        #         label_color = "red"
        #         columns_to_export = [
        #             'Row', 'Method', 'Date', 'Time', 'Identifier 1', 'Identifier 2', 'Comment',
        #             'd 13C/12C  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Mean', 'd 18O/16O  Std Dev'
        #         ]

        #     # Add a colored label next to the button indicating calibration status
        #     st.markdown(f'<span style="color:{label_color}; font-weight:bold;">{calibration_label}</span>',
        #                 unsafe_allow_html=True)


        #     # Function to convert the dataframe to an Excel file
        #     @st.cache_data
        #     def to_excel(df):
        #         output = io.BytesIO()
        #         with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        #             df.to_excel(writer, index=False, sheet_name='Data')
        #         return output.getvalue()

        #     # Filter the dataframe to include only the columns to export
        #     filtered_data = data_to_plot[columns_to_export]  # Assuming 'df' is your dataframe

        #     # Export the filtered data as Excel
        #     excel_data = to_excel(filtered_data)

        #     # Create the download button
        #     st.download_button(
        #         label="Download Data as Excel",
        #         data=excel_data,
        #         file_name=f'{identifier}_{comment}_{filename_suffix}_results.xlsx',
        #         mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        #     )
        # else:
        #     st.write("No chart displayed since 'All' was selected.")





if __name__ == '__main__':
    main()