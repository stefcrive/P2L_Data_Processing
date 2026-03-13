from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from ..domain.calibration.core import (
    _apply_manual_linearity_override_to_standards,
    _apply_linearity_correction,
    _compute_calibration_coefficients,
    _compute_linearity_fit,
    _filter_standards_remove_outliers,
    _resolve_selected_linearity_intensity_column,
    calibrate_results,
    create_calibration_plots,
)
from ..domain.contracts import (
    CalibrationConfig,
    CalibrationOfficialValue,
    CalibrationOfficialValueDeleteResult,
    CalibrationOfficialValueUpsertRequest,
    CalibrationWorkspace,
    ChartBundle,
    CycleDiagnosticsPayload,
    CycleDiagnosticsRequest,
    EditAction,
    ExportRequest,
    ImportResult,
    ProcessingConfig,
    ProcessingExportConfig,
    ProcessingWorkspace,
    SessionSnapshot,
)
from ..domain.diagnostics.core import create_diagnostic_plots
from ..domain.calibration.workspace import build_calibration_workspace
from ..domain.import_session import (
    _append_cycles_source,
    _append_rows_preserve_existing_index,
    _load_uploaded_workbooks,
)
from ..domain.processing.core import RangeConfig, _interpolate_outliers_by_identifier2
from ..domain.processing.cycles import build_cycle_diagnostics_payload, build_target_info
from ..domain.processing.edits import apply_edit_action
from ..domain.processing.export import build_client_output_workbook_bytes, build_dataset_workbook_bytes
from ..domain.processing.outliers import build_category_masks, build_outlier_type_labels, build_processing_summary
from ..domain.processing.workspace import (
    _derive_working_frame,
    _selected_processing_rows,
    build_processing_workspace,
    normalize_processing_config,
)
from ..domain.shared.json_compat import to_json_compatible
from ..domain.shared.plotting import _build_isotope_3d_scatter
from ..domain.standards import StandardsRepository
from ..session_store import FileSessionStore

app = FastAPI(title="IRMS API", version="0.1.0")


def _cors_origins() -> list[str]:
    # Accept a comma-separated allow list via env var for deploys.
    raw = os.getenv("IRMS_API_CORS_ORIGINS", "").strip()
    if raw:
        origins = [value.strip() for value in raw.split(",") if value.strip()]
        if origins:
            return origins
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = FileSessionStore()


class _NamedBytesIO(io.BytesIO):
    def __init__(self, content: bytes, name: str) -> None:
        super().__init__(content)
        self.name = name
        self.size = len(content)


def _to_session_snapshot(session_id: str) -> SessionSnapshot:
    return SessionSnapshot.model_validate(store.build_snapshot(session_id))


def _figure_json(fig: go.Figure | None) -> dict[str, Any]:
    return to_json_compatible(fig.to_plotly_json()) if fig is not None else {}


def _coerce_row_label(row_label: str, df: pd.DataFrame) -> Any:
    if row_label in df.index:
        return row_label
    try:
        numeric = int(row_label)
    except ValueError:
        numeric = row_label
    if numeric in df.index:
        return numeric
    raise HTTPException(status_code=404, detail=f"Unknown row label {row_label}")


def _load_processing_config(metadata: dict[str, Any]) -> ProcessingConfig:
    raw = metadata.get("processing", {}).get("config", {})
    return ProcessingConfig.model_validate(normalize_processing_config(raw if isinstance(raw, dict) else {}).model_dump())


def _processing_subset(df: pd.DataFrame, config: ProcessingConfig) -> pd.DataFrame:
    subset = df.copy()
    if config.selected_identifier != "All" and "Identifier 1" in subset.columns:
        subset = subset[subset["Identifier 1"] == config.selected_identifier].copy()
    return subset


def _candidate_diagnostics_color_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        "Date_ordinal",
        "Date",
        "Identifier 1",
        "Identifier 2",
        "Species",
        "Comment",
        "Label",
        "1  Cycle Int  Samp  44",
        "1  Cycle Int  Diff Samp-Ref  44",
        "leak_rate",
        "Line",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
    ]
    return [col for col in preferred if col in df.columns]


