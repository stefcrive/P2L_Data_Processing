from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover - optional for logic-only tests
    go = None

from ..contracts import IdentifierFigureSet, SpeciesSection
from ..constants import CYCLE1_SIGNAL_REF44_COL, CYCLE1_SIGNAL_SAMP44_COL
from ..shared.dataframe import _get_species_series, _parse_numeric_token
from ..shared.json_compat import to_json_compatible
from ..shared.plotting import (
    _apply_cycle_std_error_bars,
    _build_species_symbol_map,
    _build_cycle_std_lookups,
    _build_date_colorbar_ticks,
    _build_delta_point_customdata,
    _build_isotope_3d_scatter,
    _build_plotly_error_bar_for_df,
    _exclusive_outlier_masks,
    _is_date_color_column,
    _prefer_datetime_color_values,
    _prepare_color_values,
    _species_symbol_for_label,
)
from .outliers import (
    RangeConfig,
    _partial_saturation_isotope_masks,
    _signal_in_range_mask,
    build_category_masks,
    build_outlier_tables,
    compute_statistical_outlier_masks,
)


def _figure_json(fig: go.Figure | None) -> dict[str, Any]:
    if fig is None:
        return {}
    _attach_point_ids_from_customdata(fig)
    return to_json_compatible(fig.to_plotly_json())


def _extract_point_ids_from_customdata(customdata: Any) -> list[str] | None:
    if customdata is None:
        return None
    try:
        arr = np.asarray(customdata, dtype=object)
    except Exception:
        return None
    if arr.ndim == 0:
        return None
    labels: list[str] = []
    if arr.ndim == 1:
        for item in arr.tolist():
            labels.append("" if item is None else str(item).strip())
    else:
        for row in arr:
            try:
                first = row[0]
            except Exception:
                first = row
            labels.append("" if first is None else str(first).strip())
    return labels if labels else None


def _attach_point_ids_from_customdata(fig: go.Figure) -> None:
    for trace in fig.data:
        try:
            if getattr(trace, "ids", None) is not None:
                continue
            labels = _extract_point_ids_from_customdata(getattr(trace, "customdata", None))
            if not labels:
                continue
            trace.ids = labels
        except Exception:
            continue


def _scope_df(df: pd.DataFrame, selected_identifier: str) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    if selected_identifier != "All" and "Identifier 1" in df.columns:
        return df[df["Identifier 1"].astype(str) == str(selected_identifier)].copy()
    return df.copy()


def _resolve_species_labels(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=object)
    species = _get_species_series(df)
    if not isinstance(species, pd.Series):
        species = pd.Series(index=df.index, dtype=object)
    species = species.reindex(df.index)
    labels = species.fillna("").astype(str).str.strip()
    labels = labels.where(~labels.str.lower().eq("nan"), "")
    fallback = df.get("Identifier 1", pd.Series(index=df.index, dtype=object)).fillna("").astype(str).str.strip()
    labels = labels.where(labels != "", fallback)
    labels = labels.where(labels != "", "Unknown")
    return labels


def _resolve_identifier1_labels(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=object)
    identifiers = df.get("Identifier 1", pd.Series(index=df.index, dtype=object)).fillna("").astype(str).str.strip()
    identifiers = identifiers.where(~identifiers.str.lower().eq("nan"), "")
    identifiers = identifiers.where(identifiers != "", "Unknown")
    return identifiers


def _summary_trace_label(species: Any, identifier: Any) -> str:
    species_label = str(species).strip() or "Unknown"
    identifier_label = str(identifier).strip() or "Unknown"
    if species_label == identifier_label:
        return species_label
    return f"{species_label} | {identifier_label}"


def _effective_calibrated_column(df: pd.DataFrame, isotope_key: str) -> str | None:
    if df is None or df.empty:
        return None
    candidates = (
        ["d13C_calibrated_linearity_corrected", "d13C_calibrated"]
        if isotope_key == "d13C"
        else ["d18O_calibrated_linearity_corrected", "d18O_calibrated"]
    )
    for col in candidates:
        if col in df.columns and pd.to_numeric(df[col], errors="coerce").notna().any():
            return col
    return None


def _color_series_for_plot(df: pd.DataFrame, color_col: str) -> tuple[pd.Series, tuple[list[Any], list[str]] | None, bool, float, float]:
    color_values, category_ticks = _prepare_color_values(
        df.get(color_col),
        prefer_dates=_prefer_datetime_color_values(color_col),
    )
    if color_values is not None and len(color_values) == len(df):
        numeric_colors = pd.to_numeric(color_values, errors="coerce")
    else:
        numeric_colors = pd.Series(np.nan, index=df.index, dtype=float)
    has_numeric_colors = bool(numeric_colors.notna().any())
    if has_numeric_colors:
        cmin = float(numeric_colors.min())
        cmax = float(numeric_colors.max())
        if not np.isfinite(cmin) or not np.isfinite(cmax):
            cmin, cmax = 0.0, 1.0
        elif cmin == cmax:
            cmax = cmin + 1.0
    else:
        cmin, cmax = 0.0, 1.0
    return numeric_colors, category_ticks, has_numeric_colors, cmin, cmax


def _color_param_label(color_col: str) -> str:
    if _is_date_color_column(color_col):
        return "Date"
    if color_col == CYCLE1_SIGNAL_SAMP44_COL:
        return "Initial sample intensity"
    if color_col == CYCLE1_SIGNAL_REF44_COL:
        return "Initial reference gas intensity"
    return str(color_col)


