# Processing Workflow

This document locks the processing pipeline order for both the backend API and the Streamlit compatibility path.

## Canonical Pipeline

1. Load `snapshot.csv` as the canonical stored dataframe.
2. Load `cycles_snapshot.csv` when cycle diagnostics are needed.
3. Load persisted metadata:
   - calibration coefficients
   - linearity fits
   - processing config
   - export config
   - edit state
4. Build the derived working frame:
   - copy stored dataframe
   - add derived sequence/x-axis helpers
   - apply manual linearity override if enabled
   - do not mutate the stored raw dataframe during this step
5. Build processing masks:
   - signal range
   - leak range
   - d13C range
   - d18O range
   - statistical outliers
   - failed / partially saturated / fully saturated status masks
   - edited-row exclusion from automatic outliering
   - manual outlier overrides
6. Build summary and outlier tables:
   - `ProcessingSummary`
   - global outlier tables
   - species-level outlier tables
7. Build chart bundles:
   - overview figures
   - species sections
   - chart `customdata` tokens for selection and editing
8. Apply edit actions only against the stored dataframe:
   - set value
   - offset
   - interpolate
   - reset selected
   - reset all
   - set outlier override
9. After every raw-delta edit:
   - refresh collector status
   - refresh calibrated columns from stored calibration metadata
   - persist updated dataframe and edit state
10. Build cycle diagnostics on demand from the stored dataframe plus cycle dataframe:
    - selected-point target info
    - cycle chart
    - cycle table
    - valid-cycle mean
    - optional linearity preview
    - interpolation neighbor hints
11. Build export frames from the derived working frame plus current metadata:
    - filter to selected IDs
    - apply current outlier semantics
    - include or exclude outliers
    - interpolate before export if requested
    - preserve original-value columns for interpolated export
    - build `Statistics`, `Data`, `Outliers` when applicable, and `Client Output`

## Ownership Rules

Backend-owned:

- all dataframe mutation
- all outlier semantics
- all calibration-aware recalculation
- all cycle diagnostics
- all Plotly figure generation
- all export workbook generation

Frontend-owned:

- current selection
- current selection index for navigation
- raw-only display toggles
- hide-calibrated display toggles
- accordion expansion state
- unsaved local form edits before `Apply config`

## Non-Negotiable Constraints

1. Manual linearity override is derived-only and must never overwrite stored imported values.
2. Edited rows remain excluded from automatic statistical/range outliering until reset.
3. Manual outlier overrides must affect every downstream consumer: charts, tables, diagnostics, and export.
4. Streamlit remains the compatibility oracle until the dashboard and tests cover the full workflow.