def _candidate_diagnostics_z_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        "1  Cycle Int  Diff Samp-Ref  44",
        "1  Cycle Int  Samp  44",
        "total_co2",
        "p_no_acid",
        "p_gases",
        "leak_rate",
        "Line",
        "Date_ordinal",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
    ]
    ordered = [col for col in preferred if col in df.columns]
    ordered.extend(col for col in df.columns if col not in ordered)
    result: list[str] = []
    for col in ordered:
        finite = pd.to_numeric(df[col], errors="coerce")
        if np.isfinite(finite).any():
            result.append(col)
    return result


def _column_bounds(df: pd.DataFrame, column: str) -> list[float] | None:
    if column not in df.columns:
        return None
    values = pd.to_numeric(df[column], errors="coerce")
    finite = values[np.isfinite(values)]
    if finite.empty:
        return None
    return [float(finite.min()), float(finite.max())]


def _apply_diagnostics_filters(
    df: pd.DataFrame,
    identifier_filter: list[str],
    d13_min: float | None,
    d13_max: float | None,
    d18_min: float | None,
    d18_max: float | None,
) -> pd.DataFrame:
    filtered = df.copy()
    cleaned_identifiers = [str(item).strip() for item in identifier_filter if str(item).strip()]
    if cleaned_identifiers and "Identifier 1" in filtered.columns:
        filtered = filtered[filtered["Identifier 1"].astype(str).isin(cleaned_identifiers)].copy()
    if "d 13C/12C  Mean" in filtered.columns and (d13_min is not None or d13_max is not None):
        d13_vals = pd.to_numeric(filtered["d 13C/12C  Mean"], errors="coerce")
        low = float(d13_min) if d13_min is not None else float("-inf")
        high = float(d13_max) if d13_max is not None else float("inf")
        if low > high:
            low, high = high, low
        filtered = filtered[d13_vals.ge(low) & d13_vals.le(high)].copy()
    if "d 18O/16O  Mean" in filtered.columns and (d18_min is not None or d18_max is not None):
        d18_vals = pd.to_numeric(filtered["d 18O/16O  Mean"], errors="coerce")
        low = float(d18_min) if d18_min is not None else float("-inf")
        high = float(d18_max) if d18_max is not None else float("inf")
        if low > high:
            low, high = high, low
        filtered = filtered[d18_vals.ge(low) & d18_vals.le(high)].copy()
    return filtered


def _simple_processing_figures(df: pd.DataFrame, config: ProcessingConfig) -> dict[str, dict[str, Any]]:
    subset = _processing_subset(df, config)
    figures: dict[str, dict[str, Any]] = {}
    fig_3d, _ = _build_isotope_3d_scatter(
        subset,
        z_col=config.z_axis,
        z_label=config.z_axis,
        color_col=config.color_param,
        color_label=config.color_param,
        title="Processing 3D Chart",
    )
    figures["processing_3d"] = _figure_json(fig_3d)
    if subset.empty:
        return figures
    species_col = "Species" if "Species" in subset.columns else "Identifier 1"
    d13 = go.Figure()
    d18 = go.Figure()
    cross = go.Figure()
    for species, species_df in subset.groupby(species_col):
        plot_df = species_df.copy()
        if config.x_axis_option == "By Identifier 2":
            plot_df["x_axis"] = pd.to_numeric(plot_df.get("Identifier 2"), errors="coerce")
        else:
            plot_df["x_axis"] = range(len(plot_df))
        plot_df = plot_df.sort_values("x_axis", na_position="last")
        d13.add_trace(
            go.Scatter(
                x=plot_df["x_axis"],
                y=pd.to_numeric(plot_df.get("d 13C/12C  Mean"), errors="coerce"),
                mode="lines+markers",
                name=str(species),
            )
        )
        d18.add_trace(
            go.Scatter(
                x=plot_df["x_axis"],
                y=pd.to_numeric(plot_df.get("d 18O/16O  Mean"), errors="coerce"),
                mode="lines+markers",
                name=str(species),
            )
        )
        cross.add_trace(
            go.Scatter(
                x=pd.to_numeric(plot_df.get("d 18O/16O  Mean"), errors="coerce"),
                y=pd.to_numeric(plot_df.get("d 13C/12C  Mean"), errors="coerce"),
                mode="markers",
                name=str(species),
            )
        )
    d13.update_layout(title="d13C Summary", xaxis_title=config.x_axis_option, yaxis_title="d13C")
    d18.update_layout(title="d18O Summary", xaxis_title=config.x_axis_option, yaxis_title="d18O")
    cross.update_layout(title="d13C vs d18O", xaxis_title="d18O", yaxis_title="d13C")
    figures["d13_summary"] = _figure_json(d13)
    figures["d18_summary"] = _figure_json(d18)
    figures["crossplot"] = _figure_json(cross)
    return figures