def _format_hover_color_value(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        if pd.isna(value):
            return "N/A"
    except Exception:  # pragma: no cover - defensive for exotic objects
        pass
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.notna(numeric) and np.isfinite(float(numeric)):
        return f"{float(numeric):.2f}"
    return str(value)


def _attach_hover_context(df: pd.DataFrame, color_col: str) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    work = df.copy()
    if work.empty:
        work["__hover_species"] = pd.Series(index=work.index, dtype=object)
        work["__hover_color_value"] = pd.Series(index=work.index, dtype=object)
        return work
    work["__hover_species"] = _resolve_species_labels(work).reindex(work.index).fillna("Unknown").astype(str)
    source = work.get(color_col, pd.Series(index=work.index, dtype=object))
    work["__hover_color_value"] = source.reindex(work.index).map(_format_hover_color_value)
    return work


def _build_processing_point_customdata(df: pd.DataFrame, isotope_key: str) -> np.ndarray | None:
    if df is None or df.empty:
        return None
    return _build_delta_point_customdata(df, isotope_key)


def _build_standard_point_customdata(df: pd.DataFrame, isotope_key: str) -> np.ndarray | None:
    """Build hover metadata without making standard points editable."""
    customdata = _build_processing_point_customdata(df, isotope_key)
    if customdata is None:
        return None
    customdata = customdata.copy()
    customdata[:, 0] = ""
    return customdata


def _measurement_datetime_series(df: pd.DataFrame) -> pd.Series:
    """Return the best available acquisition timestamp for each measurement."""
    if df is None or df.empty:
        return pd.Series(dtype="datetime64[ns]")

    parsed = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    for date_col in ("Date", "Measurement Date", "Analysis Date"):
        if date_col not in df.columns:
            continue
        candidate = pd.to_datetime(df[date_col], errors="coerce")
        if candidate.notna().any():
            parsed = candidate
            break

    if parsed.notna().any() and "Time" in df.columns:
        time_delta = pd.to_timedelta(df["Time"].astype(str).str.strip(), errors="coerce")
        has_time = time_delta.notna()
        if has_time.any():
            parsed = parsed.copy()
            parsed.loc[has_time] = parsed.loc[has_time].dt.normalize() + time_delta.loc[has_time]

    if parsed.notna().any():
        return parsed

    ordinal_values = pd.to_numeric(
        df.get("Date_ordinal", pd.Series(index=df.index, dtype=float)),
        errors="coerce",
    )
    if not ordinal_values.notna().any():
        return parsed

    def _from_ordinal(value: Any) -> pd.Timestamp | pd.NaT:
        if pd.isna(value):
            return pd.NaT
        try:
            return pd.Timestamp.fromordinal(int(value))
        except (TypeError, ValueError, OverflowError):
            return pd.NaT

    return ordinal_values.map(_from_ordinal)


def _date_aligned_standard_rows(
    standards_df: pd.DataFrame | None,
    reference_df: pd.DataFrame,
    x_axis_option: str,
    color_col: str,
) -> pd.DataFrame:
    """Position standards on a sample x-axis by interpolating acquisition dates."""
    if standards_df is None or standards_df.empty:
        return pd.DataFrame()

    standards = _attach_hover_context(standards_df.copy(), color_col)
    standards["__measurement_datetime"] = _measurement_datetime_series(standards)

    def _native_standard_axis() -> pd.DataFrame:
        native = standards.copy()
        native["x_axis"] = _x_axis_series(native, x_axis_option)
        return native.sort_values(["x_axis", "__measurement_datetime", "Identifier 2"], na_position="last")

    if reference_df is None or reference_df.empty:
        return _native_standard_axis()

    reference = reference_df.copy()
    if "x_axis" not in reference.columns:
        reference["x_axis"] = _x_axis_series(reference, x_axis_option)
    reference["__measurement_datetime"] = _measurement_datetime_series(reference)
    reference["__axis_numeric"] = pd.to_numeric(reference["x_axis"], errors="coerce")
    reference = reference.loc[
        reference["__measurement_datetime"].notna() & reference["__axis_numeric"].notna()
    ].copy()
    if reference.empty:
        return _native_standard_axis()

    anchors = (
        reference.groupby("__measurement_datetime", as_index=False)["__axis_numeric"]
        .mean()
        .sort_values("__measurement_datetime")
    )
    anchor_dates = anchors["__measurement_datetime"].astype("int64").to_numpy(dtype=float)
    anchor_x = anchors["__axis_numeric"].to_numpy(dtype=float)

    standards_with_dates = standards.loc[standards["__measurement_datetime"].notna()].copy()
    if standards_with_dates.empty:
        return _native_standard_axis()
    standards = standards_with_dates
    standard_dates = standards["__measurement_datetime"].astype("int64").to_numpy(dtype=float)
    if len(anchor_dates) == 1:
        standards["x_axis"] = float(anchor_x[0])
    else:
        standards["x_axis"] = np.interp(standard_dates, anchor_dates, anchor_x)
    return standards.sort_values(["__measurement_datetime", "Identifier 2"], na_position="last")


def _add_standard_measurement_traces(
    fig: go.Figure,
    standards_df: pd.DataFrame | None,
    reference_df: pd.DataFrame,
    isotope_key: str,
    y_col: str,
    x_axis_option: str,
    color_col: str,
) -> None:
    aligned = _date_aligned_standard_rows(
        standards_df,
        reference_df,
        x_axis_option=x_axis_option,
        color_col=color_col,
    )
    if aligned.empty:
        return

    palette = ["#0f766e", "#7c3aed", "#b45309", "#be123c", "#0369a1", "#4d7c0f"]
    standard_labels = aligned.get(
        "Identifier 1",
        pd.Series("", index=aligned.index, dtype=object),
    ).fillna("").astype(str)
    traces_added = False
    for color_index, standard in enumerate(sorted(label for label in standard_labels.unique() if label.strip())):
        rows = aligned.loc[standard_labels == standard].copy()
        y_values = pd.to_numeric(rows.get(y_col), errors="coerce")
        rows = rows.loc[y_values.notna()].copy()
        if rows.empty:
            continue
        # Match the normal data traces: connect points from left to right in
        # the plotted sequence, not in acquisition-date order.
        rows = rows.sort_values(["x_axis", "__measurement_datetime", "Identifier 2"], na_position="last")
        color = palette[color_index % len(palette)]
        fig.add_trace(
            go.Scatter(
                x=rows["x_axis"],
                y=pd.to_numeric(rows.get(y_col), errors="coerce"),
                yaxis="y2",
                mode="lines+markers",
                name=f"Standard measured {isotope_key} - {standard}",
                legendgroup=f"standard-measured-{isotope_key}-{standard}",
                line=dict(color=color, width=2, dash="dot"),
                marker=dict(color=color, size=9, symbol="diamond", line=dict(color="white", width=1)),
                customdata=_build_standard_point_customdata(rows, isotope_key),
            )
        )
        traces_added = True

    if traces_added:
        fig.update_layout(
            yaxis2=dict(
                title=f"Standard {isotope_key}",
                overlaying="y",
                # The client copies the resolved primary-axis scale after
                # Plotly has autoranged both independent axes.
                tickmode="auto",
                side="right",
                showgrid=False,
                zeroline=False,
                automargin=True,
            )
        )


def _apply_processing_isotope_hover_templates(fig: go.Figure, isotope_key: str, color_col: str) -> None:
    color_label = _color_param_label(color_col)
    sample_template = (
        "Identifier 1: %{customdata[2]}<br>"
        "Identifier 2: %{customdata[3]}<br>"
        "Species: %{customdata[4]}<br>"
        "Row: %{customdata[0]}<br>"
        f"{color_label}: %{{customdata[5]}}<br>"
        f"{isotope_key}: %{{y:.3f}}<extra></extra>"
    )
    standard_template = (
        "Standard: %{customdata[2]}<br>"
        "Identifier 2: %{customdata[3]}<br>"
        f"{color_label}: %{{customdata[5]}}<br>"
        f"{isotope_key}: %{{y:.3f}}<extra></extra>"
    )
    for trace in fig.data:
        if getattr(trace, "customdata", None) is None:
            continue
        trace.hovertemplate = (
            standard_template
            if str(getattr(trace, "name", "")).startswith("Standard measured ")
            else sample_template
        )
        trace.hoverlabel = dict(namelength=-1)


def _apply_processing_crossplot_hover_templates(fig: go.Figure, color_col: str) -> None:
    color_label = _color_param_label(color_col)
    template = (
        "Identifier 1: %{customdata[2]}<br>"
        "Identifier 2: %{customdata[3]}<br>"
        "Species: %{customdata[4]}<br>"
        "Row: %{customdata[0]}<br>"
        f"{color_label}: %{{customdata[5]}}<br>"
        "d18O: %{x:.3f}<br>"
        "d13C: %{y:.3f}<extra></extra>"
    )
    for trace in fig.data:
        if getattr(trace, "customdata", None) is None:
            continue
        trace.hovertemplate = template
        trace.hoverlabel = dict(namelength=-1)


def _restored_row_labels(edit_state: dict[str, Any] | None, isotope_key: str | None = None) -> set[str]:
    raw_tokens = (edit_state or {}).get("restored_delta_tokens", [])
    raw_missing_tokens = (edit_state or {}).get("original_missing_delta_tokens", [])
    missing_by_isotope: dict[str, set[str]] = {"d13C": set(), "d18O": set()}
    for token in raw_missing_tokens if isinstance(raw_missing_tokens, list) else []:
        token_str = str(token).strip()
        if not token_str or "|" not in token_str:
            continue
        iso, row_label = token_str.split("|", 1)
        row_key = row_label.strip()
        if iso in missing_by_isotope and row_key:
            missing_by_isotope[iso].add(row_key)
    restored: set[str] = set()
    for token in raw_tokens if isinstance(raw_tokens, list) else []:
        token_str = str(token).strip()
        if not token_str:
            continue
        if "|" not in token_str:
            continue
        iso, row_label = token_str.split("|", 1)
        row_key = row_label.strip()
        if not row_key:
            continue
        if iso not in missing_by_isotope:
            continue
        # Only treat as restored when the isotope was originally missing.
        if row_key not in missing_by_isotope[iso]:
            continue
        if isotope_key is None or iso == isotope_key:
            restored.add(row_key)
    return restored


def _restored_crossplot_rows(edit_state: dict[str, Any] | None) -> set[str]:
    # Cross/3D restored markers should only show points restored for both isotopes.
    restored_d13 = _restored_row_labels(edit_state, isotope_key="d13C")
    restored_d18 = _restored_row_labels(edit_state, isotope_key="d18O")
    return restored_d13 & restored_d18


def _edited_row_labels(edit_state: dict[str, Any] | None) -> set[str]:
    return {str(row) for row in (edit_state or {}).get("edited_rows", [])}


def _restored_index_mask(index: pd.Index, restored_rows: set[str]) -> pd.Series:
    if len(index) == 0:
        return pd.Series(dtype=bool)
    if not restored_rows:
        return pd.Series(False, index=index, dtype=bool)
    return pd.Series(index.astype(str)).isin(restored_rows).set_axis(index)


def _build_summary_figure(
    df: pd.DataFrame,
    isotope_key: str,
    x_axis_option: str,
    color_col: str,
    show_calibrated: bool = True,
    standards_df: pd.DataFrame | None = None,
    overlay_df: pd.DataFrame | None = None,
    summary_masks: dict[str, pd.Series] | None = None,
    sat_masks: dict[str, pd.Series] | None = None,
    statistical_mask: pd.Series | None = None,
    config: Any | None = None,
    edit_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    has_samples = df is not None and not df.empty
    has_standards = standards_df is not None and not standards_df.empty
    if go is None or (not has_samples and not has_standards):
        return {}
    if df is None:
        df = pd.DataFrame()
    y_col = "d 13C/12C  Mean" if isotope_key == "d13C" else "d 18O/16O  Mean"
    cal_col = _effective_calibrated_column(df, isotope_key)
    fig = go.Figure()
    work = _attach_hover_context(df, color_col)
    work["_species_label"] = _resolve_species_labels(work)
    work["_identifier1_label"] = _resolve_identifier1_labels(work)
    if x_axis_option == "By Identifier 2":
        work["x_axis"] = work.get("Identifier 2", pd.Series(index=work.index)).apply(_parse_numeric_token)
    else:
        work["x_axis"] = range(len(work))
    color_series, _, has_numeric_colors, color_min, color_max = _color_series_for_plot(work, color_col)
    work["_color_value"] = color_series
    calibrated_legend_shown = False
    for (species, identifier), species_df in work.groupby(["_species_label", "_identifier1_label"], dropna=False):
        trace_label = _summary_trace_label(species, identifier)
        plot_df = species_df.sort_values("x_axis", na_position="last")
        marker: dict[str, Any] = dict(
            size=8,
            color=plot_df["_color_value"] if has_numeric_colors else "#2563eb",
            colorscale="Viridis",
            showscale=False,
        )
        if has_numeric_colors:
            marker.update(cmin=color_min, cmax=color_max)
        fig.add_trace(
            go.Scatter(
                x=plot_df["x_axis"],
                y=pd.to_numeric(plot_df.get(y_col), errors="coerce"),
                mode="lines+markers",
                name=f"Raw {isotope_key} - {trace_label}",
                yhoverformat=".3f",
                marker=marker,
                customdata=_build_processing_point_customdata(plot_df, isotope_key),
            )
        )
        if show_calibrated and cal_col and cal_col in plot_df.columns and pd.to_numeric(plot_df[cal_col], errors="coerce").notna().any():
            show_calibrated_legend = not calibrated_legend_shown
            fig.add_trace(
                go.Scatter(
                    x=plot_df["x_axis"],
                    y=pd.to_numeric(plot_df[cal_col], errors="coerce"),
                    mode="lines",
                    line=dict(color="#f97316", width=2),
                    name="Calibrated" if show_calibrated_legend else f"Calibrated {isotope_key} - {trace_label}",
                    showlegend=show_calibrated_legend,
                    legendgroup=f"{isotope_key.lower()}_calibrated",
                    customdata=_build_processing_point_customdata(plot_df, isotope_key),
                )
            )
            calibrated_legend_shown = True
    _add_standard_measurement_traces(
        fig,
        standards_df,
        work,
        isotope_key=isotope_key,
        y_col=y_col,
        x_axis_option=x_axis_option,
        color_col=color_col,
    )
    _apply_processing_isotope_hover_templates(fig, isotope_key, color_col)
    if (
        config is not None
        and overlay_df is not None
        and summary_masks is not None
        and sat_masks is not None
        and statistical_mask is not None
    ):
        _add_processing_summary_overlays(
            fig=fig,
            filtered_df=work,
            overlay_df=overlay_df,
            isotope_key=isotope_key,
            y_col=y_col,
            x_axis_option=x_axis_option,
            summary_masks=summary_masks,
            sat_masks=sat_masks,
            statistical_mask=statistical_mask,
            config=config,
            edit_state=edit_state,
        )
        _apply_processing_isotope_hover_templates(fig, isotope_key, color_col)
    fig.update_layout(
        title=dict(
            text=f"{isotope_key} Summary",
            x=0.0,
            xanchor="left",
            y=0.99,
            yanchor="top",
        ),
        xaxis_title=x_axis_option,
        yaxis_title=isotope_key,
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.08, x=0.0, xanchor="left"),
        margin=dict(l=40, r=76, t=120, b=40),
    )
    return _figure_json(fig)


def _add_processing_summary_overlays(
    fig: go.Figure,
    filtered_df: pd.DataFrame,
    overlay_df: pd.DataFrame,
    isotope_key: str,
    y_col: str,
    x_axis_option: str,
    summary_masks: dict[str, pd.Series],
    sat_masks: dict[str, pd.Series],
    statistical_mask: pd.Series,
    config: Any,
    edit_state: dict[str, Any] | None = None,
) -> None:
    if fig is None or overlay_df is None or overlay_df.empty:
        return

    overlays = overlay_df.copy()
    if x_axis_option == "By Identifier 2":
        overlays["x_axis"] = overlays.get("Identifier 2", pd.Series(index=overlays.index)).apply(_parse_numeric_token)
    else:
        overlays["x_axis"] = range(len(overlays))

    restored_rows_for_isotope = _restored_row_labels(edit_state, isotope_key=isotope_key)

    def _exclude_restored(rows: pd.DataFrame, include_restored: bool = False) -> pd.DataFrame:
        if include_restored or rows.empty or not restored_rows_for_isotope:
            return rows
        return rows.loc[[str(idx) not in restored_rows_for_isotope for idx in rows.index]]

    def _rows(mask: pd.Series | None, include_restored: bool = False) -> pd.DataFrame:
        if mask is None:
            return pd.DataFrame(columns=overlays.columns)
        rows = overlays.loc[mask.reindex(overlays.index, fill_value=False).astype(bool)].copy()
        return _exclude_restored(rows, include_restored=include_restored)

    def _finite_value_rows(rows: pd.DataFrame) -> pd.DataFrame:
        if rows.empty:
            return rows
        y_values = pd.to_numeric(rows.get(y_col), errors="coerce")
        return rows.loc[y_values.notna()].copy()

    baseline_values = pd.to_numeric(filtered_df.get(y_col), errors="coerce") if y_col in filtered_df.columns else pd.Series(dtype=float)
    y_min = float(baseline_values.min()) if baseline_values.notna().any() else -1.0
    y_max = float(baseline_values.max()) if baseline_values.notna().any() else 1.0
    y_span = y_max - y_min if np.isfinite(y_max - y_min) else 1.0
    y_failed = y_min - (0.1 * y_span if y_span > 0 else 0.5)

    if getattr(config.overlays, "show_statistical_outliers", False):
        statistical_rows = _finite_value_rows(_rows(statistical_mask))
        if not statistical_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=statistical_rows["x_axis"],
                    y=pd.to_numeric(statistical_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    name="Statistical Outliers",
                    marker=dict(color="red", symbol="square", size=12, line=dict(width=1.5, color="black")),
                    customdata=_build_processing_point_customdata(statistical_rows, isotope_key),
                )
            )

    if getattr(config.overlays, "show_range_outliers", False):
        range_masks = _exclusive_outlier_masks(
            [
                ("signal", summary_masks.get("Signal Intensity", pd.Series(False, index=overlays.index))),
                ("leak", summary_masks.get("Leak Rate", pd.Series(False, index=overlays.index))),
                ("d13c", summary_masks.get("d13C Range", pd.Series(False, index=overlays.index))),
                ("d18o", summary_masks.get("d18O Range", pd.Series(False, index=overlays.index))),
            ]
        )
        symbol_map = {"signal": "diamond", "leak": "star", "d13c": "cross", "d18o": "x"}
        label_map = {
            "signal": "Signal Intensity Range",
            "leak": "Leak Rate Range",
            "d13c": "d13C Range",
            "d18o": "d18O Range",
        }
        for key, mask in range_masks.items():
            rows = _finite_value_rows(_rows(mask))
            if rows.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=rows["x_axis"],
                    y=pd.to_numeric(rows.get(y_col), errors="coerce"),
                    mode="markers",
                    name=label_map[key],
                    marker=dict(color="red", symbol=symbol_map[key], size=13, opacity=1.0, line=dict(width=1.5, color="black")),
                    customdata=_build_processing_point_customdata(rows, isotope_key),
                )
            )

    if getattr(config.overlays, "show_manual_outliers", False):
        manual_rows = _finite_value_rows(_rows(summary_masks.get("Manual Override")))
        if not manual_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=manual_rows["x_axis"],
                    y=pd.to_numeric(manual_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    name="Manual Outliers",
                    marker=dict(color="#ec4899", symbol="circle-open", size=14, opacity=1.0, line=dict(width=2, color="black")),
                    customdata=_build_processing_point_customdata(manual_rows, isotope_key),
                )
            )

    if getattr(config.overlays, "show_saturated_collectors", True):
        partial_rows = _finite_value_rows(_rows(sat_masks.get(isotope_key)))
        if not partial_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=partial_rows["x_axis"],
                    y=pd.to_numeric(partial_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    name="Partially Failed (Recovered Mean)",
                    marker=dict(color="#ff7f0e", symbol="diamond-open", size=13, opacity=1.0, line=dict(width=2, color="#ff7f0e")),
                    customdata=_build_processing_point_customdata(partial_rows, isotope_key),
                )
            )

    if getattr(config.overlays, "show_saturated_samples", True):
        full_rows = _rows(summary_masks.get("Fully Saturated Collectors"))
        if not full_rows.empty:
            full_values = _finite_value_rows(full_rows)
            if not full_values.empty:
                fig.add_trace(
                    go.Scatter(
                        x=full_values["x_axis"],
                        y=pd.to_numeric(full_values.get(y_col), errors="coerce"),
                        mode="markers",
                        name="Failed Samples (Fully Saturated)",
                        marker=dict(color="#d62728", symbol="triangle-down", size=11, opacity=1.0, line=dict(width=1, color="black")),
                        customdata=_build_processing_point_customdata(full_values, isotope_key),
                    )
                )
            full_missing = full_rows.loc[~full_rows.index.isin(full_values.index)] if not full_values.empty else full_rows
            if not full_missing.empty:
                fig.add_trace(
                    go.Scatter(
                        x=full_missing["x_axis"],
                        y=[y_failed] * len(full_missing),
                        mode="markers",
                        name="Failed Samples (Fully Saturated)",
                        marker=dict(color="#d62728", symbol="triangle-down", size=11, opacity=1.0, line=dict(width=1, color="black")),
                        customdata=_build_processing_point_customdata(full_missing, isotope_key),
                        showlegend=full_values.empty,
                    )
                )

    if getattr(config.overlays, "show_failed_samples", True):
        failed_rows = _rows(summary_masks.get("Failed Sample"))
        if not failed_rows.empty:
            failed_values = pd.to_numeric(failed_rows.get(y_col), errors="coerce")
            failed_interp = failed_rows.loc[failed_values.notna()]
            failed_missing = failed_rows.loc[failed_values.isna()]
            if not failed_interp.empty:
                fig.add_trace(
                    go.Scatter(
                        x=failed_interp["x_axis"],
                        y=pd.to_numeric(failed_interp.get(y_col), errors="coerce"),
                        mode="markers",
                        name="Failed Samples (Interpolated)",
                        marker=dict(color="#ff00ff", symbol="triangle-down", size=11, opacity=1.0, line=dict(width=1)),
                        customdata=_build_processing_point_customdata(failed_interp, isotope_key),
                    )
                )
            if not failed_missing.empty:
                fig.add_trace(
                    go.Scatter(
                        x=failed_missing["x_axis"],
                        y=[y_failed] * len(failed_missing),
                        mode="markers",
                        name="Failed Samples (No Values)",
                        marker=dict(color="#7f7f7f", symbol="triangle-down", size=11, opacity=1.0, line=dict(width=1)),
                        customdata=_build_processing_point_customdata(failed_missing, isotope_key),
                    )
                )

    visible_restored_rows = {str(idx) for idx in filtered_df.index}
    restored_mask = pd.Series(
        [
            (str(idx) in restored_rows_for_isotope) and (str(idx) in visible_restored_rows)
            for idx in overlays.index
        ],
        index=overlays.index,
        dtype=bool,
    )
    restored_rows = _finite_value_rows(_rows(restored_mask, include_restored=True))
    if not restored_rows.empty:
        fig.add_trace(
            go.Scatter(
                x=restored_rows["x_axis"],
                y=pd.to_numeric(restored_rows.get(y_col), errors="coerce"),
                mode="markers",
                name="Restored Samples",
                marker=dict(color="#22c55e", symbol="star", size=14, opacity=1.0, line=dict(width=1.5, color="#166534")),
                customdata=_build_processing_point_customdata(restored_rows, isotope_key),
            )
        )

    edited_rows_for_isotope = _edited_row_labels(edit_state) - restored_rows_for_isotope
    if edited_rows_for_isotope:
        edited_mask = pd.Series(
            [str(idx) in edited_rows_for_isotope for idx in overlays.index],
            index=overlays.index,
            dtype=bool,
        )
        edited_rows = _finite_value_rows(_rows(edited_mask, include_restored=True))
        if not edited_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=edited_rows["x_axis"],
                    y=pd.to_numeric(edited_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    name="Edited Samples",
                    marker=dict(color="#ff00ff", symbol="circle", size=13, opacity=1.0, line=dict(width=1.5, color="#ff00ff")),
                    customdata=_build_processing_point_customdata(edited_rows, isotope_key),
                )
            )