def _build_processing_summary(df: pd.DataFrame, config: ProcessingConfig) -> dict[str, Any]:
    range_config = RangeConfig(
        signal_range=config.signal_range,
        leak_range=config.leak_range,
        d13c_range=config.d13c_range,
        d18o_range=config.d18o_range,
        partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
    )
    subset = _processing_subset(df, config)
    range_mask = _range_outlier_mask(subset, range_config)
    partial_masks = _partial_saturation_isotope_masks(subset)
    return {
        "rows": int(len(subset)),
        "range_outliers": int(range_mask.sum()),
        "partial_saturation": int(partial_masks["any"].sum()) if "any" in partial_masks else 0,
    }


def _session_exists_or_404(session_id: str) -> None:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")


def _autosave_paths_payload(session_id: str) -> dict[str, Any]:
    paths = store._paths(session_id)
    return {
        "save_dir": str(paths.root),
        "log_path": str(paths.log_path),
        "snapshot_path": str(paths.snapshot_path),
        "meta_path": str(paths.metadata_path),
        "session_token": session_id,
    }


def _persist_session_update(
    session_id: str,
    *,
    action: str,
    payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    df: pd.DataFrame | None = None,
    cycles_df: pd.DataFrame | None = None,
    resumed: bool | None = None,
) -> dict[str, Any]:
    meta = metadata if metadata is not None else store.load_metadata(session_id)
    autosave = dict(meta.get("autosave", {}))
    autosave.update(_autosave_paths_payload(session_id))
    autosave.setdefault("initialized_at", datetime.now(timezone.utc).isoformat())
    if resumed is not None:
        autosave["resumed"] = bool(resumed)
    autosave["last_action"] = action
    autosave["last_saved_at"] = datetime.now(timezone.utc).isoformat()
    autosave["event_count"] = int(autosave.get("event_count", 0)) + 1
    meta["autosave"] = autosave

    frame = df
    cycle_frame = cycles_df
    if frame is None and cycles_df is not None:
        frame = store.load_frame(session_id)
    if frame is not None and cycles_df is None:
        cycle_frame = store.load_cycles_frame(session_id)

    if frame is not None:
        store.save_frames(session_id, frame, cycle_frame)
        meta["row_count"] = int(len(frame))
        meta["cycles_row_count"] = int(len(cycle_frame)) if cycle_frame is not None else 0

    store.write_metadata(session_id, meta)
    store.append_log(session_id, action, payload or {})
    return meta


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/standards/official-values", response_model=list[CalibrationOfficialValue])
def list_official_standard_values() -> list[CalibrationOfficialValue]:
    repo = StandardsRepository.default()
    return [CalibrationOfficialValue.model_validate(item) for item in repo.all_official_values()]