def _build_overview_outlier_context(
    unfiltered_df: pd.DataFrame,
    config: Any,
    edit_state: dict[str, Any] | None,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    if unfiltered_df is None or unfiltered_df.empty:
        empty = pd.Series(dtype=bool)
        return {}, {"d13C": empty, "d18O": empty, "any": empty}
    summary_masks = build_category_masks(
        unfiltered_df,
        RangeConfig(
            signal_range=config.signal_range,
            leak_range=config.leak_range,
            d13c_range=config.d13c_range,
            d18o_range=config.d18o_range,
            partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
        ),
        edit_state=edit_state,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    sat_masks = _partial_saturation_isotope_masks(unfiltered_df)
    return summary_masks, sat_masks


def _numeric_axis_rows(df: pd.DataFrame | None, cols: list[str]) -> pd.DataFrame:
    if df is None or df.empty or any(col not in df.columns for col in cols):
        return pd.DataFrame(columns=cols)
    rows = df[cols].copy()
    for col in cols:
        rows[col] = pd.to_numeric(rows[col], errors="coerce")
    return rows.dropna(subset=cols)


def _overlay_rows_for_mask(overlay_df: pd.DataFrame, mask: pd.Series | None, cols: list[str]) -> pd.DataFrame:
    if overlay_df is None or overlay_df.empty or mask is None:
        return pd.DataFrame(columns=cols)
    keep = mask.reindex(overlay_df.index, fill_value=False).astype(bool)
    if not keep.any():
        return pd.DataFrame(columns=cols)
    return _numeric_axis_rows(overlay_df.loc[keep], cols)


def _visible_overlay_axis_rows(
    overlay_df: pd.DataFrame,
    summary_masks: dict[str, pd.Series],
    sat_masks: dict[str, pd.Series],
    config: Any,
    cols: list[str],
) -> pd.DataFrame:
    if overlay_df is None or overlay_df.empty:
        return pd.DataFrame(columns=cols)
    frames: list[pd.DataFrame] = []

    if getattr(config.overlays, "show_statistical_outliers", False):
        frames.append(_overlay_rows_for_mask(overlay_df, summary_masks.get("Statistical"), cols))

    if getattr(config.overlays, "show_range_outliers", False):
        range_masks = _exclusive_outlier_masks(
            [
                ("signal", summary_masks.get("Signal Intensity", pd.Series(False, index=overlay_df.index))),
                ("leak", summary_masks.get("Leak Rate", pd.Series(False, index=overlay_df.index))),
                ("d13c", summary_masks.get("d13C Range", pd.Series(False, index=overlay_df.index))),
                ("d18o", summary_masks.get("d18O Range", pd.Series(False, index=overlay_df.index))),
            ]
        )
        for mask in range_masks.values():
            frames.append(_overlay_rows_for_mask(overlay_df, mask, cols))

    if getattr(config.overlays, "show_manual_outliers", False):
        frames.append(_overlay_rows_for_mask(overlay_df, summary_masks.get("Manual Override"), cols))

    if getattr(config.overlays, "show_saturated_collectors", True):
        frames.append(_overlay_rows_for_mask(overlay_df, sat_masks.get("any"), cols))

    if getattr(config.overlays, "show_saturated_samples", True):
        frames.append(_overlay_rows_for_mask(overlay_df, summary_masks.get("Fully Saturated Collectors"), cols))

    visible_frames = [frame for frame in frames if not frame.empty]
    if not visible_frames:
        return pd.DataFrame(columns=cols)
    return pd.concat(visible_frames, axis=0, ignore_index=True)


def _axis_range(values: pd.Series, pad_fraction: float = 0.05, default_pad: float = 0.5) -> list[float] | None:
    finite = pd.to_numeric(values, errors="coerce").dropna()
    if finite.empty:
        return None
    min_val = float(finite.min())
    max_val = float(finite.max())
    span = max_val - min_val
    pad = pad_fraction * span if np.isfinite(span) and span > 0 else float(default_pad)
    return [min_val - pad, max_val + pad]


def _add_processing_crossplot_overlays(
    fig: go.Figure,
    overlay_df: pd.DataFrame,
    summary_masks: dict[str, pd.Series],
    sat_masks: dict[str, pd.Series],
    config: Any,
    restored_visible_index: pd.Index | None = None,
    edit_state: dict[str, Any] | None = None,
) -> None:
    if fig is None or overlay_df is None or overlay_df.empty:
        return

    x_vals = pd.to_numeric(overlay_df.get("d 18O/16O  Mean"), errors="coerce")
    y_vals = pd.to_numeric(overlay_df.get("d 13C/12C  Mean"), errors="coerce")
    valid_xy = x_vals.notna() & y_vals.notna()
    if not valid_xy.any():
        return

    restored_rows_all = _restored_crossplot_rows(edit_state)
    visible_restored_rows = {str(idx) for idx in restored_visible_index} if restored_visible_index is not None else None
    edited_rows_all = _edited_row_labels(edit_state) - restored_rows_all

    def _rows(mask: pd.Series | None, include_restored: bool = False) -> pd.DataFrame:
        if mask is None:
            return pd.DataFrame(columns=overlay_df.columns)
        m = mask.reindex(overlay_df.index, fill_value=False).astype(bool) & valid_xy
        if not include_restored and restored_rows_all:
            m = m & ~pd.Series([str(idx) in restored_rows_all for idx in overlay_df.index], index=overlay_df.index, dtype=bool)
        return overlay_df.loc[m].copy()

    if getattr(config.overlays, "show_statistical_outliers", False):
        statistical_rows = _rows(summary_masks.get("Statistical"))
        if not statistical_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(statistical_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(statistical_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name="Statistical Outliers",
                    marker=dict(size=12, symbol="square", color="red", line=dict(width=1.5, color="black")),
                    customdata=_build_processing_point_customdata(statistical_rows, "cross"),
                )
            )

    if getattr(config.overlays, "show_range_outliers", False):
        range_masks = _exclusive_outlier_masks(
            [
                ("signal", summary_masks.get("Signal Intensity", pd.Series(False, index=overlay_df.index))),
                ("leak", summary_masks.get("Leak Rate", pd.Series(False, index=overlay_df.index))),
                ("d13c", summary_masks.get("d13C Range", pd.Series(False, index=overlay_df.index))),
                ("d18o", summary_masks.get("d18O Range", pd.Series(False, index=overlay_df.index))),
            ]
        )
        symbol_map = {"signal": "diamond", "leak": "x", "d13c": "cross", "d18o": "square-open"}
        label_map = {
            "signal": "Signal Intensity Range",
            "leak": "Leak Rate Range",
            "d13c": "d13C Range",
            "d18o": "d18O Range",
        }
        for key, mask in range_masks.items():
            rows = _rows(mask)
            if rows.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name=label_map[key],
                    marker=dict(size=12, symbol=symbol_map[key], color="red", line=dict(width=1.5, color="black")),
                    customdata=_build_processing_point_customdata(rows, "cross"),
                )
            )

    if getattr(config.overlays, "show_manual_outliers", False):
        manual_rows = _rows(summary_masks.get("Manual Override"))
        if not manual_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(manual_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(manual_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name="Manual Outliers",
                    marker=dict(size=13, symbol="circle-open", color="#ec4899", line=dict(width=2, color="black")),
                    customdata=_build_processing_point_customdata(manual_rows, "cross"),
                )
            )

    if getattr(config.overlays, "show_saturated_collectors", True):
        partial_rows = _rows(sat_masks.get("any"))
        if not partial_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(partial_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(partial_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name="Partially Failed (Recovered Mean)",
                    marker=dict(size=15, symbol="diamond-open", color="#ff7f0e", line=dict(width=2, color="#ff7f0e")),
                    customdata=_build_processing_point_customdata(partial_rows, "cross"),
                )
            )

    if getattr(config.overlays, "show_saturated_samples", True):
        full_rows = _rows(summary_masks.get("Fully Saturated Collectors"))
        if not full_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(full_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(full_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name="Failed Samples (Fully Saturated)",
                    marker=dict(size=10, symbol="triangle-down", color="#d62728", line=dict(width=1, color="black")),
                    customdata=_build_processing_point_customdata(full_rows, "cross"),
                )
            )

    restored_rows = _rows(
        pd.Series(
            [
                (str(idx) in restored_rows_all)
                and (visible_restored_rows is None or str(idx) in visible_restored_rows)
                for idx in overlay_df.index
            ],
            index=overlay_df.index,
            dtype=bool,
        ),
        include_restored=True,
    )
    if not restored_rows.empty:
        fig.add_trace(
            go.Scatter(
                x=pd.to_numeric(restored_rows.get("d 18O/16O  Mean"), errors="coerce"),
                y=pd.to_numeric(restored_rows.get("d 13C/12C  Mean"), errors="coerce"),
                mode="markers",
                name="Restored Samples",
                marker=dict(size=13, symbol="star", color="#22c55e", line=dict(width=1.5, color="#166534")),
                customdata=_build_processing_point_customdata(restored_rows, "cross"),
            )
        )

    if edited_rows_all:
        edited_rows = _rows(
            pd.Series([str(idx) in edited_rows_all for idx in overlay_df.index], index=overlay_df.index, dtype=bool),
            include_restored=True,
        )
        if not edited_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(edited_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(edited_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name="Edited Samples",
                    marker=dict(size=14, symbol="circle", color="#ff00ff", line=dict(width=1.5, color="#ff00ff")),
                    customdata=_build_processing_point_customdata(edited_rows, "cross"),
                )
            )


def _add_processing_3d_overlays(
    fig: go.Figure,
    overlay_df: pd.DataFrame,
    summary_masks: dict[str, pd.Series],
    sat_masks: dict[str, pd.Series],
    z_col: str,
    z_label: str,
    config: Any,
    restored_visible_index: pd.Index | None = None,
    edit_state: dict[str, Any] | None = None,
) -> None:
    if fig is None or overlay_df is None or overlay_df.empty:
        return

    x_vals = pd.to_numeric(overlay_df.get("d 18O/16O  Mean"), errors="coerce")
    y_vals = pd.to_numeric(overlay_df.get("d 13C/12C  Mean"), errors="coerce")
    z_vals = pd.to_numeric(overlay_df.get(z_col), errors="coerce")
    valid_xyz = x_vals.notna() & y_vals.notna() & z_vals.notna()
    if not valid_xyz.any():
        return

    restored_rows_all = _restored_crossplot_rows(edit_state)
    visible_restored_rows = {str(idx) for idx in restored_visible_index} if restored_visible_index is not None else None
    edited_rows_all = _edited_row_labels(edit_state) - restored_rows_all

    def _rows(mask: pd.Series | None, include_restored: bool = False) -> pd.DataFrame:
        if mask is None:
            return pd.DataFrame(columns=overlay_df.columns)
        m = mask.reindex(overlay_df.index, fill_value=False).astype(bool) & valid_xyz
        if not include_restored and restored_rows_all:
            m = m & ~pd.Series([str(idx) in restored_rows_all for idx in overlay_df.index], index=overlay_df.index, dtype=bool)
        return overlay_df.loc[m].copy()

    color_label = _color_param_label(config.color_param)
    hover_template = (
        "Identifier 1: %{customdata[2]}<br>"
        "Identifier 2: %{customdata[3]}<br>"
        "Species: %{customdata[4]}<br>"
        "Row: %{customdata[0]}<br>"
        f"{color_label}: %{{customdata[5]}}<br>"
        "d18O: %{x:.3f}<br>"
        "d13C: %{y:.3f}<br>"
        f"{z_label}: %{{z:.3f}}<extra></extra>"
    )

    if getattr(config.overlays, "show_statistical_outliers", False):
        statistical_rows = _rows(summary_masks.get("Statistical"))
        if not statistical_rows.empty:
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(statistical_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(statistical_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(statistical_rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name="Statistical Outliers",
                    marker=dict(size=8, symbol="square", color="red", line=dict(width=1.5, color="black"), opacity=1.0),
                    customdata=_build_processing_point_customdata(statistical_rows, "cross"),
                    hovertemplate=hover_template,
                )
            )

    if getattr(config.overlays, "show_range_outliers", False):
        range_masks = _exclusive_outlier_masks(
            [
                ("signal", summary_masks.get("Signal Intensity", pd.Series(False, index=overlay_df.index))),
                ("leak", summary_masks.get("Leak Rate", pd.Series(False, index=overlay_df.index))),
                ("d13c", summary_masks.get("d13C Range", pd.Series(False, index=overlay_df.index))),
                ("d18o", summary_masks.get("d18O Range", pd.Series(False, index=overlay_df.index))),
            ]
        )
        symbol_map = {"signal": "diamond", "leak": "x", "d13c": "cross", "d18o": "square-open"}
        label_map = {
            "signal": "Signal Intensity Range",
            "leak": "Leak Rate Range",
            "d13c": "d13C Range",
            "d18o": "d18O Range",
        }
        for key, mask in range_masks.items():
            rows = _rows(mask)
            if rows.empty:
                continue
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name=label_map[key],
                    marker=dict(size=8, symbol=symbol_map[key], color="red", line=dict(width=1.5, color="black"), opacity=1.0),
                    customdata=_build_processing_point_customdata(rows, "cross"),
                    hovertemplate=hover_template,
                )
            )

    if getattr(config.overlays, "show_manual_outliers", False):
        manual_rows = _rows(summary_masks.get("Manual Override"))
        if not manual_rows.empty:
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(manual_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(manual_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(manual_rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name="Manual Outliers",
                    marker=dict(size=9, symbol="circle-open", color="#ec4899", line=dict(width=2, color="black"), opacity=1.0),
                    customdata=_build_processing_point_customdata(manual_rows, "cross"),
                    hovertemplate=hover_template,
                )
            )

    if getattr(config.overlays, "show_saturated_collectors", True):
        partial_rows = _rows(sat_masks.get("any"))
        if not partial_rows.empty:
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(partial_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(partial_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(partial_rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name="Partially Failed (Recovered Mean)",
                    marker=dict(size=9, symbol="diamond-open", color="#ff7f0e", line=dict(width=2, color="#ff7f0e"), opacity=1.0),
                    customdata=_build_processing_point_customdata(partial_rows, "cross"),
                    hovertemplate=hover_template,
                )
            )

    if getattr(config.overlays, "show_saturated_samples", True):
        full_rows = _rows(summary_masks.get("Fully Saturated Collectors"))
        if not full_rows.empty:
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(full_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(full_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(full_rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name="Failed Samples (Fully Saturated)",
                    marker=dict(size=9, symbol="square-open", color="#d62728", line=dict(width=2, color="#d62728"), opacity=1.0),
                    customdata=_build_processing_point_customdata(full_rows, "cross"),
                    hovertemplate=hover_template,
                )
            )

    restored_rows = _rows(
        pd.Series(
            [
                (str(idx) in restored_rows_all)
                and (visible_restored_rows is None or str(idx) in visible_restored_rows)
                for idx in overlay_df.index
            ],
            index=overlay_df.index,
            dtype=bool,
        ),
        include_restored=True,
    )
    if not restored_rows.empty:
        fig.add_trace(
            go.Scatter3d(
                x=pd.to_numeric(restored_rows.get("d 18O/16O  Mean"), errors="coerce"),
                y=pd.to_numeric(restored_rows.get("d 13C/12C  Mean"), errors="coerce"),
                z=pd.to_numeric(restored_rows.get(z_col), errors="coerce"),
                mode="text",
                name="Restored Samples",
                text=["*"] * len(restored_rows),
                textposition="middle center",
                textfont=dict(size=18, color="#22c55e"),
                customdata=_build_processing_point_customdata(restored_rows, "cross"),
                hovertemplate=hover_template,
            )
        )

    if edited_rows_all:
        edited_rows = _rows(
            pd.Series([str(idx) in edited_rows_all for idx in overlay_df.index], index=overlay_df.index, dtype=bool),
            include_restored=True,
        )
        if not edited_rows.empty:
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(edited_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(edited_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(edited_rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name="Edited Samples",
                    marker=dict(size=9, symbol="circle", color="#ff00ff", line=dict(width=2, color="#ff00ff"), opacity=1.0),
                    customdata=_build_processing_point_customdata(edited_rows, "cross"),
                    hovertemplate=hover_template,
                )
            )


def _apply_processing_3d_layout_tuning(fig: go.Figure) -> None:
    """Tune processing 3D figure spacing so the chart fills the card and colorbar stays right-aligned."""
    if fig is None:
        return

    colorbar_updated = False
    for trace in fig.data:
        marker = getattr(trace, "marker", None)
        if marker is None:
            continue
        colorbar = getattr(marker, "colorbar", None)
        if colorbar is None:
            continue
        # Push the colorbar into the right margin so the 3D scene can use the full chart domain.
        colorbar.x = 1.01
        colorbar.xanchor = "left"
        colorbar.y = 0.56
        colorbar.yanchor = "middle"
        colorbar.len = 0.82
        colorbar_updated = True
        break

    layout_updates: dict[str, Any] = {
        "autosize": True,
        "height": None,
        "scene": {"domain": {"x": [0.0, 1.0], "y": [0.14, 1.0]}, "aspectmode": "cube"},
        "title": {"x": 0.0, "xanchor": "left", "y": 0.99, "yanchor": "top"},
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 0.0,
            "x": 0.0,
            "xanchor": "left",
            "entrywidthmode": "pixels",
            "entrywidth": 140,
            "itemsizing": "constant",
            "tracegroupgap": 6,
        },
        "margin": {"l": 12, "r": 92 if colorbar_updated else 28, "t": 64, "b": 24},
    }
    fig.update_layout(**layout_updates)


def build_overview_figures(
    filtered_df: pd.DataFrame,
    scoped_df: pd.DataFrame,
    unfiltered_scoped_df: pd.DataFrame,
    config: Any,
    edit_state: dict[str, Any] | None = None,
    standards_df: pd.DataFrame | None = None,
) -> dict[str, dict[str, Any]]:
    figures: dict[str, dict[str, Any]] = {}
    overlays_df = _attach_hover_context(unfiltered_scoped_df.copy(), config.color_param) if unfiltered_scoped_df is not None else pd.DataFrame()
    summary_masks, sat_masks = _build_overview_outlier_context(overlays_df, config, edit_state)
    stat_mask_d13, stat_mask_d18, stat_mask_combined = compute_statistical_outlier_masks(
        overlays_df,
        sigma_level=float(config.sigma_level_data),
        edit_state=edit_state,
        species_series=_get_species_series(overlays_df),
        method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    filtered_base_df = _attach_hover_context(filtered_df.copy(), config.color_param) if filtered_df is not None else pd.DataFrame()
    if not filtered_base_df.empty:
        filtered_base_df = filtered_base_df.loc[
            ~stat_mask_combined.reindex(filtered_base_df.index, fill_value=False).astype(bool)
        ].copy()
    fig_3d, _ = _build_isotope_3d_scatter(
        filtered_base_df,
        z_col=config.z_axis,
        z_label=config.z_axis,
        color_col=config.color_param,
        color_label=config.color_param,
        title="Processing 3D Chart",
        include_row_metadata=True,
        isotope_key="cross",
    )
    if fig_3d is not None:
        _add_processing_3d_overlays(
            fig_3d,
            overlays_df,
            summary_masks,
            sat_masks,
            z_col=config.z_axis,
            z_label=config.z_axis,
            config=config,
            restored_visible_index=filtered_base_df.index,
            edit_state=edit_state,
        )
        axis_cols = ["d 18O/16O  Mean", "d 13C/12C  Mean", str(config.z_axis)]
        axis_df = pd.concat(
            [
                _numeric_axis_rows(filtered_base_df, axis_cols),
                _visible_overlay_axis_rows(overlays_df, summary_masks, sat_masks, config, axis_cols),
            ],
            axis=0,
            ignore_index=True,
        )
        if not axis_df.empty:
            x_range = _axis_range(axis_df["d 18O/16O  Mean"])
            y_range = _axis_range(axis_df["d 13C/12C  Mean"])
            z_range = _axis_range(axis_df[str(config.z_axis)])
            scene_update: dict[str, Any] = {}
            if x_range is not None:
                scene_update["xaxis"] = {"range": x_range}
            if y_range is not None:
                scene_update["yaxis"] = {"range": y_range}
            if z_range is not None:
                scene_update["zaxis"] = {"range": z_range}
            if scene_update:
                fig_3d.update_layout(scene=scene_update)
        _apply_processing_3d_layout_tuning(fig_3d)
    figures["processing_3d"] = _figure_json(fig_3d)
    scoped_base_df = scoped_df.copy() if scoped_df is not None else pd.DataFrame()
    scoped_d13_mask = stat_mask_d13.reindex(scoped_base_df.index, fill_value=False).astype(bool)
    scoped_d18_mask = stat_mask_d18.reindex(scoped_base_df.index, fill_value=False).astype(bool)
    scoped_d13 = scoped_base_df.loc[~scoped_d13_mask].copy()
    scoped_d18 = scoped_base_df.loc[~scoped_d18_mask].copy()
    figures["d13_summary"] = _build_summary_figure(
        scoped_d13,
        "d13C",
        config.x_axis_option,
        config.color_param,
        standards_df=standards_df,
        overlay_df=overlays_df,
        summary_masks=summary_masks,
        sat_masks=sat_masks,
        statistical_mask=stat_mask_d13,
        config=config,
        edit_state=edit_state,
    )
    figures["d18_summary"] = _build_summary_figure(
        scoped_d18,
        "d18O",
        config.x_axis_option,
        config.color_param,
        standards_df=standards_df,
        overlay_df=overlays_df,
        summary_masks=summary_masks,
        sat_masks=sat_masks,
        statistical_mask=stat_mask_d18,
        config=config,
        edit_state=edit_state,
    )

    if go is None or (scoped_df is None or scoped_df.empty) and (overlays_df is None or overlays_df.empty):
        figures["crossplot"] = {}
        return figures
    cross_df = _attach_hover_context(scoped_df.copy(), config.color_param) if scoped_df is not None else pd.DataFrame()
    if not cross_df.empty:
        cross_df = cross_df.loc[~stat_mask_combined.reindex(cross_df.index, fill_value=False).astype(bool)].copy()
    cross_df["_species_label"] = _resolve_species_labels(cross_df)
    color_series, colorbar_category_ticks, has_numeric_colors, color_min, color_max = _color_series_for_plot(cross_df, config.color_param)
    is_date_color = _is_date_color_column(config.color_param)
    cross_df["_color_value"] = color_series
    species_symbol_map = _build_species_symbol_map(cross_df.get("_species_label", pd.Series(index=cross_df.index, dtype=object)))
    fig_cross = go.Figure()
    show_colorbar = has_numeric_colors
    for species, species_df in cross_df.groupby("_species_label", dropna=False):
        plot_df = species_df.copy()
        x_vals = pd.to_numeric(plot_df.get("d 18O/16O  Mean"), errors="coerce")
        y_vals = pd.to_numeric(plot_df.get("d 13C/12C  Mean"), errors="coerce")
        valid = x_vals.notna() & y_vals.notna()
        if not valid.any():
            continue
        plot_df = plot_df.loc[valid].copy()
        marker: dict[str, Any] = dict(
            size=10,
            opacity=1.0,
            symbol=_species_symbol_for_label(species, species_symbol_map),
        )
        if has_numeric_colors:
            marker.update(
                color=plot_df["_color_value"],
                colorscale="Viridis",
                cmin=color_min,
                cmax=color_max,
                showscale=show_colorbar,
            )
            if show_colorbar:
                colorbar_cfg: dict[str, Any] = {
                    "title": {
                        "text": "Date" if is_date_color else str(config.color_param),
                        "side": "right",
                    },
                    "thickness": 16,
                    "len": 0.86,
                    "x": 1.02,
                    "xanchor": "left",
                    "y": 0.53,
                    "yanchor": "middle",
                }
                if is_date_color:
                    tickvals, ticktext = _build_date_colorbar_ticks(color_series)
                    if tickvals and ticktext:
                        colorbar_cfg.update(tickmode="array", tickvals=tickvals, ticktext=ticktext)
                elif colorbar_category_ticks is not None:
                    tickvals, ticktext = colorbar_category_ticks
                    if tickvals and ticktext:
                        colorbar_cfg.update(tickmode="array", tickvals=tickvals, ticktext=ticktext)
                marker["colorbar"] = colorbar_cfg
                show_colorbar = False
        else:
            marker["color"] = "#1d4ed8"
        fig_cross.add_trace(
            go.Scatter(
                x=pd.to_numeric(plot_df.get("d 18O/16O  Mean"), errors="coerce"),
                y=pd.to_numeric(plot_df.get("d 13C/12C  Mean"), errors="coerce"),
                mode="markers",
                name=str(species),
                marker=marker,
                customdata=_build_processing_point_customdata(plot_df, "cross"),
            )
        )
    _add_processing_crossplot_overlays(
        fig_cross,
        overlays_df,
        summary_masks,
        sat_masks,
        config,
        restored_visible_index=cross_df.index,
        edit_state=edit_state,
    )
    _apply_processing_crossplot_hover_templates(fig_cross, config.color_param)
    axis_cols = ["d 18O/16O  Mean", "d 13C/12C  Mean"]
    axis_df = pd.concat(
        [
            _numeric_axis_rows(cross_df, axis_cols),
            _visible_overlay_axis_rows(overlays_df, summary_masks, sat_masks, config, axis_cols),
        ],
        axis=0,
        ignore_index=True,
    )
    cross_x_range = _axis_range(axis_df.get("d 18O/16O  Mean", pd.Series(dtype=float)))
    cross_y_range = _axis_range(axis_df.get("d 13C/12C  Mean", pd.Series(dtype=float)))
    x_axis: dict[str, Any] = {"title": "d18O", "constrain": "domain"}
    y_axis: dict[str, Any] = {"title": "d13C", "constrain": "domain"}
    if cross_x_range is not None:
        x_axis["range"] = cross_x_range
    else:
        x_axis["autorange"] = True
    if cross_y_range is not None:
        y_axis["range"] = cross_y_range
    else:
        y_axis["autorange"] = True
    fig_cross.update_layout(
        title=dict(
            text="d13C vs d18O",
            x=0.0,
            xanchor="left",
            y=0.99,
            yanchor="top",
        ),
        xaxis=x_axis,
        yaxis=y_axis,
        hovermode="closest",
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.18,
            x=0.0,
            xanchor="left",
            entrywidthmode="pixels",
            entrywidth=140,
            itemsizing="constant",
            tracegroupgap=6,
        ),
        margin=dict(l=40, r=108, t=92, b=170),
        height=720,
    )
    figures["crossplot"] = _figure_json(fig_cross)
    return figures


def _x_axis_series(df: pd.DataFrame, x_axis_option: str) -> pd.Series:
    if x_axis_option == "By Identifier 2":
        return df.get("Identifier 2", pd.Series(index=df.index)).apply(_parse_numeric_token)
    return pd.Series(np.arange(len(df)), index=df.index)


def _build_identifier_figure(
    species_df: pd.DataFrame,
    species_unfiltered: pd.DataFrame,
    identifier: str,
    isotope_key: str,
    config: Any,
    edit_state: dict[str, Any] | None,
    standards_df: pd.DataFrame | None = None,
) -> tuple[dict[str, Any], bool]:
    if go is None:
        return {}, False
    y_col = "d 13C/12C  Mean" if isotope_key == "d13C" else "d 18O/16O  Mean"
    species_col = "Species" if "Species" in species_unfiltered.columns else "Identifier 1"
    filtered_identifier = species_df[species_df["Identifier 1"].astype(str) == str(identifier)].copy()
    unfiltered_identifier = species_unfiltered[species_unfiltered["Identifier 1"].astype(str) == str(identifier)].copy()
    cal_col = _effective_calibrated_column(unfiltered_identifier, isotope_key)
    if filtered_identifier.empty and unfiltered_identifier.empty:
        return {}, False

    filtered_identifier["x_axis"] = _x_axis_series(filtered_identifier, config.x_axis_option)
    unfiltered_identifier["x_axis"] = _x_axis_series(unfiltered_identifier, config.x_axis_option)
    filtered_identifier = filtered_identifier.sort_values("x_axis", na_position="last")
    unfiltered_identifier = unfiltered_identifier.sort_values("x_axis", na_position="last")
    filtered_identifier = _attach_hover_context(filtered_identifier, config.color_param)
    unfiltered_identifier = _attach_hover_context(unfiltered_identifier, config.color_param)

    summary_masks = build_category_masks(
        unfiltered_identifier,
        RangeConfig(
            signal_range=config.signal_range,
            leak_range=config.leak_range,
            d13c_range=config.d13c_range,
            d18o_range=config.d18o_range,
            partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
        ),
        edit_state=edit_state,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    signal_ok = _signal_in_range_mask(unfiltered_identifier.get("1  Cycle Int  Samp  44"), config.signal_range)
    leak_ok = pd.to_numeric(unfiltered_identifier.get("leak_rate"), errors="coerce").between(
        *config.leak_range,
        inclusive="both",
    )
    d13_ok = pd.to_numeric(unfiltered_identifier.get("d 13C/12C  Mean"), errors="coerce").between(
        *config.d13c_range,
        inclusive="both",
    )
    d18_ok = pd.to_numeric(unfiltered_identifier.get("d 18O/16O  Mean"), errors="coerce").between(
        *config.d18o_range,
        inclusive="both",
    )
    sat_masks_for_stats = _partial_saturation_isotope_masks(unfiltered_identifier)
    partial_keep = pd.Series(False, index=unfiltered_identifier.index, dtype=bool)
    if bool(config.overlays.show_saturated_collectors):
        partial_keep = signal_ok & leak_ok & (
            (sat_masks_for_stats["d13C"] & d13_ok) | (sat_masks_for_stats["d18O"] & d18_ok)
        )
    stat_source_mask = (signal_ok & leak_ok & d13_ok & d18_ok) | partial_keep
    stat_source = unfiltered_identifier.loc[stat_source_mask].copy()
    stat_mask_d13, stat_mask_d18, _ = compute_statistical_outlier_masks(
        stat_source,
        sigma_level=float(config.sigma_level_data),
        edit_state=edit_state,
        species_series=_get_species_series(stat_source),
        method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    statistical_mask = stat_mask_d13 if isotope_key == "d13C" else stat_mask_d18
    restored_rows_for_isotope = _restored_row_labels(edit_state, isotope_key=isotope_key)
    filtered_identifier_stat_mask = statistical_mask.reindex(filtered_identifier.index, fill_value=False).astype(bool)
    filtered_identifier_stat_mask = filtered_identifier_stat_mask & ~_restored_index_mask(
        filtered_identifier.index,
        restored_rows_for_isotope,
    )
    filtered_identifier = filtered_identifier.loc[~filtered_identifier_stat_mask].copy()
    color_series, _, has_numeric_colors, color_min, color_max = _color_series_for_plot(filtered_identifier, config.color_param)
    filtered_identifier["_color_value"] = color_series
    edited_row_tokens = {str(row) for row in (edit_state or {}).get("edited_rows", [])}
    edited_mask = pd.Series(
        [str(idx) in edited_row_tokens and str(idx) not in restored_rows_for_isotope for idx in filtered_identifier.index],
        index=filtered_identifier.index,
        dtype=bool,
    )
    sat_masks = _partial_saturation_isotope_masks(unfiltered_identifier)
    d13_std_lookup, d18_std_lookup = _build_cycle_std_lookups(unfiltered_identifier)

    def _exclude_restored(rows: pd.DataFrame) -> pd.DataFrame:
        if rows.empty or not restored_rows_for_isotope:
            return rows
        return rows.loc[[str(idx) not in restored_rows_for_isotope for idx in rows.index]]

    fig = go.Figure()
    if getattr(config.overlays, "show_statistical_outliers", False):
        statistical_outliers = unfiltered_identifier.loc[statistical_mask.reindex(unfiltered_identifier.index, fill_value=False)]
        statistical_outliers = _exclude_restored(statistical_outliers)
        if not statistical_outliers.empty:
            fig.add_trace(
                go.Scatter(
                    x=statistical_outliers["x_axis"],
                    y=pd.to_numeric(statistical_outliers.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="red", symbol="square", size=13, opacity=1.0, line=dict(width=1.5, color="black")),
                    name="Statistical Outliers",
                    customdata=_build_processing_point_customdata(statistical_outliers, isotope_key),
                )
            )
    # Do not leave a species chart completely blank solely because every one of
    # its measurements is outside the active processing ranges.  A blank chart
    # looks like an identifier/species mapping failure, particularly after a
    # name is changed on the import page.  Preserve the normal overlay setting
    # whenever there are accepted rows, but make the range-outlier points
    # discoverable as a fallback when there are none.
    range_outlier_masks = _exclusive_outlier_masks(
        [
            ("signal", summary_masks["Signal Intensity"]),
            ("leak", summary_masks["Leak Rate"]),
            ("d13c", summary_masks["d13C Range"]),
            ("d18o", summary_masks["d18O Range"]),
        ]
    )
    has_range_outliers = any(bool(mask.any()) for mask in range_outlier_masks.values())
    show_range_outliers = bool(getattr(config.overlays, "show_range_outliers", False)) or (
        filtered_identifier.empty and has_range_outliers
    )
    forced_range_outlier_visibility = show_range_outliers and not bool(
        getattr(config.overlays, "show_range_outliers", False)
    )
    if show_range_outliers:
        symbol_map = {
            "signal": "diamond",
            "leak": "star",
            "d13c": "cross",
            "d18o": "x",
        }
        label_map = {
            "signal": "Signal Intensity Range",
            "leak": "Leak Rate Range",
            "d13c": "d13C Range",
            "d18o": "d18O Range",
        }
        for key, mask in range_outlier_masks.items():
            rows = unfiltered_identifier.loc[mask]
            rows = _exclude_restored(rows)
            if rows.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=rows["x_axis"],
                    y=pd.to_numeric(rows.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="red", symbol=symbol_map[key], size=13, opacity=1.0, line=dict(width=1.5, color="black")),
                    name=label_map[key],
                    customdata=_build_processing_point_customdata(rows, isotope_key),
                )
            )
    if forced_range_outlier_visibility:
        fig.add_annotation(
            text="All measurements are outside the active processing ranges and are shown as range outliers.",
            xref="paper",
            yref="paper",
            x=0,
            y=1.14,
            xanchor="left",
            yanchor="bottom",
            showarrow=False,
            font=dict(size=11, color="#b91c1c"),
        )
    if getattr(config.overlays, "show_manual_outliers", False):
        manual_mask = summary_masks.get("Manual Override", pd.Series(False, index=unfiltered_identifier.index))
        manual_rows = unfiltered_identifier.loc[manual_mask.reindex(unfiltered_identifier.index, fill_value=False).astype(bool)]
        manual_rows = _exclude_restored(manual_rows)
        if not manual_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=manual_rows["x_axis"],
                    y=pd.to_numeric(manual_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="#ec4899", symbol="circle-open", size=14, opacity=1.0, line=dict(width=2, color="black")),
                    name="Manual Outliers",
                    customdata=_build_processing_point_customdata(manual_rows, isotope_key),
                )
            )
    if getattr(config.overlays, "show_saturated_collectors", True):
        status_rows = unfiltered_identifier.loc[sat_masks[isotope_key]]
        status_rows = _exclude_restored(status_rows)
        if not status_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=status_rows["x_axis"],
                    y=pd.to_numeric(status_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="#ff7f0e", symbol="diamond-open", size=13, opacity=1.0, line=dict(width=2, color="#ff7f0e")),
                    name="Partially Failed (Recovered Mean)",
                    customdata=_build_processing_point_customdata(status_rows, isotope_key),
                )
            )
    if getattr(config.overlays, "show_saturated_samples", True):
        full_rows = unfiltered_identifier.loc[summary_masks["Fully Saturated Collectors"]]
        full_rows = _exclude_restored(full_rows)
        if not full_rows.empty:
            y_vals = pd.to_numeric(filtered_identifier.get(y_col), errors="coerce")
            y_min = y_vals.min() if y_vals.notna().any() else -1.0
            y_max = y_vals.max() if y_vals.notna().any() else 1.0
            y_range = y_max - y_min if np.isfinite(y_max - y_min) else 1.0
            y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
            fig.add_trace(
                go.Scatter(
                    x=full_rows["x_axis"],
                    y=[y_failed] * len(full_rows),
                    mode="markers",
                    marker=dict(color="#d62728", symbol="triangle-down", size=11, opacity=1.0, line=dict(width=1)),
                    name="Failed Samples (Fully Saturated)",
                    customdata=_build_processing_point_customdata(full_rows, isotope_key),
                )
            )
    if getattr(config.overlays, "show_failed_samples", True):
        failed_rows = unfiltered_identifier.loc[summary_masks["Failed Sample"]]
        failed_rows = _exclude_restored(failed_rows)
        if not failed_rows.empty:
            failed_values = pd.to_numeric(failed_rows.get(y_col), errors="coerce")
            failed_interp = failed_rows.loc[failed_values.notna()]
            failed_missing = failed_rows.loc[failed_values.isna()]
            if not failed_interp.empty:
                fig.add_trace(
                    go.Scatter(
                        x=failed_interp["x_axis"],
                        y=pd.to_numeric(failed_interp.get(y_col), errors="coerce"),
                        mode="markers",
                        marker=dict(color="#ff00ff", symbol="triangle-down", size=11, opacity=1.0, line=dict(width=1)),
                        name="Failed Samples (Interpolated)",
                        customdata=_build_processing_point_customdata(failed_interp, isotope_key),
                    )
                )
            if not failed_missing.empty:
                y_vals = pd.to_numeric(filtered_identifier.get(y_col), errors="coerce")
                y_min = y_vals.min() if y_vals.notna().any() else -1.0
                y_max = y_vals.max() if y_vals.notna().any() else 1.0
                y_range = y_max - y_min if np.isfinite(y_max - y_min) else 1.0
                y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                fig.add_trace(
                    go.Scatter(
                        x=failed_missing["x_axis"],
                        y=[y_failed] * len(failed_missing),
                        mode="markers",
                        marker=dict(color="#7f7f7f", symbol="triangle-down", size=11, opacity=1.0, line=dict(width=1)),
                        name="Failed Samples (No Values)",
                        customdata=_build_processing_point_customdata(failed_missing, isotope_key),
                    )
                )
    restored_rows = filtered_identifier.loc[
        pd.Series(
            [str(idx) in restored_rows_for_isotope for idx in filtered_identifier.index],
            index=filtered_identifier.index,
            dtype=bool,
        )
    ]
    if not restored_rows.empty:
        restored_values = pd.to_numeric(restored_rows.get(y_col), errors="coerce")
        restored_interp = restored_rows.loc[restored_values.notna()]
        show_legend = True
        if not restored_interp.empty:
            fig.add_trace(
                go.Scatter(
                    x=restored_interp["x_axis"],
                    y=pd.to_numeric(restored_interp.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="#22c55e", symbol="star", size=14, opacity=1.0, line=dict(width=1.5, color="#166534")),
                    name="Restored Samples",
                    showlegend=show_legend,
                    customdata=_build_processing_point_customdata(restored_interp, isotope_key),
                )
            )
    _apply_processing_isotope_hover_templates(fig, isotope_key, config.color_param)

    if not filtered_identifier.empty:
        error_y = _build_plotly_error_bar_for_df(filtered_identifier, isotope_key, d13_std_lookup, d18_std_lookup)
        raw_marker_sizes = [0 if str(idx) in restored_rows_for_isotope else 8 for idx in filtered_identifier.index]
        marker: dict[str, Any] = dict(size=8, color="#2563eb")
        if has_numeric_colors:
            marker = dict(
                size=raw_marker_sizes,
                color=filtered_identifier["_color_value"],
                colorscale="Viridis",
                cmin=color_min,
                cmax=color_max,
                showscale=False,
            )
        else:
            marker = dict(size=raw_marker_sizes, color="#2563eb")
        fig.add_trace(
            go.Scatter(
                x=filtered_identifier["x_axis"],
                y=pd.to_numeric(filtered_identifier.get(y_col), errors="coerce"),
                mode="lines+markers",
                line=dict(color="#2563eb", width=1.5),
                marker=marker,
                name=f"Raw {isotope_key} - {identifier}",
                yhoverformat=".3f",
                error_y=error_y,
                customdata=_build_processing_point_customdata(filtered_identifier, isotope_key),
            )
        )
        if edited_mask.any():
            edited_rows = filtered_identifier.loc[edited_mask]
            fig.add_trace(
                go.Scatter(
                    x=edited_rows["x_axis"],
                    y=pd.to_numeric(edited_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="#ff00ff", symbol="circle", size=12, line=dict(width=1, color="#ff00ff")),
                    name="Edited Samples",
                    customdata=_build_processing_point_customdata(edited_rows, isotope_key),
                )
            )
        has_calibrated = bool(
            cal_col
            and cal_col in filtered_identifier.columns
            and pd.to_numeric(filtered_identifier[cal_col], errors="coerce").notna().any()
        )
        if has_calibrated:
            fig.add_trace(
                go.Scatter(
                    x=filtered_identifier["x_axis"],
                    y=pd.to_numeric(filtered_identifier.get(str(cal_col)), errors="coerce"),
                    mode="lines",
                    line=dict(color="#f97316", width=2),
                    name=f"Calibrated {isotope_key} - {identifier}",
                    customdata=_build_processing_point_customdata(filtered_identifier, isotope_key),
                )
            )
        _add_standard_measurement_traces(
            fig,
            standards_df,
            filtered_identifier,
            isotope_key=isotope_key,
            y_col=y_col,
            x_axis_option=config.x_axis_option,
            color_col=config.color_param,
        )
        _apply_processing_isotope_hover_templates(fig, isotope_key, config.color_param)
        fig.update_layout(
            title=f"{identifier} - {isotope_key} for Species: {species_unfiltered[species_col].iloc[0] if species_col in species_unfiltered.columns and not species_unfiltered.empty else ''}",
            xaxis_title="Sample Number" if config.x_axis_option == "By Sequence" else "Identifier 2",
            yaxis_title=isotope_key,
            hovermode="closest",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0.0, xanchor="left"),
            margin=dict(l=40, r=76, t=80, b=40),
        )
        _apply_cycle_std_error_bars(fig, d13_std_lookup, d18_std_lookup)
        return _figure_json(fig), bool(has_calibrated)
    return _figure_json(fig), False