@app.post("/standards/official-values", response_model=CalibrationOfficialValue)
def upsert_official_standard_value(payload: CalibrationOfficialValueUpsertRequest) -> CalibrationOfficialValue:
    repo = StandardsRepository.default()
    record = repo.upsert_official_value(
        standard=payload.standard,
        isotopic_value_type=payload.isotopic_value_type,
        value=payload.value,
        source=payload.source,
    )
    return CalibrationOfficialValue.model_validate(record)


@app.delete("/standards/official-values/{standard}", response_model=CalibrationOfficialValueDeleteResult)
def delete_standard_official_values(standard: str) -> CalibrationOfficialValueDeleteResult:
    repo = StandardsRepository.default()
    deleted_rows = repo.delete_standard(standard)
    return CalibrationOfficialValueDeleteResult(standard=standard, deleted_rows=deleted_rows)


@app.delete(
    "/standards/official-values/{standard}/{isotopic_value_type}",
    response_model=CalibrationOfficialValueDeleteResult,
)
def delete_single_official_value(
    standard: str,
    isotopic_value_type: str,
) -> CalibrationOfficialValueDeleteResult:
    repo = StandardsRepository.default()
    deleted_rows = repo.delete_official_value(standard, isotopic_value_type)
    return CalibrationOfficialValueDeleteResult(
        standard=standard,
        isotopic_value_type=isotopic_value_type,
        deleted_rows=deleted_rows,
    )


@app.post("/sessions/import", response_model=ImportResult)
async def import_session(files: list[UploadFile] = File(...)) -> ImportResult:
    uploaded = []
    raw_uploads: list[tuple[str, bytes]] = []
    for file in files:
        content = await file.read()
        raw_uploads.append((file.filename or "upload.xlsx", content))
        uploaded.append(_NamedBytesIO(content, file.filename or "upload.xlsx"))
    df, cycles_df, specs, errors = _load_uploaded_workbooks(uploaded)
    if df is None:
        raise HTTPException(status_code=400, detail={"errors": errors or ["No workbook rows were loaded"]})
    session_id = store.create_session({"source_files": specs, "errors": errors})
    for filename, content in raw_uploads:
        store.save_upload(session_id, filename, content)
    metadata = store.load_metadata(session_id)
    metadata["source_files"] = specs
    metadata["errors"] = errors
    _persist_session_update(
        session_id,
        action="session_loaded",
        payload={"source_files": specs, "errors": errors},
        metadata=metadata,
        df=df,
        cycles_df=cycles_df,
        resumed=False,
    )
    return ImportResult(session=_to_session_snapshot(session_id))


@app.post("/sessions/{session_id}/append", response_model=ImportResult)
async def append_session(session_id: str, files: list[UploadFile] = File(...)) -> ImportResult:
    _session_exists_or_404(session_id)
    current_df = store.load_frame(session_id)
    current_cycles = store.load_cycles_frame(session_id)
    metadata = store.load_metadata(session_id)
    uploaded = []
    raw_uploads: list[tuple[str, bytes]] = []
    for file in files:
        content = await file.read()
        raw_uploads.append((file.filename or "upload.xlsx", content))
        uploaded.append(_NamedBytesIO(content, file.filename or "upload.xlsx"))
    append_df, append_cycles, specs, errors = _load_uploaded_workbooks(uploaded)
    if append_df is None:
        raise HTTPException(status_code=400, detail={"errors": errors or ["No workbook rows were appended"]})
    merged = _append_rows_preserve_existing_index(current_df, append_df)
    merged_cycles = _append_cycles_source(current_cycles, append_cycles)
    for filename, content in raw_uploads:
        store.save_upload(session_id, filename, content)
    metadata["source_files"] = list(metadata.get("source_files", [])) + specs
    metadata["errors"] = list(metadata.get("errors", [])) + errors
    _persist_session_update(
        session_id,
        action="session_appended",
        payload={"appended_files": specs, "errors": errors},
        metadata=metadata,
        df=merged,
        cycles_df=merged_cycles,
    )
    return ImportResult(session=_to_session_snapshot(session_id))