def build_species_sections(
    filtered_df: pd.DataFrame,
    unfiltered_df: pd.DataFrame,
    config: Any,
    edit_state: dict[str, Any] | None,
    species_section_filter: set[str] | None = None,
    standards_df: pd.DataFrame | None = None,
) -> list[SpeciesSection]:
    if filtered_df is None or unfiltered_df is None:
        return []
    species_col = "Species" if "Species" in unfiltered_df.columns else "Identifier 1"
    species_values = _get_species_series(unfiltered_df).fillna("").astype(str)
    sections: list[SpeciesSection] = []
    if species_values.empty:
        return sections
    scoped_filtered = _scope_df(filtered_df, config.selected_identifier)
    scoped_unfiltered = _scope_df(unfiltered_df, config.selected_identifier)
    for species in sorted(value for value in species_values.unique().tolist() if str(value).strip() != ""):
        species_df = scoped_filtered[_get_species_series(scoped_filtered).fillna("").astype(str) == str(species)].copy()
        species_unfiltered = scoped_unfiltered[_get_species_series(scoped_unfiltered).fillna("").astype(str) == str(species)].copy()
        if species_df.empty and species_unfiltered.empty:
            continue
        identifier_figures: list[IdentifierFigureSet] = []
        identifiers = sorted(
            {
                str(value)
                for value in species_unfiltered.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist()
                if str(value).strip() != ""
            }
        )
        if species_section_filter is not None and str(species) not in species_section_filter:
            sections.append(
                SpeciesSection(
                    species=str(species),
                    identifier_count=len(identifiers),
                    identifier_figures=[],
                    outlier_tables=[],
                )
            )
            continue
        for identifier in identifiers:
            d13_fig, has_cal_d13 = _build_identifier_figure(
                species_df,
                species_unfiltered,
                identifier,
                "d13C",
                config,
                edit_state,
                standards_df=standards_df,
            )
            d18_fig, has_cal_d18 = _build_identifier_figure(
                species_df,
                species_unfiltered,
                identifier,
                "d18O",
                config,
                edit_state,
                standards_df=standards_df,
            )
            if d13_fig or d18_fig:
                identifier_figures.append(
                    IdentifierFigureSet(
                        identifier=identifier,
                        d13c=d13_fig,
                        d18o=d18_fig,
                        has_calibrated_d13c=has_cal_d13,
                        has_calibrated_d18o=has_cal_d18,
                    )
                )
        category_masks = build_category_masks(
            species_unfiltered,
            RangeConfig(
                signal_range=config.signal_range,
                leak_range=config.leak_range,
                d13c_range=config.d13c_range,
                d18o_range=config.d18o_range,
                partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
            ),
            edit_state=edit_state,
            sigma_level=float(config.sigma_level_data),
            statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
            iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
        )
        outlier_tables = build_outlier_tables(species_unfiltered, category_masks, species_col, scope_title=str(species))
        sections.append(
            SpeciesSection(
                species=str(species),
                identifier_count=len(identifiers),
                identifier_figures=identifier_figures,
                outlier_tables=outlier_tables,
            )
        )
    return sections