@app.get("/sessions", response_model=list[SessionSnapshot])
def list_sessions(limit: int = Query(50, ge=1, le=500)) -> list[SessionSnapshot]:
    return [SessionSnapshot.model_validate(item) for item in store.list_sessions(limit=limit)]


@app.get("/sessions/{session_id}", response_model=SessionSnapshot)
def get_session(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    return _to_session_snapshot(session_id)


@app.post("/sessions/{session_id}/open", response_model=SessionSnapshot)
def open_session(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    _persist_session_update(
        session_id,
        action="session_opened",
        payload={"resumed": True},
        metadata=metadata,
        resumed=True,
    )
    return _to_session_snapshot(session_id)


@app.post("/sessions/{session_id}/save", response_model=SessionSnapshot)
def save_session(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    _persist_session_update(
        session_id,
        action="session_saved_manual",
        payload={"trigger": "manual"},
        metadata=store.load_metadata(session_id),
        df=store.load_frame(session_id),
        cycles_df=store.load_cycles_frame(session_id),
    )
    return _to_session_snapshot(session_id)


@app.post("/sessions/{session_id}/close", response_model=SessionSnapshot)
def close_session(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    _persist_session_update(
        session_id,
        action="session_closed",
        payload={"closed": True},
        metadata=metadata,
        resumed=False,
    )
    return _to_session_snapshot(session_id)


@app.delete("/sessions/{session_id}")
def discard_session(session_id: str) -> dict[str, Any]:
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return {"session_id": session_id, "deleted": True}


@app.get("/sessions/{session_id}/diagnostics", response_model=ChartBundle)
def diagnostics(
    session_id: str,
    color_param: str = Query("Date_ordinal"),
    z_axis: str | None = Query(None),
    identifier_filter: list[str] = Query([]),
    d13_min: float | None = Query(None),
    d13_max: float | None = Query(None),
    d18_min: float | None = Query(None),
    d18_max: float | None = Query(None),
) -> ChartBundle:
    _session_exists_or_404(session_id)
    df = store.load_frame(session_id)
    available_color_params = _candidate_diagnostics_color_columns(df)
    available_z_axes = _candidate_diagnostics_z_columns(df)
    if color_param not in df.columns and available_color_params:
        color_param = available_color_params[0]
    if z_axis not in available_z_axes:
        if "1  Cycle Int  Diff Samp-Ref  44" in available_z_axes:
            z_axis = "1  Cycle Int  Diff Samp-Ref  44"
        elif "1  Cycle Int  Samp  44" in available_z_axes:
            z_axis = "1  Cycle Int  Samp  44"
        elif available_z_axes:
            z_axis = available_z_axes[0]
        else:
            z_axis = "1  Cycle Int  Samp  44"
    filtered_df = _apply_diagnostics_filters(df, identifier_filter, d13_min, d13_max, d18_min, d18_max)
    fig = create_diagnostic_plots(filtered_df, color_param)
    fig_3d, _ = _build_isotope_3d_scatter(
        df,
        z_col=z_axis,
        z_label=z_axis,
        color_col=color_param,
        color_label=color_param,
        title="Diagnostics 3D Chart",
        open_circle_identifier="SHP2L",
    )
    identifiers = (
        sorted({str(value) for value in df.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist() if str(value).strip()})
        if "Identifier 1" in df.columns
        else []
    )
    return ChartBundle(
        session_id=session_id,
        figures={"diagnostics": _figure_json(fig), "diagnostics_3d": _figure_json(fig_3d)},
        summary={
            "available_color_params": available_color_params,
            "available_z_axis_options": available_z_axes,
            "available_identifiers": identifiers,
            "d13_bounds": _column_bounds(df, "d 13C/12C  Mean"),
            "d18_bounds": _column_bounds(df, "d 18O/16O  Mean"),
            "active_filters": {
                "color_param": color_param,
                "z_axis": z_axis,
                "identifier_filter": [str(item).strip() for item in identifier_filter if str(item).strip()],
                "d13_min": d13_min,
                "d13_max": d13_max,
                "d18_min": d18_min,
                "d18_max": d18_max,
            },
            "row_count_before": int(len(df)),
            "row_count_after": int(len(filtered_df)),
        },
    )


@app.post("/sessions/{session_id}/calibration/run", response_model=SessionSnapshot)
def run_calibration(session_id: str, config: CalibrationConfig) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    standards_repo = StandardsRepository.default()
    if len(config.selected_standards) not in (1, 2):
        raise HTTPException(status_code=400, detail="Please select either one or two standards for calibration.")
    standards_adjusted_df = _apply_manual_linearity_override_to_standards(
        df,
        config.selected_standards,
        enabled=config.linearity.manual_override_enabled,
        d13_per_10v=config.linearity.manual_d13_per_10v,
        d18_per_10v=config.linearity.manual_d18_per_10v,
        use_diff_intensity=config.linearity.use_diff_intensity,
    )
    clean_stds = _filter_standards_remove_outliers(
        standards_adjusted_df,
        config.selected_standards,
        config.calibration_type,
        config.sigma_level,
        config.iqr_multiplier,
        config.independent_isotope_outliers,
    )
    calibrated = calibrate_results(clean_stds if not clean_stds.empty else df, df, config.selected_standards, standards_repo)
    fits: dict[str, Any] = {}
    if config.linearity.apply:
        intensity_col = _resolve_selected_linearity_intensity_column(
            df=clean_stds if not clean_stds.empty else calibrated,
            use_diff_intensity=config.linearity.use_diff_intensity,
        )
        fit13 = _compute_linearity_fit(clean_stds, "d 13C/12C  Mean", intensity_col) if not clean_stds.empty else {}
        fit18 = _compute_linearity_fit(clean_stds, "d 18O/16O  Mean", intensity_col) if not clean_stds.empty else {}
        fits = {"d13C": fit13, "d18O": fit18, "intensity_col": intensity_col}
        calibrated = _apply_linearity_correction(calibrated, intensity_col, fits)
    metadata["calibration"] = {
        "config": config.model_dump(),
        "coefficients": _compute_calibration_coefficients(clean_stds, config.selected_standards, standards_repo),
        "linearity_fits": fits,
        "selected_standards": config.selected_standards,
    }
    _persist_session_update(
        session_id,
        action="calibration_run",
        payload=metadata["calibration"],
        metadata=metadata,
        df=calibrated,
        cycles_df=store.load_cycles_frame(session_id),
    )
    return _to_session_snapshot(session_id)


@app.get("/sessions/{session_id}/calibration/workspace", response_model=CalibrationWorkspace)
def calibration_workspace(session_id: str) -> CalibrationWorkspace:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return build_calibration_workspace(
        session_id=session_id,
        df=store.load_frame(session_id),
        metadata=store.load_metadata(session_id),
    )


@app.post("/sessions/{session_id}/calibration/workspace", response_model=CalibrationWorkspace)
def calibration_workspace_preview(session_id: str, config: CalibrationConfig) -> CalibrationWorkspace:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return build_calibration_workspace(
        session_id=session_id,
        df=store.load_frame(session_id),
        metadata=store.load_metadata(session_id),
        config_override=config,
    )


@app.get("/sessions/{session_id}/calibration/charts", response_model=ChartBundle)
def calibration_charts(session_id: str, color_param: str = Query("Date_ordinal")) -> ChartBundle:
    _session_exists_or_404(session_id)
    workspace = build_calibration_workspace(
        session_id=session_id,
        df=store.load_frame(session_id),
        metadata=store.load_metadata(session_id),
    )
    payload = dict(workspace.figures)
    if color_param and color_param != workspace.config.color_param:
        # Preserve the legacy endpoint signature without adding a second config store path.
        metadata = store.load_metadata(session_id)
        config_payload = dict(metadata.get("calibration", {}).get("config", {}))
        config_payload["color_param"] = color_param
        workspace = build_calibration_workspace(
            session_id=session_id,
            df=store.load_frame(session_id),
            metadata={"calibration": {**metadata.get("calibration", {}), "config": config_payload}},
        )
        payload = dict(workspace.figures)
    return ChartBundle(session_id=session_id, figures=payload)


def _build_processing_workspace_response(
    session_id: str,
    metadata: dict[str, Any] | None = None,
    df: pd.DataFrame | None = None,
    cycles_df: pd.DataFrame | None = None,
) -> ProcessingWorkspace:
    meta = metadata if metadata is not None else store.load_metadata(session_id)
    frame = df if df is not None else store.load_frame(session_id)
    cycles_frame = cycles_df if cycles_df is not None else store.load_cycles_frame(session_id)
    return build_processing_workspace(session_id, frame, cycles_frame, meta)


def _workspace_to_chart_bundle(workspace: ProcessingWorkspace) -> ChartBundle:
    return ChartBundle(
        session_id=workspace.session_id,
        figures=dict(workspace.overview_figures),
        tables={
            table.name: list(table.rows)
            for table in workspace.outlier_tables
        },
        summary=workspace.summary.model_dump(),
    )


@app.get("/sessions/{session_id}/processing/workspace", response_model=ProcessingWorkspace)
def processing_workspace(session_id: str) -> ProcessingWorkspace:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return _build_processing_workspace_response(session_id)


@app.post("/sessions/{session_id}/processing/config", response_model=ProcessingWorkspace)
def set_processing_config(session_id: str, config: ProcessingConfig) -> ProcessingWorkspace:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    metadata.setdefault("processing", {})
    metadata["processing"]["config"] = config.model_dump()
    _persist_session_update(
        session_id,
        action="processing_config_updated",
        payload=config.model_dump(),
        metadata=metadata,
    )
    return _build_processing_workspace_response(session_id, metadata=metadata)


@app.post("/sessions/{session_id}/processing/edit", response_model=ProcessingWorkspace)
def edit_processing(session_id: str, edit: EditAction) -> ProcessingWorkspace:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    edit_state = metadata.setdefault(
        "edit_state",
        {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
    )
    calibration = metadata.get("calibration", {})
    coeffs = calibration.get("coefficients", {})
    fits = calibration.get("linearity_fits", {})
    try:
        updated_df, updated_edit_state = apply_edit_action(
            df,
            edit_state,
            edit,
            calibration_coefficients=coeffs,
            linearity_fits=fits,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metadata["edit_state"] = updated_edit_state
    _persist_session_update(
        session_id,
        action="processing_edit",
        payload=edit.model_dump(),
        metadata=metadata,
        df=updated_df,
        cycles_df=store.load_cycles_frame(session_id),
    )
    return _build_processing_workspace_response(session_id, metadata=metadata, df=updated_df)


@app.post("/sessions/{session_id}/processing/cycle-diagnostics", response_model=CycleDiagnosticsPayload)
def processing_cycle_diagnostics(session_id: str, request: CycleDiagnosticsRequest) -> CycleDiagnosticsPayload:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    cycles_df = store.load_cycles_frame(session_id)
    config = _load_processing_config(metadata)
    row_label = _coerce_row_label(request.target.row_label, df)
    target = build_target_info(df, row_label, request.target.isotope_key, metadata.get("edit_state", {}))
    if target is None:
        raise HTTPException(status_code=404, detail="Unknown processing target")
    return build_cycle_diagnostics_payload(
        session_id=session_id,
        df=df,
        cycles_df=cycles_df,
        target=target,
        config=RangeConfig(
            signal_range=config.signal_range,
            leak_range=config.leak_range,
            d13c_range=config.d13c_range,
            d18o_range=config.d18o_range,
            partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
        ),
        edit_state=metadata.get("edit_state", {}),
        target_intensity=request.target_intensity,
        correct_linearity=bool(request.correct_linearity),
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )


@app.get("/sessions/{session_id}/processing/charts", response_model=ChartBundle)
def processing_charts(session_id: str) -> ChartBundle:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return _workspace_to_chart_bundle(_build_processing_workspace_response(session_id))


@app.post("/sessions/{session_id}/exports/dataset")
def export_dataset(session_id: str, request: ExportRequest) -> Response:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    config = normalize_processing_config(metadata.get("processing", {}).get("config", {}))
    config.export = ProcessingExportConfig.model_validate(request.model_dump(exclude={"output_type"}))
    metadata.setdefault("processing", {})
    metadata["processing"]["config"] = config.model_dump()

    calibration = metadata.get("calibration", {})
    working_df = _derive_working_frame(df, config, calibration_meta=calibration)
    data_to_process = _selected_processing_rows(working_df, list(config.export.selected_ids))
    range_config = RangeConfig(
        signal_range=config.signal_range,
        leak_range=config.leak_range,
        d13c_range=config.d13c_range,
        d18o_range=config.d18o_range,
        partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
    )
    selected_standards = metadata.get("calibration", {}).get("selected_standards", [])
    all_standards = StandardsRepository.default().standards_list() + list(selected_standards)
    summary = build_processing_summary(
        data_to_process,
        range_config,
        edit_state=metadata.get("edit_state", {}),
        standards_to_exclude=all_standards,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    category_masks = build_category_masks(
        data_to_process,
        range_config,
        edit_state=metadata.get("edit_state", {}),
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    outlier_types = build_outlier_type_labels(data_to_process, category_masks)
    effective_outlier_mask = outlier_types.astype(str).str.strip().replace({"": np.nan}).notna()

    if bool(config.export.include_outliers):
        main_data = data_to_process.copy()
        main_data["Outlier Types"] = outlier_types
        outliers_df = pd.DataFrame()
        if bool(config.export.interpolate_outliers):
            columns_to_interp = [
                "1  Cycle Int  Samp  44",
                "d 13C/12C  Mean",
                "d 13C/12C  Std Dev",
                "d 18O/16O  Mean",
                "d 18O/16O  Std Dev",
                "d13C_calibrated",
                "d18O_calibrated",
            ]
            present_cols = [col for col in columns_to_interp if col in main_data.columns]
            original_cols: list[str] = []
            for col in present_cols:
                original_name = f"Original {col}"
                main_data[original_name] = main_data[col]
                original_cols.append(original_name)
            if present_cols:
                main_data = _interpolate_outliers_by_identifier2(main_data, effective_outlier_mask, present_cols)
                if "Outlier Types" in main_data.columns and original_cols:
                    cols = list(main_data.columns)
                    pos = cols.index("Outlier Types")
                    for original_name in original_cols:
                        if original_name in cols:
                            cols.remove(original_name)
                    cols = cols[: pos + 1] + original_cols + cols[pos + 1 :]
                    main_data = main_data[cols]
    else:
        main_data = data_to_process.loc[~effective_outlier_mask].copy()
        outliers_df = data_to_process.loc[effective_outlier_mask].copy()
        if not outliers_df.empty:
            outliers_df["Category"] = outlier_types.loc[outliers_df.index]

    statistics_rows = [
        {"Metric": metric.metric, "Value": metric.value, "Details": metric.details}
        for metric in summary.metrics
    ]
    output_type = request.output_type
    if output_type == "client_output":
        client_source = main_data if bool(config.export.include_outliers) else data_to_process.copy()
        workbook, filename = build_client_output_workbook_bytes(
            client_source,
            selected_standards=selected_standards,
            client_name=config.export.client_name,
            comment_map=config.export.comment_map,
        )
    else:
        workbook, filename = build_dataset_workbook_bytes(
            main_data,
            outliers=outliers_df,
            selected_standards=selected_standards,
            client_name=config.export.client_name,
            statistics_rows=statistics_rows,
        )
    _persist_session_update(
        session_id,
        action="dataset_exported",
        payload=config.export.model_dump(),
        metadata=metadata,
    )
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
