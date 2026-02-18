import streamlit as st
import pandas as pd
import numpy as np
import io
import hashlib
import json
from datetime import datetime
from pathlib import Path

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

# Optional helper to interpolate outliers during export
try:
    from interpolate_outliers import interpolate_columns
except Exception:
    interpolate_columns = None


st.set_page_config(layout="wide")

# Constants for isotopic type keys (canonical)
# standards.csv uses the plain VPDB/VSMOW values (no leading delta)
ISOTYPE_D13C = 'VPDB(13C)'
ISOTYPE_D18O = 'VSMOW(18O)'

AUTOSAVE_LOG_PATH_KEY = "autosave_log_path"
AUTOSAVE_SNAPSHOT_PATH_KEY = "autosave_snapshot_path"
AUTOSAVE_SAVE_DIR_KEY = "autosave_save_dir"
AUTOSAVE_ERROR_KEY = "autosave_error"
AUTOSAVE_EVENT_COUNT_KEY = "autosave_event_count"
AUTOSAVE_INIT_TS_KEY = "autosave_initialized_at"
AUTOSAVE_DIR_OVERRIDE_KEY = "autosave_dir_override"
AUTOSAVE_SOURCE_FILES_KEY = "autosave_source_files"
AUTOSAVE_META_PATH_KEY = "autosave_meta_path"
AUTOSAVE_RESUMED_KEY = "autosave_resumed"
AUTOSAVE_SESSION_TOKEN_KEY = "autosave_session_token"


def _safe_filename_fragment(value):
    """Return a filesystem-safe filename fragment."""
    text = str(value).strip()
    if text == "":
        return "session"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = text.strip("._")
    return text if text else "session"


def _numeric_or_none(value):
    """Convert numeric-like values to float; return None for NaN/invalid."""
    num = pd.to_numeric(pd.Series([value]), errors='coerce').iloc[0]
    return float(num) if pd.notna(num) else None


def _normalize_upload_spec(upload_item):
    """Normalize uploaded file metadata to {'name','size','md5','raw_name'}."""
    raw_name = upload_item
    raw_size = None
    raw_md5 = None
    if isinstance(upload_item, dict):
        raw_name = upload_item.get("name")
        raw_size = upload_item.get("size")
        raw_md5 = upload_item.get("md5")

    raw_name_text = "" if raw_name is None else str(raw_name).strip()
    if raw_name_text == "":
        return None

    size_val = None
    try:
        if raw_size is not None and not pd.isna(raw_size):
            size_val = int(raw_size)
            if size_val < 0:
                size_val = None
    except Exception:
        size_val = None

    md5_val = None
    if raw_md5 is not None:
        text = str(raw_md5).strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", text):
            md5_val = text

    return {
        "raw_name": raw_name_text,
        "name": Path(raw_name_text).name,
        "size": size_val,
        "md5": md5_val,
    }


def _iter_autosave_search_roots():
    """Yield preferred roots to search for uploaded workbooks."""
    roots = []
    seen = set()

    def _add(path_obj):
        if path_obj is None:
            return
        try:
            p = Path(path_obj).resolve()
        except Exception:
            return
        key = str(p).lower()
        if key in seen or not p.exists() or not p.is_dir():
            return
        seen.add(key)
        roots.append(p)

    home = Path.home()
    _add(home / "Desktop")
    _add(home / "Documents")
    _add(home / "Downloads")
    _add(home)
    cwd = Path.cwd()
    _add(cwd)
    _add(cwd.parent)
    return roots


def _file_md5(path_obj, chunk_size=1024 * 1024):
    """Compute MD5 for a local file."""
    digest = hashlib.md5()
    with Path(path_obj).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _find_upload_matches(name, size=None, md5=None, max_matches=40):
    """Find local files matching uploaded name, preferring size matches."""
    matches = []
    for root in _iter_autosave_search_roots():
        try:
            for path in root.rglob(name):
                if not path.is_file():
                    continue
                if size is not None:
                    try:
                        if int(path.stat().st_size) != int(size):
                            continue
                    except Exception:
                        continue
                if md5:
                    try:
                        if _file_md5(path).lower() != str(md5).lower():
                            continue
                    except Exception:
                        continue
                matches.append(path.resolve())
                if len(matches) >= max_matches:
                    return matches
        except Exception:
            continue
    return matches


def _candidate_upload_directories(upload_item):
    """Best-effort local folder detection for an uploaded workbook."""
    spec = _normalize_upload_spec(upload_item)
    if spec is None:
        return []

    raw_name = spec["raw_name"]
    name = spec["name"]
    size = spec["size"]
    md5 = spec.get("md5")

    dirs = []
    seen = set()

    def _add_dir(path_obj):
        if path_obj is None:
            return
        try:
            p = Path(path_obj).resolve()
        except Exception:
            return
        key = str(p).lower()
        if key in seen:
            return
        seen.add(key)
        dirs.append(p)

    raw_path = Path(raw_name)
    if raw_path.is_absolute() and raw_path.exists() and raw_path.is_file():
        _add_dir(raw_path.parent)

    matches = _find_upload_matches(name, size=size, md5=md5)
    if not matches and size is not None:
        matches = _find_upload_matches(name, size=None, md5=md5)
    if not matches and md5:
        matches = _find_upload_matches(name, size=size, md5=None)
    for m in matches:
        _add_dir(m.parent)
    return dirs


def _resolve_autosave_directory(upload_files):
    """Resolve autosave folder, preferring detected Excel directory."""
    override_raw = st.session_state.get(AUTOSAVE_DIR_OVERRIDE_KEY)
    if override_raw is not None and str(override_raw).strip() != "":
        override_path = Path(str(override_raw).strip()).expanduser()
        if not override_path.is_absolute():
            override_path = (Path.cwd() / override_path).resolve()
        return override_path

    dirs = []
    for upload_item in upload_files:
        dirs.extend(_candidate_upload_directories(upload_item))
    if not dirs:
        return Path.cwd().resolve()

    counts = {}
    for folder in dirs:
        key = str(folder)
        counts[key] = counts.get(key, 0) + 1
    cwd_key = str(Path.cwd().resolve()).lower()
    best = max(
        counts.items(),
        key=lambda item: (item[1], 0 if str(item[0]).lower() == cwd_key else 1),
    )[0]
    return Path(best)


def _build_autosave_session_token(upload_specs):
    """Build a stable token from uploaded file names and sizes."""
    if not upload_specs:
        return "nofiles"
    parts = []
    for spec in sorted(upload_specs, key=lambda s: str(s.get("name", "")).lower()):
        name = str(spec.get("name", "")).strip().lower()
        size = spec.get("size")
        size_text = "na" if size is None else str(int(size))
        md5_text = spec.get("md5") or "nomd5"
        parts.append(f"{name}:{size_text}:{md5_text}")
    payload = "|".join(parts)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]


def _summarize_autosave_log(log_path):
    """Return (event_count, edited_rows_from_last_event) for a JSONL log."""
    event_count = 0
    last_payload = None
    try:
        with Path(log_path).open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line == "":
                    continue
                event_count += 1
                try:
                    last_payload = json.loads(line)
                except Exception:
                    continue
    except Exception:
        return 0, set()

    edited_rows = set()
    if isinstance(last_payload, dict):
        rows = last_payload.get("edited_rows", [])
        if isinstance(rows, (list, tuple, set)):
            edited_rows = {str(r) for r in rows if str(r).strip() != ""}
    return event_count, edited_rows


def _write_autosave_metadata():
    """Persist autosave session metadata."""
    meta_path_raw = st.session_state.get(AUTOSAVE_META_PATH_KEY)
    if not meta_path_raw:
        return False
    meta_path = Path(meta_path_raw)
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "save_dir": st.session_state.get(AUTOSAVE_SAVE_DIR_KEY),
        "log_path": st.session_state.get(AUTOSAVE_LOG_PATH_KEY),
        "snapshot_path": st.session_state.get(AUTOSAVE_SNAPSHOT_PATH_KEY),
        "session_token": st.session_state.get(AUTOSAVE_SESSION_TOKEN_KEY),
        "source_files": st.session_state.get(AUTOSAVE_SOURCE_FILES_KEY, []),
        "event_count": int(st.session_state.get(AUTOSAVE_EVENT_COUNT_KEY, 0)),
        "resumed": bool(st.session_state.get(AUTOSAVE_RESUMED_KEY, False)),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


def _reset_autosave_state():
    """Clear autosave-related session-state keys."""
    for key in [
        AUTOSAVE_LOG_PATH_KEY,
        AUTOSAVE_SNAPSHOT_PATH_KEY,
        AUTOSAVE_SAVE_DIR_KEY,
        AUTOSAVE_ERROR_KEY,
        AUTOSAVE_EVENT_COUNT_KEY,
        AUTOSAVE_INIT_TS_KEY,
        AUTOSAVE_SOURCE_FILES_KEY,
        AUTOSAVE_META_PATH_KEY,
        AUTOSAVE_RESUMED_KEY,
        AUTOSAVE_SESSION_TOKEN_KEY,
    ]:
        st.session_state.pop(key, None)


def _initialize_autosave_session(upload_files, base_df=None):
    """Create or resume autosave files for the current workbook session."""
    try:
        normalized = []
        for item in (upload_files or []):
            spec = _normalize_upload_spec(item)
            if spec is not None:
                normalized.append(spec)

        names = [spec["name"] for spec in normalized]
        st.session_state[AUTOSAVE_SOURCE_FILES_KEY] = names
        save_dir = _resolve_autosave_directory(normalized)
        save_dir.mkdir(parents=True, exist_ok=True)
        session_token = _build_autosave_session_token(normalized)

        if len(names) == 1:
            base_name = _safe_filename_fragment(Path(names[0]).stem)
        elif len(names) > 1:
            ordered_names = sorted(names, key=lambda v: str(v).lower())
            base_name = f"{_safe_filename_fragment(Path(ordered_names[0]).stem)}_plus_{len(names)-1}"
        else:
            base_name = "irms_data"
        base_name = (base_name[:64] if len(base_name) > 64 else base_name) + f"_{session_token}"

        log_path = save_dir / f"{base_name}_session_edits.jsonl"
        snapshot_path = save_dir / f"{base_name}_session_snapshot.csv"
        meta_path = save_dir / f"{base_name}_session_meta.json"

        resumed_df = None
        resumed = False
        if snapshot_path.exists():
            try:
                if snapshot_path.stat().st_size > 0:
                    resumed_df = pd.read_csv(snapshot_path, low_memory=False)
                    resumed = True
            except Exception:
                resumed_df = None
                resumed = False

        # Ensure files exist so users can verify autosave location.
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not log_path.exists():
            log_path.write_text("", encoding="utf-8")

        if resumed_df is None:
            if base_df is not None:
                base_df.to_csv(snapshot_path, index=False)
            elif not snapshot_path.exists():
                snapshot_path.write_text("", encoding="utf-8")
        elif not snapshot_path.exists():
            snapshot_path.write_text("", encoding="utf-8")

        event_count, edited_rows = _summarize_autosave_log(log_path)

        st.session_state[AUTOSAVE_LOG_PATH_KEY] = str(log_path)
        st.session_state[AUTOSAVE_SNAPSHOT_PATH_KEY] = str(snapshot_path)
        st.session_state[AUTOSAVE_META_PATH_KEY] = str(meta_path)
        st.session_state[AUTOSAVE_SAVE_DIR_KEY] = str(save_dir)
        st.session_state[AUTOSAVE_ERROR_KEY] = None
        st.session_state[AUTOSAVE_EVENT_COUNT_KEY] = int(event_count)
        st.session_state[AUTOSAVE_INIT_TS_KEY] = datetime.now().isoformat(timespec='seconds')
        st.session_state[AUTOSAVE_RESUMED_KEY] = bool(resumed)
        st.session_state[AUTOSAVE_SESSION_TOKEN_KEY] = session_token
        _write_autosave_metadata()
        return {
            "ok": True,
            "resumed": bool(resumed),
            "resumed_df": resumed_df,
            "edited_rows": edited_rows,
        }
    except Exception as exc:
        st.session_state[AUTOSAVE_LOG_PATH_KEY] = None
        st.session_state[AUTOSAVE_SNAPSHOT_PATH_KEY] = None
        st.session_state[AUTOSAVE_META_PATH_KEY] = None
        st.session_state[AUTOSAVE_SAVE_DIR_KEY] = None
        st.session_state[AUTOSAVE_RESUMED_KEY] = False
        st.session_state[AUTOSAVE_SESSION_TOKEN_KEY] = None
        st.session_state[AUTOSAVE_ERROR_KEY] = f"Autosave initialization failed: {exc}"
        return {
            "ok": False,
            "resumed": False,
            "resumed_df": None,
            "edited_rows": set(),
        }


def _append_autosave_event(action, changes=None, context=None):
    """Append a single JSONL event to the autosave log."""
    log_path_raw = st.session_state.get(AUTOSAVE_LOG_PATH_KEY)
    if not log_path_raw:
        return False

    payload = {
        "timestamp": datetime.now().isoformat(timespec='seconds'),
        "action": str(action),
        "changes": changes or [],
        "context": context or {},
        "edited_rows": sorted(_get_edited_row_tokens()),
        "row_count": int(len(st.session_state.df)) if st.session_state.get('df') is not None else 0,
    }
    log_path = Path(log_path_raw)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")
    st.session_state[AUTOSAVE_EVENT_COUNT_KEY] = int(st.session_state.get(AUTOSAVE_EVENT_COUNT_KEY, 0)) + 1
    return True


def _write_autosave_snapshot():
    """Write the full current dataframe snapshot to CSV."""
    snapshot_path_raw = st.session_state.get(AUTOSAVE_SNAPSHOT_PATH_KEY)
    df = st.session_state.get('df')
    if not snapshot_path_raw or df is None:
        return False

    snapshot_path = Path(snapshot_path_raw)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(snapshot_path, index=False)
    return True


def _autosave_session_update(action, changes=None, context=None):
    """Persist an autosave event and refreshed dataframe snapshot."""
    try:
        logged = _append_autosave_event(action, changes=changes, context=context)
        snapshotted = _write_autosave_snapshot()
        if not logged and not snapshotted:
            return False
        _write_autosave_metadata()
        st.session_state[AUTOSAVE_ERROR_KEY] = None
        return True
    except Exception as exc:
        st.session_state[AUTOSAVE_ERROR_KEY] = str(exc)
        return False

# Helper: build readable date ticks for colorbars when coloring by date ordinals
def _build_date_colorbar_ticks(values, n=6, date_format='%Y-%m-%d'):
    try:
        s = pd.to_numeric(pd.Series(values), errors='coerce').dropna()
    except Exception:
        return None, None
    if s.empty:
        return None, None
    vmin, vmax = float(s.min()), float(s.max())
    if not np.isfinite(vmin) or not np.isfinite(vmax):
        return None, None
    # Evenly spaced tick locations across the ordinal range
    tickvals = np.linspace(vmin, vmax, int(max(2, n)))
    # Convert ordinal numbers back to date strings
    ticktext = []
    for v in tickvals:
        try:
            # Round to the nearest day to avoid fractional ordinals
            ts = pd.Timestamp.fromordinal(int(round(v)))
            ticktext.append(ts.strftime(date_format))
        except Exception:
            ticktext.append(str(v))
    return tickvals.tolist(), ticktext

def _prepare_color_values(values):
    """Coerce color values to numeric, with categorical fallback + ticks."""
    if values is None:
        return None, None
    series = pd.Series(values)
    numeric = pd.to_numeric(series, errors='coerce')
    if numeric.notna().any():
        return numeric, None
    categories = series.where(series.notna(), 'Unknown').astype(str)
    codes, uniques = pd.factorize(categories, sort=True)
    ticks = (list(range(len(uniques))), [str(u) for u in uniques])
    return pd.Series(codes, index=series.index), ticks

def _compose_label_series(identifier_series, species_series):
    """Compose labels as 'Identifier 1 - Species' when species exists."""
    ids = pd.Series(identifier_series).fillna('').astype(str).str.strip()
    species = pd.Series(species_series).fillna('').astype(str).str.strip()
    labels = ids
    has_species = species != ''
    labels = labels.where(~has_species, ids + ' - ' + species)
    labels = labels.replace({'': 'Unknown'})
    return labels


def _build_delta_point_customdata(df, isotope_key):
    """Attach row/index metadata to chart points so clicks can edit source rows."""
    if df is None or df.empty:
        return None
    idx_vals = pd.Series(df.index, index=df.index).astype(str).to_numpy()
    id1_vals = df.get('Identifier 1', pd.Series(index=df.index, dtype=object)).fillna('').astype(str).to_numpy()
    id2_vals = df.get('Identifier 2', pd.Series(index=df.index, dtype=object)).fillna('').astype(str).to_numpy()
    iso_vals = np.full(len(df), str(isotope_key), dtype=object)
    return np.column_stack((idx_vals, iso_vals, id1_vals, id2_vals))


def _get_edited_row_tokens():
    """Return edited row labels as string tokens."""
    edited = st.session_state.get('edited_delta_rows', set())
    if isinstance(edited, set):
        return {str(x) for x in edited}
    if edited is None:
        return set()
    if isinstance(edited, (list, tuple, pd.Index, np.ndarray)):
        return {str(x) for x in edited}
    return {str(edited)}


def _is_row_edited(row_label):
    """Check whether a sample row has been edited in this session."""
    return str(row_label) in _get_edited_row_tokens()


def _get_selected_plotly_points(chart_state):
    """Return all selected Plotly points from a Streamlit chart event."""
    if chart_state is None:
        return []
    selection = None
    try:
        selection = chart_state.selection
    except Exception:
        if isinstance(chart_state, dict):
            selection = chart_state.get('selection')
    if selection is None:
        return []
    if isinstance(selection, dict):
        points = selection.get('points', []) or []
    else:
        points = getattr(selection, 'points', None) or []
    return [p for p in points if p is not None]


def _event_point_get(point, key, default=None):
    """Read a key from Plotly event point objects or plain dictionaries."""
    if isinstance(point, dict):
        return point.get(key, default)
    value = getattr(point, key, None)
    if value is not None:
        return value
    try:
        return point.get(key, default)
    except Exception:
        return default


def _token_from_customdata_payload(customdata):
    """Convert trace customdata payload to a canonical editor token."""
    if hasattr(customdata, 'tolist'):
        customdata = customdata.tolist()
    if not isinstance(customdata, (list, tuple, np.ndarray)) or len(customdata) < 2:
        return None
    isotope_key = str(customdata[1]).strip()
    raw_row_label = customdata[0]
    row_label = None
    try:
        if st.session_state.df is not None:
            row_label = _resolve_index_label(st.session_state.df.index, raw_row_label)
    except Exception:
        row_label = None
    row_key = str(row_label) if row_label is not None else str(raw_row_label)
    if isotope_key == '' or row_key == '':
        return None
    return f"{isotope_key}|{row_key}"


def _apply_editor_selection_to_figure(fig, editor_key_prefix):
    """Re-apply persisted point selection so multi-select can continue across reruns."""
    tokens_raw = st.session_state.get(f"{editor_key_prefix}_selected_tokens", [])
    if isinstance(tokens_raw, str):
        token_set = {tokens_raw}
    elif isinstance(tokens_raw, (list, tuple, set)):
        token_set = {str(t) for t in tokens_raw if str(t).strip() != ''}
    else:
        token_set = set()
    if not token_set:
        return

    for trace in getattr(fig, 'data', []):
        customdata = getattr(trace, 'customdata', None)
        if customdata is None:
            continue
        selected_idx = []
        try:
            for i, payload in enumerate(customdata):
                token = _token_from_customdata_payload(payload)
                if token in token_set:
                    selected_idx.append(i)
        except Exception:
            continue
        trace.selectedpoints = selected_idx if selected_idx else None


def _resolve_index_label(index_obj, raw_value):
    """Resolve selected-point row token back to a DataFrame index label."""
    candidates = [raw_value, str(raw_value)]
    try:
        as_int = int(float(raw_value))
        candidates.extend([as_int, str(as_int)])
    except Exception:
        pass
    for candidate in candidates:
        try:
            if candidate in index_obj:
                return candidate
        except Exception:
            continue
    return None


def _get_active_editor_target(editor_key_prefix):
    """Return currently navigated editor target from session state, if available."""
    if st.session_state.df is None:
        return None
    token = st.session_state.get(f"{editor_key_prefix}_nav_token")
    if not token or '|' not in str(token):
        return None
    isotope_key, raw_row_key = str(token).split('|', 1)
    isotope_key = str(isotope_key).strip()
    row_label = _resolve_index_label(st.session_state.df.index, raw_row_key)
    if row_label is None or row_label not in st.session_state.df.index:
        return None
    col_map = {
        'd13C': 'd 13C/12C  Mean',
        'd18O': 'd 18O/16O  Mean',
    }
    target_col = col_map.get(isotope_key)
    if target_col is None or target_col not in st.session_state.df.columns:
        return None
    value_raw = pd.to_numeric(pd.Series([st.session_state.df.at[row_label, target_col]]), errors='coerce').iloc[0]
    status_value = ""
    if 'Collector Status' in st.session_state.df.columns:
        raw_status = st.session_state.df.at[row_label, 'Collector Status']
        if pd.notna(raw_status) and str(raw_status).strip() != '':
            status_value = str(raw_status).strip()
    return {
        'isotope_key': isotope_key,
        'row_label': row_label,
        'target_col': target_col,
        'value': float(value_raw) if pd.notna(value_raw) else None,
        'collector_status': status_value,
    }


def _refresh_collector_status_after_delta_edit(row_label):
    """Keep collector status aligned after manual delta edits/interpolation."""
    if st.session_state.df is None or row_label not in st.session_state.df.index:
        return
    if 'Collector Status' not in st.session_state.df.columns:
        return
    if 'd 13C/12C  Mean' not in st.session_state.df.columns or 'd 18O/16O  Mean' not in st.session_state.df.columns:
        return

    status_raw = st.session_state.df.at[row_label, 'Collector Status']
    status = '' if pd.isna(status_raw) else str(status_raw).strip()
    # Respect fully saturated classification produced by cycle diagnostics.
    if status == 'Fully Saturated Collectors':
        return

    d13 = pd.to_numeric(pd.Series([st.session_state.df.at[row_label, 'd 13C/12C  Mean']]), errors='coerce').iloc[0]
    d18 = pd.to_numeric(pd.Series([st.session_state.df.at[row_label, 'd 18O/16O  Mean']]), errors='coerce').iloc[0]
    has_d13 = bool(pd.notna(d13))
    has_d18 = bool(pd.notna(d18))

    if not has_d13 and not has_d18:
        st.session_state.df.at[row_label, 'Collector Status'] = 'Failed Sample'
    elif status == 'Failed Sample':
        st.session_state.df.at[row_label, 'Collector Status'] = 'Partially Saturated Collectors'


def _get_isotope_columns(isotope_key):
    """Return raw/calibrated/corrected column names for an isotope key."""
    key = str(isotope_key).strip()
    if key == 'd13C':
        return ('d 13C/12C  Mean', 'd13C_calibrated', 'd13C_calibrated_linearity_corrected')
    if key == 'd18O':
        return ('d 18O/16O  Mean', 'd18O_calibrated', 'd18O_calibrated_linearity_corrected')
    return (None, None, None)


def _estimate_calibration_affine(raw_col, cal_col, exclude_row_label=None):
    """Estimate affine calibration y = m*x + b from existing dataset columns."""
    if st.session_state.df is None or raw_col not in st.session_state.df.columns or cal_col not in st.session_state.df.columns:
        return (None, None)
    raw_series = pd.to_numeric(st.session_state.df[raw_col], errors='coerce')
    cal_series = pd.to_numeric(st.session_state.df[cal_col], errors='coerce')
    valid = raw_series.notna() & cal_series.notna()
    if exclude_row_label is not None and exclude_row_label in valid.index:
        valid.loc[exclude_row_label] = False
    x = raw_series[valid]
    y = cal_series[valid]
    if len(x) >= 2:
        x_vals = x.to_numpy(dtype=float)
        y_vals = y.to_numpy(dtype=float)
        if np.nanstd(x_vals) > 0:
            m, b = np.polyfit(x_vals, y_vals, 1)
            if np.isfinite(m) and np.isfinite(b):
                return (float(m), float(b))
    if len(x) == 1:
        xv = float(x.iloc[0])
        yv = float(y.iloc[0])
        if np.isfinite(xv) and np.isfinite(yv):
            return (1.0, yv - xv)
    return (None, None)


def _refresh_calibrated_after_delta_edit(row_label, isotope_key, previous_raw=None, previous_calibrated=None):
    """Update calibrated values for an edited row when calibration columns are present."""
    raw_col, cal_col, corrected_col = _get_isotope_columns(isotope_key)
    if st.session_state.df is None or raw_col is None or cal_col is None:
        return
    if row_label not in st.session_state.df.index:
        return
    if raw_col not in st.session_state.df.columns or cal_col not in st.session_state.df.columns:
        return

    new_raw = pd.to_numeric(pd.Series([st.session_state.df.at[row_label, raw_col]]), errors='coerce').iloc[0]
    if pd.isna(new_raw):
        st.session_state.df.at[row_label, cal_col] = np.nan
        if corrected_col in st.session_state.df.columns:
            st.session_state.df.at[row_label, corrected_col] = np.nan
        return
    new_raw = float(new_raw)

    m = None
    b = None
    coeffs = st.session_state.get('calibration_coefficients')
    if isinstance(coeffs, dict):
        iso_coeff = coeffs.get(str(isotope_key).strip(), {})
        m_c = pd.to_numeric(pd.Series([iso_coeff.get('slope')]), errors='coerce').iloc[0]
        b_c = pd.to_numeric(pd.Series([iso_coeff.get('intercept')]), errors='coerce').iloc[0]
        if np.isfinite(m_c) and np.isfinite(b_c):
            m = float(m_c)
            b = float(b_c)
    if m is None or b is None:
        m, b = _estimate_calibration_affine(raw_col, cal_col, exclude_row_label=row_label)

    if m is not None and b is not None and np.isfinite(m) and np.isfinite(b):
        new_cal = m * new_raw + b
    else:
        prev_raw_num = pd.to_numeric(pd.Series([previous_raw]), errors='coerce').iloc[0]
        prev_cal_num = pd.to_numeric(pd.Series([previous_calibrated]), errors='coerce').iloc[0]
        if np.isfinite(prev_raw_num) and np.isfinite(prev_cal_num):
            new_cal = float(prev_cal_num) + (new_raw - float(prev_raw_num))
        else:
            return

    if np.isfinite(new_cal):
        st.session_state.df.at[row_label, cal_col] = float(new_cal)
    else:
        st.session_state.df.at[row_label, cal_col] = np.nan
        if corrected_col in st.session_state.df.columns:
            st.session_state.df.at[row_label, corrected_col] = np.nan
        return

    if corrected_col in st.session_state.df.columns:
        fits = st.session_state.get('linearity_fits', {})
        fit = fits.get(str(isotope_key).strip(), {}) if isinstance(fits, dict) else {}
        slope_lin = pd.to_numeric(pd.Series([fit.get('slope')]), errors='coerce').iloc[0]
        x_ref = pd.to_numeric(pd.Series([fit.get('x_ref')]), errors='coerce').iloc[0]
        intensity = np.nan
        if '1  Cycle Int  Samp  44' in st.session_state.df.columns:
            intensity = pd.to_numeric(pd.Series([st.session_state.df.at[row_label, '1  Cycle Int  Samp  44']]), errors='coerce').iloc[0]
        if np.isfinite(slope_lin) and np.isfinite(x_ref) and np.isfinite(intensity):
            st.session_state.df.at[row_label, corrected_col] = float(new_cal - float(slope_lin) * (float(intensity) - float(x_ref)))
        else:
            st.session_state.df.at[row_label, corrected_col] = float(new_cal)


def _augment_curve_with_edited_rows(base_curve_df, species_data_unfiltered, identifier):
    """Ensure edited points (including edited outliers/failed rows) appear in curve traces."""
    base = base_curve_df.copy() if base_curve_df is not None else pd.DataFrame()
    if species_data_unfiltered is None or species_data_unfiltered.empty or 'Identifier 1' not in species_data_unfiltered.columns:
        return base.sort_values(by='x_axis', na_position='last') if 'x_axis' in base.columns else base
    edited_mask = pd.Series(species_data_unfiltered.index.map(_is_row_edited), index=species_data_unfiltered.index, dtype=bool)
    extra = species_data_unfiltered[
        species_data_unfiltered['Identifier 1'].astype(str).str.strip().eq(str(identifier).strip()) & edited_mask
    ].copy()
    if extra.empty:
        return base.sort_values(by='x_axis', na_position='last') if 'x_axis' in base.columns else base
    if base.empty:
        merged = extra
    else:
        merged = pd.concat([base, extra], axis=0, sort=False)
    try:
        merged = merged[~merged.index.duplicated(keep='last')]
    except Exception:
        pass
    if 'x_axis' in merged.columns:
        merged = merged.sort_values(by='x_axis', na_position='last')
    return merged


def _extract_mass_from_intensity_column(col_name):
    """Extract mass number (44/45/46) from an intensity column name."""
    low = _normalize_column_key(col_name)
    m = re.search(r'(?<!\d)(44|45|46)(?:\.0+)?(?!\d)', low)
    return int(m.group(1)) if m else None


def _pick_cycle_value_column(df, primary_col, patterns):
    """Pick a cycle-level isotope value column, preferring explicit cycle columns."""
    if df is None or df.empty:
        return None
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = _normalize_column_key(col)
        if 'standard' in low:
            continue
        if any(term in low for term in ('std', 'sd', 'se')):
            continue
        if 'mean' in low:
            continue
        if any(re.search(pat, low) for pat in patterns):
            vals = pd.to_numeric(df[col], errors='coerce')
            if vals.notna().any():
                return col
    if primary_col in df.columns:
        vals = pd.to_numeric(df[primary_col], errors='coerce')
        if vals.notna().any():
            return primary_col
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = _normalize_column_key(col)
        if 'standard' in low:
            continue
        if any(term in low for term in ('std', 'sd', 'se')):
            continue
        if any(re.search(pat, low) for pat in patterns):
            vals = pd.to_numeric(df[col], errors='coerce')
            if vals.notna().any():
                return col
    return primary_col if primary_col in df.columns else None


def _build_saturation_mask_from_intensity_df(intensity_df, required_masses, threshold=48.0):
    """Build a per-cycle saturation mask for required masses."""
    if intensity_df is None or intensity_df.empty:
        return pd.Series(False, index=pd.Index([], dtype=int), dtype=bool)
    sat_mask = pd.Series(False, index=intensity_df.index, dtype=bool)
    has_mass_cols = False
    for mass in required_masses:
        mass_cols = [c for c in intensity_df.columns if _extract_mass_from_intensity_column(c) == mass]
        if not mass_cols:
            continue
        has_mass_cols = True
        mass_sat = (intensity_df[mass_cols] > float(threshold)).any(axis=1)
        sat_mask = sat_mask | mass_sat
    if not has_mass_cols:
        return pd.Series(False, index=intensity_df.index, dtype=bool)
    return sat_mask


def _get_cycles_for_selected_point(row_label, target_col):
    """Find cycle rows corresponding to a selected processed sample row."""
    raw_df = st.session_state.get('df_cycles_source')
    if raw_df is None or raw_df.empty or 'Cycle Number' not in raw_df.columns:
        return None, None
    if st.session_state.df is None or row_label not in st.session_state.df.index:
        return None, None

    processed_row = st.session_state.df.loc[row_label]
    if isinstance(processed_row, pd.DataFrame):
        processed_row = processed_row.iloc[0]

    work = raw_df.copy()
    cycle_order = work['Cycle Number'].apply(_extract_cycle_order)
    is_pre = work['Cycle Number'].astype(str).str.strip().str.lower().eq('pre')
    cycle_order = cycle_order.where(~is_pre, 0)
    work['_cycle_order'] = cycle_order
    group_id = is_pre.cumsum()
    group_id = group_id.where(is_pre | cycle_order.notna(), np.nan)
    work['_cycle_group'] = group_id

    id_cols = [
        'Identifier 1', 'Identifier 2', 'Label', 'Species', 'Comment', 'Run ID',
        'Line', 'Date', 'Date_ordinal', 'Sample Type', 'Reference', 'Excel File'
    ]
    for col in id_cols:
        if col in work.columns:
            work[col] = work.groupby('_cycle_group')[col].ffill()

    pre_rows = work[is_pre].copy()
    if pre_rows.empty:
        return None, None

    def _value_present(v):
        if v is None:
            return False
        if isinstance(v, float) and np.isnan(v):
            return False
        return str(v).strip() != ''

    candidates = pre_rows.copy()
    for col in ['Excel File', 'Identifier 1', 'Identifier 2']:
        if col not in candidates.columns:
            continue
        val = processed_row[col] if col in processed_row.index else None
        if not _value_present(val):
            continue
        mask = candidates[col].astype(str).str.strip().eq(str(val).strip())
        if mask.any():
            candidates = candidates[mask]
    if candidates.empty:
        return None, None

    for col in ['Run ID', 'Line', 'Date']:
        if col not in candidates.columns:
            continue
        val = processed_row[col] if col in processed_row.index else None
        if not _value_present(val):
            continue
        if col == 'Date':
            p_date = pd.to_datetime(val, errors='coerce')
            if pd.notna(p_date):
                c_dates = pd.to_datetime(candidates[col], errors='coerce')
                mask = c_dates.eq(p_date)
                if mask.any():
                    candidates = candidates[mask]
        else:
            mask = candidates[col].astype(str).str.strip().eq(str(val).strip())
            if mask.any():
                candidates = candidates[mask]
    if candidates.empty:
        return None, None

    selected_pre = None
    if len(candidates) == 1:
        selected_pre = candidates.iloc[0]
    else:
        cand_vals = pd.to_numeric(candidates.get(target_col), errors='coerce')
        proc_val = pd.to_numeric(pd.Series([processed_row.get(target_col)]), errors='coerce').iloc[0]
        if pd.notna(proc_val) and cand_vals.notna().any():
            selected_pre = candidates.loc[(cand_vals - float(proc_val)).abs().idxmin()]
        else:
            selected_pre = candidates.iloc[0]

    group = selected_pre.get('_cycle_group')
    if pd.isna(group):
        return None, None
    cycles = work[(work['_cycle_group'] == group) & (work['_cycle_order'] > 0)].copy()
    if cycles.empty:
        return None, None
    cycles = cycles.sort_values('_cycle_order')
    return cycles, selected_pre


def _build_selected_point_diagnostics_inline(target, pre_row=None):
    """Build fixed diagnostics text for the selected datapoint as a single inline line."""
    if st.session_state.df is None:
        return ""
    if target['row_label'] not in st.session_state.df.index:
        return ""

    processed_row = st.session_state.df.loc[target['row_label']]
    if isinstance(processed_row, pd.DataFrame):
        processed_row = processed_row.iloc[0]

    row_sources = []
    if isinstance(processed_row, pd.Series):
        row_sources.append(processed_row)
    if isinstance(pre_row, pd.Series):
        row_sources.append(pre_row)
    if not row_sources:
        return ""

    field_map = [
        ('Line', ['Line']),
        ('Signal Intensity', ['1  Cycle Int  Samp  44']),
        ('d18O values', ['d 18O/16O  Mean']),
        ('d13C values', ['d 13C/12C  Mean']),
        ('Leak Rate', ['leak_rate']),
        ('Total CO2', ['total_co2']),
        ('P gasses', ['p_gases']),
        ('P no acid', ['p_no_acid']),
        ('Date', ['Date', 'Date_ordinal']),
    ]

    def _has_value(value):
        if value is None:
            return False
        if isinstance(value, (float, np.floating)) and np.isnan(value):
            return False
        try:
            if pd.isna(value):
                return False
        except Exception:
            pass
        return str(value).strip() != ''

    def _get_value_from_series(src, candidates):
        if not isinstance(src, pd.Series):
            return None, None
        src_norm_map = {_normalize_column_key(col): col for col in src.index}
        for cand in candidates:
            if cand in src.index and _has_value(src[cand]):
                return src[cand], cand
        for cand in candidates:
            col = src_norm_map.get(_normalize_column_key(cand))
            if col is not None and _has_value(src[col]):
                return src[col], col
        return None, None

    def _format_value(label, value, source_col):
        if not _has_value(value):
            return "N/A"
        if label == 'Date':
            parsed = pd.to_datetime(value, errors='coerce')
            if pd.notna(parsed):
                return parsed.strftime('%Y-%m-%d')
            if _normalize_column_key(source_col) == 'date_ordinal':
                try:
                    parsed_ord = pd.Timestamp.fromordinal(int(float(value)))
                    return parsed_ord.strftime('%Y-%m-%d')
                except Exception:
                    pass
            return str(value)
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        if isinstance(value, (float, np.floating)):
            if np.isfinite(value):
                if label == 'Line' and float(value).is_integer():
                    return str(int(value))
                return f"{float(value):.4f}"
            return str(value)
        return str(value)

    parts = []
    for label, candidates in field_map:
        selected_value = None
        selected_col = None
        for src in row_sources:
            value, col = _get_value_from_series(src, candidates)
            if _has_value(value):
                selected_value = value
                selected_col = col
                break
        display_value = _format_value(label, selected_value, selected_col)
        parts.append(f"**{label}:** `{display_value}`")

    return " | ".join(parts)


def _render_selected_point_cycle_diagnostics(target, key_prefix):
    """Render cycle-level chart and table for the selected sample point."""
    cycles, pre_row = _get_cycles_for_selected_point(target['row_label'], target['target_col'])
    diagnostics_line = _build_selected_point_diagnostics_inline(target, pre_row=pre_row)
    if diagnostics_line:
        st.markdown(diagnostics_line)

    if cycles is None or cycles.empty:
        st.markdown("Cycle-level diagnostics are unavailable for this datapoint.")
        return

    st.markdown("##### Cycle Diagnostics")

    intensity_cols = _find_cycle_intensity_columns(cycles)
    intensity_cols = [c for c in intensity_cols if c in cycles.columns]
    intensity_entries = []
    for col in intensity_cols:
        vals = _normalize_signal_intensity(cycles[col])
        if vals.notna().sum() == 0:
            continue
        mass = _extract_mass_from_intensity_column(col)
        if mass not in {44, 45, 46}:
            continue
        low = _normalize_column_key(col)
        role = 'UNK'
        if 'standard' in low or re.search(r'\bstd\b', low) or re.search(r'\bref\b', low):
            role = 'STD'
        elif 'sample' in low or 'samp' in low or re.search(r'\bsmp\b', low):
            role = 'SMP'
        median_val = float(vals.median(skipna=True)) if vals.notna().any() else -np.inf
        intensity_entries.append({'col': col, 'mass': mass, 'role': role, 'median': median_val})

    mass_roles = {44: {'SMP': None, 'STD': None}, 45: {'SMP': None, 'STD': None}, 46: {'SMP': None, 'STD': None}}
    for mass in [44, 45, 46]:
        entries = [e for e in intensity_entries if e['mass'] == mass]
        if not entries:
            continue
        smp = [e for e in entries if e['role'] == 'SMP']
        std = [e for e in entries if e['role'] == 'STD']
        if smp:
            mass_roles[mass]['SMP'] = sorted(smp, key=lambda e: e['median'], reverse=True)[0]['col']
        if std:
            mass_roles[mass]['STD'] = sorted(std, key=lambda e: e['median'])[0]['col']
        if mass_roles[mass]['SMP'] is None or mass_roles[mass]['STD'] is None:
            sorted_entries = sorted(entries, key=lambda e: e['median'], reverse=True)
            if mass_roles[mass]['SMP'] is None and sorted_entries:
                mass_roles[mass]['SMP'] = sorted_entries[0]['col']
            if mass_roles[mass]['STD'] is None and len(sorted_entries) > 1:
                mass_roles[mass]['STD'] = sorted_entries[-1]['col']

    x_cycles = pd.to_numeric(cycles['_cycle_order'], errors='coerce')
    fig = go.Figure()
    mass_colors = {44: '#E67E22', 45: '#1E7D2B', 46: '#D4A017'}
    for mass in [44, 45, 46]:
        color = mass_colors[mass]
        smp_col = mass_roles[mass]['SMP']
        std_col = mass_roles[mass]['STD']
        if smp_col is not None:
            fig.add_trace(go.Scatter(
                x=x_cycles,
                y=_normalize_signal_intensity(cycles[smp_col]),
                mode='lines+markers',
                name=f'{mass:.2f} m/z SMP',
                line=dict(color=color, width=2, dash='solid'),
                marker=dict(size=6)
            ))
        if std_col is not None:
            fig.add_trace(go.Scatter(
                x=x_cycles,
                y=_normalize_signal_intensity(cycles[std_col]),
                mode='lines+markers',
                name=f'{mass:.2f} m/z STD',
                line=dict(color=color, width=2, dash='dash'),
                marker=dict(size=6)
            ))

    d13_col = _pick_cycle_value_column(cycles, 'd 13C/12C  Mean', [r'd13', r'd ?13c', r'd45co2', r'\bd45\b'])
    d18_col = _pick_cycle_value_column(cycles, 'd 18O/16O  Mean', [r'd18', r'd ?18o', r'd46co2', r'\bd46\b'])

    cycle_table = pd.DataFrame({
        'Cycle': pd.to_numeric(cycles['_cycle_order'], errors='coerce').astype('Int64')
    }, index=cycles.index)
    if d13_col and d13_col in cycles.columns:
        cycle_table['d13C'] = pd.to_numeric(cycles[d13_col], errors='coerce')
    if d18_col and d18_col in cycles.columns:
        cycle_table['d18O'] = pd.to_numeric(cycles[d18_col], errors='coerce')

    intensity_for_mask = pd.DataFrame(index=cycles.index)
    for col in intensity_cols:
        if col in cycles.columns:
            intensity_for_mask[col] = _normalize_signal_intensity(cycles[col])
    sat_d13 = _build_saturation_mask_from_intensity_df(intensity_for_mask, [44, 45]).reindex(cycles.index, fill_value=False)
    sat_d18 = _build_saturation_mask_from_intensity_df(intensity_for_mask, [44, 45, 46]).reindex(cycles.index, fill_value=False)
    cycle_table['Excluded d13C'] = sat_d13.to_numpy(dtype=bool)
    cycle_table['Excluded d18O'] = sat_d18.to_numpy(dtype=bool)
    cycle_table['Excluded (Saturation)'] = cycle_table['Excluded d13C'] | cycle_table['Excluded d18O']

    def _highlight_excluded(row):
        color = "background-color: rgba(220, 53, 69, 0.18);"
        return [color if bool(row.get('Excluded (Saturation)', False)) else "" for _ in row]

    diag_col_chart, diag_col_table = st.columns([3, 2], gap="medium")
    with diag_col_chart:
        if len(fig.data) > 0:
            fig.update_layout(
                title="Cycle Intensities (Sample vs Reference Gas)",
                xaxis_title="Cycles",
                yaxis_title="Intensity (V)",
                height=460,
                margin=dict(l=20, r=20, t=40, b=20),
                legend=dict(orientation='h', yanchor='top', y=-0.25, x=0.0)
            )
            st.plotly_chart(
                fig,
                width='stretch',
                key=f"cycle_diag_fig_{key_prefix}_{target['isotope_key']}_{target['row_key']}"
            )
        else:
            st.markdown("No cycle intensity columns were detected for this datapoint.")

    with diag_col_table:
        if isinstance(pre_row, pd.Series):
            status_val = pre_row.get('Collector Status')
            if pd.notna(status_val) and str(status_val).strip() != '':
                st.markdown(f"**Collector Status:** `{status_val}`")
        st.dataframe(
            cycle_table.reset_index(drop=True).style.apply(_highlight_excluded, axis=1),
            hide_index=True,
            width='stretch'
        )

def _render_delta_editor_from_chart_selection(chart_state, editor_key_prefix):
    """Render a delta-value editor when the user selects a valid chart point."""
    col_map = {
        'd13C': 'd 13C/12C  Mean',
        'd18O': 'd 18O/16O  Mean',
    }
    if st.session_state.df is None:
        return

    chart_nonce_key = f"{editor_key_prefix}_chart_nonce"
    nav_token_key = f"{editor_key_prefix}_nav_token"
    selected_tokens_key = f"{editor_key_prefix}_selected_tokens"

    def _build_target(isotope_key, row_label, identifier_1=None, identifier_2=None):
        target_col = col_map.get(str(isotope_key).strip())
        if target_col is None or target_col not in st.session_state.df.columns:
            return None
        if row_label not in st.session_state.df.index:
            return None
        status_value = ""
        if 'Collector Status' in st.session_state.df.columns:
            raw_status = st.session_state.df.at[row_label, 'Collector Status']
            if pd.notna(raw_status) and str(raw_status).strip() != '':
                status_value = str(raw_status).strip()
        source_excel = "Unknown"
        if 'Excel File' in st.session_state.df.columns:
            source_val = st.session_state.df.at[row_label, 'Excel File']
            if pd.notna(source_val) and str(source_val).strip() != '':
                source_excel = str(source_val).strip()
        if identifier_1 is None and 'Identifier 1' in st.session_state.df.columns:
            identifier_1 = st.session_state.df.at[row_label, 'Identifier 1']
        if identifier_2 is None and 'Identifier 2' in st.session_state.df.columns:
            identifier_2 = st.session_state.df.at[row_label, 'Identifier 2']
        current_value_raw = pd.to_numeric(pd.Series([st.session_state.df.at[row_label, target_col]]), errors='coerce').iloc[0]
        has_value = pd.notna(current_value_raw)
        current_value = float(current_value_raw) if has_value else 0.0
        original_map = st.session_state.get('original_delta_values', {})
        original_key = f"{str(isotope_key).strip()}|{str(row_label)}"
        original_value_raw = original_map.get(original_key, current_value_raw if has_value else np.nan)
        original_value_num = pd.to_numeric(pd.Series([original_value_raw]), errors='coerce').iloc[0]
        original_value = float(original_value_num) if pd.notna(original_value_num) else None
        return {
            'row_label': row_label,
            'row_key': str(row_label),
            'isotope_key': str(isotope_key).strip(),
            'identifier_1': '' if identifier_1 is None or pd.isna(identifier_1) else str(identifier_1),
            'identifier_2': '' if identifier_2 is None or pd.isna(identifier_2) else str(identifier_2),
            'target_col': target_col,
            'source_excel': source_excel,
            'collector_status': status_value,
            'is_failed_sample': status_value == 'Failed Sample',
            'has_value': bool(has_value),
            'current_value': current_value,
            'original_value': original_value,
        }

    def _token_for_target(target):
        return f"{target['isotope_key']}|{target['row_key']}"

    def _target_from_token(token):
        if not token or '|' not in str(token):
            return None
        isotope_key, raw_row_key = str(token).split('|', 1)
        row_label = _resolve_index_label(st.session_state.df.index, raw_row_key)
        if row_label is None:
            return None
        return _build_target(isotope_key, row_label)

    def _restore_targets_from_tokens(tokens):
        restored = []
        seen = set()
        if isinstance(tokens, str):
            tokens = [tokens]
        if not isinstance(tokens, (list, tuple, set)):
            return restored
        for token in tokens:
            built = _target_from_token(token)
            if built is None:
                continue
            token_val = _token_for_target(built)
            if token_val in seen:
                continue
            seen.add(token_val)
            restored.append(built)
        return restored

    def _build_navigation_targets(active_target):
        target_col = active_target['target_col']
        id1 = active_target.get('identifier_1', '')
        base = st.session_state.df.copy()
        if 'Identifier 1' in base.columns and str(id1).strip() != '':
            id_mask = base['Identifier 1'].astype(str).str.strip().eq(str(id1).strip())
            if id_mask.any():
                base = base[id_mask].copy()
        if base.empty:
            return [active_target]
        active_row = active_target['row_label']
        if active_row not in base.index and active_row in st.session_state.df.index:
            base = pd.concat([base, st.session_state.df.loc[[active_row]]], axis=0)
        y_vals = pd.to_numeric(base[target_col], errors='coerce')
        if y_vals.notna().any():
            keep_mask = y_vals.notna() | (base.index == active_row)
            base = base.loc[keep_mask].copy()
        if base.empty:
            return [active_target]

        sort_rows = []
        for idx, row in base.iterrows():
            id2_val = row.get('Identifier 2', None)
            id2_num = _parse_numeric_token(id2_val)
            key = (
                id2_num is None,
                float(id2_num) if id2_num is not None else float('inf'),
                '' if id2_val is None or pd.isna(id2_val) else str(id2_val),
                int(idx) if isinstance(idx, (int, np.integer)) else str(idx),
            )
            sort_rows.append((key, idx, row))
        sort_rows.sort(key=lambda x: x[0])

        nav_targets = []
        for _, idx, row in sort_rows:
            built = _build_target(
                active_target['isotope_key'],
                idx,
                identifier_1=row.get('Identifier 1', None),
                identifier_2=row.get('Identifier 2', None),
            )
            if built is not None:
                nav_targets.append(built)
        return nav_targets if nav_targets else [active_target]

    def _find_interpolation_neighbors(active_target):
        target_col = active_target['target_col']
        id1 = active_target.get('identifier_1', '')
        base = st.session_state.df.copy()
        if 'Identifier 1' in base.columns and str(id1).strip() != '':
            id_mask = base['Identifier 1'].astype(str).str.strip().eq(str(id1).strip())
            if id_mask.any():
                base = base[id_mask].copy()
        if base.empty or active_target['row_label'] not in base.index:
            return None, None

        sort_rows = []
        for idx, row in base.iterrows():
            id2_val = row.get('Identifier 2', None)
            id2_num = _parse_numeric_token(id2_val)
            key = (
                id2_num is None,
                float(id2_num) if id2_num is not None else float('inf'),
                '' if id2_val is None or pd.isna(id2_val) else str(id2_val),
                int(idx) if isinstance(idx, (int, np.integer)) else str(idx),
            )
            sort_rows.append((key, idx, row))
        sort_rows.sort(key=lambda x: x[0])
        ordered_rows = [idx for _, idx, _ in sort_rows]
        try:
            anchor_idx = ordered_rows.index(active_target['row_label'])
        except ValueError:
            return None, None

        # Interpolation neighbors should ignore rows currently considered outliers.
        status_series = base.get('Collector Status', pd.Series('', index=base.index)).astype(str).str.strip()
        status_excluded = status_series.isin({
            'Failed Sample',
            'Fully Saturated Collectors',
            'Partially Saturated Collectors',
        })

        range_excluded = pd.Series(False, index=base.index, dtype=bool)
        try:
            if 'd 13C/12C  Mean' in base.columns:
                d13_vals = pd.to_numeric(base['d 13C/12C  Mean'], errors='coerce')
                range_excluded = range_excluded | (
                    d13_vals.notna() &
                    ((d13_vals < float(st.session_state.d13c_range[0])) | (d13_vals > float(st.session_state.d13c_range[1])))
                )
            if 'd 18O/16O  Mean' in base.columns:
                d18_vals = pd.to_numeric(base['d 18O/16O  Mean'], errors='coerce')
                range_excluded = range_excluded | (
                    d18_vals.notna() &
                    ((d18_vals < float(st.session_state.d18o_range[0])) | (d18_vals > float(st.session_state.d18o_range[1])))
                )
            if '1  Cycle Int  Samp  44' in base.columns:
                sig_vals = pd.to_numeric(base['1  Cycle Int  Samp  44'], errors='coerce')
                range_excluded = range_excluded | (
                    sig_vals.notna() &
                    ((sig_vals < float(st.session_state.signal_range[0])) | (sig_vals > float(st.session_state.signal_range[1])))
                )
            if 'leak_rate' in base.columns:
                leak_vals = pd.to_numeric(base['leak_rate'], errors='coerce')
                range_excluded = range_excluded | (
                    leak_vals.notna() &
                    ((leak_vals < float(st.session_state.leak_range[0])) | (leak_vals > float(st.session_state.leak_range[1])))
                )
        except Exception:
            pass

        sigma_excluded = pd.Series(False, index=base.index, dtype=bool)
        sigma_level = float(st.session_state.get('sigma_level_data', 4.0))
        if sigma_level > 0:
            if 'd 13C/12C  Mean' in base.columns:
                d13_vals = pd.to_numeric(base['d 13C/12C  Mean'], errors='coerce')
                mean_d13 = d13_vals.mean(skipna=True)
                std_d13 = d13_vals.std(skipna=True)
                if np.isfinite(std_d13) and std_d13 > 0 and np.isfinite(mean_d13):
                    sigma_excluded = sigma_excluded | (
                        d13_vals.notna() &
                        ((d13_vals < (mean_d13 - sigma_level * std_d13)) | (d13_vals > (mean_d13 + sigma_level * std_d13)))
                    )
            if 'd 18O/16O  Mean' in base.columns:
                d18_vals = pd.to_numeric(base['d 18O/16O  Mean'], errors='coerce')
                mean_d18 = d18_vals.mean(skipna=True)
                std_d18 = d18_vals.std(skipna=True)
                if np.isfinite(std_d18) and std_d18 > 0 and np.isfinite(mean_d18):
                    sigma_excluded = sigma_excluded | (
                        d18_vals.notna() &
                        ((d18_vals < (mean_d18 - sigma_level * std_d18)) | (d18_vals > (mean_d18 + sigma_level * std_d18)))
                    )

        candidate_mask = ~(status_excluded | range_excluded | sigma_excluded)

        prev_neighbor = None
        for i in range(anchor_idx - 1, -1, -1):
            idx = ordered_rows[i]
            if idx not in candidate_mask.index or not bool(candidate_mask.loc[idx]):
                continue
            val = pd.to_numeric(pd.Series([base.at[idx, target_col]]), errors='coerce').iloc[0]
            if pd.notna(val):
                id2_val = base.at[idx, 'Identifier 2'] if 'Identifier 2' in base.columns else ''
                prev_neighbor = {
                    'row_label': idx,
                    'identifier_2': '' if pd.isna(id2_val) else str(id2_val),
                    'value': float(val),
                }
                break

        next_neighbor = None
        for i in range(anchor_idx + 1, len(ordered_rows)):
            idx = ordered_rows[i]
            if idx not in candidate_mask.index or not bool(candidate_mask.loc[idx]):
                continue
            val = pd.to_numeric(pd.Series([base.at[idx, target_col]]), errors='coerce').iloc[0]
            if pd.notna(val):
                id2_val = base.at[idx, 'Identifier 2'] if 'Identifier 2' in base.columns else ''
                next_neighbor = {
                    'row_label': idx,
                    'identifier_2': '' if pd.isna(id2_val) else str(id2_val),
                    'value': float(val),
                }
                break

        return prev_neighbor, next_neighbor

    points = _get_selected_plotly_points(chart_state)
    selected_targets = []
    seen_targets = set()
    persisted_targets = _restore_targets_from_tokens(st.session_state.get(selected_tokens_key, []))
    persisted_tokens = {_token_for_target(t) for t in persisted_targets}
    for point in points:
        customdata = _event_point_get(point, 'customdata')
        if hasattr(customdata, 'tolist'):
            customdata = customdata.tolist()
        if not isinstance(customdata, (list, tuple, np.ndarray)) or len(customdata) < 4:
            continue
        raw_row_label = customdata[0]
        isotope_key = str(customdata[1]).strip()
        identifier_1 = str(customdata[2]).strip()
        identifier_2 = str(customdata[3]).strip()
        row_label = _resolve_index_label(st.session_state.df.index, raw_row_label)
        if row_label is None:
            continue
        target_token = (isotope_key, str(row_label))
        if target_token in seen_targets:
            continue
        built = _build_target(isotope_key, row_label, identifier_1=identifier_1, identifier_2=identifier_2)
        if built is None:
            continue
        seen_targets.add(target_token)
        selected_targets.append(built)

    if selected_targets:
        # In Streamlit rerun flow, shift+click can arrive as only the newest point.
        # Merge with previous persisted selection so additive multi-select remains usable.
        if len(selected_targets) == 1 and persisted_targets:
            merged = persisted_targets[:]
            merged_tokens = set(persisted_tokens)
            for t in selected_targets:
                tok = _token_for_target(t)
                if tok not in merged_tokens:
                    merged.append(t)
                    merged_tokens.add(tok)
            selected_targets = merged
        st.session_state[selected_tokens_key] = [_token_for_target(t) for t in selected_targets]

    if len(selected_targets) == 1:
        st.session_state[nav_token_key] = _token_for_target(selected_targets[0])
    elif len(selected_targets) == 0:
        restored_targets = persisted_targets
        if restored_targets:
            selected_targets = restored_targets
        else:
            nav_target = _target_from_token(st.session_state.get(nav_token_key))
            if nav_target is not None:
                selected_targets = [nav_target]
            else:
                st.session_state.pop(selected_tokens_key, None)

    if not selected_targets:
        st.markdown("Select a primary data marker to edit its delta value or apply an offset.")
        return

    single_mode = len(selected_targets) == 1
    nav_targets = []
    nav_index = -1
    if single_mode:
        nav_targets = _build_navigation_targets(selected_targets[0])
        current_token = _token_for_target(selected_targets[0])
        for i, t in enumerate(nav_targets):
            if _token_for_target(t) == current_token:
                nav_index = i
                break
        if nav_index < 0 and nav_targets:
            nav_index = 0
            selected_targets = [nav_targets[0]]
            st.session_state[nav_token_key] = _token_for_target(nav_targets[0])
            st.session_state[selected_tokens_key] = [st.session_state[nav_token_key]]

    selection_signature = '|'.join(sorted([f"{t['isotope_key']}:{t['row_key']}" for t in selected_targets]))
    selection_hash = hashlib.md5(selection_signature.encode('utf-8')).hexdigest()[:10]

    header_col, spacer_col, prev_col, next_col, close_col = st.columns([8, 8, 2, 2, 1], gap="small")
    with header_col:
        st.markdown("#### Edit Selected Delta Value")
    with spacer_col:
        st.empty()

    prev_disabled = not single_mode or nav_index <= 0
    next_disabled = not single_mode or nav_index < 0 or nav_index >= (len(nav_targets) - 1)
    with prev_col:
        if st.button("Prev <", key=f"{editor_key_prefix}_prev", help="Previous datapoint", disabled=prev_disabled, use_container_width=True):
            prev_target = nav_targets[nav_index - 1]
            st.session_state[nav_token_key] = _token_for_target(prev_target)
            st.session_state[selected_tokens_key] = [st.session_state[nav_token_key]]
            st.session_state[chart_nonce_key] = int(st.session_state.get(chart_nonce_key, 0)) + 1
            st.rerun()
    with next_col:
        if st.button("Next >", key=f"{editor_key_prefix}_next", help="Next datapoint", disabled=next_disabled, use_container_width=True):
            next_target = nav_targets[nav_index + 1]
            st.session_state[nav_token_key] = _token_for_target(next_target)
            st.session_state[selected_tokens_key] = [st.session_state[nav_token_key]]
            st.session_state[chart_nonce_key] = int(st.session_state.get(chart_nonce_key, 0)) + 1
            st.rerun()
    with close_col:
        if st.button("X", key=f"{editor_key_prefix}_close", help="Exit edit mode", type="secondary"):
            st.session_state.pop(nav_token_key, None)
            st.session_state.pop(selected_tokens_key, None)
            st.session_state[chart_nonce_key] = int(st.session_state.get(chart_nonce_key, 0)) + 1
            st.rerun()

    submitted_offset = False
    submitted_interpolate = False
    interpolation_plan = []
    if single_mode:
        target = selected_targets[0]
        is_failed_sample = bool(target.get('is_failed_sample', False))
        original_display = "N/A" if target['original_value'] is None else f"{target['original_value']:.4f}"
        st.markdown(
            f"**Identifier 1:** `{target['identifier_1']}` | **Identifier 2:** `{target['identifier_2']}` | "
            f"**Origin Excel File:** `{target['source_excel']}` | "
            f"**Original {target['isotope_key']}:** `{original_display}`"
        )
        form_cols = st.columns(3, gap="medium")
        form_col_set = form_cols[0]
        form_col_offset = form_cols[1]
        with form_col_set:
            with st.form(key=f"{editor_key_prefix}_set_form_{selection_hash}"):
                set_value_sig = hashlib.md5(f"{target['current_value']:.8f}".encode('utf-8')).hexdigest()[:6]
                set_input_key = f"{editor_key_prefix}_set_input_{selection_hash}_{set_value_sig}"
                new_value = st.number_input(
                    f"{target['isotope_key']} value (per mil)",
                    value=target['current_value'],
                    step=0.001,
                    format="%.4f",
                    key=set_input_key,
                )
                submitted_set = st.form_submit_button(f"Update {target['isotope_key']}")
        with form_col_offset:
            with st.form(key=f"{editor_key_prefix}_offset_form_{selection_hash}"):
                offset_value = st.number_input(
                    "Offset to add (per mil)",
                    value=0.0,
                    step=0.001,
                    format="%.4f",
                    key=f"{editor_key_prefix}_offset_input_{selection_hash}",
                )
                submitted_offset = st.form_submit_button("Apply Offset")
        prev_neighbor, next_neighbor = _find_interpolation_neighbors(target)
        interp_help = (
            "Interpolate requires both previous and next samples with valid delta values."
            if prev_neighbor is None or next_neighbor is None
            else (
                f"Interpolated from {prev_neighbor['value']:.4f} and {next_neighbor['value']:.4f} per mil."
            )
        )
        with form_cols[2]:
            st.caption(interp_help)
            submitted_interpolate = st.button(
                "Interpolate",
                key=f"{editor_key_prefix}_interpolate_{selection_hash}",
                disabled=(prev_neighbor is None or next_neighbor is None),
                use_container_width=True,
            )
        if submitted_interpolate and prev_neighbor is not None and next_neighbor is not None:
            interpolation_plan = [
                {
                    "target": target,
                    "value": (prev_neighbor['value'] + next_neighbor['value']) / 2.0,
                }
            ]
        if not is_failed_sample:
            _render_selected_point_cycle_diagnostics(target, editor_key_prefix)

        if submitted_set:
            original_map = st.session_state.setdefault('original_delta_values', {})
            original_key = f"{target['isotope_key']}|{target['row_key']}"
            if original_key not in original_map:
                original_map[original_key] = float(target['current_value'])
            prev_raw = pd.to_numeric(
                pd.Series([st.session_state.df.at[target['row_label'], target['target_col']]]),
                errors='coerce'
            ).iloc[0]
            prev_status = None
            if 'Collector Status' in st.session_state.df.columns:
                prev_status = st.session_state.df.at[target['row_label'], 'Collector Status']
            _, cal_col, _ = _get_isotope_columns(target['isotope_key'])
            prev_cal = np.nan
            if cal_col in st.session_state.df.columns:
                prev_cal = pd.to_numeric(pd.Series([st.session_state.df.at[target['row_label'], cal_col]]), errors='coerce').iloc[0]
            edited_rows = _get_edited_row_tokens()
            edited_rows.add(target['row_key'])
            st.session_state['edited_delta_rows'] = edited_rows
            st.session_state.df.at[target['row_label'], target['target_col']] = float(new_value)
            _refresh_collector_status_after_delta_edit(target['row_label'])
            _refresh_calibrated_after_delta_edit(
                target['row_label'],
                target['isotope_key'],
                previous_raw=prev_raw,
                previous_calibrated=prev_cal
            )
            new_cal = np.nan
            if cal_col in st.session_state.df.columns:
                new_cal = pd.to_numeric(pd.Series([st.session_state.df.at[target['row_label'], cal_col]]), errors='coerce').iloc[0]
            new_status = None
            if 'Collector Status' in st.session_state.df.columns:
                new_status = st.session_state.df.at[target['row_label'], 'Collector Status']
            _autosave_session_update(
                "set_delta_value",
                changes=[
                    {
                        "isotope": target['isotope_key'],
                        "row_label": str(target['row_label']),
                        "column": target['target_col'],
                        "identifier_1": target['identifier_1'],
                        "identifier_2": target['identifier_2'],
                        "excel_file": target['source_excel'],
                        "previous_value": _numeric_or_none(prev_raw),
                        "new_value": _numeric_or_none(new_value),
                        "previous_calibrated": _numeric_or_none(prev_cal),
                        "new_calibrated": _numeric_or_none(new_cal),
                        "previous_status": None if pd.isna(prev_status) else str(prev_status),
                        "new_status": None if pd.isna(new_status) else str(new_status),
                    }
                ],
                context={"editor": editor_key_prefix},
            )
            st.success(
                f"Updated {target['isotope_key']} to {float(new_value):.4f} for "
                f"{target['identifier_1']} / {target['identifier_2']}."
            )
            st.rerun()
    else:
        isotopes = sorted({t['isotope_key'] for t in selected_targets})
        st.markdown(
            f"{len(selected_targets)} datapoints selected ({', '.join(isotopes)}). "
            "Single-point absolute value edit is hidden for multi-select."
        )
        multi_cols = st.columns(2, gap="medium")
        with multi_cols[0]:
            with st.form(key=f"{editor_key_prefix}_offset_form_{selection_hash}"):
                offset_value = st.number_input(
                    "Offset to add",
                    value=0.0,
                    step=1.0,
                    format="%.4f",
                    key=f"{editor_key_prefix}_offset_input_{selection_hash}",
                )
                submitted_offset = st.form_submit_button(
                    f"Apply Offset to {len(selected_targets)} point{'s' if len(selected_targets) != 1 else ''}"
                )
        eligible_count = 0
        for target in selected_targets:
            prev_neighbor, next_neighbor = _find_interpolation_neighbors(target)
            if prev_neighbor is None or next_neighbor is None:
                continue
            interpolation_plan.append(
                {
                    "target": target,
                    "value": (prev_neighbor['value'] + next_neighbor['value']) / 2.0,
                }
            )
            eligible_count += 1
        with multi_cols[1]:
            st.caption(
                f"Interpolate available for {eligible_count}/{len(selected_targets)} selected points."
            )
            submitted_interpolate = st.button(
                f"Interpolate {eligible_count} point{'s' if eligible_count != 1 else ''}",
                key=f"{editor_key_prefix}_interpolate_multi_{selection_hash}",
                disabled=(eligible_count == 0),
                use_container_width=True,
            )

    if submitted_offset:
        offset_value = float(offset_value)
        original_map = st.session_state.setdefault('original_delta_values', {})
        edited_rows = _get_edited_row_tokens()
        autosave_changes = []
        for target in selected_targets:
            current_value = pd.to_numeric(
                pd.Series([st.session_state.df.at[target['row_label'], target['target_col']]]),
                errors='coerce'
            ).iloc[0]
            current_value = float(current_value) if pd.notna(current_value) else 0.0
            prev_status = None
            if 'Collector Status' in st.session_state.df.columns:
                prev_status = st.session_state.df.at[target['row_label'], 'Collector Status']
            original_key = f"{target['isotope_key']}|{target['row_key']}"
            if original_key not in original_map:
                original_map[original_key] = float(current_value)
            _, cal_col, _ = _get_isotope_columns(target['isotope_key'])
            prev_cal = np.nan
            if cal_col in st.session_state.df.columns:
                prev_cal = pd.to_numeric(pd.Series([st.session_state.df.at[target['row_label'], cal_col]]), errors='coerce').iloc[0]
            new_value = current_value + offset_value
            st.session_state.df.at[target['row_label'], target['target_col']] = new_value
            _refresh_collector_status_after_delta_edit(target['row_label'])
            _refresh_calibrated_after_delta_edit(
                target['row_label'],
                target['isotope_key'],
                previous_raw=current_value,
                previous_calibrated=prev_cal
            )
            new_cal = np.nan
            if cal_col in st.session_state.df.columns:
                new_cal = pd.to_numeric(pd.Series([st.session_state.df.at[target['row_label'], cal_col]]), errors='coerce').iloc[0]
            new_status = None
            if 'Collector Status' in st.session_state.df.columns:
                new_status = st.session_state.df.at[target['row_label'], 'Collector Status']
            autosave_changes.append(
                {
                    "isotope": target['isotope_key'],
                    "row_label": str(target['row_label']),
                    "column": target['target_col'],
                    "identifier_1": target.get('identifier_1'),
                    "identifier_2": target.get('identifier_2'),
                    "excel_file": target.get('source_excel'),
                    "previous_value": _numeric_or_none(current_value),
                    "new_value": _numeric_or_none(new_value),
                    "offset_applied": _numeric_or_none(offset_value),
                    "previous_calibrated": _numeric_or_none(prev_cal),
                    "new_calibrated": _numeric_or_none(new_cal),
                    "previous_status": None if pd.isna(prev_status) else str(prev_status),
                    "new_status": None if pd.isna(new_status) else str(new_status),
                }
            )
            edited_rows.add(target['row_key'])
        st.session_state['edited_delta_rows'] = edited_rows
        _autosave_session_update(
            "offset_delta_values",
            changes=autosave_changes,
            context={
                "offset": _numeric_or_none(offset_value),
                "count": len(selected_targets),
                "editor": editor_key_prefix,
            },
        )
        st.success(
            f"Applied offset {offset_value:+.4f} to {len(selected_targets)} "
            f"datapoint{'s' if len(selected_targets) != 1 else ''}."
        )
        st.rerun()

    if submitted_interpolate and interpolation_plan:
        original_map = st.session_state.setdefault('original_delta_values', {})
        edited_rows = _get_edited_row_tokens()
        autosave_changes = []
        for plan_item in interpolation_plan:
            target = plan_item["target"]
            interpolated_value = float(plan_item["value"])
            prev_raw = pd.to_numeric(
                pd.Series([st.session_state.df.at[target['row_label'], target['target_col']]]),
                errors='coerce'
            ).iloc[0]
            prev_status = None
            if 'Collector Status' in st.session_state.df.columns:
                prev_status = st.session_state.df.at[target['row_label'], 'Collector Status']
            _, cal_col, _ = _get_isotope_columns(target['isotope_key'])
            prev_cal = np.nan
            if cal_col in st.session_state.df.columns:
                prev_cal = pd.to_numeric(pd.Series([st.session_state.df.at[target['row_label'], cal_col]]), errors='coerce').iloc[0]
            original_key = f"{target['isotope_key']}|{target['row_key']}"
            if original_key not in original_map and pd.notna(prev_raw):
                original_map[original_key] = float(prev_raw)
            edited_rows.add(target['row_key'])
            st.session_state.df.at[target['row_label'], target['target_col']] = interpolated_value
            _refresh_collector_status_after_delta_edit(target['row_label'])
            _refresh_calibrated_after_delta_edit(
                target['row_label'],
                target['isotope_key'],
                previous_raw=prev_raw,
                previous_calibrated=prev_cal
            )
            new_cal = np.nan
            if cal_col in st.session_state.df.columns:
                new_cal = pd.to_numeric(pd.Series([st.session_state.df.at[target['row_label'], cal_col]]), errors='coerce').iloc[0]
            new_status = None
            if 'Collector Status' in st.session_state.df.columns:
                new_status = st.session_state.df.at[target['row_label'], 'Collector Status']
            autosave_changes.append(
                {
                    "isotope": target['isotope_key'],
                    "row_label": str(target['row_label']),
                    "column": target['target_col'],
                    "identifier_1": target['identifier_1'],
                    "identifier_2": target['identifier_2'],
                    "excel_file": target['source_excel'],
                    "previous_value": _numeric_or_none(prev_raw),
                    "new_value": _numeric_or_none(interpolated_value),
                    "previous_calibrated": _numeric_or_none(prev_cal),
                    "new_calibrated": _numeric_or_none(new_cal),
                    "previous_status": None if pd.isna(prev_status) else str(prev_status),
                    "new_status": None if pd.isna(new_status) else str(new_status),
                }
            )
        st.session_state['edited_delta_rows'] = edited_rows
        _autosave_session_update(
            "interpolate_delta_value" if len(autosave_changes) == 1 else "interpolate_delta_values",
            changes=autosave_changes,
            context={"editor": editor_key_prefix, "count": len(autosave_changes)},
        )
        if len(autosave_changes) == 1:
            target = interpolation_plan[0]["target"]
            value = float(interpolation_plan[0]["value"])
            st.success(
                f"Interpolated {target['isotope_key']} to {value:.4f} for "
                f"{target['identifier_1']} / {target['identifier_2']}."
            )
        else:
            st.success(
                f"Interpolated {len(autosave_changes)} datapoints."
            )
        st.rerun()

# Initialize session state variables if they don't exist
if 'df' not in st.session_state:
    st.session_state.df = None
if 'file_processed' not in st.session_state:
    st.session_state.file_processed = False
if 'df_cycles_source' not in st.session_state:
    st.session_state.df_cycles_source = None
if 'edited_delta_rows' not in st.session_state:
    st.session_state.edited_delta_rows = set()
if 'original_delta_values' not in st.session_state:
    st.session_state.original_delta_values = {}
if 'calibration_coefficients' not in st.session_state:
    st.session_state.calibration_coefficients = {}
if 'include_outliers' not in st.session_state:
    st.session_state.include_outliers = "No"
if 'selected_ids' not in st.session_state:
    st.session_state.selected_ids = ["All"]
if 'interpolate_outliers_export' not in st.session_state:
    st.session_state.interpolate_outliers_export = False
if AUTOSAVE_LOG_PATH_KEY not in st.session_state:
    st.session_state[AUTOSAVE_LOG_PATH_KEY] = None
if AUTOSAVE_SNAPSHOT_PATH_KEY not in st.session_state:
    st.session_state[AUTOSAVE_SNAPSHOT_PATH_KEY] = None
if AUTOSAVE_SAVE_DIR_KEY not in st.session_state:
    st.session_state[AUTOSAVE_SAVE_DIR_KEY] = None
if AUTOSAVE_ERROR_KEY not in st.session_state:
    st.session_state[AUTOSAVE_ERROR_KEY] = None
if AUTOSAVE_EVENT_COUNT_KEY not in st.session_state:
    st.session_state[AUTOSAVE_EVENT_COUNT_KEY] = 0
if AUTOSAVE_INIT_TS_KEY not in st.session_state:
    st.session_state[AUTOSAVE_INIT_TS_KEY] = None
if AUTOSAVE_DIR_OVERRIDE_KEY not in st.session_state:
    st.session_state[AUTOSAVE_DIR_OVERRIDE_KEY] = ""
if AUTOSAVE_SOURCE_FILES_KEY not in st.session_state:
    st.session_state[AUTOSAVE_SOURCE_FILES_KEY] = []
if AUTOSAVE_META_PATH_KEY not in st.session_state:
    st.session_state[AUTOSAVE_META_PATH_KEY] = None
if AUTOSAVE_RESUMED_KEY not in st.session_state:
    st.session_state[AUTOSAVE_RESUMED_KEY] = False
if AUTOSAVE_SESSION_TOKEN_KEY not in st.session_state:
    st.session_state[AUTOSAVE_SESSION_TOKEN_KEY] = None

# Initialize range variables in session state with safe defaults
if 'signal_range' not in st.session_state:
    st.session_state.signal_range = (1.0, 50.0)  # Signal intensity in volts (default low cutoff 1V)
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

def _parse_numeric_token(token):
    """Parse a numeric token with optional thousands/decimal separators."""
    if token is None or pd.isna(token):
        return None
    text = str(token).strip()
    if text == "":
        return None
    # Find the first number-like chunk (digits with optional separators/sign).
    match = re.search(r'[-+]?[\d.,]+', text)
    if not match:
        return None
    num = match.group(0)
    # Remove spaces (including non-breaking/thin spaces) used as thousands separators.
    num = re.sub(r"[\s\u00A0\u2009]", "", num)

    if "," in num and "." in num:
        # Decide decimal separator by last occurrence; other is thousands separator.
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "")
            num = num.replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        if num.count(",") > 1:
            num = num.replace(",", "")
        else:
            left, right = num.split(",", 1)
            if right.isdigit():
                # Treat comma as decimal when precision is >= 1 digit and not a clear thousands group.
                if len(right) in (1, 2):
                    num = left + "." + right
                elif len(right) == 3 and left.isdigit() and left not in ("0", "+0", "-0"):
                    num = left + right
                else:
                    num = left + "." + right
            else:
                num = left + right
    elif "." in num:
        if num.count(".") > 1:
            num = num.replace(".", "")
        else:
            left, right = num.split(".", 1)
            # If it looks like a thousands separator (e.g., 1.234), collapse it.
            if right.isdigit() and len(right) == 3 and left.isdigit() and len(left) <= 3:
                num = left + right

    try:
        return float(num)
    except Exception:
        return None


def _extract_numeric(series):
    """Extract numeric values from a mixed/unit string series."""
    if series is None:
        return pd.Series(dtype='float64')
    ser = series if isinstance(series, pd.Series) else pd.Series(series)
    parsed = ser.map(_parse_numeric_token)
    return pd.to_numeric(parsed, errors='coerce')

def _normalize_signal_intensity(series):
    """Normalize signal intensity to volts when values appear to be in mV."""
    numeric = _extract_numeric(series)
    max_val = numeric.max(skipna=True)
    # Treat clearly mV-scale values as millivolts (e.g., 48000 mV -> 48 V).
    # Keep normal volt-scale cycle values (e.g., 52 V) unchanged.
    if pd.notna(max_val) and max_val > 1000:
        numeric = numeric / 1000.0
    return numeric

def _coalesce_duplicate_columns(df):
    """Resolve duplicate column names while preserving independent collector columns."""
    if df is None or df.columns.is_unique:
        return df
    canonical_merge_cols = {
        'd 13C/12C  Mean',
        'd 13C/12C  Std Dev',
        'd 18O/16O  Mean',
        'd 18O/16O  Std Dev',
        'Identifier 1',
        'Identifier 2',
        'Cycle Number',
    }
    result_parts = []
    cols = pd.Index(df.columns)
    for col in cols.unique():
        subset = df.loc[:, cols == col]
        if isinstance(subset, pd.Series):
            result_parts.append(subset.to_frame(name=col))
            continue
        if subset.shape[1] == 1:
            result_parts.append(subset.iloc[:, [0]].rename(columns={subset.columns[0]: col}))
            continue
        if col in canonical_merge_cols:
            merged_col = subset.bfill(axis=1).iloc[:, 0].to_frame(name=col)
            result_parts.append(merged_col)
        else:
            renamed = subset.copy()
            renamed.columns = [col if i == 0 else f"{col}__dup{i+1}" for i in range(subset.shape[1])]
            result_parts.append(renamed)
    return pd.concat(result_parts, axis=1)

def _find_cycle_intensity_columns(df):
    """Find per-cycle intensity columns in the dataset."""
    if df is None:
        return []
    cols = []
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = _normalize_column_key(col)
        has_mass = bool(re.search(r'\b4[4-6](?:\.\d+)?\b', low) or 'm/z' in low or 'mz' in low)
        is_signal_named = bool('intensit' in low or re.search(r'\bint\b', low) or 'signal' in low)
        looks_delta = bool('delta' in low or re.search(r'\bd4[5-6]co2\b', low) or low.startswith('d45') or low.startswith('d46'))
        if (is_signal_named and has_mass) or (has_mass and not looks_delta):
            cols.append(col)
    if cols:
        return cols
    # Fallback: accept intensity columns without explicit sample label
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = _normalize_column_key(col)
        if ('intensit' in low or re.search(r'\bint\b', low) or 'signal' in low):
            if ('m/z' in low or 'mz' in low or re.search(r'\b4[4-6](?:\.\d+)?\b', low) or 'cycle' in low):
                cols.append(col)
    return cols

def _pick_intensity_column(cols, masses=None):
    """Pick the best intensity column, preferring specific masses."""
    if not cols:
        return None
    if masses:
        for mass in masses:
            pattern = rf'(?<!\\d){mass}(?!\\d)'
            for col in cols:
                if re.search(pattern, str(col)):
                    return col
    return cols[0]

def _extract_cycle_order(value):
    """Extract cycle order as integer (Pre -> 0, Cycle N -> N)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = str(value).strip().lower()
    if text == '' or text == 'nan':
        return np.nan
    if text == 'pre':
        return 0
    match = re.search(r'(\d+)', text)
    if match:
        return int(match.group(1))
    return np.nan

def _detect_saturated_prefix(series, min_tail=3, sigma=3.0, min_abs_shift=1.0):
    """Detect a saturated prefix in cycle values using a robust tail-based threshold.

    Returns a list of cycle orders to exclude (prefix only).
    """
    if series is None:
        return []
    s = pd.Series(series).dropna()
    if s.empty or len(s) < max(3, min_tail):
        return []
    s = s.sort_index()
    tail = s.tail(min(min_tail, len(s)))
    med = float(tail.median())
    mad = float((tail - med).abs().median())
    # Convert MAD to sigma-like; fallback to std when MAD is zero
    spread = 1.4826 * mad
    if not np.isfinite(spread) or spread == 0:
        spread = float(tail.std())
    if not np.isfinite(spread) or spread == 0:
        # Final fallback: small tolerance scaled to signal magnitude
        spread = max(float(tail.abs().median()) * 0.02, 0.05)
    tol = sigma * spread
    inlier = (s - med).abs() <= tol
    window = min(min_tail, len(s))
    for i in range(len(s) - window + 1):
        if bool(inlier.iloc[i:i + window].all()):
            excluded = s.iloc[:i]
            if excluded.empty:
                return []
            # Guard against over-detection on normal drift when no intensity columns exist.
            if (excluded - med).abs().max() < max(tol, float(min_abs_shift)):
                return []
            return list(s.index[:i])
    # No stable window detected -> treat all cycles as saturated
    if (s - med).abs().max() < max(tol, float(min_abs_shift)):
        return []
    return list(s.index)

def _apply_cycle_averages(df):
    """Compute per-sample d13C/d18O means from cycle rows, excluding saturated cycles."""
    if df is None or 'Cycle Number' not in df.columns:
        return df

    work = df.copy()

    # Coalesce duplicate isotope columns (keep first non-null across duplicates)
    for col in ['d 13C/12C  Mean', 'd 18O/16O  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Std Dev']:
        dup_positions = [i for i, c in enumerate(work.columns) if c == col]
        if len(dup_positions) > 1:
            subset = work.iloc[:, dup_positions]
            combined = subset.bfill(axis=1).iloc[:, 0]
            work = work.drop(columns=[col])
            work[col] = combined
    cycle_order = work['Cycle Number'].apply(_extract_cycle_order)
    is_pre = work['Cycle Number'].astype(str).str.strip().str.lower().eq('pre')
    cycle_order = cycle_order.where(~is_pre, 0)
    work['_cycle_order'] = cycle_order

    # Build group id for sequences starting at Pre
    group_id = is_pre.cumsum()
    group_id = group_id.where(is_pre | cycle_order.notna(), np.nan)
    work['_cycle_group'] = group_id

    # Forward-fill key identifiers within each group to attach cycle rows to samples
    id_cols = [
        'Identifier 1', 'Identifier 2', 'Label', 'Species', 'Comment', 'Run ID',
        'Line', 'Date', 'Date_ordinal', 'Sample Type', 'Reference'
    ]
    for col in id_cols:
        if col in work.columns:
            work[col] = work.groupby('_cycle_group')[col].ffill()

    pre_rows = work[is_pre].copy()
    cycle_rows = work[(work['_cycle_order'] > 0) & work['_cycle_group'].notna()].copy()

    if pre_rows.empty:
        return df

    # Ensure numeric cycle values
    for col in ['d 13C/12C  Mean', 'd 18O/16O  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Std Dev']:
        if col in work.columns:
            col_positions = [i for i, c in enumerate(work.columns) if c == col]
            for pos in col_positions:
                work.iloc[:, pos] = pd.to_numeric(work.iloc[:, pos], errors='coerce')

    # Initialize status columns
    pre_rows['Collector Status'] = 'OK'
    pre_rows['Cycles Total'] = 0
    pre_rows['d13C Cycles Used'] = 0
    pre_rows['d18O Cycles Used'] = 0
    pre_rows['d13C Cycles Excluded'] = 0
    pre_rows['d18O Cycles Excluded'] = 0

    intensity_cols = _find_cycle_intensity_columns(work)
    saturation_threshold = 48.0
    low_signal_threshold = 0.2

    def _pick_sample_intensity_columns(source_df, cols):
        labeled_sample = []
        for c in cols:
            low = _normalize_column_key(c)
            if 'standard' in low:
                continue
            if 'sample' in low or 'samp' in low:
                labeled_sample.append(c)
        if labeled_sample:
            return labeled_sample
        if len(cols) >= 4:
            # If sample/reference labels are missing, sample cups are typically the higher-voltage set.
            medians = []
            for c in cols:
                vals = _normalize_signal_intensity(source_df[c]) if c in source_df.columns else pd.Series(dtype='float64')
                medians.append((c, float(vals.median(skipna=True)) if vals.notna().any() else -np.inf))
            medians.sort(key=lambda t: t[1], reverse=True)
            top = [c for c, _ in medians[:3]]
            if top:
                return top
        return cols

    sample_intensity_cols = _pick_sample_intensity_columns(work, intensity_cols)

    def _pick_cycle_value_source(col_main, patterns):
        # Prefer per-cycle isotope columns (e.g., d45CO2/d46CO2) over mean columns.
        for col in work.columns:
            if not isinstance(col, str):
                continue
            low = _normalize_column_key(col)
            if 'standard' in low:
                continue
            if any(term in low for term in ('std', 'sd', 'se')):
                continue
            if 'mean' in low:
                continue
            if any(re.search(pat, low) for pat in patterns):
                vals = pd.to_numeric(cycle_rows[col], errors='coerce')
                if vals.notna().any():
                    return col
        # Fallback to canonical mean column only when no cycle-specific value source exists.
        if col_main in work.columns:
            vals = pd.to_numeric(cycle_rows[col_main], errors='coerce')
            if vals.notna().any():
                return col_main
        for col in work.columns:
            if not isinstance(col, str):
                continue
            low = _normalize_column_key(col)
            if 'standard' in low:
                continue
            if any(term in low for term in ('std', 'sd', 'se')):
                continue
            if any(re.search(pat, low) for pat in patterns):
                vals = pd.to_numeric(cycle_rows[col], errors='coerce')
                if vals.notna().any():
                    return col
        return col_main if col_main in work.columns else None

    d13_value_col = _pick_cycle_value_source(
        'd 13C/12C  Mean',
        [r'd13', r'd ?13c', r'd45co2', r'\bd45\b']
    )
    d18_value_col = _pick_cycle_value_source(
        'd 18O/16O  Mean',
        [r'd18', r'd ?18o', r'd46co2', r'\bd46\b']
    )

    def _extract_col_mass(col_name):
        low = _normalize_column_key(col_name)
        m = re.search(r'(?<!\d)(44|45|46)(?:\.0+)?(?!\d)', low)
        if m:
            return int(m.group(1))
        return None

    def _compute_cycle_intensity_frame(cycles_df):
        cols = [c for c in intensity_cols if c in cycles_df.columns]
        intensity_df = None
        if cols:
            intensity_df = pd.DataFrame({
                col: _normalize_signal_intensity(cycles_df[col])
                for col in cols
            })
        else:
            # Fallback: use any numeric columns in cycle rows (excluding known isotope/ID fields)
            exclude = {
                'Cycle Number', 'Identifier 1', 'Identifier 2', 'Label', 'Species', 'Comment',
                'Run ID', 'Line', 'Date', 'Date_ordinal', 'Sample Type', 'Reference',
                'd 13C/12C  Mean', 'd 13C/12C  Std Dev',
                'd 18O/16O  Mean', 'd 18O/16O  Std Dev'
            }
            data = {}
            for col in cycles_df.columns:
                if col in exclude or not isinstance(col, str):
                    continue
                low = _normalize_column_key(col)
                if not ('intensit' in low or re.search(r'\bint\b', low) or 'signal' in low):
                    continue
                if not ('m/z' in low or 'mz' in low or re.search(r'\b4[4-6](?:\.\d+)?\b', low) or 'cycle' in low):
                    continue
                vals = _normalize_signal_intensity(cycles_df[col])
                if vals.notna().any():
                    data[col] = vals
            if data:
                intensity_df = pd.DataFrame(data)
        if intensity_df is None or intensity_df.empty:
            return None
        valid = intensity_df.notna().any(axis=1)
        if valid.any():
            return intensity_df
        return None

    def _pick_mass_sample_column(cycles_df, mass_value):
        cols = [c for c in intensity_cols if c in cycles_df.columns and _extract_col_mass(c) == mass_value]
        if not cols:
            return None
        labeled_sample = []
        for c in cols:
            low = _normalize_column_key(c)
            if 'standard' in low:
                continue
            if 'sample' in low or 'samp' in low:
                labeled_sample.append(c)
        if labeled_sample:
            return labeled_sample[0]
        # Fallback: choose the column with the highest median intensity
        medians = []
        for c in cols:
            vals = _normalize_signal_intensity(cycles_df[c])
            medians.append((c, float(vals.median(skipna=True)) if vals.notna().any() else -np.inf))
        medians.sort(key=lambda t: t[1], reverse=True)
        return medians[0][0] if medians else None

    def _build_saturation_mask(intensity_df, required_masses):
        if intensity_df is None or intensity_df.empty:
            return None
        sat_mask = pd.Series(False, index=intensity_df.index, dtype=bool)
        has_mass_cols = False
        for mass in required_masses:
            mass_cols = [c for c in intensity_df.columns if _extract_col_mass(c) == mass]
            if not mass_cols:
                continue
            has_mass_cols = True
            # For each required mass, if any collector (sample/reference) is saturated, exclude the cycle.
            mass_sat = (intensity_df[mass_cols] > saturation_threshold).any(axis=1)
            sat_mask = sat_mask | mass_sat
        if not has_mass_cols:
            return None
        return sat_mask

    for group in pre_rows['_cycle_group'].dropna().unique():
        sample_mask = pre_rows['_cycle_group'] == group
        sample_idx = pre_rows.index[sample_mask][0]
        cycles = cycle_rows[cycle_rows['_cycle_group'] == group]
        # Only true analysis cycles contribute to recovered means (exclude "Pre").
        sample_cycles = cycles.copy()

        total_cycles = int(cycles.shape[0])
        pre_rows.at[sample_idx, 'Cycles Total'] = total_cycles

        saturated_any = False
        has_cycle_intensity = False
        sat_mask_d13 = None
        sat_mask_d18 = None
        intensity_df = _compute_cycle_intensity_frame(sample_cycles)
        if intensity_df is not None:
            has_cycle_intensity = True
            sat_mask_d13 = _build_saturation_mask(intensity_df, [44, 45])
            sat_mask_d18 = _build_saturation_mask(intensity_df, [44, 45, 46])

            # Use Cycle 1 m/z44 sample collector as signal intensity for outlier checks.
            samp44_col = _pick_mass_sample_column(sample_cycles, 44)
            if samp44_col is not None:
                cycle1 = sample_cycles[sample_cycles['_cycle_order'] == 1]
                if not cycle1.empty:
                    cycle1_val = _normalize_signal_intensity(cycle1[samp44_col]).iloc[0]
                else:
                    cycle1_val = _normalize_signal_intensity(sample_cycles[samp44_col]).dropna().iloc[0] if _normalize_signal_intensity(sample_cycles[samp44_col]).notna().any() else np.nan
                if pd.notna(cycle1_val):
                    pre_rows.at[sample_idx, '1  Cycle Int  Samp  44'] = float(cycle1_val)
        low_signal_failed = False
        pre_intensity_cols = [c for c in sample_intensity_cols if c in pre_rows.columns]
        if pre_intensity_cols:
            pre_vals = _normalize_signal_intensity(pre_rows.loc[sample_idx, pre_intensity_cols])
            pre_max = pre_vals.max(skipna=True)
            if pd.notna(pre_max) and pre_max < low_signal_threshold:
                low_signal_failed = True
        elif '1  Cycle Int  Samp  44' in pre_rows.columns:
            pre_val = _parse_numeric_token(pre_rows.at[sample_idx, '1  Cycle Int  Samp  44'])
            if pre_val is not None and pre_val < low_signal_threshold:
                low_signal_failed = True
        # d13C
        d13_mean = np.nan
        d13_std = np.nan
        d13_used = 0
        d13_excl = 0
        d13_has_cycles = False
        if d13_value_col and d13_value_col in sample_cycles.columns:
            d13_vals = pd.to_numeric(sample_cycles[d13_value_col], errors='coerce')
            d13_cycles = sample_cycles.assign(_d13=d13_vals)
            d13_cycles = d13_cycles[d13_cycles['_d13'].notna()]
            if not d13_cycles.empty:
                d13_has_cycles = True
                d13_filtered = pd.Series(dtype='float64')
                if has_cycle_intensity and sat_mask_d13 is not None:
                    sat_mask = sat_mask_d13.reindex(d13_cycles.index).fillna(False)
                    d13_excl = int(sat_mask.sum())
                    if d13_excl > 0:
                        saturated_any = True
                    d13_filtered = d13_cycles.loc[~sat_mask, '_d13']
                else:
                    # No cycle intensity available: keep all valid cycles.
                    d13_filtered = d13_cycles['_d13']
                if not d13_filtered.empty:
                    d13_mean = float(d13_filtered.mean())
                    d13_std = float(d13_filtered.std()) if len(d13_filtered) > 1 else np.nan
                    d13_used = int(d13_filtered.shape[0])

        # d18O
        d18_mean = np.nan
        d18_std = np.nan
        d18_used = 0
        d18_excl = 0
        d18_has_cycles = False
        if d18_value_col and d18_value_col in sample_cycles.columns:
            d18_vals = pd.to_numeric(sample_cycles[d18_value_col], errors='coerce')
            d18_cycles = sample_cycles.assign(_d18=d18_vals)
            d18_cycles = d18_cycles[d18_cycles['_d18'].notna()]
            if not d18_cycles.empty:
                d18_has_cycles = True
                d18_filtered = pd.Series(dtype='float64')
                if has_cycle_intensity and sat_mask_d18 is not None:
                    sat_mask = sat_mask_d18.reindex(d18_cycles.index).fillna(False)
                    d18_excl = int(sat_mask.sum())
                    if d18_excl > 0:
                        saturated_any = True
                    d18_filtered = d18_cycles.loc[~sat_mask, '_d18']
                else:
                    # No cycle intensity available: keep all valid cycles.
                    d18_filtered = d18_cycles['_d18']
                if not d18_filtered.empty:
                    d18_mean = float(d18_filtered.mean())
                    d18_std = float(d18_filtered.std()) if len(d18_filtered) > 1 else np.nan
                    d18_used = int(d18_filtered.shape[0])

        # Apply cycle-derived means when available; otherwise keep existing pre values
        if np.isfinite(d13_mean):
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = d13_mean
        if np.isfinite(d13_std):
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = d13_std
        if np.isfinite(d18_mean):
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = d18_mean
        if np.isfinite(d18_std):
            pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = d18_std

        # Isotope-specific failure handling:
        # If one isotope has cycle data but all those cycles are excluded (e.g., persistent cup saturation),
        # keep the other isotope and force this isotope to NaN so it is not included in results.
        if d13_has_cycles and d13_used == 0:
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = np.nan
        if d18_has_cycles and d18_used == 0:
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = np.nan

        pre_rows.at[sample_idx, 'd13C Cycles Used'] = d13_used
        pre_rows.at[sample_idx, 'd18O Cycles Used'] = d18_used
        pre_rows.at[sample_idx, 'd13C Cycles Excluded'] = d13_excl
        pre_rows.at[sample_idx, 'd18O Cycles Excluded'] = d18_excl

        # Determine collector status
        pre_d13 = pre_rows.at[sample_idx, 'd 13C/12C  Mean'] if 'd 13C/12C  Mean' in pre_rows.columns else np.nan
        pre_d18 = pre_rows.at[sample_idx, 'd 18O/16O  Mean'] if 'd 18O/16O  Mean' in pre_rows.columns else np.nan
        has_pre_d13 = bool(np.isfinite(pre_d13))
        has_pre_d18 = bool(np.isfinite(pre_d18))
        both_missing = (not has_pre_d13) and (not has_pre_d18)
        one_missing = has_pre_d13 ^ has_pre_d18
        fully_saturated = (
            has_cycle_intensity and
            (d13_has_cycles or d18_has_cycles) and
            d13_used == 0 and d18_used == 0 and
            (d13_excl > 0 or d18_excl > 0)
        )
        if fully_saturated:
            pre_rows.at[sample_idx, 'Collector Status'] = 'Fully Saturated Collectors'
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = np.nan
        elif low_signal_failed:
            pre_rows.at[sample_idx, 'Collector Status'] = 'Failed Sample'
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = np.nan
        elif saturated_any and (has_pre_d13 or has_pre_d18):
            pre_rows.at[sample_idx, 'Collector Status'] = 'Partially Saturated Collectors'
        elif both_missing or one_missing:
            pre_rows.at[sample_idx, 'Collector Status'] = 'Failed Sample'

    # Keep non-cycle rows (rows without Cycle Number)
    other_rows = work[work['_cycle_order'].isna()].copy()
    if not other_rows.empty and 'Collector Status' not in other_rows.columns:
        other_rows['Collector Status'] = 'OK'

    result = pd.concat([pre_rows, other_rows], axis=0).sort_index()
    result = result.drop(columns=['_cycle_order', '_cycle_group'], errors='ignore')
    return result

def _get_species_series(df):
    """Prefer Species column; else use Label species or Label identifier when species missing."""
    if df is None:
        return pd.Series(dtype=object)
    if 'Species' in df.columns and not df['Species'].isna().all():
        return df['Species']
    if 'Label' in df.columns and not df['Label'].isna().all():
        label_parts = df['Label'].apply(_split_label_species)
        label_ident = label_parts.map(lambda v: v[0] if v else None)
        label_species = label_parts.map(lambda v: v[1] if v else None)
        # Use species when present; otherwise fall back to identifier (first part of Label)
        return label_species.where(
            label_species.notna() & (label_species.astype(str).str.strip() != ''),
            label_ident
        )
    return pd.Series(index=df.index, dtype=object)

def _normalize_column_key(name):
    """Normalize column labels for robust matching across unicode variants."""
    if not isinstance(name, str):
        return ''
    text = name.strip()
    # Normalize common unicode variants (CO₂, µ/μ)
    text = text.replace('\u2082', '2')
    text = text.replace('\u00b5', 'u').replace('\u03bc', 'u')
    text = re.sub(r'\s+', ' ', text)
    return text.lower()

def _find_column(df, *candidates):
    """Find a column in df by exact or normalized label match."""
    if df is None:
        return None
    norm_map = {_normalize_column_key(col): col for col in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        key = _normalize_column_key(cand)
        if key in norm_map:
            return norm_map[key]
    return None

def _split_label_species(label):
    """Split Label into identifier and species using 'Label - Species' convention."""
    if not isinstance(label, str):
        return label, None
    parts = label.split('-', 1)
    if len(parts) == 2:
        ident = parts[0].strip() or label
        species = parts[1].strip() or None
        return ident, species
    return label, None

def _canonicalize_header_columns(df):
    """Normalize key column names after multi-row header merge."""
    if df is None:
        return df
    rename = {}
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = _normalize_column_key(col)
        target = None
        if re.search(r'\bindex\b', low):
            target = 'Index'
        elif 'user name' in low:
            target = 'User name'
        elif 'start time' in low:
            target = 'Start Time'
        elif 'stop time' in low:
            target = 'Stop Time'
        elif re.search(r'\bstatus\b', low):
            target = 'Status'
        elif 'mark to pause' in low:
            target = 'Mark To Pause'
        elif re.search(r'\blabel\b', low):
            target = 'Label'
        elif re.search(r'\bcomment\b', low):
            target = 'Comment'
        elif 'run id' in low:
            target = 'Run ID'
        elif re.search(r'\bline\b', low):
            target = 'Line'
        elif re.search(r'\bvial\b', low):
            target = 'Vial'
        elif 'evaluate' in low:
            target = 'Evaluate'
        elif 'sample type' in low:
            target = 'Sample Type'
        elif 'reference' in low:
            target = 'Reference'
        elif 'cycle number' in low:
            target = 'Cycle Number'
        elif low == 'information' or low.endswith(' information'):
            target = 'Information'
        elif low == 'date':
            target = 'Date'
        if target and target not in df.columns and target not in rename.values():
            rename[col] = target
    if rename:
        df = df.rename(columns=rename)
    return df

def _parse_new_table_layout(raw_df):
    """Parse the 'New Table' layout with multi-row headers."""
    header_idx = None
    for i in range(min(len(raw_df), 20)):
        row_vals = raw_df.iloc[i].astype(str).tolist()
        if 'Index' in row_vals and 'User name' in row_vals:
            header_idx = i
            break
    if header_idx is None:
        return None

    header_row = raw_df.iloc[header_idx].tolist()
    header_start_idx = header_idx
    if header_idx > 0:
        prev_row = raw_df.iloc[header_idx - 1]
        if prev_row.notna().any():
            header_start_idx = header_idx - 1
    index_col_pos = None
    for idx, val in enumerate(header_row):
        if isinstance(val, str) and val.strip().lower() == 'index':
            index_col_pos = idx
            break

    data_start_idx = header_idx + 1
    if index_col_pos is not None:
        for i in range(header_idx + 1, len(raw_df)):
            val = raw_df.iat[i, index_col_pos]
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            text = str(val).strip()
            if text == '' or text.lower() == 'nan':
                continue
            if _parse_numeric_token(val) is not None:
                data_start_idx = i
                break

    unit_tokens = {'\u2030', 'â€°', '%', 'ppm', 'ppb', 'mv', 'v', 'c'}
    cols = []
    for col_idx in range(raw_df.shape[1]):
        parts = []
        for row_idx in range(header_start_idx, data_start_idx):
            val = raw_df.iat[row_idx, col_idx]
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            text = str(val).strip()
            if text == '' or text.lower() == 'nan':
                continue
            if text.lower() in unit_tokens:
                continue
            parts.append(text)
        dedup = []
        for part in parts:
            if not dedup or dedup[-1].lower() != part.lower():
                dedup.append(part)
        col_name = ' '.join(dedup) if dedup else f'Unnamed: {col_idx}'
        cols.append(col_name)

    df = raw_df.iloc[data_start_idx:].copy()
    df.columns = cols
    df = _canonicalize_header_columns(df)

    # Drop a units row if present immediately after headers (fallback)
    if len(df) > 0:
        unit_row = df.iloc[0]
        unit_hits = 0
        non_empty = 0
        for val in unit_row.values.tolist():
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            text = str(val).strip().lower()
            if text == '' or text == 'nan':
                continue
            non_empty += 1
            if text in unit_tokens:
                unit_hits += 1
        if non_empty > 0 and unit_hits / max(non_empty, 1) >= 0.6:
            df = df.iloc[1:].copy()

    if 'Index' in df.columns:
        if 'Cycle Number' in df.columns:
            df = df[df['Index'].notna() | df['Cycle Number'].notna()].copy()
        else:
            df = df[df['Index'].notna()].copy()

    return df
def _standardize_isotope_columns(df):
    """Map isotope columns to canonical names used throughout the app."""
    rename_map = {}
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = col.strip().lower()
        if 'd13' in low or 'δ13' in low:
            if 'mean' in low:
                rename_map[col] = 'd 13C/12C  Mean'
            elif 'sd' in low or 'std' in low:
                rename_map[col] = 'd 13C/12C  Std Dev'
        if 'd18' in low or 'δ18' in low:
            if 'mean' in low:
                rename_map[col] = 'd 18O/16O  Mean'
            elif 'sd' in low or 'std' in low:
                rename_map[col] = 'd 18O/16O  Std Dev'
    if rename_map:
        df = df.rename(columns=rename_map)
    return df

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
        'p_no_acid': r'P\s*no\s*Acid\s*:\s*([\d.]+)',
        'p_gases': r'P\s*gases\s*:\s*([\d.]+)',
        'total_co2': r'Total\s*CO(?:2|\u2082)\s*:\s*([\d.]+)',
        'co2_after_exp': r'CO(?:2|\u2082)\s*after\s*Exp\.:\s*([\d.]+)',
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
            match = re.search(pattern, info, flags=re.IGNORECASE)
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
    # Coerce to numeric to avoid silent failures on string/object columns
    series = pd.to_numeric(data[column], errors='coerce')
    mean_val, std_val, outliers = _compute_sigma_stats(series, sigma_level)
    if outliers is None:
        return pd.Series(False, index=data.index)
    return outliers.reindex(data.index, fill_value=False)


def _compute_sigma_stats(series, sigma_level):
    """Compute mean/std and outlier mask using a two-pass sigma calculation."""
    valid_mask = series.notna()
    if valid_mask.sum() < 2:
        return (np.nan, np.nan, None)

    vals = series[valid_mask]
    mean1 = vals.mean()
    std1 = vals.std()
    if not np.isfinite(mean1) or not np.isfinite(std1) or std1 == 0:
        return (mean1, std1, None)

    lower1 = mean1 - sigma_level * std1
    upper1 = mean1 + sigma_level * std1
    inliers1 = valid_mask & (series >= lower1) & (series <= upper1)
    base = series[inliers1]
    if base.dropna().shape[0] >= 2:
        mean2 = base.mean()
        std2 = base.std()
    else:
        mean2 = mean1
        std2 = std1

    if not np.isfinite(mean2) or not np.isfinite(std2) or std2 == 0:
        return (mean2, std2, None)

    lower2 = mean2 - sigma_level * std2
    upper2 = mean2 + sigma_level * std2
    outliers = valid_mask & ((series < lower2) | (series > upper2))
    return (mean2, std2, outliers)

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
    # Coerce to numeric to avoid silent failures on string/object columns
    series = pd.to_numeric(data[column], errors='coerce')
    valid = series.dropna()
    if valid.empty:
        return pd.Series(False, index=data.index)

    # Calculate Q1, Q3, and IQR for the column
    q1 = valid.quantile(0.25)
    q3 = valid.quantile(0.75)
    iqr = q3 - q1
    if not np.isfinite(iqr):
        return pd.Series(False, index=data.index)

    # Define the upper and lower bounds for outliers using the provided multiplier
    upper_bound = q3 + iqr_multiplier * iqr
    lower_bound = q1 - iqr_multiplier * iqr

    # Identify outliers (values outside the upper and lower bounds)
    outliers = (series > upper_bound) | (series < lower_bound)

    return outliers.fillna(False)

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


# Load standards reference (tolerant to encoding/case differences)
try:
    standards_df = pd.read_csv("standards.csv", encoding="utf-8")
except Exception:
    standards_df = pd.read_csv("Standards.csv", encoding="utf-8")
# Normalize isotopic type labels to match internal constants
try:
    standards_df['Isotopic_Value_Type'] = (
        standards_df['Isotopic_Value_Type']
        .astype(str)
        .str.strip()
        .replace({
            'VPDB(13C)': ISOTYPE_D13C,
            'VSMOW(18O)': ISOTYPE_D18O,
            'dVPDB(13C)': ISOTYPE_D13C,
            'dVSMOW(18O)': ISOTYPE_D18O,
            '?VPDB(13C)': ISOTYPE_D13C,
            '?VSMOW(18O)': ISOTYPE_D18O,
            'δVPDB(13C)': ISOTYPE_D13C,
            'δVSMOW(18O)': ISOTYPE_D18O,
            'Î´VPDB(13C)': ISOTYPE_D13C,
            'Î´VSMOW(18O)': ISOTYPE_D18O,
            '??VPDB(13C)': ISOTYPE_D13C,
            '??VSMOW(18O)': ISOTYPE_D18O,
        })
    )
except Exception:
    pass

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

def _filter_standards_remove_outliers(df, standards, method, sigma, iqr_mult):
    '''Return selected standards with outliers removed.'''
    if not standards:
        return pd.DataFrame(columns=df.columns)
    parts = []
    for std in standards:
        std_df = df[df['Identifier 1'] == std].copy()
        if std_df.empty:
            continue
        try:
            if method == 'Z-Score':
                out13 = identify_outliers(std_df, 'd 13C/12C  Mean', sigma)
                out18 = identify_outliers(std_df, 'd 18O/16O  Mean', sigma)
            else:
                out13 = identify_outliers_iqr(std_df, 'd 13C/12C  Mean', iqr_mult)
                out18 = identify_outliers_iqr(std_df, 'd 18O/16O  Mean', iqr_mult)
            keep = ~(out13 | out18)
            parts.append(std_df.loc[keep])
        except Exception:
            parts.append(std_df)
    if not parts:
        return pd.DataFrame(columns=df.columns)
    return pd.concat(parts, axis=0, ignore_index=True)

def _compute_linearity_fit(clean_df, y_col, x_col):
    '''Compute linear regression y = a + b*x. Returns dict with slope, intercept, r2, x_ref, n.'''
    result = {'slope': np.nan, 'intercept': np.nan, 'r2': np.nan, 'x_ref': np.nan, 'n': 0}
    if clean_df is None or clean_df.empty:
        return result
    x = pd.to_numeric(clean_df[x_col], errors='coerce')
    y = pd.to_numeric(clean_df[y_col], errors='coerce')
    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]
    y = y[m]
    if len(x) < 2:
        return result
    lr = linregress(x, y)
    result['slope'] = float(lr.slope)
    result['intercept'] = float(lr.intercept)
    result['r2'] = float(lr.rvalue ** 2)
    result['x_ref'] = float(np.median(x.values))
    result['n'] = int(len(x))
    return result

def _apply_linearity_correction(df, intensity_col, fits):
    '''Apply linearity correction to raw and calibrated isotope columns.'''
    i = pd.to_numeric(df[intensity_col], errors='coerce')
    if 'd 13C/12C  Mean' in df.columns and np.isfinite(fits.get('d13C', {}).get('slope', np.nan)):
        slope = fits['d13C']['slope']; x_ref = fits['d13C']['x_ref']
        y = pd.to_numeric(df['d 13C/12C  Mean'], errors='coerce')
        df['d13C_linearity_corrected'] = (y - slope * (i - x_ref)).where(np.isfinite(y) & np.isfinite(i))
    if 'd 18O/16O  Mean' in df.columns and np.isfinite(fits.get('d18O', {}).get('slope', np.nan)):
        slope = fits['d18O']['slope']; x_ref = fits['d18O']['x_ref']
        y = pd.to_numeric(df['d 18O/16O  Mean'], errors='coerce')
        df['d18O_linearity_corrected'] = (y - slope * (i - x_ref)).where(np.isfinite(y) & np.isfinite(i))
    if 'd13C_calibrated' in df.columns and np.isfinite(fits.get('d13C', {}).get('slope', np.nan)):
        slope = fits['d13C']['slope']; x_ref = fits['d13C']['x_ref']
        y = pd.to_numeric(df['d13C_calibrated'], errors='coerce')
        df['d13C_calibrated_linearity_corrected'] = (y - slope * (i - x_ref)).where(np.isfinite(y) & np.isfinite(i))
    if 'd18O_calibrated' in df.columns and np.isfinite(fits.get('d18O', {}).get('slope', np.nan)):
        slope = fits['d18O']['slope']; x_ref = fits['d18O']['x_ref']
        y = pd.to_numeric(df['d18O_calibrated'], errors='coerce')
        df['d18O_calibrated_linearity_corrected'] = (y - slope * (i - x_ref)).where(np.isfinite(y) & np.isfinite(i))
    return df

def _interpolate_outliers_by_identifier2(df, outlier_mask, cols, id2_col='Identifier 2'):
    """Interpolate specified columns for rows flagged as outliers, using
    the sequence defined by ``Identifier 2`` as the order reference.

    Only values on outlier rows are replaced by the interpolation; non-outlier
    rows retain their original values. Interpolation is linear and uses the
    previous and next measurements in Identifier 2 order.

    Parameters
    ----------
    df : pandas.DataFrame
        Source dataframe.
    outlier_mask : pandas.Series of bool
        Boolean mask (aligned to df.index) indicating outlier rows.
    cols : list of str
        Columns to interpolate.
    id2_col : str
        Column used to define the sequence (default: 'Identifier 2').

    Returns
    -------
    pandas.DataFrame
        A copy of df with interpolated values for outlier rows.
    """
    if df is None or len(df) == 0 or not any(c in df.columns for c in cols):
        return df

    work = df.copy()

    # Build an order column from Identifier 2; prefer numeric, fallback to extracted number, then to original order
    if id2_col in work.columns:
        order = pd.to_numeric(work[id2_col], errors='coerce')
        if order.isna().all():
            # Try extracting numbers from strings
            try:
                order = work[id2_col].apply(lambda v: extract_number(v))
                order = pd.to_numeric(order, errors='coerce')
            except Exception:
                order = pd.Series(np.arange(len(work)), index=work.index)
    else:
        order = pd.Series(np.arange(len(work)), index=work.index)

    work['_order_irms'] = order
    work['_orig_pos_irms'] = np.arange(len(work))

    # Sort by order then by original position to keep stability; NaNs go to the end
    work_sorted = work.sort_values(['_order_irms', '_orig_pos_irms'], na_position='last')
    mask_sorted = outlier_mask.reindex(work_sorted.index).fillna(False)

    for col in cols:
        if col not in work_sorted.columns:
            continue
        s = pd.to_numeric(work_sorted[col], errors='coerce')
        s_masked = s.copy()
        s_masked[mask_sorted] = np.nan
        s_interp = s_masked.interpolate(method='linear', limit_direction='both')
        # Assign back only for the outlier rows
        idx_to_update = mask_sorted[mask_sorted].index
        work_sorted.loc[idx_to_update, col] = s_interp.loc[idx_to_update]

    # Restore original order
    work_sorted = work_sorted.sort_values('_orig_pos_irms')
    work_sorted = work_sorted.drop(columns=['_order_irms', '_orig_pos_irms'])
    return work_sorted

def _sanitize_filename(name: str) -> str:
    try:
        s = str(name)
    except Exception:
        return "output"
    s = re.sub(r'[\\/:*?"<>|]', '_', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s or "output"

def _build_client_filename(client_name: str, client_df: pd.DataFrame) -> str:
    client_part = _sanitize_filename(client_name) if client_name else "Client"
    try:
        raw_ids = [str(x) for x in client_df['Identifier'].dropna().unique().tolist()]
    except Exception:
        raw_ids = []
    # Always include the explicit list of Identifier 1 values (sanitized), no count summary
    ids_sanitized = [_sanitize_filename(x) for x in raw_ids]
    id_part = " ".join(ids_sanitized).strip()

    date_str = pd.Timestamp.today().strftime('%d%m%Y')
    # Use exact label requested, preserving '&'
    title = "Stable C&O isosopes results P2L"
    parts = [p for p in [client_part, id_part, title, date_str] if p]
    return (" ".join(parts) + ".xlsx").strip()


def _compute_calibration_coefficients(standards_df, selected_standards):
    """Compute per-isotope affine coefficients for calibrated = slope*raw + intercept."""
    coeffs = {}
    if standards_df is None or len(selected_standards) not in (1, 2):
        return coeffs

    isotopic_types = {
        'd13C': (ISOTYPE_D13C, 'd 13C/12C  Mean'),
        'd18O': (ISOTYPE_D18O, 'd 18O/16O  Mean'),
    }
    for iso_key, (iso_type_name, raw_col) in isotopic_types.items():
        if raw_col not in standards_df.columns:
            continue
        slope = np.nan
        intercept = np.nan
        if len(selected_standards) == 1:
            standard = selected_standards[0]
            raw_std = pd.to_numeric(
                standards_df.loc[standards_df['Identifier 1'] == standard, raw_col],
                errors='coerce'
            ).mean()
            true_std = pd.to_numeric(pd.Series([get_true_value(standard, iso_type_name)]), errors='coerce').iloc[0]
            if np.isfinite(raw_std) and np.isfinite(true_std) and np.isfinite(raw_std + 1000) and abs(raw_std + 1000) > 1e-12:
                slope = (true_std + 1000.0) / (raw_std + 1000.0)
                intercept = (1000.0 * slope) - 1000.0
        else:
            standard1, standard2 = selected_standards
            raw_rm1 = pd.to_numeric(
                standards_df.loc[standards_df['Identifier 1'] == standard1, raw_col],
                errors='coerce'
            ).mean()
            raw_rm2 = pd.to_numeric(
                standards_df.loc[standards_df['Identifier 1'] == standard2, raw_col],
                errors='coerce'
            ).mean()
            true_rm1 = pd.to_numeric(pd.Series([get_true_value(standard1, iso_type_name)]), errors='coerce').iloc[0]
            true_rm2 = pd.to_numeric(pd.Series([get_true_value(standard2, iso_type_name)]), errors='coerce').iloc[0]
            denom = raw_rm1 - raw_rm2
            if np.isfinite(raw_rm1) and np.isfinite(raw_rm2) and np.isfinite(true_rm1) and np.isfinite(true_rm2) and np.isfinite(denom) and abs(denom) > 1e-12:
                slope = (true_rm1 - true_rm2) / denom
                intercept = true_rm1 - slope * raw_rm1
        if np.isfinite(slope) and np.isfinite(intercept):
            coeffs[iso_key] = {'slope': float(slope), 'intercept': float(intercept)}
    return coeffs


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
        ISOTYPE_D13C: ('d 13C/12C  Mean', 'd13C_calibrated'),
        ISOTYPE_D18O: ('d 18O/16O  Mean', 'd18O_calibrated')
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
    Create calibration plots for d13C and d18O using Plotly.

    Parameters:
    standards_reference_df (pd.DataFrame): DataFrame containing the reference standards data.
    measurement_df (pd.DataFrame): DataFrame containing the measured values.
    selected_standards (list): List of selected standard names.
    color_param (str): Column name in measurement_df to use for point coloring.

    Returns:
    dict: Dictionary containing calibration plots for d13C and d18O.
    """
    # Initialize dictionary for storing plots
    figs = {}

    # Define isotope mappings for processing
    isotopes = {
        ISOTYPE_D13C: {
            'y_label': 'd13C',
            'measurement_col': 'd 13C/12C  Mean'
        },
        ISOTYPE_D18O: {
            'y_label': 'd18O',
            'measurement_col': 'd 18O/16O  Mean'
        }
    }

    for isotope_type, isotope_data in isotopes.items():
        fig = go.Figure()
        true_values = []
        measured_values = []
        color_values = []

        # Build a shared coloraxis so all traces use the same colorbar
        coloraxis_cfg = dict(
            colorscale='Viridis',
            colorbar=dict(
                title='Date' if color_param == 'Date_ordinal' else color_param,
                thickness=20,
                len=0.75,
                y=0.5,
                yanchor='middle',
                x=1.15,
                xanchor='right'
            )
        )
        color_values_all, colorbar_category_ticks = _prepare_color_values(
            measurement_df[color_param] if color_param in measurement_df.columns else None
        )
        if color_values_all is not None:
            try:
                cdata = pd.to_numeric(color_values_all, errors='coerce')
                cmin = float(np.nanmin(cdata))
                cmax = float(np.nanmax(cdata))
                if np.isfinite(cmin) and np.isfinite(cmax):
                    coloraxis_cfg.update(cmin=cmin, cmax=cmax)
            except Exception:
                pass
        if color_param == 'Date_ordinal' and color_param in measurement_df.columns:
            tickvals, ticktext = _build_date_colorbar_ticks(measurement_df[color_param])
            if tickvals and ticktext:
                coloraxis_cfg['colorbar'].update(tickmode='array', tickvals=tickvals, ticktext=ticktext)
        elif colorbar_category_ticks is not None:
            tickvals, ticktext = colorbar_category_ticks
            if tickvals and ticktext:
                coloraxis_cfg['colorbar'].update(tickmode='array', tickvals=tickvals, ticktext=ticktext)

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
            measured_series = pd.to_numeric(
                measurement_df.loc[measurement_df['Identifier 1'] == standard, isotope_data['measurement_col']],
                errors='coerce'
            )
            valid_mask = measured_series.notna() & np.isfinite(measured_series)
            measured_values_for_standard = measured_series.loc[valid_mask].values

            color_values_for_standard = None
            if color_values_all is not None:
                color_values_for_standard = color_values_all.loc[
                    measurement_df['Identifier 1'] == standard
                ]
                color_values_for_standard = color_values_for_standard.loc[valid_mask].values

            print(f"Standard: {standard}")
            print(f"Measured values for {isotope_data['y_label']}: {measured_values_for_standard}")
            print(f"Color values: {color_values_for_standard}")

            # Skip standards with no measurements
            if len(measured_values_for_standard) == 0:
                st.warning(f"No measured values found for standard {standard}. Skipping.")
                continue

            # Append values for calibration processing
            true_val = pd.to_numeric(pd.Series([true_value]), errors='coerce').iloc[0]
            if not np.isfinite(true_val):
                st.warning(f"Invalid true value for standard {standard}. Skipping.")
                continue
            true_values.extend([true_val] * len(measured_values_for_standard))
            measured_values.extend(measured_values_for_standard)
            if color_values_for_standard is not None:
                color_values.extend(color_values_for_standard)

            # Add scatter points for this standard
            marker_kwargs = dict(size=10)
            if color_values_for_standard is not None and pd.notna(color_values_for_standard).any():
                marker_kwargs.update(color=color_values_for_standard, coloraxis='coloraxis')
            else:
                marker_kwargs.update(color='rgba(150,150,150,0.8)')
            fig.add_trace(go.Scatter(
                x=[true_value] * len(measured_values_for_standard),
                y=measured_values_for_standard,
                mode='markers',
                name=f'{standard}',
                marker=marker_kwargs
            ))

        # Determine calibration method (single or double anchor)
        true_arr = np.array(true_values, dtype=float)
        measured_arr = np.array(measured_values, dtype=float)
        valid = np.isfinite(true_arr) & np.isfinite(measured_arr)
        true_arr = true_arr[valid]
        measured_arr = measured_arr[valid]

        if len(selected_standards) == 1:
            # Single anchor calibration
            if len(true_arr) > 0 and len(measured_arr) > 0:
                offset = np.mean(measured_arr - true_arr)
                annotation_text = f"Offset = {offset:.3f}"
                try:
                    x_min, x_max = float(np.min(true_arr)) - 1, float(np.max(true_arr)) + 1
                except ValueError:
                    x_min, x_max = -1, 1
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
                annotation_text = "No valid points for calibration"
        else:
            # Double anchor calibration
            try:
                if len(true_arr) < 2:
                    raise ValueError("Insufficient data for linear regression.")
                slope, intercept, _, _, _ = linregress(true_arr, measured_arr)
                annotation_text = f"y = {slope:.3f}x + {intercept:.3f}"
                x_min, x_max = float(np.min(true_arr)) - 1, float(np.max(true_arr)) + 1
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

        # Update layout with annotation and axis labels (attach shared coloraxis)
        fig.update_layout(
            title=f"{'Single' if len(selected_standards) == 1 else 'Double'} Anchor Calibration for {isotope_type}",
            xaxis_title=f"True {isotope_data['y_label']} value",
            yaxis_title=f"Raw/Measured {isotope_data['y_label']} value",
            showlegend=True,
            width=900,   # Increased width to accommodate colorbar
            height=600,
            margin=dict(r=150),  # Add right margin for colorbar
            coloraxis=coloraxis_cfg,
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
            'Leak Rate vs d13C', 'P no Acid vs d13C', 'Total CO2 vs d13C',
            'Leak Rate vs d18O', 'P no Acid vs d18O', 'Total CO2 vs d18O',
            'Leak Rate vs Line', 'Signal Intensity vs pCO2', 'Signal Intensity vs d13C',
            'Signal Intensity vs d18O', 'd13C vs Line', 'd18O vs Line',
            'Leak Rate vs pCO2', 'd13C vs d18O', 'Total CO2 vs Line',
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

    # Build colorbar configuration for the first trace (readable dates if needed)
    colorbar_cfg = dict(
        title='Date' if color_param == 'Date_ordinal' else color_param,
        thickness=20,
        len=0.75,  # Longer colorbar
        y=0.5,     # Center vertically
        yanchor='middle',
        x=1.15,    # Move further right
        xanchor='right'
    )
    color_values, colorbar_category_ticks = _prepare_color_values(df[color_param])
    if color_param == 'Date_ordinal' and color_param in df.columns:
        tickvals, ticktext = _build_date_colorbar_ticks(df[color_param])
        if tickvals and ticktext:
            colorbar_cfg.update(tickmode='array', tickvals=tickvals, ticktext=ticktext)
    elif colorbar_category_ticks is not None:
        tickvals, ticktext = colorbar_category_ticks
        if tickvals and ticktext:
            colorbar_cfg.update(tickmode='array', tickvals=tickvals, ticktext=ticktext)

    # Scatter plots with coloring by selected parameter
    # First trace with the colorbar
    fig.add_trace(go.Scatter(
        x=df['leak_rate'],
        y=df['d 13C/12C  Mean'],
        mode='markers',
        marker=dict(
            color=color_values,
            colorscale='Viridis',
            symbol=marker_symbols,
            colorbar=colorbar_cfg,
            showscale=True
        ),
        text=hover_text,
        hoverinfo='text+x+y'
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['p_no_acid'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=1, col=2)
    fig.add_trace(go.Scatter(x=df['total_co2'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=1, col=3)

    fig.add_trace(go.Scatter(x=df['leak_rate'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['p_no_acid'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=2)
    fig.add_trace(go.Scatter(x=df['total_co2'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=3)

    fig.add_trace(go.Box(x=df['Line'], y=df['leak_rate']), row=3, col=1)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['total_co2'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
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

    # Plot the sorted quadratic fit as a line (only if fit succeeded)
    if len(x_data_clean) >= 3:
        fig.add_trace(go.Scatter(
            x=x_data_sorted, y=quadratic_curve_sorted, mode='lines', name='Quadratic Fit',
            line=dict(color='red', dash='dash')
        ), row=3, col=2)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=3, col=3)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=4, col=1)
    fig.add_trace(go.Box(x=df['Line'], y=df['d 13C/12C  Mean']), row=4, col=2)
    fig.add_trace(go.Box(x=df['Line'], y=df['d 18O/16O  Mean']), row=4, col=3)

    fig.add_trace(go.Scatter(x=df['leak_rate'], y=df['total_co2'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=5, col=1)
    fig.add_trace(go.Scatter(x=df['d 13C/12C  Mean'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, symbol=marker_symbols, colorscale='Viridis', showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=5, col=2)
    fig.add_trace(go.Box(x=df['Line'], y=df['total_co2']), row=5, col=3)



    # Add scatter plots with coloring by selected parameter, adjusting marker style for standards
    fig.add_trace(go.Scatter(
        x=df['leak_rate'], y=df['1  Cycle Int  Samp  44'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=6, col=1)

    fig.add_trace(go.Scatter(
        x=df['p_no_acid'], y=df['leak_rate'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=6, col=2)

    fig.add_trace(go.Scatter(
        x=df['p_gases'], y=df['leak_rate'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=6, col=3)

    # Perform PCA
    features = ['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'total_co2', 'd 18O/16O  Mean', 'Line',
                '1  Cycle Int  Samp  44']
    X = df[features].dropna()
    if X.empty:
        fig.update_layout(
            title_text='Diagnostic Plots',
            height=2600,
            showlegend=False,
            margin=dict(r=150)
        )
        return fig

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
        pca_color = color_values.loc[X.index] if color_values is not None else df.loc[X.index, color_param]
        pca_hover = df.loc[X.index, 'Identifier 2']
        fig.add_trace(go.Scatter(
            x=components[:, 0], y=components[:, 1], mode='markers',
            marker=dict(color=pca_color, colorscale='Viridis', symbol=marker_symbols, showscale=False),
            text=pca_hover, hoverinfo='text+x+y'
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
        height=2600,
        showlegend=False,
        margin=dict(r=150)  # Add right margin for colorbar
    )

    return fig


def download_excel(df, outliers=None, filename="data.xlsx", selected_standards=None,
                   calibration_type=None, sigma_level=None, irq_multiplier=None,
                   client_name=None, comment_map=None):
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
        if False and selected_standards:  # legacy block disabled; see new block below
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

        # Build Client Output sheet with corrected values and information box
        try:
            # Determine linearity fits (reuse from session or recompute on-the-fly from standards)
            fits = st.session_state.get('linearity_fits') if isinstance(st.session_state, dict) else None
            intensity_col = '1  Cycle Int  Samp  44'

            # If no fits in session, compute using currently selected standards (cleaned by chosen outlier method)
            if (not fits) and selected_standards:
                try:
                    _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
                    _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
                    _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)
                    clean_stds_all = _filter_standards_remove_outliers(df, selected_standards, _method, _sigma, _iqr)
                    fit13 = _compute_linearity_fit(clean_stds_all, 'd 13C/12C  Mean', intensity_col)
                    fit18 = _compute_linearity_fit(clean_stds_all, 'd 18O/16O  Mean', intensity_col)
                    fits = {
                        'd13C': {'slope': fit13.get('slope', np.nan), 'x_ref': fit13.get('x_ref', np.nan)},
                        'd18O': {'slope': fit18.get('slope', np.nan), 'x_ref': fit18.get('x_ref', np.nan)},
                        'raw': {'fit13': fit13, 'fit18': fit18},
                    }
                except Exception:
                    fits = None

            # Build corrected columns with best available data
            def _build_corrected(series_cal, series_raw, isotope_key):
                try:
                    s_cal = pd.to_numeric(series_cal, errors='coerce') if series_cal is not None else None
                except Exception:
                    s_cal = None
                try:
                    s_raw = pd.to_numeric(series_raw, errors='coerce') if series_raw is not None else None
                except Exception:
                    s_raw = None
                if s_cal is not None and f"{isotope_key}_calibrated_linearity_corrected" in df.columns:
                    # Prefer precomputed calibrated+linearity-corrected column if present in export df
                    return pd.to_numeric(df[f"{isotope_key}_calibrated_linearity_corrected"], errors='coerce')
                if s_cal is not None and fits and np.isfinite(fits.get(isotope_key, {}).get('slope', np.nan)) and intensity_col in df.columns:
                    # Apply linearity correction to calibrated values
                    i = pd.to_numeric(df[intensity_col], errors='coerce')
                    slope = fits[isotope_key]['slope']; xr = fits[isotope_key]['x_ref']
                    return (s_cal - slope * (i - xr)).where(np.isfinite(s_cal) & np.isfinite(i))
                # Fallbacks
                if s_cal is not None:
                    return s_cal
                if s_raw is not None and fits and np.isfinite(fits.get(isotope_key, {}).get('slope', np.nan)) and intensity_col in df.columns:
                    i = pd.to_numeric(df[intensity_col], errors='coerce')
                    slope = fits[isotope_key]['slope']; xr = fits[isotope_key]['x_ref']
                    return (s_raw - slope * (i - xr)).where(np.isfinite(s_raw) & np.isfinite(i))
                return s_raw if s_raw is not None else pd.Series(index=df.index, dtype=float)

            corrected_d13 = _build_corrected(
                df.get('d13C_calibrated'),
                df.get('d 13C/12C  Mean'),
                'd13C'
            )
            corrected_d18 = _build_corrected(
                df.get('d18O_calibrated'),
                df.get('d 18O/16O  Mean'),
                'd18O'
            )

            # Prepare client output dataframe (non-standards only)
            species_series = _get_species_series(df)
            client_df = pd.DataFrame({
                'Identifier': df['Identifier 1'],
                'Sample #': df.get('Identifier 2', pd.Series(index=df.index, dtype=object)),
                'Species': species_series,
                'd13C (‰, VPDB)  Mean': pd.to_numeric(df.get('d 13C/12C  Mean'), errors='coerce'),
                'd13C (‰, VPDB)  Std Dev': pd.to_numeric(df.get('d 13C/12C  Std Dev'), errors='coerce'),
                'd18O (‰, VPDB)  Mean': pd.to_numeric(df.get('d 18O/16O  Mean'), errors='coerce'),
                'd18O (‰, VPDB)  Std Dev': pd.to_numeric(df.get('d 18O/16O  Std Dev'), errors='coerce'),
                'Corrected d13C (‰, VPDB)': corrected_d13,
                'Corrected d18O (‰, VPDB)': corrected_d18,
            })

            # Keep only non-standards entries if selected_standards is provided
            if selected_standards:
                client_df = client_df[~df['Identifier 1'].isin(selected_standards)]

            # Round numeric columns to 2 decimals specifically for Client Output
            round_cols = [
                'd13C (‰, VPDB)  Mean', 'd13C (‰, VPDB)  Std Dev',
                'd18O (‰, VPDB)  Mean', 'd18O (‰, VPDB)  Std Dev',
                'Corrected d13C (‰, VPDB)', 'Corrected d18O (‰, VPDB)'
            ]
            for rc in round_cols:
                if rc in client_df.columns:
                    client_df[rc] = pd.to_numeric(client_df[rc], errors='coerce').round(2)

            # Do not write Client Output into dataset workbook; handled as a separate file below
            client_sheet = "Client Output"
            workbook = writer.book
            # Intentionally do not create worksheet here to avoid including it in dataset file

            # Basic formatting: header bold, corrected columns purple
            header_fmt = workbook.add_format({'bold': True})
            corrected_hdr_fmt = workbook.add_format({'bold': True, 'font_color': '#6A1B9A'})
            num_fmt = workbook.add_format({'num_format': '0.00'})
            num_fmt_sd = workbook.add_format({'num_format': '0.00'})

            # Set column widths and header formats
            headers = list(client_df.columns)
            for col_idx, col_name in enumerate(headers):
                fmt = header_fmt
                if 'Corrected' in col_name:
                    fmt = corrected_hdr_fmt
                worksheet.write(0, col_idx, col_name, fmt)
                # Reasonable widths
                width = 15
                if col_name in ('Identifier', 'Species'):
                    width = 18
                elif 'Corrected' in col_name:
                    width = 22
                worksheet.set_column(col_idx, col_idx, width)

            # Apply numeric format to measure columns
            meas_cols = [
                'd13C (‰, VPDB)  Mean', 'd13C (‰, VPDB)  Std Dev',
                'd18O (‰, VPDB)  Mean', 'd18O (‰, VPDB)  Std Dev',
                'Corrected d13C (‰, VPDB)', 'Corrected d18O (‰, VPDB)'
            ]
            for col_name in meas_cols:
                if col_name in headers:
                    col_idx = headers.index(col_name)
                    worksheet.set_column(col_idx, col_idx, None, num_fmt if 'Std Dev' not in col_name else num_fmt_sd)

            # Compute SHP2L precision over period using standards-style logic
            d13c_sd_val = np.nan
            d18o_sd_val = np.nan
            n_used = 0
            try:
                _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
                _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
                _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)

                shp = df[df['Identifier 1'] == 'SHP2L'].copy() if 'Identifier 1' in df.columns else pd.DataFrame()
                if not shp.empty:
                    # Remove outliers on SHP2L like in standards table
                    if _method == "Z-Score":
                        m13 = identify_outliers(shp, 'd 13C/12C  Mean', _sigma)
                        m18 = identify_outliers(shp, 'd 18O/16O  Mean', _sigma)
                    else:
                        m13 = identify_outliers_iqr(shp, 'd 13C/12C  Mean', _iqr)
                        m18 = identify_outliers_iqr(shp, 'd 18O/16O  Mean', _iqr)
                    clean_shp = shp.loc[~(m13 | m18)].copy()
                    n_used = len(clean_shp)

                    # Ensure we have fits; compute from selected standards or SHP2L itself
                    if not fits:
                        try:
                            if selected_standards:
                                clean_all = _filter_standards_remove_outliers(df, selected_standards, _method, _sigma, _iqr)
                            else:
                                clean_all = clean_shp
                            f13 = _compute_linearity_fit(clean_all, 'd 13C/12C  Mean', intensity_col)
                            f18 = _compute_linearity_fit(clean_all, 'd 18O/16O  Mean', intensity_col)
                            fits = {
                                'd13C': {'slope': f13.get('slope', np.nan), 'x_ref': f13.get('x_ref', np.nan)},
                                'd18O': {'slope': f18.get('slope', np.nan), 'x_ref': f18.get('x_ref', np.nan)},
                            }
                        except Exception:
                            fits = None

                    # Compute linearity-corrected precision
                    y13s = pd.to_numeric(clean_shp.get('d 13C/12C  Mean'), errors='coerce')
                    y18s = pd.to_numeric(clean_shp.get('d 18O/16O  Mean'), errors='coerce')
                    if fits and np.isfinite(fits.get('d13C', {}).get('slope', np.nan)) and intensity_col in clean_shp.columns:
                        i = pd.to_numeric(clean_shp[intensity_col], errors='coerce')
                        y13s = (y13s - fits['d13C']['slope'] * (i - fits['d13C']['x_ref'])).where(np.isfinite(y13s) & np.isfinite(i))
                    if fits and np.isfinite(fits.get('d18O', {}).get('slope', np.nan)) and intensity_col in clean_shp.columns:
                        i = pd.to_numeric(clean_shp[intensity_col], errors='coerce')
                        y18s = (y18s - fits['d18O']['slope'] * (i - fits['d18O']['x_ref'])).where(np.isfinite(y18s) & np.isfinite(i))
                    d13c_sd_val = float(y13s.std()) if y13s is not None else np.nan
                    d18o_sd_val = float(y18s.std()) if y18s is not None else np.nan
            except Exception:
                pass

            # Write Equipment and standard deviation block on the right
            equip_title_fmt = workbook.add_format({'bold': True})
            worksheet.write(1, 10, "Equiment:", equip_title_fmt)
            worksheet.write(1, 11, "ThermoFisher Scientific MAT253 gas isotope ratio mass spectrometer")
            worksheet.write(2, 11, "Kiel IV automated carbonate preparation device")
            worksheet.write(4, 10, "Standard deviation of SHP2L over measurement period:", equip_title_fmt)
            worksheet.write(5, 11, f"{0.00 if np.isnan(d13c_sd_val) else d13c_sd_val:.2f} ‰ for d13C")
            worksheet.write(6, 11, f"{0.00 if np.isnan(d18o_sd_val) else d18o_sd_val:.2f} ‰ for d18O")
            worksheet.write(7, 11, f"{n_used} n")

            # Insert textbox with provided content
            materials_text = (
                "When results produced at P2L are being published, we suggest to use the following text in the “Material and Methods” section of the publication:\n\n"
                "\"Analyses on (your samples) for determination of d13C and d18O were performed at the Paleoceanography and Paleoclimatology Laboratory, School of Arts, Sciences and Humanities of the University of Sāo Paulo, Brazil. The laboratory is equipped with a Thermo Fisher Scientific™ MAT253 isotope ratio mass spectrometer (IRMS) coupled with a Thermo Fisher Scientific™ Kiel IV carbonate preparation device. The details on the laboratory analytical setup and performance are described in Crivellari et al. (2021). The IRMS measures the isotopic composition of the CO2 developed by the reaction between the sample carbonate and orthophosphoric acid at 70°C. Measurements were calibrated against repeated analyses of SHP2L reference material which is used as internal working standard (Crivellari et al., 2021). SHP2L is in turn calibrated against international reference material NBS19 and values are anchored to the Vienna Pee Dee Belemnite (VPDB) scale. Analytical precision was better than (please use the value informed by P2L) ‰ for d13C and (please use the value informed by P2L) ‰ for d18O (±1 s, n = please use the value informed by P2L).\"\n\n"
                "Reference\nCrivellari, S., Viana, P.J., Campos, M.D., Kuhnert, H., Lopes, A.B.M., da Cruz, F.W., Chiessi, C.M., 2021. Development and characterization of a new in-house reference material for stable carbon and oxygen isotopes analyses. Journal of Analytical Atomic Spectrometry 36, 1125-1134. DOI: 10.1039/D1JA00030F."
            )
            # Skip adding textbox since the sheet is not created in this workbook
        except Exception:
            # Silently skip any errors in this bypassed block
            pass
        
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
                        'd13C Precision': d13c_precision,
                        'd13C Average': d13c_average,
                        'd18O Precision': d18o_precision,
                        'd18O Average': d18o_average,
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

        # New standards export block aligned with Calibration outlier filtering
        if selected_standards:
            # Determine outlier method/thresholds from args or session_state
            _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
            _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
            _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)

            standards_rows = []

            # Write (unfiltered) standards measurements if not already present
            try:
                if "Standards Measurements" not in writer.sheets:
                    std_mask = df['Identifier 1'].isin(selected_standards)
                    df[std_mask].to_excel(writer, index=False, sheet_name="Standards Measurements")
            except Exception:
                pass

            # Build a consolidated clean standards frame for fits/params
            try:
                clean_stds_all = _filter_standards_remove_outliers(
                    df,
                    selected_standards,
                    _method,
                    _sigma,
                    _iqr,
                )
            except Exception:
                clean_stds_all = df[df['Identifier 1'].isin(selected_standards)].copy()

            # Compute linearity fits on cleaned standards (used for precision-after-correction)
            intensity_col = '1  Cycle Int  Samp  44'
            try:
                fit13 = _compute_linearity_fit(clean_stds_all, 'd 13C/12C  Mean', intensity_col)
            except Exception:
                fit13 = {'slope': np.nan, 'intercept': np.nan, 'r2': np.nan, 'x_ref': np.nan, 'n': 0}
            try:
                fit18 = _compute_linearity_fit(clean_stds_all, 'd 18O/16O  Mean', intensity_col)
            except Exception:
                fit18 = {'slope': np.nan, 'intercept': np.nan, 'r2': np.nan, 'x_ref': np.nan, 'n': 0}

            # Build per-standard summary rows, including precision after linearity correction
            for _std in selected_standards:
                _std_df = df[df['Identifier 1'] == _std].copy()
                if _std_df.empty:
                    continue

                _total = len(_std_df)
                try:
                    if _method == "Z-Score":
                        _d13 = identify_outliers(_std_df, 'd 13C/12C  Mean', _sigma)
                        _d18 = identify_outliers(_std_df, 'd 18O/16O  Mean', _sigma)
                    else:
                        _d13 = identify_outliers_iqr(_std_df, 'd 13C/12C  Mean', _iqr)
                        _d18 = identify_outliers_iqr(_std_df, 'd 18O/16O  Mean', _iqr)
                    _clean = _std_df.loc[~(_d13 | _d18)].copy()
                except Exception:
                    _clean = _std_df.copy()

                _included = len(_clean)
                _d13p = _clean['d 13C/12C  Mean'].std()
                _d13m = _clean['d 13C/12C  Mean'].mean()
                _d18p = _clean['d 18O/16O  Mean'].std()
                _d18m = _clean['d 18O/16O  Mean'].mean()

                # Precision after linearity correction (if fits available)
                _d13p_lin = np.nan
                _d18p_lin = np.nan
                try:
                    if np.isfinite(fit13.get('slope', np.nan)) and intensity_col in _clean.columns:
                        i = pd.to_numeric(_clean[intensity_col], errors='coerce')
                        y = pd.to_numeric(_clean['d 13C/12C  Mean'], errors='coerce')
                        corr = (y - fit13['slope'] * (i - fit13['x_ref'])).where(np.isfinite(y) & np.isfinite(i))
                        _d13p_lin = float(corr.std())
                    if np.isfinite(fit18.get('slope', np.nan)) and intensity_col in _clean.columns:
                        i = pd.to_numeric(_clean[intensity_col], errors='coerce')
                        y = pd.to_numeric(_clean['d 18O/16O  Mean'], errors='coerce')
                        corr = (y - fit18['slope'] * (i - fit18['x_ref'])).where(np.isfinite(y) & np.isfinite(i))
                        _d18p_lin = float(corr.std())
                except Exception:
                    pass

                _method_label = f"Z-Score (Ïƒ={_sigma})" if _method == "Z-Score" else f"IQR (Ã—{_iqr})"

                standards_rows.append({
                    'Standard': _std,
                    'd13C Precision': _d13p,
                    'd13C Precision (Lin-Corr)': _d13p_lin,
                    'd13C Average': _d13m,
                    'd18O Precision': _d18p,
                    'd18O Precision (Lin-Corr)': _d18p_lin,
                    'd18O Average': _d18m,
                    'Sample Count': _included,
                    'Total Samples': _total,
                    'Outlier Method': _method_label,
                    'Calibration Type': 'Single Anchor' if len(selected_standards) == 1 else 'Double Anchor',
                })

            if standards_rows:
                # Write the per-standard summary table
                pd.DataFrame(standards_rows).to_excel(writer, index=False, sheet_name="Standards Results")

                # Append calibration and linearity parameters below the table
                try:
                    worksheet = writer.sheets["Standards Results"]
                    start_row = len(standards_rows) + 2

                    # Calibration parameters section
                    worksheet.write(start_row, 0, "Calibration Parameters")
                    start_row += 1
                    cal_type_text = 'Single Anchor' if len(selected_standards) == 1 else 'Double Anchor'
                    worksheet.write(start_row, 0, f"Type: {cal_type_text}")
                    start_row += 1
                    worksheet.write(start_row, 0, f"Standards used: {', '.join(selected_standards)}")
                    start_row += 2

                    # Compute calibration parameters from cleaned standards
                    try:
                        if len(selected_standards) == 1:
                            std = selected_standards[0]
                            # d13C single-point
                            raw13 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == std, 'd 13C/12C  Mean'], errors='coerce').mean()
                            tru13 = get_true_value(std, ISOTYPE_D13C)
                            worksheet.write(start_row, 0, "d13C Single-Point: raw_std(mean)")
                            worksheet.write(start_row, 1, float(raw13) if pd.notna(raw13) else np.nan)
                            worksheet.write(start_row, 2, "true_std")
                            worksheet.write(start_row, 3, float(tru13))
                            start_row += 1
                            # d18O single-point
                            raw18 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == std, 'd 18O/16O  Mean'], errors='coerce').mean()
                            tru18 = get_true_value(std, ISOTYPE_D18O)
                            worksheet.write(start_row, 0, "d18O Single-Point: raw_std(mean)")
                            worksheet.write(start_row, 1, float(raw18) if pd.notna(raw18) else np.nan)
                            worksheet.write(start_row, 2, "true_std")
                            worksheet.write(start_row, 3, float(tru18))
                            start_row += 2
                            worksheet.write(start_row, 0, "Formula")
                            worksheet.write(start_row, 1, "((raw+1000)*(true+1000))/(raw_std+1000)-1000")
                            start_row += 2
                        elif len(selected_standards) == 2:
                            s1, s2 = selected_standards
                            # d13C double-point
                            raw13_1 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == s1, 'd 13C/12C  Mean'], errors='coerce').mean()
                            raw13_2 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == s2, 'd 13C/12C  Mean'], errors='coerce').mean()
                            tru13_1 = get_true_value(s1, ISOTYPE_D13C)
                            tru13_2 = get_true_value(s2, ISOTYPE_D13C)
                            m13 = (tru13_2 - tru13_1) / (raw13_2 - raw13_1) if pd.notna(raw13_1) and pd.notna(raw13_2) else np.nan
                            b13 = tru13_1 - m13 * raw13_1 if pd.notna(m13) and pd.notna(raw13_1) else np.nan
                            worksheet.write(start_row, 0, "d13C Double-Point: slope (m)")
                            worksheet.write(start_row, 1, float(m13) if pd.notna(m13) else np.nan)
                            worksheet.write(start_row, 2, "intercept (b)")
                            worksheet.write(start_row, 3, float(b13) if pd.notna(b13) else np.nan)
                            start_row += 1
                            # d18O double-point
                            raw18_1 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == s1, 'd 18O/16O  Mean'], errors='coerce').mean()
                            raw18_2 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == s2, 'd 18O/16O  Mean'], errors='coerce').mean()
                            tru18_1 = get_true_value(s1, ISOTYPE_D18O)
                            tru18_2 = get_true_value(s2, ISOTYPE_D18O)
                            m18 = (tru18_2 - tru18_1) / (raw18_2 - raw18_1) if pd.notna(raw18_1) and pd.notna(raw18_2) else np.nan
                            b18 = tru18_1 - m18 * raw18_1 if pd.notna(m18) and pd.notna(raw18_1) else np.nan
                            worksheet.write(start_row, 0, "d18O Double-Point: slope (m)")
                            worksheet.write(start_row, 1, float(m18) if pd.notna(m18) else np.nan)
                            worksheet.write(start_row, 2, "intercept (b)")
                            worksheet.write(start_row, 3, float(b18) if pd.notna(b18) else np.nan)
                            start_row += 2
                            worksheet.write(start_row, 0, "Formula")
                            worksheet.write(start_row, 1, "cal = m*raw + b")
                            start_row += 2
                    except Exception:
                        # Ignore if parameter derivation fails
                        pass

                    # Linearity correction parameters section
                    worksheet.write(start_row, 0, "Linearity Correction Parameters")
                    start_row += 1
                    # d13C
                    worksheet.write(start_row, 0, "d13C slope")
                    worksheet.write(start_row, 1, float(fit13.get('slope', np.nan)) if np.isfinite(fit13.get('slope', np.nan)) else np.nan)
                    worksheet.write(start_row, 2, "intercept")
                    worksheet.write(start_row, 3, float(fit13.get('intercept', np.nan)) if np.isfinite(fit13.get('intercept', np.nan)) else np.nan)
                    start_row += 1
                    worksheet.write(start_row, 0, "d13C R^2")
                    worksheet.write(start_row, 1, float(fit13.get('r2', np.nan)) if np.isfinite(fit13.get('r2', np.nan)) else np.nan)
                    worksheet.write(start_row, 2, "x_ref (V)")
                    worksheet.write(start_row, 3, float(fit13.get('x_ref', np.nan)) if np.isfinite(fit13.get('x_ref', np.nan)) else np.nan)
                    start_row += 1
                    worksheet.write(start_row, 0, "d13C points (n)")
                    worksheet.write(start_row, 1, int(fit13.get('n', 0)))
                    start_row += 1
                    # d18O
                    worksheet.write(start_row, 0, "d18O slope")
                    worksheet.write(start_row, 1, float(fit18.get('slope', np.nan)) if np.isfinite(fit18.get('slope', np.nan)) else np.nan)
                    worksheet.write(start_row, 2, "intercept")
                    worksheet.write(start_row, 3, float(fit18.get('intercept', np.nan)) if np.isfinite(fit18.get('intercept', np.nan)) else np.nan)
                    start_row += 1
                    worksheet.write(start_row, 0, "d18O R^2")
                    worksheet.write(start_row, 1, float(fit18.get('r2', np.nan)) if np.isfinite(fit18.get('r2', np.nan)) else np.nan)
                    worksheet.write(start_row, 2, "x_ref (V)")
                    worksheet.write(start_row, 3, float(fit18.get('x_ref', np.nan)) if np.isfinite(fit18.get('x_ref', np.nan)) else np.nan)
                    start_row += 1
                    worksheet.write(start_row, 0, "d18O points (n)")
                    worksheet.write(start_row, 1, int(fit18.get('n', 0)))
                except Exception:
                    # If appending extra info fails, continue without blocking export
                    pass
    
    towrite.seek(0)

    # Create dataset download button
    st.download_button(
        label="Download Dataset Excel",
        data=towrite,
        file_name=filename,
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        key=f"download_dataset_{filename}"
    )

    # Build a separate Client Output file and download button
    try:
        intensity_col = '1  Cycle Int  Samp  44'
        fits = st.session_state.get('linearity_fits') if isinstance(st.session_state, dict) else None
        if (not fits) and selected_standards:
            _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
            _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
            _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)
            # Use the full session dataset for computing fits, not the filtered export frame
            base_df = st.session_state.df if 'df' in st.session_state else df
            clean_stds_all = _filter_standards_remove_outliers(base_df, selected_standards, _method, _sigma, _iqr)
            f13 = _compute_linearity_fit(clean_stds_all, 'd 13C/12C  Mean', intensity_col)
            f18 = _compute_linearity_fit(clean_stds_all, 'd 18O/16O  Mean', intensity_col)
            fits = {
                'd13C': {'slope': f13.get('slope', np.nan), 'x_ref': f13.get('x_ref', np.nan)},
                'd18O': {'slope': f18.get('slope', np.nan), 'x_ref': f18.get('x_ref', np.nan)},
            }

        def _build_corrected(series_cal, series_raw, isotope_key):
            try:
                s_cal = pd.to_numeric(series_cal, errors='coerce') if series_cal is not None else None
            except Exception:
                s_cal = None
            try:
                s_raw = pd.to_numeric(series_raw, errors='coerce') if series_raw is not None else None
            except Exception:
                s_raw = None
            if s_cal is not None and fits and np.isfinite(fits.get(isotope_key, {}).get('slope', np.nan)) and intensity_col in df.columns:
                i = pd.to_numeric(df[intensity_col], errors='coerce')
                slope = fits[isotope_key]['slope']; xr = fits[isotope_key]['x_ref']
                return (s_cal - slope * (i - xr)).where(np.isfinite(s_cal) & np.isfinite(i))
            if s_cal is not None:
                return s_cal
            if s_raw is not None and fits and np.isfinite(fits.get(isotope_key, {}).get('slope', np.nan)) and intensity_col in df.columns:
                i = pd.to_numeric(df[intensity_col], errors='coerce')
                slope = fits[isotope_key]['slope']; xr = fits[isotope_key]['x_ref']
                return (s_raw - slope * (i - xr)).where(np.isfinite(s_raw) & np.isfinite(i))
            return s_raw if s_raw is not None else pd.Series(index=df.index, dtype=float)

        # Recompute calibrated series from the (possibly interpolated) export dataframe so corrected values reflect interpolation
        s_cal13 = pd.to_numeric(df['d13C_calibrated'], errors='coerce') if 'd13C_calibrated' in df.columns else None
        s_cal18 = pd.to_numeric(df['d18O_calibrated'], errors='coerce') if 'd18O_calibrated' in df.columns else None
        if selected_standards and len(selected_standards) in (1, 2):
            try:
                _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
                _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
                _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)
                base_df = st.session_state.df if 'df' in st.session_state else df
                clean_stds_cal = _filter_standards_remove_outliers(base_df, selected_standards, _method, _sigma, _iqr)
                cal_tmp = calibrate_results(standards_df=clean_stds_cal if clean_stds_cal is not None else base_df,
                                            full_df=df.copy(),
                                            selected_standards=selected_standards)
                s_cal13 = pd.to_numeric(cal_tmp.get('d13C_calibrated'), errors='coerce')
                s_cal18 = pd.to_numeric(cal_tmp.get('d18O_calibrated'), errors='coerce')
            except Exception:
                pass
        corrected_d13 = _build_corrected(s_cal13 if s_cal13 is not None else df.get('d13C_calibrated'), df.get('d 13C/12C  Mean'), 'd13C')
        corrected_d18 = _build_corrected(s_cal18 if s_cal18 is not None else df.get('d18O_calibrated'), df.get('d 18O/16O  Mean'), 'd18O')

        species_series = _get_species_series(df)
        client_df = pd.DataFrame({
            'Identifier': df['Identifier 1'],
            'Sample #': df.get('Identifier 2', pd.Series(index=df.index, dtype=object)),
            'Species': species_series,
            'd13C (‰, VPDB)  Mean': pd.to_numeric(df.get('d 13C/12C  Mean'), errors='coerce'),
            'd13C (‰, VPDB)  Std Dev': pd.to_numeric(df.get('d 13C/12C  Std Dev'), errors='coerce'),
            'd18O (‰, VPDB)  Mean': pd.to_numeric(df.get('d 18O/16O  Mean'), errors='coerce'),
            'd18O (‰, VPDB)  Std Dev': pd.to_numeric(df.get('d 18O/16O  Std Dev'), errors='coerce'),
            'Corrected d13C (‰, VPDB)': corrected_d13,
            'Corrected d18O (‰, VPDB)': corrected_d18,
        })
        # Apply species replacements if provided
        if comment_map:
            try:
                client_df['Species'] = client_df['Species'].astype(str).map(lambda v: comment_map.get(v, v))
            except Exception:
                pass
        if selected_standards:
            client_df = client_df[~df['Identifier 1'].isin(selected_standards)]

        for rc in ['d13C (‰, VPDB)  Mean','d13C (‰, VPDB)  Std Dev','d18O (‰, VPDB)  Mean','d18O (‰, VPDB)  Std Dev','Corrected d13C (‰, VPDB)','Corrected d18O (‰, VPDB)']:
            if rc in client_df.columns:
                client_df[rc] = pd.to_numeric(client_df[rc], errors='coerce').round(2)

        # SHP2L precision
        d13c_sd_val = np.nan; d18o_sd_val = np.nan; n_used = 0
        try:
            _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
            _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
            _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)
            base_df = st.session_state.df if 'df' in st.session_state else df
            shp = base_df[base_df['Identifier 1'] == 'SHP2L'].copy() if 'Identifier 1' in base_df.columns else pd.DataFrame()
            if not shp.empty:
                if _method == "Z-Score":
                    m13 = identify_outliers(shp, 'd 13C/12C  Mean', _sigma)
                    m18 = identify_outliers(shp, 'd 18O/16O  Mean', _sigma)
                else:
                    m13 = identify_outliers_iqr(shp, 'd 13C/12C  Mean', _iqr)
                    m18 = identify_outliers_iqr(shp, 'd 18O/16O  Mean', _iqr)
                clean_shp = shp.loc[~(m13 | m18)].copy()
                n_used = len(clean_shp)
                y13s = pd.to_numeric(clean_shp.get('d 13C/12C  Mean'), errors='coerce')
                y18s = pd.to_numeric(clean_shp.get('d 18O/16O  Mean'), errors='coerce')
                if fits and np.isfinite(fits.get('d13C', {}).get('slope', np.nan)) and intensity_col in clean_shp.columns:
                    i = pd.to_numeric(clean_shp[intensity_col], errors='coerce')
                    y13s = (y13s - fits['d13C']['slope'] * (i - fits['d13C']['x_ref'])).where(np.isfinite(y13s) & np.isfinite(i))
                if fits and np.isfinite(fits.get('d18O', {}).get('slope', np.nan)) and intensity_col in clean_shp.columns:
                    i = pd.to_numeric(clean_shp[intensity_col], errors='coerce')
                    y18s = (y18s - fits['d18O']['slope'] * (i - fits['d18O']['x_ref'])).where(np.isfinite(y18s) & np.isfinite(i))
                d13c_sd_val = float(y13s.std()) if y13s is not None else np.nan
                d18o_sd_val = float(y18s.std()) if y18s is not None else np.nan
        except Exception:
            pass

        client_towrite = BytesIO()
        with pd.ExcelWriter(client_towrite, engine='xlsxwriter') as w2:
            client_df.to_excel(w2, index=False, sheet_name='Client Output')
            wb = w2.book; ws = w2.sheets['Client Output']
            header_fmt = wb.add_format({'bold': True}); corrected_hdr_fmt = wb.add_format({'bold': True, 'font_color': '#6A1B9A'})
            num_fmt = wb.add_format({'num_format': '0.00'})
            headers = list(client_df.columns)
            for col_idx, col_name in enumerate(headers):
                ws.write(0, col_idx, col_name, corrected_hdr_fmt if 'Corrected' in col_name else header_fmt)
                width = 18 if col_name in ('Identifier','Species') else (22 if 'Corrected' in col_name else 15)
                ws.set_column(col_idx, col_idx, width, num_fmt if col_name in ['d13C (‰, VPDB)  Mean','d13C (‰, VPDB)  Std Dev','d18O (‰, VPDB)  Mean','d18O (‰, VPDB)  Std Dev','Corrected d13C (‰, VPDB)','Corrected d18O (‰, VPDB)'] else None)
            equip_title_fmt = wb.add_format({'bold': True})
            ws.write(1, 10, 'Equiment:', equip_title_fmt)
            ws.write(1, 11, 'ThermoFisher Scientific MAT253 gas isotope ratio mass spectrometer')
            ws.write(2, 11, 'Kiel IV automated carbonate preparation device')
            ws.write(4, 10, 'Standard deviation of SHP2L over measurement period:', equip_title_fmt)
            ws.write(5, 11, f"{0.00 if np.isnan(d13c_sd_val) else d13c_sd_val:.2f} ‰ for d13C")
            ws.write(6, 11, f"{0.00 if np.isnan(d18o_sd_val) else d18o_sd_val:.2f} ‰ for d18O")
            ws.write(7, 11, f"{n_used} n")
            materials_text = (
                "When results produced at P2L are being published, we suggest to use the following text in the “Material and Methods” section of the publication:\n\n"
                "\"Analyses on (your samples) for determination of d13C and d18O were performed at the Paleoceanography and Paleoclimatology Laboratory, School of Arts, Sciences and Humanities of the University of Sāo Paulo, Brazil. The laboratory is equipped with a Thermo Fisher Scientific™ MAT253 isotope ratio mass spectrometer (IRMS) coupled with a Thermo Fisher Scientific™ Kiel IV carbonate preparation device. The details on the laboratory analytical setup and performance are described in Crivellari et al. (2021). The IRMS measures the isotopic composition of the CO2 developed by the reaction between the sample carbonate and orthophosphoric acid at 70°C. Measurements were calibrated against repeated analyses of SHP2L reference material which is used as internal working standard (Crivellari et al., 2021). SHP2L is in turn calibrated against international reference material NBS19 and values are anchored to the Vienna Pee Dee Belemnite (VPDB) scale. Analytical precision was better than (please use the value informed by P2L) ‰ for d13C and (please use the value informed by P2L) ‰ for d18O (±1 s, n = please use the value informed by P2L).\"\n\n"
                "Reference\nCrivellari, S., Viana, P.J., Campos, M.D., Kuhnert, H., Lopes, A.B.M., da Cruz, F.W., Chiessi, C.M., 2021. Development and characterization of a new in-house reference material for stable carbon and oxygen isotopes analyses. Journal of Analytical Atomic Spectrometry 36, 1125-1134. DOI: 10.1039/D1JA00030F."
            )
            ws.insert_textbox('L10', materials_text, {'width': 820, 'height': 580, 'line': {'color': '#4F81BD'}})

        client_towrite.seek(0)
        client_filename = _build_client_filename(client_name, client_df)
        st.download_button(
            label='Download Client Output',
            data=client_towrite,
            file_name=client_filename,
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            key=f"download_client_{client_filename}"
        )
    except Exception as e:
        st.warning(f"Client Output creation failed: {e}")


if "df" not in st.session_state:
    st.session_state.df = None

def main():
    st.title('Isotope Ratio Mass Spectrometer Data Analyzer')

    # Initialize session state variables if they don't exist
    if 'df' not in st.session_state:
        st.session_state.df = None
    if 'df_cycles_source' not in st.session_state:
        st.session_state.df_cycles_source = None
    if 'edited_delta_rows' not in st.session_state:
        st.session_state.edited_delta_rows = set()
    if 'original_delta_values' not in st.session_state:
        st.session_state.original_delta_values = {}
    if 'calibration_coefficients' not in st.session_state:
        st.session_state.calibration_coefficients = {}
    if 'file_processed' not in st.session_state:
        st.session_state.file_processed = False
    if 'confirm_reset' not in st.session_state:
        st.session_state.confirm_reset = False
    if AUTOSAVE_LOG_PATH_KEY not in st.session_state:
        st.session_state[AUTOSAVE_LOG_PATH_KEY] = None
    if AUTOSAVE_SNAPSHOT_PATH_KEY not in st.session_state:
        st.session_state[AUTOSAVE_SNAPSHOT_PATH_KEY] = None
    if AUTOSAVE_SAVE_DIR_KEY not in st.session_state:
        st.session_state[AUTOSAVE_SAVE_DIR_KEY] = None
    if AUTOSAVE_ERROR_KEY not in st.session_state:
        st.session_state[AUTOSAVE_ERROR_KEY] = None
    if AUTOSAVE_EVENT_COUNT_KEY not in st.session_state:
        st.session_state[AUTOSAVE_EVENT_COUNT_KEY] = 0
    if AUTOSAVE_INIT_TS_KEY not in st.session_state:
        st.session_state[AUTOSAVE_INIT_TS_KEY] = None
    if AUTOSAVE_DIR_OVERRIDE_KEY not in st.session_state:
        st.session_state[AUTOSAVE_DIR_OVERRIDE_KEY] = ""
    if AUTOSAVE_SOURCE_FILES_KEY not in st.session_state:
        st.session_state[AUTOSAVE_SOURCE_FILES_KEY] = []
    if AUTOSAVE_META_PATH_KEY not in st.session_state:
        st.session_state[AUTOSAVE_META_PATH_KEY] = None
    if AUTOSAVE_RESUMED_KEY not in st.session_state:
        st.session_state[AUTOSAVE_RESUMED_KEY] = False
    if AUTOSAVE_SESSION_TOKEN_KEY not in st.session_state:
        st.session_state[AUTOSAVE_SESSION_TOKEN_KEY] = None

    tab_import, tab1, tab2, tab3 = st.tabs([
        'Data import',
        'Diagnostics',
        'Calibration',
        'Data Processing'
    ])

    has_data = False

    with tab_import:
        autosave_dir_override = st.text_input(
            "Session autosave folder (optional)",
            value=st.session_state.get(AUTOSAVE_DIR_OVERRIDE_KEY, ""),
            help=(
                "Leave blank to auto-detect from uploaded filenames (fallback: current app folder). "
                "Set this path to force autosave into a specific Excel folder."
            ),
        )
        st.session_state[AUTOSAVE_DIR_OVERRIDE_KEY] = str(autosave_dir_override).strip()

        # File uploader
        uploaded_files = st.file_uploader(
            "Choose XLS files",
            type=['xls', 'xlsx'],
            accept_multiple_files=True
        )

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
                st.session_state.df_cycles_source = None
                st.session_state.edited_delta_rows = set()
                st.session_state.original_delta_values = {}
                st.session_state.calibration_coefficients = {}
                _reset_autosave_state()
                st.session_state.confirm_reset = False  # Reset confirmation state
            elif col2.button("Cancel", key="cancel_load_btn"):
                st.session_state.confirm_reset = False  # Cancel reset and close prompt

        # Only load files if they haven't been processed yet
        if uploaded_files and not st.session_state.file_processed:
            try:
                dfs = []
                dfs_cycles_source = []
                loaded_file_specs = []
                for uploaded_file in uploaded_files:
                    try:
                        # First try with openpyxl engine (check for multi-row headers)
                        raw = pd.read_excel(uploaded_file, header=None, engine='openpyxl')
                        df = _parse_new_table_layout(raw)
                        if df is None:
                            uploaded_file.seek(0)
                            df = pd.read_excel(uploaded_file, engine='openpyxl')
                    except Exception as e:
                        try:
                            # If openpyxl fails, try with xlrd engine
                            uploaded_file.seek(0)
                            raw = pd.read_excel(uploaded_file, header=None, engine='xlrd')
                            df = _parse_new_table_layout(raw)
                            if df is None:
                                uploaded_file.seek(0)
                                df = pd.read_excel(uploaded_file, engine='xlrd')
                        except Exception as e:
                            st.error(f"Failed to read Excel file '{uploaded_file.name}': {str(e)}")
                            continue
                    
                    # Standardize types and create a clean copy
                    df = _coalesce_duplicate_columns(df)
                    df = df.convert_dtypes()
                    df.reset_index(drop=True, inplace=True)
                    df = df.map(lambda x: None if pd.isna(x) else x)
                    df['Excel File'] = uploaded_file.name

                    # Normalize isotope column names
                    df = _standardize_isotope_columns(df)
                    df = _coalesce_duplicate_columns(df)
                    # Ensure isotope mean/std columns exist
                    for col in ['d 13C/12C  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Mean', 'd 18O/16O  Std Dev']:
                        if col not in df.columns:
                            df[col] = np.nan

                    # Convert the DataFrame 'Date' column to datetime with explicit format
                    if 'Date' in df.columns:
                        df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%y', errors='coerce')
                    elif 'Start Time' in df.columns:
                        df['Date'] = pd.to_datetime(df['Start Time'], errors='coerce')

                    if 'Date' in df.columns:
                        df['Date_ordinal'] = pd.to_numeric(
                            df['Date'].map(lambda x: x.toordinal() if pd.notnull(x) else None)
                        )

                    # Save original columns for reference
                    original_columns = df.columns.tolist()

                    # Extract values from Information column when present
                    if 'Information' in df.columns:
                        df = extract_info_values(df)

                    # Map structured columns to analysis fields when present (tolerate unicode variants)
                    leak_col = _find_column(df, 'Kiel IV Leak Rate')
                    if leak_col and 'leak_rate' not in df.columns:
                        df['leak_rate'] = _extract_numeric(df[leak_col])
                    gases_col = _find_column(df, 'Kiel IV Non Condensable Pressure', 'Kiel IV Non-Condensable Pressure')
                    if gases_col and 'p_gases' not in df.columns:
                        df['p_gases'] = _extract_numeric(df[gases_col])
                    residual_col = _find_column(df, 'Kiel IV Residual CO2 Pressure')
                    if residual_col and 'p_no_acid' not in df.columns:
                        df['p_no_acid'] = _extract_numeric(df[residual_col])
                    sample_col = _find_column(df, 'Kiel IV CO2 Sample Pressure')
                    if sample_col and 'total_co2' not in df.columns:
                        df['total_co2'] = _extract_numeric(df[sample_col])

                    if '1  Cycle Int  Samp  44' in df.columns:
                        df['1  Cycle Int  Samp  44'] = _normalize_signal_intensity(df['1  Cycle Int  Samp  44'])
                    else:
                        intensity_candidates = [
                            'Pressure Adjust Result Intensity',
                            'Pressure Adjust Initial Intensity',
                            'Initial Intensity from µ-Volume',
                            'Initial Intensity from μ-Volume',
                            'Initial Intensity from Âµ-Volume'
                        ]
                        for cand in intensity_candidates:
                            col = _find_column(df, cand)
                            if col:
                                df['1  Cycle Int  Samp  44'] = _normalize_signal_intensity(df[col])
                                break

                    if 'Label' in df.columns:
                        label_parts = df['Label'].apply(_split_label_species)
                        if 'Identifier 1' not in df.columns:
                            df['Identifier 1'] = label_parts.map(lambda v: v[0] if v else None)
                        if 'Species' not in df.columns:
                            df['Species'] = label_parts.map(lambda v: v[1] if v else None)
                    elif 'Identifier 1' not in df.columns:
                        if 'Sample' in df.columns:
                            df['Identifier 1'] = df['Sample']
                        else:
                            df['Identifier 1'] = None
                    if 'Identifier 2' not in df.columns:
                        if 'Comment' in df.columns:
                            df['Identifier 2'] = df['Comment']
                        elif 'Run ID' in df.columns:
                            df['Identifier 2'] = df['Run ID']
                        elif 'Index' in df.columns:
                            df['Identifier 2'] = df['Index']
                        else:
                            df['Identifier 2'] = None

                    if 'Comment' not in df.columns and 'Sample Type' in df.columns:
                        df['Comment'] = df['Sample Type']
                    # Leave Species empty unless provided or parsed from Label

                    # Normalize Label to "Identifier 1 - Species" when possible
                    if 'Identifier 1' in df.columns:
                        df['Label'] = _compose_label_series(
                            df['Identifier 1'],
                            df.get('Species', pd.Series(index=df.index, dtype=object))
                        )

                    # Ensure required analysis columns exist
                    for col in ['leak_rate', 'p_no_acid', 'total_co2', 'p_gases', '1  Cycle Int  Samp  44', 'Line']:
                        if col not in df.columns:
                            df[col] = np.nan

                    # Preserve full rows (including cycle rows) for point-level diagnostics.
                    dfs_cycles_source.append(df.copy())

                    # Compute per-sample means from cycles when Cycle Number is present
                    df = _apply_cycle_averages(df)

                    # Ensure all original columns are included
                    for col in original_columns:
                        if col not in df.columns:
                            df[col] = None

                    dfs.append(df)
                    file_size = getattr(uploaded_file, "size", None)
                    try:
                        file_size = int(file_size) if file_size is not None else None
                    except Exception:
                        file_size = None
                    file_md5 = None
                    try:
                        if hasattr(uploaded_file, "getvalue"):
                            file_md5 = hashlib.md5(uploaded_file.getvalue()).hexdigest().lower()
                    except Exception:
                        file_md5 = None
                    loaded_file_specs.append({"name": uploaded_file.name, "size": file_size, "md5": file_md5})

                if not dfs:
                    return

                df = pd.concat(dfs, ignore_index=True, sort=False) if len(dfs) > 1 else dfs[0]
                df_cycles_source = (
                    pd.concat(dfs_cycles_source, ignore_index=True, sort=False)
                    if len(dfs_cycles_source) > 1 else
                    (dfs_cycles_source[0] if dfs_cycles_source else None)
                )

                autosave_state = _initialize_autosave_session(loaded_file_specs, base_df=df)
                resumed_df = autosave_state.get("resumed_df")
                active_df = resumed_df if resumed_df is not None else df

                # Save df to session_state
                st.session_state.df = active_df
                st.session_state.df_cycles_source = df_cycles_source
                restored_rows = autosave_state.get("edited_rows", set()) if autosave_state.get("resumed") else set()
                st.session_state.edited_delta_rows = set(restored_rows)
                st.session_state.original_delta_values = {}
                st.session_state.calibration_coefficients = {}
                st.session_state.file_processed = True

                autosave_initialized = bool(autosave_state.get("ok"))
                if autosave_initialized:
                    _autosave_session_update(
                        "session_resumed" if autosave_state.get("resumed") else "session_loaded",
                        changes=[],
                        context={
                            "uploaded_files": [str(s.get("name", "")) for s in loaded_file_specs],
                            "source_folder": st.session_state.get(AUTOSAVE_SAVE_DIR_KEY),
                            "resumed": bool(autosave_state.get("resumed")),
                        },
                    )

            except Exception as e:
                st.error(f"Error loading file: {e}")

        # Display a warning if no file is uploaded
        if st.session_state.df is None:
            st.warning("Please upload a file to begin analysis.")
        else:
            autosave_log = st.session_state.get(AUTOSAVE_LOG_PATH_KEY)
            autosave_snapshot = st.session_state.get(AUTOSAVE_SNAPSHOT_PATH_KEY)
            autosave_error = st.session_state.get(AUTOSAVE_ERROR_KEY)
            autosave_events = int(st.session_state.get(AUTOSAVE_EVENT_COUNT_KEY, 0))
            autosave_resumed = bool(st.session_state.get(AUTOSAVE_RESUMED_KEY, False))
            if autosave_log:
                st.caption(f"Session edit log: {autosave_log}")
            if autosave_snapshot:
                st.caption(f"Session autosave snapshot: {autosave_snapshot}")
            st.caption(f"Autosave events written: {autosave_events}")
            if autosave_resumed:
                st.caption("Autosave status: resumed existing session snapshot.")
            if autosave_error:
                st.warning(f"Session autosave warning: {autosave_error}")
            if not st.session_state.get(AUTOSAVE_DIR_OVERRIDE_KEY):
                st.info(
                    "Autosave folder is auto-detected from matching workbook files on this computer. "
                    "If detection picked the wrong folder, set 'Session autosave folder (optional)' above."
                )
            if st.button("Save Session Now", key="save_session_now_btn"):
                wrote = _autosave_session_update("manual_save", changes=[], context={"trigger": "manual_button"})
                if wrote:
                    st.success("Session saved to autosave files.")
                else:
                    st.warning("Session save did not run. Check autosave warning above.")

            # Display data preview if available
            with st.expander("Data Table", expanded=True):
                # Display the DataFrame using Streamlit's native table component
                st.dataframe(
                    st.session_state.df,
                    height=400,  # Set table height for vertical scroll
                    width='stretch'  # Use full width of the container
                )

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
            st.dataframe(count_df, hide_index=True, width='stretch')
            # Display metrics
            metrics_col1, metrics_col2 = st.columns(2)
            metrics_col1.metric("Total Unique Samples", total_unique)
            metrics_col2.metric("Total Measurements", total_measurements)

        has_data = st.session_state.df is not None

    if not has_data:
        return
    # Sidebar for user-selected sigma level
    # with st.sidebar:
    #     sigma_level = st.number_input("Set Sigma Level for Outlier Exclusion",
    #                                   min_value=0.1,
    #                                   max_value=5.0,
    #                                   value=1.0,
    #                                   step=0.1)

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
        
        # Create two columns for controls
        col1, col2 = st.columns(2)

        with col1:
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
            
        with col2:
            st.subheader('Value Ranges')
            # d13C/12C Mean range selector
            d13c_min = float(st.session_state.df['d 13C/12C  Mean'].min())
            d13c_max = float(st.session_state.df['d 13C/12C  Mean'].max())
            d13c_range = st.slider(
                "Select min and max d13C/12C Mean",
                min_value=d13c_min,
                max_value=d13c_max,
                value=(d13c_min, d13c_max),
                step=0.1
            )
            
            # d18O/16O Mean range selector
            d18o_min = float(st.session_state.df['d 18O/16O  Mean'].min())
            d18o_max = float(st.session_state.df['d 18O/16O  Mean'].max())
            d18o_range = st.slider(
                "Select min and max d18O/16O Mean",
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
        st.plotly_chart(fig, width='stretch')

    with tab2:
            st.header("Calibration")

            # Load standards reference data
            standards_reference = pd.read_csv('standards.csv')
            # Normalize isotopic type labels to avoid encoding mismatches
            try:
                standards_reference['Isotopic_Value_Type'] = (
                    standards_reference['Isotopic_Value_Type']
                    .astype(str)
                    .str.strip()
                    .replace({
                        'VPDB(13C)': ISOTYPE_D13C,
                        'VSMOW(18O)': ISOTYPE_D18O,
                        'dVPDB(13C)': ISOTYPE_D13C,
                        'dVSMOW(18O)': ISOTYPE_D18O,
                        '?VPDB(13C)': ISOTYPE_D13C,
                        '?VSMOW(18O)': ISOTYPE_D18O,
                        'δVPDB(13C)': ISOTYPE_D13C,
                        'δVSMOW(18O)': ISOTYPE_D18O,
                        'Î´VPDB(13C)': ISOTYPE_D13C,
                        'Î´VSMOW(18O)': ISOTYPE_D18O,
                        '??VPDB(13C)': ISOTYPE_D13C,
                        '??VSMOW(18O)': ISOTYPE_D18O,
                    })
                )
            except Exception:
                pass

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
                st.session_state.selected_standards = selected_standards

            with col2:
                st.markdown("#### Outlier Detection")
                sigma_level = st.number_input("Set Sigma Level for standardÂ´s Outlier Exclusion",
                                            min_value=0.1,
                                            max_value=5.0,
                                            value=1.0,
                                            step=0.1)

                irq_multiplier = st.number_input("Set IQR Multiplier for standardÂ´s Outlier Exclusion",
                                                min_value=1.0,
                                                max_value=10.0,
                                                value=1.5,
                                                step=0.1)

                # User selects the calibration method
                calibration_type = st.selectbox("Choose Outlier Detection Method", options=["Z-Score", "IQR"])

                # Persist current calibration/outlier settings for reuse (e.g., Excel export)
                st.session_state.calibration_type = calibration_type
                st.session_state.sigma_level = sigma_level
                st.session_state.irq_multiplier = irq_multiplier

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

            # Precision date range selection (standards only)
            precision_date_bounds = None
            date_col = _find_column(st.session_state.df, 'Date')
            if date_col:
                date_series_all = pd.to_datetime(st.session_state.df[date_col], errors='coerce')
                valid_dates = date_series_all.dropna()
                if not valid_dates.empty:
                    min_date = valid_dates.min().date()
                    max_date = valid_dates.max().date()

                    def _normalize_date_range(value):
                        if isinstance(value, (list, tuple)) and len(value) == 2:
                            start_val, end_val = value
                        elif value is not None:
                            start_val = value
                            end_val = value
                        else:
                            start_val = min_date
                            end_val = max_date
                        try:
                            start_val = pd.Timestamp(start_val).date()
                            end_val = pd.Timestamp(end_val).date()
                        except Exception:
                            start_val = min_date
                            end_val = max_date
                        if start_val < min_date:
                            start_val = min_date
                        if end_val > max_date:
                            end_val = max_date
                        if start_val > end_val:
                            start_val, end_val = end_val, start_val
                        return start_val, end_val

                    stored_range = st.session_state.get('precision_date_range')
                    default_start, default_end = _normalize_date_range(stored_range)
                    if 'precision_date_range_input' not in st.session_state:
                        st.session_state.precision_date_range_input = (default_start, default_end)

                    st.markdown("#### Precision Date Range")
                    precision_date_range = st.date_input(
                        "Select date range for precision calculations (standards only):",
                        min_value=min_date,
                        max_value=max_date,
                        value=st.session_state.precision_date_range_input,
                        key="precision_date_range_input",
                    )
                    start_date, end_date = _normalize_date_range(precision_date_range)
                    st.session_state.precision_date_range = (start_date, end_date)
                    if start_date is not None and end_date is not None:
                        st.caption(
                            f"Precision calculations use standards dated {start_date:%Y-%m-%d} to {end_date:%Y-%m-%d}."
                        )
                        precision_date_bounds = (
                            pd.Timestamp(start_date),
                            pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1),
                        )
                else:
                    st.info("No valid dates available; precision calculations will use all standards.")
            else:
                st.info("No Date column available; precision calculations will use all standards.")
            
            # Compute a clean standards dataframe (outliers removed) for charts/fits as soon as standards are selected
            clean_stds = None
            if selected_standards:
                clean_stds = _filter_standards_remove_outliers(
                    st.session_state.df,
                    selected_standards,
                    calibration_type,
                    sigma_level,
                    irq_multiplier
                )
            clean_stds_for_charts = clean_stds
            if clean_stds_for_charts is not None and precision_date_bounds and date_col and date_col in clean_stds_for_charts.columns:
                date_series_chart = pd.to_datetime(clean_stds_for_charts[date_col], errors='coerce')
                date_mask_chart = (date_series_chart >= precision_date_bounds[0]) & (date_series_chart <= precision_date_bounds[1])
                clean_stds_for_charts = clean_stds_for_charts.loc[date_mask_chart].copy()

            # Action row: Calibrate results + optional linearity correction toggle
            if selected_standards:
                act_c1, act_c2 = st.columns([2, 1])
                with act_c1:
                    calibrate_clicked = st.button("Calibrate results", width='stretch')
                with act_c2:
                    apply_linearity_toggle = st.checkbox("Apply linearity correction", key="apply_linearity_toggle")

                if calibrate_clicked:
                    if len(selected_standards) not in [1, 2]:
                        st.warning("Please select either 1 or 2 standards for calibration.")
                    else:
                        # Perform calibration (using standards with outliers removed)
                        try:
                            calibrated_df = calibrate_results(
                                standards_df=clean_stds if clean_stds is not None else st.session_state.df,
                                full_df=st.session_state.df,
                                selected_standards=selected_standards
                            )
                            st.session_state.df = calibrated_df
                            st.session_state.calibration_coefficients = _compute_calibration_coefficients(
                                clean_stds if clean_stds is not None else st.session_state.df,
                                selected_standards
                            )
                            _autosave_session_update(
                                "calibrate_results",
                                changes=[],
                                context={
                                    "selected_standards": [str(s) for s in selected_standards],
                                    "calibration_type": str(calibration_type),
                                },
                            )
                            st.success("Calibration completed for both isotopic types.")
                        except Exception as e:
                            st.error(f"Calibration failed: {e}")

                        # Optionally compute and apply linearity correction across the whole dataset
                        if apply_linearity_toggle:
                            try:
                                intensity_col = '1  Cycle Int  Samp  44'
                                y13_col = 'd 13C/12C  Mean'
                                y18_col = 'd 18O/16O  Mean'
                                fit13 = _compute_linearity_fit(clean_stds, y13_col, intensity_col) if clean_stds is not None else {'slope': np.nan, 'x_ref': np.nan}
                                fit18 = _compute_linearity_fit(clean_stds, y18_col, intensity_col) if clean_stds is not None else {'slope': np.nan, 'x_ref': np.nan}
                                fits = {
                                    'd13C': {'slope': fit13.get('slope', np.nan), 'x_ref': fit13.get('x_ref', np.nan)},
                                    'd18O': {'slope': fit18.get('slope', np.nan), 'x_ref': fit18.get('x_ref', np.nan)},
                                }
                                st.session_state.df = _apply_linearity_correction(st.session_state.df, intensity_col, fits)
                                # Store fits for downstream display use
                                st.session_state.linearity_fits = fits
                                _autosave_session_update(
                                    "apply_linearity_correction",
                                    changes=[],
                                    context={
                                        "selected_standards": [str(s) for s in selected_standards],
                                        "fits": fits,
                                    },
                                )
                                if np.isfinite(fit13.get('slope', np.nan)) or np.isfinite(fit18.get('slope', np.nan)):
                                    st.success(
                                        f"Applied linearity correction. Slopes: d13C={fit13.get('slope', float('nan')):.6f} per V, "
                                        f"d18O={fit18.get('slope', float('nan')):.6f} per V."
                                    )
                                else:
                                    st.info("Linearity correction requested, but insufficient data to compute fits.")
                            except Exception as e:
                                st.error(f"Linearity correction failed: {e}")

            # Always show calibration charts when standards are selected (using cleaned standards)
            if selected_standards:
                try:
                    chart_src = clean_stds_for_charts if clean_stds_for_charts is not None else clean_stds if clean_stds is not None else st.session_state.df
                    figs = create_calibration_plots(standards_reference, chart_src, selected_standards, color_param)
                    col_cal1, col_cal2 = st.columns(2)
                    with col_cal1:
                        st.plotly_chart(figs[ISOTYPE_D13C], width='stretch')
                    with col_cal2:
                        st.plotly_chart(figs[ISOTYPE_D18O], width='stretch')
                except Exception as e:
                    st.warning(f"Unable to render calibration charts: {e}")

            if False:
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
                            st.plotly_chart(figs[ISOTYPE_D13C], width='stretch')
                        with col2:
                            st.plotly_chart(figs[ISOTYPE_D18O], width='stretch')

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

            # Linearity correction section (charts only; application handled by the toggle above)
            st.subheader("Linearity Correction")
            if not selected_standards:
                st.info("Select one or more standards above to compute linearity.")
            else:
                intensity_col = '1  Cycle Int  Samp  44'
                y13_col = 'd 13C/12C  Mean'
                y18_col = 'd 18O/16O  Mean'

                clean_stds = clean_stds if 'clean_stds' in locals() and clean_stds is not None else _filter_standards_remove_outliers(
                    st.session_state.df,
                    selected_standards,
                    calibration_type,
                    sigma_level,
                    irq_multiplier,
                )
                linearity_src = clean_stds_for_charts if 'clean_stds_for_charts' in locals() and clean_stds_for_charts is not None else clean_stds
                if linearity_src is not None and precision_date_bounds and date_col and date_col in linearity_src.columns:
                    date_series_chart = pd.to_datetime(linearity_src[date_col], errors='coerce')
                    date_mask_chart = (date_series_chart >= precision_date_bounds[0]) & (date_series_chart <= precision_date_bounds[1])
                    linearity_src = linearity_src.loc[date_mask_chart].copy()

                fit13 = _compute_linearity_fit(linearity_src, y13_col, intensity_col)
                fit18 = _compute_linearity_fit(linearity_src, y18_col, intensity_col)

                def _build_linearity_fig(df_src, y_col, fit, title_prefix):
                    fig = go.Figure()
                    color_vals, _ = _prepare_color_values(df_src.get(color_param, None))
                    fig.add_trace(go.Scatter(
                        x=df_src[intensity_col],
                        y=df_src[y_col],
                        mode='markers',
                        marker=dict(color=color_vals, colorscale='Viridis', showscale=False),
                        name='Standards'
                    ))
                    x = pd.to_numeric(df_src[intensity_col], errors='coerce')
                    y = pd.to_numeric(df_src[y_col], errors='coerce')
                    m = np.isfinite(x) & np.isfinite(y)
                    if fit['n'] >= 2 and np.any(m):
                        x_min = float(np.nanmin(x[m]))
                        x_max = float(np.nanmax(x[m]))
                        xs = np.linspace(x_min, x_max, 100)
                        ys = fit['intercept'] + fit['slope'] * xs
                        fig.add_trace(go.Scatter(x=xs, y=ys, mode='lines', name='Fit', line=dict(color='orange')))
                        eq = f"y = {fit['intercept']:.3f} + {fit['slope']:.6f}*I (R^2={fit['r2']:.3f})"
                    else:
                        eq = "Insufficient data for regression"
                    fig.update_layout(
                        title=f"{title_prefix}: {y_col} vs Intensity",
                        xaxis_title='Signal Intensity (V) - 1  Cycle Int  Samp  44',
                        yaxis_title=y_col,
                        annotations=[dict(x=0.02, y=0.98, xref='paper', yref='paper',
                                          text=eq, showarrow=False,
                                          bgcolor='white', bordercolor='black', borderwidth=1, font=dict(size=12))],
                        height=400,
                    )
                    return fig

                def _build_corrected_fig(df_src, y_col, fit, title_prefix):
                    if not fit or not np.isfinite(fit.get('slope', np.nan)):
                        return go.Figure()
                    x = pd.to_numeric(df_src[intensity_col], errors='coerce')
                    y = pd.to_numeric(df_src[y_col], errors='coerce')
                    corr = (y - fit['slope'] * (x - fit['x_ref']))
                    plot_df = pd.DataFrame({
                        'x': x,
                        'y_corr': corr,
                        'color': df_src.get(color_param, None)
                    })
                    color_vals, _ = _prepare_color_values(plot_df['color'])
                    if color_vals is not None:
                        plot_df['color'] = color_vals
                    m = np.isfinite(plot_df['x']) & np.isfinite(plot_df['y_corr'])
                    plot_df = plot_df[m]
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=plot_df['x'],
                        y=plot_df['y_corr'],
                        mode='markers',
                        marker=dict(color=plot_df['color'], colorscale='Viridis', showscale=False),
                        name='Corrected'
                    ))
                    if len(plot_df) >= 2:
                        lr = linregress(plot_df['x'], plot_df['y_corr'])
                        xs = np.linspace(float(plot_df['x'].min()), float(plot_df['x'].max()), 100)
                        ys = lr.intercept + lr.slope * xs
                        fig.add_trace(go.Scatter(x=xs, y=ys, mode='lines', name='Post-correction Fit', line=dict(color='green', dash='dash')))
                        eq = f"y = {lr.intercept:.3f} + {lr.slope:.6f}*I (R^2={lr.rvalue**2:.3f})"
                    else:
                        eq = "Insufficient data for regression"
                    fig.update_layout(
                        title=f"{title_prefix}: {y_col} vs Intensity (Corrected)",
                        xaxis_title='Signal Intensity (V) - 1  Cycle Int  Samp  44',
                        yaxis_title=f"{y_col} (linearity corrected)",
                        annotations=[dict(x=0.02, y=0.98, xref='paper', yref='paper',
                                          text=eq, showarrow=False,
                                          bgcolor='white', bordercolor='black', borderwidth=1, font=dict(size=12))],
                        height=400,
                    )
                    return fig

                c1, c2 = st.columns(2)
                with c1:
                    st.plotly_chart(_build_linearity_fig(linearity_src, y13_col, fit13, 'Linearity (Standards)'), width='stretch')
                with c2:
                    st.plotly_chart(_build_corrected_fig(linearity_src, y13_col, fit13, 'Linearity (Standards)'), width='stretch')

                c3, c4 = st.columns(2)
                with c3:
                    st.plotly_chart(_build_linearity_fig(linearity_src, y18_col, fit18, 'Linearity (Standards)'), width='stretch')
                with c4:
                    st.plotly_chart(_build_corrected_fig(linearity_src, y18_col, fit18, 'Linearity (Standards)'), width='stretch')

                # Persist the latest fit parameters for downstream precision display
                try:
                    st.session_state.linearity_fits = {
                        'd13C': {'slope': fit13.get('slope', np.nan), 'x_ref': fit13.get('x_ref', np.nan)},
                        'd18O': {'slope': fit18.get('slope', np.nan), 'x_ref': fit18.get('x_ref', np.nan)},
                    }
                except Exception:
                    pass

            # print(calibration_type)
            if selected_standards:
                for standard in selected_standards:
                    established_values = standards_reference[standards_reference['Standard'] == standard]

                    if established_values.empty:
                        st.warning(f"No established values found for the standard: {standard}")
                        continue

                    cond13 = established_values['Isotopic_Value_Type'] == ISOTYPE_D13C
                    cond18 = established_values['Isotopic_Value_Type'] == ISOTYPE_D18O
                    vals13 = established_values.loc[cond13, 'Value']
                    vals18 = established_values.loc[cond18, 'Value']
                    if vals13.empty or vals18.empty:
                        st.warning(f"Isotopic values not found for the standard: {standard}. Check standards.csv encoding.")
                        continue
                    d13c_established = vals13.iloc[0]
                    d18o_established = vals18.iloc[0]

                    shp2l_filtered_data = st.session_state.df[
                        st.session_state.df['Identifier 1'] == standard]

                    if precision_date_bounds and date_col:
                        date_series_std = pd.to_datetime(shp2l_filtered_data[date_col], errors='coerce')
                        date_mask = (date_series_std >= precision_date_bounds[0]) & (date_series_std <= precision_date_bounds[1])
                        shp2l_filtered_data = shp2l_filtered_data.loc[date_mask]

                    # print(f"Number of rows: {len(shp2l_filtered_data)}")

                    if shp2l_filtered_data.empty:
                        if precision_date_bounds:
                            start_ts = precision_date_bounds[0].strftime('%Y-%m-%d')
                            end_ts = precision_date_bounds[1].strftime('%Y-%m-%d')
                            st.warning(f"No data available for the standard: {standard} in the selected precision date range ({start_ts} to {end_ts}).")
                        else:
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
                            st.markdown("### d13C Outliers:")
                            d13c_outliers_data = shp2l_filtered_data.loc[d13c_outliers, ['d 13C/12C  Mean']]
                            if not d13c_outliers_data.empty:
                                st.dataframe(d13c_outliers_data.style.highlight_max(axis=0))
                            else:
                                st.write("No d13C outliers found.")

                        with col2:
                            st.markdown("### d18O Outliers:")
                            d18o_outliers_data = shp2l_filtered_data.loc[d18o_outliers, ['d 18O/16O  Mean']]
                            if not d18o_outliers_data.empty:
                                st.dataframe(d18o_outliers_data.style.highlight_max(axis=0))
                            else:
                                st.write("No d18O outliers found.")
                    else:
                        st.write("No outliers identified at this sigma level.")

                    # Filter out outliers for precision and average calculations
                    shp2l_clean = shp2l_filtered_data.loc[~(d13c_outliers | d18o_outliers)]

                    # Display precision (standard deviation) and averages
                    # Calculate the number of standards and percentage
                    total_standards = len(shp2l_filtered_data)
                    included_standards = len(shp2l_clean)
                    standards_percentage = (included_standards / total_standards) * 100 if total_standards > 0 else 0

                    # Calculate precision values (raw)
                    d13c_precision = shp2l_clean['d 13C/12C  Mean'].std()
                    d18o_precision = shp2l_clean['d 18O/16O  Mean'].std()

                    # Precision per line (1/2), excluding outliers
                    line_precision_markup = ""
                    line_col = _find_column(shp2l_clean, 'Line')
                    if line_col is not None:
                        line_df = shp2l_clean.copy()
                        line_df['_line_val'] = pd.to_numeric(line_df[line_col], errors='coerce')
                        line_df = line_df.dropna(subset=['_line_val'])
                        if not line_df.empty:
                            line_blocks = []
                            for line_value in sorted(line_df['_line_val'].unique()):
                                if not np.isfinite(line_value):
                                    continue
                                if line_value not in (1, 2):
                                    continue
                                line_subset = line_df[line_df['_line_val'] == line_value]
                                d13_line = pd.to_numeric(line_subset['d 13C/12C  Mean'], errors='coerce').std()
                                d18_line = pd.to_numeric(line_subset['d 18O/16O  Mean'], errors='coerce').std()
                                d13_text = "--" if pd.isna(d13_line) else f"{d13_line:.3f}‰"
                                d18_text = "--" if pd.isna(d18_line) else f"{d18_line:.3f}‰"
                                line_blocks.append(
                                    f"<p style='font-size: 16px; margin: 4px 0;'>"
                                    f"<b>Line {int(line_value)} precision:</b> d13C {d13_text} | d18O {d18_text}"
                                    f"</p>"
                                )
                            if line_blocks:
                                line_precision_markup = (
                                    "<div style='margin-top: 8px;'>" + "".join(line_blocks) + "</div>"
                                )

                    # Calculate precision after linearity correction (if available)
                    d13c_lin_prec = None
                    d18o_lin_prec = None
                    try:
                        fits = st.session_state.get('linearity_fits')
                        if fits:
                            i_series = pd.to_numeric(shp2l_clean['1  Cycle Int  Samp  44'], errors='coerce')
                            y13_series = pd.to_numeric(shp2l_clean['d 13C/12C  Mean'], errors='coerce')
                            y18_series = pd.to_numeric(shp2l_clean['d 18O/16O  Mean'], errors='coerce')
                            if np.isfinite(fits.get('d13C', {}).get('slope', np.nan)):
                                s = fits['d13C']['slope']; xr = fits['d13C']['x_ref']
                                d13_corr = (y13_series - s * (i_series - xr)).where(np.isfinite(y13_series) & np.isfinite(i_series))
                                d13c_lin_prec = float(d13_corr.std())
                            if np.isfinite(fits.get('d18O', {}).get('slope', np.nan)):
                                s = fits['d18O']['slope']; xr = fits['d18O']['x_ref']
                                d18_corr = (y18_series - s * (i_series - xr)).where(np.isfinite(y18_series) & np.isfinite(i_series))
                                d18o_lin_prec = float(d18_corr.std())
                    except Exception:
                        pass

                    # Determine colors based on precision values and standards percentage
                    d13c_precision_color = '#ff4444' if d13c_precision > 0.1 else '#2ecc71'
                    d18o_precision_color = '#ff4444' if d18o_precision > 0.1 else '#2ecc71'
                    d13c_lin_color = None if d13c_lin_prec is None else ('#ff4444' if d13c_lin_prec > 0.1 else '#2ecc71')
                    d18o_lin_color = None if d18o_lin_prec is None else ('#ff4444' if d18o_lin_prec > 0.1 else '#2ecc71')
                    standards_percentage_color = '#2ecc71' if standards_percentage >= 75 else '#666666'

                    # Optional markup for linearity-corrected precision display
                    d13c_lin_markup = (f"<p style='font-size: 16px; margin: 2px 0;'><i>d13C Precision (linearity corrected):</i> "
                                       f"<span style='color: {d13c_lin_color}'>{d13c_lin_prec:.3f}‰</span></p>") if d13c_lin_prec is not None else ""
                    d18o_lin_markup = (f"<p style='font-size: 16px; margin: 2px 0;'><i>d18O Precision (linearity corrected):</i> "
                                       f"<span style='color: {d18o_lin_color}'>{d18o_lin_prec:.3f}‰</span></p>") if d18o_lin_prec is not None else ""
                    
                    st.markdown(f"""
                    <div style='background-color: #f0f2f6; padding: 20px; border-radius: 10px; margin: 10px 0;'>
                        <h3 style='color: #1f77b4; margin-bottom: 15px;'>Precision and Averages for {standard} (Excluding Outliers)</h3>
                        <div style='display: flex; justify-content: space-between;'>
                            <div style='flex: 1; margin-right: 20px;'>
                                <p style='font-size: 18px; margin: 5px 0;'><b>d13C Precision:</b> <span style='color: {d13c_precision_color}'>{d13c_precision:.3f}‰</span></p>
                                {d13c_lin_markup}
                                <p style='font-size: 18px; margin: 5px 0;'><b>d13C Average:</b> <span style='color: #000000'>{shp2l_clean['d 13C/12C  Mean'].mean():.3f}‰</span></p>
                            </div>
                            <div style='flex: 1;'>
                                <p style='font-size: 18px; margin: 5px 0;'><b>d18O Precision:</b> <span style='color: {d18o_precision_color}'>{d18o_precision:.3f}‰</span></p>
                                {d18o_lin_markup}
                                <p style='font-size: 18px; margin: 5px 0;'><b>d18O Average:</b> <span style='color: #000000'>{shp2l_clean['d 18O/16O  Mean'].mean():.3f}‰</span></p>
                            </div>
                        </div>
                        {line_precision_markup}
                        <div style='margin-top: 15px; padding-top: 10px; border-top: 1px solid #ddd;'>
                            <p style='font-size: 16px; color: {standards_percentage_color};'>Standards included: {included_standards} out of {total_standards} ({standards_percentage:.1f}%)</p>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    # Calculate statistics for both methods
                    # IMPORTANT: compute stats from the same data shown on the plot.
                    # Use nonâ€‘outlier points when outlier detection is enabled to avoid biased/offset lines.
                    try:
                        stats_source = shp2l_filtered_data.loc[~(d13c_outliers | d18o_outliers)].copy()
                    except Exception:
                        # Fallback to all data if for any reason the masks are unavailable
                        stats_source = shp2l_filtered_data.copy()

                    # Use the same base data for thresholds as the outlier detection
                    threshold_source = shp2l_filtered_data

                    # Ensure numeric dtype and ignore NaNs
                    d13c_series = pd.to_numeric(threshold_source['d 13C/12C  Mean'], errors='coerce')
                    d18o_series = pd.to_numeric(threshold_source['d 18O/16O  Mean'], errors='coerce')

                    # Compute mean/std using the same sigma logic as outlier detection
                    d13c_mean, d13c_std, _ = _compute_sigma_stats(d13c_series, sigma_level)
                    d18o_mean, d18o_std, _ = _compute_sigma_stats(d18o_series, sigma_level)

                    # Sigma level lines (for Z-Score method)
                    sigma_level_d13c_plus = d13c_mean + sigma_level * d13c_std
                    sigma_level_d13c_minus = d13c_mean - sigma_level * d13c_std
                    sigma_level_d18o_plus = d18o_mean + sigma_level * d18o_std
                    sigma_level_d18o_minus = d18o_mean - sigma_level * d18o_std

                    # IQR statistics with the irq_multiplier instead of hardcoded 1.5
                    q1_d13c = d13c_series.quantile(0.25)
                    q3_d13c = d13c_series.quantile(0.75)
                    iqr_d13c = q3_d13c - q1_d13c
                    iqr_level_d13c_plus = q3_d13c + irq_multiplier * iqr_d13c
                    iqr_level_d13c_minus = q1_d13c - irq_multiplier * iqr_d13c

                    q1_d18o = d18o_series.quantile(0.25)
                    q3_d18o = d18o_series.quantile(0.75)
                    iqr_d18o = q3_d18o - q1_d18o
                    iqr_level_d18o_plus = q3_d18o + irq_multiplier * iqr_d18o
                    iqr_level_d18o_minus = q1_d18o - irq_multiplier * iqr_d18o

                    # Define sequence index for plotting (full dataset for alignment)
                    seq_index = pd.Series(range(1, len(shp2l_filtered_data) + 1), index=shp2l_filtered_data.index)
                    outlier_mask = (d13c_outliers | d18o_outliers).reindex(shp2l_filtered_data.index).fillna(False)
                    inlier_mask = ~outlier_mask
                    inlier_df = shp2l_filtered_data.loc[inlier_mask]
                    outlier_df = shp2l_filtered_data.loc[outlier_mask]

                    # Generate plots based on user choice
                    if calibration_type == "Z-Score":
                        # Plot for Î´13C with Z-Score thresholds
                        fig_d13c = px.scatter(
                            x=seq_index.loc[inlier_df.index],
                            y=inlier_df['d 13C/12C  Mean'],
                            color=inlier_df[color_param],  # Add color parameter
            title=f'SHP2L d13C Calibration Values (Z-Score Method)',
            labels={'y': 'd13C (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d13c.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        if not outlier_df.empty:
                            fig_d13c.add_trace(go.Scatter(
                                x=seq_index.loc[outlier_df.index],
                                y=outlier_df['d 13C/12C  Mean'],
                                mode='markers',
                                name='Outliers',
                                marker=dict(color='rgba(220, 50, 50, 0.9)', symbol='x', size=10)
                            ))
                        fig_d13c.add_hline(y=sigma_level_d13c_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{sigma_level}Ïƒ')
                        fig_d13c.add_hline(y=sigma_level_d13c_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{sigma_level}Ïƒ')
                        fig_d13c.add_hline(y=d13c_mean, line_color='purple', line_dash='solid',
                                           annotation_text='Mean Value')

                        # Plot for Î´18O with Z-Score thresholds
                        fig_d18o = px.scatter(
                            x=seq_index.loc[inlier_df.index],
                            y=inlier_df['d 18O/16O  Mean'],
                            color=inlier_df[color_param],  # Add color parameter
            title=f'SHP2L d18O Calibration Values (Z-Score Method)',
            labels={'y': 'd18O (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d18o.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        if not outlier_df.empty:
                            fig_d18o.add_trace(go.Scatter(
                                x=seq_index.loc[outlier_df.index],
                                y=outlier_df['d 18O/16O  Mean'],
                                mode='markers',
                                name='Outliers',
                                marker=dict(color='rgba(220, 50, 50, 0.9)', symbol='x', size=10)
                            ))
                        fig_d18o.add_hline(y=sigma_level_d18o_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{sigma_level}Ïƒ')
                        fig_d18o.add_hline(y=sigma_level_d18o_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{sigma_level}Ïƒ')
                        fig_d18o.add_hline(y=d18o_mean, line_color='purple', line_dash='solid',
                                           annotation_text='Mean Value')

                    elif calibration_type == "IQR":
                        # Plot for Î´13C with IQR thresholds
                        fig_d13c = px.scatter(
                            x=seq_index.loc[inlier_df.index],
                            y=inlier_df['d 13C/12C  Mean'],
                            color=inlier_df[color_param],  # Add color parameter
            title=f'SHP2L d13C Calibration Values (IQR Method)',
            labels={'y': 'd13C (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d13c.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        if not outlier_df.empty:
                            fig_d13c.add_trace(go.Scatter(
                                x=seq_index.loc[outlier_df.index],
                                y=outlier_df['d 13C/12C  Mean'],
                                mode='markers',
                                name='Outliers',
                                marker=dict(color='rgba(220, 50, 50, 0.9)', symbol='x', size=10)
                            ))
                        fig_d13c.add_hline(y=iqr_level_d13c_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{irq_multiplier:g} IQR')
                        fig_d13c.add_hline(y=iqr_level_d13c_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{irq_multiplier:g} IQR')
                        fig_d13c.add_hline(y=q3_d13c, line_color='purple', line_dash='solid',
                                           annotation_text='Q3 (75th Percentile)')
                        fig_d13c.add_hline(y=q1_d13c, line_color='purple', line_dash='solid',
                                           annotation_text='Q1 (25th Percentile)')

                        # Plot for Î´18O with IQR thresholds
                        fig_d18o = px.scatter(
                            x=seq_index.loc[inlier_df.index],
                            y=inlier_df['d 18O/16O  Mean'],
                            color=inlier_df[color_param],  # Add color parameter
            title=f'SHP2L d18O Calibration Values (IQR Method)',
            labels={'y': 'd18O (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d18o.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        if not outlier_df.empty:
                            fig_d18o.add_trace(go.Scatter(
                                x=seq_index.loc[outlier_df.index],
                                y=outlier_df['d 18O/16O  Mean'],
                                mode='markers',
                                name='Outliers',
                                marker=dict(color='rgba(220, 50, 50, 0.9)', symbol='x', size=10)
                            ))
                        fig_d18o.add_hline(y=iqr_level_d18o_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{irq_multiplier:g} IQR')
                        fig_d18o.add_hline(y=iqr_level_d18o_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{irq_multiplier:g} IQR')
                        fig_d18o.add_hline(y=q3_d18o, line_color='purple', line_dash='solid',
                                           annotation_text='Q3 (75th Percentile)')
                        fig_d18o.add_hline(y=q1_d18o, line_color='purple', line_dash='solid',
                                           annotation_text='Q1 (25th Percentile)')

                    # Display the plots
                    st.plotly_chart(fig_d13c, width='stretch')
                    st.plotly_chart(fig_d18o, width='stretch')


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
            signal_min = 0.0
            signal_max = 50.0
            # Store ranges in session state to make them available throughout the app
            st.session_state.signal_range = st.slider(
                'Filter by Signal Intensity',
                min_value=signal_min,
                max_value=signal_max,
                value=(1.0, signal_max)
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
            # d13C filter
            d13c_min = float(df_copy['d 13C/12C  Mean'].min())
            d13c_max = float(df_copy['d 13C/12C  Mean'].max())
            st.session_state.d13c_range = st.slider(
                'Filter by d13C',
                min_value=d13c_min,
                max_value=d13c_max,
                value=(float(-10), float(10))
            )

            # d18O filter
            d18o_min = float(df_copy['d 18O/16O  Mean'].min())
            d18o_max = float(df_copy['d 18O/16O  Mean'].max())
            st.session_state.d18o_range = st.slider(
                'Filter by d18O',
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

        # Exclude selected standards from plotting data
        standards_to_exclude = st.session_state.get('selected_standards', selected_standards if 'selected_standards' in locals() else [])
        if standards_to_exclude:
            df_filtered = df_filtered[~df_filtered['Identifier 1'].isin(standards_to_exclude)]
            df_unfiltered = df_unfiltered[~df_unfiltered['Identifier 1'].isin(standards_to_exclude)]
        
        # Calculate total excluded after applying all filters
        total_excluded = total_samples - len(df_copy)
        
        # # Display excluded samples information
        # st.markdown("#### Samples Excluded by Filters")
        # col1, col2 = st.columns(2)
        # with col1:
        #     st.write(f"Signal Intensity: {excluded_by_signal:,d} samples")
        #     st.write(f"Leak Rate: {excluded_by_leak:,d} samples")
        # with col2:
        #     st.write(f"Î´13C Range: {excluded_by_d13c:,d} samples")
        #     st.write(f"Î´18O Range: {excluded_by_d18o:,d} samples")
        # st.markdown(f"**Total Samples Excluded: {total_excluded:,d} of {total_samples:,d}**")

        st.subheader("Statistical Outlier Settings")
        sigma_level_data = st.number_input("Set Sigma Level for data Outlier Exclusion",
                                         min_value=0.1,
                                         max_value=6.0,
                                         value=4.0,
                                         step=0.1)
        st.session_state['sigma_level_data'] = float(sigma_level_data)

        

        # Create a subheader and expander to show active filters

        # with st.expander("Active Filters"):
        #     st.write("Signal Intensity Range:", f"{signal_range[0]:.2f} to {signal_range[1]:.2f}")
        #     st.write("Leak Rate Range:", f"{leak_range[0]:.2f} to {leak_range[1]:.2f}")
        #     st.write("Î´13C Range:", f"{d13c_range[0]:.2f} to {d13c_range[1]:.2f}")
        #     st.write("Î´18O Range:", f"{d18o_range[0]:.2f} to {d18o_range[1]:.2f}")

        # Prepare main dataset based on user selections
        data_to_process = df_copy.copy()
        
        # Filter by selected Identifier 1 values if not "All"
        if "All" not in st.session_state.selected_ids:
            data_to_process = data_to_process[data_to_process['Identifier 1'].isin(st.session_state.selected_ids)]

        # Initialize mask for statistical outliers
        statistical_mask = pd.Series(False, index=data_to_process.index, dtype=bool)
        edited_mask_data = pd.Series(data_to_process.index.map(_is_row_edited), index=data_to_process.index, dtype=bool)
        group_series = _get_species_series(data_to_process)
        
        # Calculate statistical outliers separately for each identifier and comment group
        for identifier in data_to_process['Identifier 1'].unique():
            for group_val in group_series[data_to_process['Identifier 1'] == identifier].unique():
                group_mask = (data_to_process['Identifier 1'] == identifier) & (group_series == group_val)
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
                    statistical_mask.loc[group_mask] = group_stat_outliers.astype(bool).to_numpy()
        statistical_mask = statistical_mask & ~edited_mask_data
                    
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
        status_series_all = data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index))
        not_saturated_samples = status_series_all != 'Fully Saturated Collectors'
        not_failed_samples = status_series_all != 'Failed Sample'
        within_ranges = (
            (data_to_process['d 13C/12C  Mean'] >= st.session_state.d13c_range[0]) &
            (data_to_process['d 13C/12C  Mean'] <= st.session_state.d13c_range[1]) &
            (data_to_process['d 18O/16O  Mean'] >= st.session_state.d18o_range[0]) &
            (data_to_process['d 18O/16O  Mean'] <= st.session_state.d18o_range[1]) &
            (data_to_process['1  Cycle Int  Samp  44'] >= st.session_state.signal_range[0]) &
            (data_to_process['1  Cycle Int  Samp  44'] <= st.session_state.signal_range[1]) &
            (data_to_process['leak_rate'] >= st.session_state.leak_range[0]) &
            (data_to_process['leak_rate'] <= st.session_state.leak_range[1]) &
            not_saturated_samples &
            not_failed_samples &
            ~edited_mask_data
        )

        # Combine range and statistical masks
        within_all = within_ranges & within_statistical

        # Filter out standards from the data before calculating statistics
        non_standards_mask = ~data_to_process['Identifier 1'].isin(all_standards)
        data_without_standards = data_to_process[non_standards_mask].copy()
        edited_mask_no_std = pd.Series(data_without_standards.index.map(_is_row_edited), index=data_without_standards.index, dtype=bool)

        # Calculate total samples (excluding standards)
        # Count unique samples and total measurements
        unique_samples = data_without_standards.groupby(['Identifier 1', 'Identifier 2']).size().reset_index().shape[0]
        total_measurements = len(data_without_standards)

        # Calculate outliers using data_without_standards
        stat_outliers = sum(statistical_mask[non_standards_mask])
        d13c_mask = ((data_without_standards['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (data_without_standards['d 13C/12C  Mean'] > st.session_state.d13c_range[1])) & ~edited_mask_no_std
        d18o_mask = ((data_without_standards['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (data_without_standards['d 18O/16O  Mean'] > st.session_state.d18o_range[1])) & ~edited_mask_no_std
        signal_mask = ((data_without_standards['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (data_without_standards['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])) & ~edited_mask_no_std
        leak_mask = ((data_without_standards['leak_rate'] < st.session_state.leak_range[0]) | (data_without_standards['leak_rate'] > st.session_state.leak_range[1])) & ~edited_mask_no_std
        status_series_no_std = data_without_standards.get('Collector Status', pd.Series(False, index=data_without_standards.index))
        failed_mask = (status_series_no_std == 'Failed Sample') & ~edited_mask_no_std
        saturated_mask = (status_series_no_std == 'Partially Saturated Collectors') & ~edited_mask_no_std
        saturated_sample_mask = (status_series_no_std == 'Fully Saturated Collectors') & ~edited_mask_no_std

        # Count outliers
        d13c_outliers = sum(d13c_mask)
        d18o_outliers = sum(d18o_mask)
        signal_outliers = sum(signal_mask)
        leak_outliers = sum(leak_mask)
        failed_outliers = int(failed_mask.sum())
        saturated_collectors = int(saturated_mask.sum())
        saturated_samples = int(saturated_sample_mask.sum())

        # Calculate final analyses (total samples minus all outliers)
        total_outliers = stat_outliers + d13c_outliers + d18o_outliers + signal_outliers + leak_outliers + failed_outliers + saturated_samples
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
                'Metric': 'd13C Range Outliers',
                'Value': d13c_outliers,
                'Details': f'({(d13c_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        if d18o_outliers > 0:
            stats_data.append({
                'Metric': 'd18O Range Outliers',
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
            'Metric': 'Failed Samples',
            'Value': failed_outliers,
            'Details': f'({(failed_outliers/total_measurements)*100:.1f}% of measurements)'
        })
        stats_data.append({
            'Metric': 'Partially Failed (Recovered Mean)',
            'Value': saturated_collectors,
            'Details': f'({(saturated_collectors/total_measurements)*100:.1f}% of measurements)'
        })
        stats_data.append({
            'Metric': 'Fully Saturated Collectors',
            'Value': saturated_samples,
            'Details': f'({(saturated_samples/total_measurements)*100:.1f}% of measurements)'
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
        col1, col2, col3, col4 = st.columns(4)
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

            # When including outliers, allow optional interpolation before export
            if st.session_state.include_outliers == "Yes":
                # Let the widget manage session_state via its key; don't assign to session_state directly
                _interpolate_outliers_export = st.checkbox(
                    "Interpolate outliers before export",
                    value=st.session_state.get("interpolate_outliers_export", False),
                    help="Linearly interpolate values for rows flagged as outliers before downloading.",
                    key="interpolate_outliers_export"
                )
            else:
                st.session_state.interpolate_outliers_export = False

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
        with col4:
            st.text_input(
                "Client name",
                value=st.session_state.get('client_name', ''),
                key='client_name'
            )
            # Species replacement mapping for Client Output
            try:
                unique_species_vals = sorted([str(x) for x in _get_species_series(df_copy).dropna().unique().tolist()])
            except Exception:
                unique_species_vals = []
            existing_map = st.session_state.get('comment_replacements', {}) or {}
            with st.expander("Species labels (from Label/Species) — customize for Client Output"):
                new_map = {}
                for i, sval in enumerate(unique_species_vals):
                    key = f"species_map_{i}"
                    default_val = existing_map.get(sval, sval)
                    user_val = st.text_input(f"{sval}", value=str(default_val), key=key)
                    new_map[sval] = user_val
                st.session_state.comment_replacements = new_map
        st.markdown("---")

        # Combine range and statistical masks
        within_all = within_ranges & within_statistical

        # Separate data into main_data and outliers_df
        main_data = data_to_process[within_all].copy() if st.session_state.include_outliers == "No" else data_to_process.copy()
        edited_mask_out = pd.Series(data_to_process.index.map(_is_row_edited), index=data_to_process.index, dtype=bool)
        if st.session_state.include_outliers == "No":
            # Collect outliers with their categories
            outliers_df = pd.DataFrame()
            
            # Failed samples (pre with empty data)
            failed_outliers_df = data_to_process[
                data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index)) == 'Failed Sample'
            ].copy()
            failed_outliers_df = failed_outliers_df[~failed_outliers_df.index.map(_is_row_edited)]
            if not failed_outliers_df.empty:
                failed_outliers_df['Category'] = 'Failed Sample'
                outliers_df = pd.concat([outliers_df, failed_outliers_df])
            
            saturated_samples_df = data_to_process[
                data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index)) == 'Fully Saturated Collectors'
            ].copy()
            saturated_samples_df = saturated_samples_df[~saturated_samples_df.index.map(_is_row_edited)]
            if not saturated_samples_df.empty:
                saturated_samples_df['Category'] = 'Fully Saturated Collectors'
                outliers_df = pd.concat([outliers_df, saturated_samples_df])
            
            # Statistical outliers - making sure to use the correct index
            statistical_mask = pd.Series(False, index=data_to_process.index, dtype=bool)
            # Calculate statistical outliers by group
            for identifier in data_to_process['Identifier 1'].unique():
                for group_val in group_series[data_to_process['Identifier 1'] == identifier].unique():
                    group_mask = (data_to_process['Identifier 1'] == identifier) & (group_series == group_val)
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
                        statistical_mask.loc[group_mask] = group_stat_outliers.astype(bool).to_numpy()
            statistical_mask = statistical_mask & ~edited_mask_out
            
            statistical_outliers = data_to_process[statistical_mask].copy()
            if not statistical_outliers.empty:
                statistical_outliers['Category'] = 'Statistical'
                outliers_df = pd.concat([outliers_df, statistical_outliers])
            
            # Range outliers by category
            d13c_outliers = data_to_process[
                (data_to_process['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (data_to_process['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
            ].copy()
            d13c_outliers = d13c_outliers[~d13c_outliers.index.map(_is_row_edited)]
            if not d13c_outliers.empty:
                d13c_outliers['Category'] = 'd13C Range'
                outliers_df = pd.concat([outliers_df, d13c_outliers])
            
            d18o_outliers = data_to_process[
                (data_to_process['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (data_to_process['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
            ].copy()
            d18o_outliers = d18o_outliers[~d18o_outliers.index.map(_is_row_edited)]
            if not d18o_outliers.empty:
                d18o_outliers['Category'] = 'd18O Range'
                outliers_df = pd.concat([outliers_df, d18o_outliers])
            
            signal_outliers = data_to_process[
                (data_to_process['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (data_to_process['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
            ].copy()
            signal_outliers = signal_outliers[~signal_outliers.index.map(_is_row_edited)]
            if not signal_outliers.empty:
                signal_outliers['Category'] = 'Signal Intensity'
                outliers_df = pd.concat([outliers_df, signal_outliers])
            
            leak_outliers = data_to_process[
                (data_to_process['leak_rate'] < st.session_state.leak_range[0]) |
                (data_to_process['leak_rate'] > st.session_state.leak_range[1])
            ].copy()
            leak_outliers = leak_outliers[~leak_outliers.index.map(_is_row_edited)]
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
        # Clarify in filename if interpolation will be applied
        if st.session_state.include_outliers == "Yes" and st.session_state.get("interpolate_outliers_export"):
            if filename.lower().endswith(".xlsx"):
                filename = filename[:-5] + "_interpolated.xlsx"

        # For "Include outliers = Yes", add an outlier-type column and merge outliers
        if st.session_state.include_outliers == "Yes":
            # Build per-row outlier category labels based on current settings
            try:
                stat_mask_all = statistical_mask.reindex(data_to_process.index, fill_value=False)
            except Exception:
                # Fallback in case statistical_mask is not aligned
                stat_mask_all = pd.Series(False, index=data_to_process.index)

            d13c_out_mask = (
                (data_to_process['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (data_to_process['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
            ) & ~edited_mask_out
            d18o_out_mask = (
                (data_to_process['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (data_to_process['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
            ) & ~edited_mask_out
            signal_out_mask = (
                (data_to_process['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (data_to_process['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
            ) & ~edited_mask_out
            leak_out_mask = (
                (data_to_process['leak_rate'] < st.session_state.leak_range[0]) |
                (data_to_process['leak_rate'] > st.session_state.leak_range[1])
            ) & ~edited_mask_out
            failed_out_mask = (data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index)) == 'Failed Sample') & ~edited_mask_out
            saturated_sample_out_mask = (data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index)) == 'Fully Saturated Collectors') & ~edited_mask_out

            cat_bools = pd.DataFrame({
                'Statistical': stat_mask_all,
                'd13C Range': d13c_out_mask,
                'd18O Range': d18o_out_mask,
                'Signal Intensity': signal_out_mask,
                'Leak Rate': leak_out_mask,
                'Failed Sample': failed_out_mask,
                'Fully Saturated Collectors': saturated_sample_out_mask,
            }, index=data_to_process.index)

            # Join multiple categories with '; ' for rows that meet several outlier conditions
            outlier_types = cat_bools.apply(
                lambda row: '; '.join([cat for cat, is_out in row.items() if bool(is_out)]), axis=1
            )

            # Attach the outlier types to the main dataset being exported
            main_data = data_to_process.copy()
            main_data['Outlier Types'] = outlier_types

            # Clear outliers_df so only one consolidated sheet is exported
            outliers_df = pd.DataFrame()
            
            # Optionally interpolate outlier rows before export
            if st.session_state.get("interpolate_outliers_export"):
                try:
                    outlier_mask = main_data['Outlier Types'].astype(str).str.strip().replace({"": np.nan}).notna()
                    cols_to_interp = [
                        "1  Cycle Int  Samp  44",  # signal intensity for linearity correction
                        "d 13C/12C  Mean",
                        "d 13C/12C  Std Dev",
                        "d 18O/16O  Mean",
                        "d 18O/16O  Std Dev",
                        "d13C_calibrated",
                        "d18O_calibrated",
                    ]
                    present_cols = [c for c in cols_to_interp if c in main_data.columns]

                    if present_cols:
                        # Preserve originals in dedicated columns before interpolation
                        original_cols = []
                        for c in present_cols:
                            new_name = f"Original {c}"
                            main_data[new_name] = main_data[c]
                            original_cols.append(new_name)

                        # Interpolate using Identifier 2 ordering
                        main_data = _interpolate_outliers_by_identifier2(main_data, outlier_mask, present_cols, id2_col='Identifier 2')

                        # Reorder columns so that original columns sit next to Outlier Types
                        try:
                            cols = list(main_data.columns)
                            if 'Outlier Types' in cols:
                                pos = cols.index('Outlier Types')
                                # Remove originals from current position
                                for oc in original_cols:
                                    if oc in cols:
                                        cols.remove(oc)
                                # Insert originals after Outlier Types
                                cols = cols[:pos+1] + original_cols + cols[pos+1:]
                                main_data = main_data[cols]
                        except Exception:
                            pass
                except Exception as e:
                    st.warning(f"Interpolation step skipped due to error: {e}")
            
        download_excel(
            main_data,
            outliers=outliers_df,
            filename=filename,
            selected_standards=selected_standards,
            calibration_type=st.session_state.get('calibration_type'),
            sigma_level=st.session_state.get('sigma_level'),
            irq_multiplier=st.session_state.get('irq_multiplier'),
            client_name=st.session_state.get('client_name'),
            comment_map=st.session_state.get('comment_replacements'),
        )

        # Read the standards.csv file
        standards_df = pd.read_csv("standards.csv")
        standard_identifiers = standards_df['Standard'].unique()

        # Get unique identifiers excluding those in the standards file
        unique_identifiers = [
            identifier for identifier in df_copy['Identifier 1'].unique()
            if pd.notna(identifier) and identifier not in standard_identifiers
        ]

        # Remove any standards explicitly selected in the Calibration tab
        if standards_to_exclude:
            unique_identifiers = [identifier for identifier in unique_identifiers if identifier not in standards_to_exclude]

        # Add 'All' option to the unique_identifiers list (this will allow the user to select all identifiers)
        unique_identifiers.insert(0, 'All')

        # Charts Settings section
        st.subheader("Charts Settings")
        
        col1, col2 = st.columns(2)
        with col1:
            selected_identifier = st.selectbox("Select Identifier 1 (from Label):", options=unique_identifiers)
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
            show_saturated_collectors = st.checkbox("Show partially failed (recovered) samples on chart", value=True, key="show_saturated_collectors")
            show_saturated_samples = st.checkbox("Show failed samples (fully saturated) on chart", value=True, key="show_saturated_samples")
            show_failed_samples = st.checkbox("Show failed samples (no values) on chart", value=True, key="show_failed_samples")

        color_param_tab3_value_col = "_tab3_color_value"
        color_source_tab3 = df_filtered[color_param_tab3]
        color_values_tab3 = pd.to_numeric(color_source_tab3, errors='coerce')
        colorbar_category_ticks = None
        if color_values_tab3.isna().all():
            categories = color_source_tab3.astype(str)
            codes, uniques = pd.factorize(categories, sort=True)
            color_values_tab3 = pd.Series(codes, index=df_filtered.index)
            colorbar_category_ticks = (list(range(len(uniques))), [str(u) for u in uniques])
        df_filtered[color_param_tab3_value_col] = color_values_tab3

        # If 'All' is selected, include data for all identifiers
        if selected_identifier == 'All':
            subset_data = df_filtered
            subset_data_unfiltered = df_unfiltered
            
            # Get the actual data range for the selected parameter
            param_min = color_values_tab3.min()
            param_max = color_values_tab3.max()
            
            # Create a shared colorbar figure
            # Build colorbar configuration and use readable dates if needed
            colorbar_cfg = dict(
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
            if color_param_tab3 == 'Date_ordinal' and color_param_tab3 in df_filtered.columns:
                tickvals, ticktext = _build_date_colorbar_ticks(color_values_tab3)
                if tickvals and ticktext:
                    colorbar_cfg.update(tickvals=tickvals, ticktext=ticktext)

            if colorbar_category_ticks is not None:
                tickvals, ticktext = colorbar_category_ticks
                if tickvals and ticktext:
                    colorbar_cfg.update(tickvals=tickvals, ticktext=ticktext)

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
                    colorbar=colorbar_cfg
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
                st.plotly_chart(colorbar_fig, width='stretch')
        else:
            subset_data = df_filtered[df_filtered['Identifier 1'] == selected_identifier]
            subset_data_unfiltered = df_unfiltered[df_unfiltered['Identifier 1'] == selected_identifier]




        # Use "Identifier 1 - Species" when Species exists; otherwise fall back to Identifier 1 only
        plot_label_col = "_plot_label"
        if 'Species' in subset_data.columns and not subset_data['Species'].isna().all():
            subset_data.loc[:, plot_label_col] = _compose_label_series(
                subset_data['Identifier 1'],
                subset_data['Species']
            )
            subset_data_unfiltered.loc[:, plot_label_col] = _compose_label_series(
                subset_data_unfiltered.get('Identifier 1', pd.Series(index=subset_data_unfiltered.index, dtype=object)),
                subset_data_unfiltered.get('Species', pd.Series(index=subset_data_unfiltered.index, dtype=object))
            )
        else:
            subset_data.loc[:, plot_label_col] = subset_data['Identifier 1'].fillna("Unknown").astype(str)
            subset_data_unfiltered.loc[:, plot_label_col] = subset_data_unfiltered.get(
                'Identifier 1', pd.Series(index=subset_data_unfiltered.index, dtype=object)
            ).fillna("Unknown").astype(str)
        species_col = plot_label_col

        # Iterate through unique species (including the placeholder)
        unique_species = subset_data[species_col].unique()

        # Assign a distinct marker symbol to each species for summary charts
        # Avoid symbols used for outliers ('x', 'cross', 'diamond', 'star')
        species_symbol_cycle = [
            'circle', 'square', 'triangle-up', 'triangle-down', 'triangle-left', 'triangle-right'
        ]
        species_symbol_map = {
            sp: species_symbol_cycle[i % len(species_symbol_cycle)]
            for i, sp in enumerate([s for s in unique_species if s != "Unknown"])
        }

        # Create x_axis values
        subset_data['x_axis'] = np.nan
        if x_axis_option == "By Identifier 2":
            subset_data['x_axis'] = subset_data['Identifier 2'].apply(
                lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                    r'\d+\.?\d*', str(x)) else None
            )
        else:
            subset_data['x_axis'] = range(len(subset_data))
        # Also create x_axis for unfiltered subset (used for status overlays)
        subset_data_unfiltered['x_axis'] = np.nan
        if x_axis_option == "By Identifier 2":
            subset_data_unfiltered['x_axis'] = subset_data_unfiltered['Identifier 2'].apply(
                lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                    r'\d+\.?\d*', str(x)) else None
            )
        else:
            subset_data_unfiltered['x_axis'] = range(len(subset_data_unfiltered))

        # Summary Charts
        st.subheader("Summary Charts")
        d13_summary_editor_prefix = "tab3_d13c_summary_editor"
        d18_summary_editor_prefix = "tab3_d18o_summary_editor"
        
        # Create summary chart for d13C
        d13c_summary = go.Figure()
        d13_active_target = _get_active_editor_target(d13_summary_editor_prefix)
        d13_legend_partial_shown = False
        d13_legend_failed_full_shown = False
        d13_legend_failed_no_values_shown = False
        d13_legend_failed_interp_shown = False
        d13_legend_edited_shown = False
        for species in unique_species:
            if species == "Unknown":
                continue
            
            species_data = subset_data[subset_data[species_col] == species]
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered[species_col] == species]
            if species_data_unfiltered.empty and species_data.empty:
                continue
            edited_mask_species = pd.Series(species_data.index.map(_is_row_edited), index=species_data.index, dtype=bool)

            status_series = species_data_unfiltered.get('Collector Status', pd.Series(False, index=species_data_unfiltered.index))
            saturated_collectors_mask = status_series == 'Partially Saturated Collectors'
            saturated_samples_mask = status_series == 'Fully Saturated Collectors'
            failed_mask = status_series == 'Failed Sample'
            saturated_samples_idx = species_data_unfiltered[saturated_samples_mask].index
            failed_idx = species_data_unfiltered[failed_mask].index
            edited_mask_species = pd.Series(species_data.index.map(_is_row_edited), index=species_data.index, dtype=bool)
            edited_mask_species_unfiltered = pd.Series(
                species_data_unfiltered.index.map(_is_row_edited),
                index=species_data_unfiltered.index,
                dtype=bool
            )
            failed_idx = species_data_unfiltered[failed_mask].index
            edited_mask_species = pd.Series(species_data.index.map(_is_row_edited), index=species_data.index, dtype=bool)
            edited_mask_species_unfiltered = pd.Series(
                species_data_unfiltered.index.map(_is_row_edited),
                index=species_data_unfiltered.index,
                dtype=bool
            )
            failed_idx = species_data_unfiltered[failed_mask].index
            
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
            outlier_mask = outlier_mask & ~edited_mask_species
            # Store statistical outliers
            statistical_outliers = species_data[outlier_mask].copy()

            # Calculate range outliers mask using unfiltered data so signal/leak outliers aren't dropped
            range_mask_unfiltered = (
                (species_data_unfiltered['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (species_data_unfiltered['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                (species_data_unfiltered['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (species_data_unfiltered['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                (species_data_unfiltered['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (species_data_unfiltered['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                (species_data_unfiltered['leak_rate'] < st.session_state.leak_range[0]) |
                (species_data_unfiltered['leak_rate'] > st.session_state.leak_range[1])
            )
            range_mask_unfiltered = range_mask_unfiltered & ~edited_mask_species_unfiltered
            range_mask_for_plot = range_mask_unfiltered.reindex(species_data.index, fill_value=False)

            # Store range outliers if showing them
            if show_range_outliers:
                range_outliers = species_data_unfiltered[range_mask_unfiltered].copy()
                # Add x_axis values to range outliers
                if x_axis_option == "By Identifier 2":
                    range_outliers['x_axis'] = range_outliers['Identifier 2'].apply(
                        lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                            r'\d+\.?\d*', str(x)) else None
                    )
                else:
                    range_outliers['x_axis'] = range(len(range_outliers))
            else:
                range_outliers = pd.DataFrame(columns=species_data_unfiltered.columns)
                
            # Filter data to plot - exclude statistical, range outliers, and saturated samples
            data_to_plot = species_data[
                ~(outlier_mask | range_mask_for_plot | species_data.index.isin(saturated_samples_idx) | species_data.index.isin(failed_idx))
            ].copy()
            
            # Sort data by x_axis to ensure sequential line connections
            data_to_plot = data_to_plot.sort_values('x_axis')

            # Plot main data
            # Generate unique color based on species/comment
            species_color = f'rgb({hash(species) % 255}, {(hash(species) >> 8) % 255}, {(hash(species) >> 16) % 255})'
            d13_customdata = _build_delta_point_customdata(data_to_plot, 'd13C')
            
            d13c_summary.add_trace(go.Scatter(
                x=data_to_plot['x_axis'],
                y=data_to_plot['d 13C/12C  Mean'],
                mode='lines+markers',
                name=species,
                marker=dict(
                    size=8,
                    color=data_to_plot[color_param_tab3_value_col],
                    colorscale="Viridis",
                    showscale=False,
                    symbol=species_symbol_map.get(species, 'circle')
                ),
                line=dict(width=1, color=species_color),
                legendgroup=species,
                customdata=d13_customdata,
                hovertemplate=(
                    'Identifier 1: %{customdata[2]}<br>'
                    'Identifier 2: %{customdata[3]}<br>'
                    'd13C: %{y:.4f}<extra></extra>'
                )
            ))
            edited_points = data_to_plot[data_to_plot.index.map(_is_row_edited)]
            if not edited_points.empty:
                edited_customdata = _build_delta_point_customdata(edited_points, 'd13C')
                d13c_summary.add_trace(go.Scatter(
                    x=edited_points['x_axis'],
                    y=edited_points['d 13C/12C  Mean'],
                    mode='markers',
                    name='Edited Samples',
                    marker=dict(
                        size=12,
                        symbol='circle',
                        color='#ff00ff',
                        line=dict(width=1, color='#ff00ff')
                    ),
                    showlegend=not d13_legend_edited_shown,
                    legendgroup='edited_samples',
                    customdata=edited_customdata,
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd13C: %{y:.4f}<extra></extra>'
                    )
                ))
                d13_legend_edited_shown = True

            # Highlight saturated collectors (compromised but valid)
            if show_saturated_collectors and saturated_collectors_mask.any():
                sat_collectors = species_data_unfiltered[saturated_collectors_mask]
                sat_collectors_customdata = _build_delta_point_customdata(sat_collectors, 'd13C')
                d13c_summary.add_trace(go.Scatter(
                    x=sat_collectors['x_axis'],
                    y=sat_collectors['d 13C/12C  Mean'],
                    mode='markers',
                    name='Partially Failed (Recovered Mean)',
                    marker=dict(
                        size=12,
                        symbol='diamond-open',
                        color='#ff7f0e',
                        line=dict(width=2, color='#ff7f0e')
                    ),
                    showlegend=not d13_legend_partial_shown,
                    legendgroup='collector_status',
                    customdata=sat_collectors_customdata,
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd13C: %{y:.4f}<extra></extra>'
                    )
                ))
                d13_legend_partial_shown = True

            # Plot saturated samples as outliers
            if show_saturated_samples and saturated_samples_mask.any():
                sat_samples = species_data_unfiltered[saturated_samples_mask]
                sat_customdata = _build_delta_point_customdata(sat_samples, 'd13C')
                y_vals_sat = pd.to_numeric(sat_samples['d 13C/12C  Mean'], errors='coerce')
                if y_vals_sat.notna().any():
                    y_sat = y_vals_sat.tolist()
                else:
                    y_vals = pd.to_numeric(species_data['d 13C/12C  Mean'], errors='coerce')
                    y_min = y_vals.min()
                    y_max = y_vals.max()
                    if not np.isfinite(y_min):
                        y_min, y_max = -1.0, 1.0
                    y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                    y_sat = [y_min - (0.15 * y_range if y_range > 0 else 0.75)] * len(sat_samples)
                d13c_summary.add_trace(go.Scatter(
                    x=sat_samples['x_axis'],
                    y=y_sat,
                    mode='markers',
                    name='Failed Samples (Fully Saturated)',
                    marker=dict(
                        size=12,
                        symbol='triangle-down',
                        color='#d62728',
                        line=dict(width=2, color='#d62728')
                    ),
                    showlegend=not d13_legend_failed_full_shown,
                    legendgroup='outliers',
                    customdata=sat_customdata,
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd13C: %{y:.4f}<extra></extra>'
                    )
                ))
                d13_legend_failed_full_shown = True

            if show_failed_samples and failed_mask.any():
                failed_samples = species_data_unfiltered[failed_mask]
                failed_vals = pd.to_numeric(failed_samples['d 13C/12C  Mean'], errors='coerce')
                failed_is_edited = pd.Series(failed_samples.index.map(_is_row_edited), index=failed_samples.index)
                failed_interp = failed_samples[failed_vals.notna() & failed_is_edited].copy()
                failed_recovered = failed_samples[failed_vals.notna() & ~failed_is_edited].copy()
                failed_missing = failed_samples[failed_vals.isna()].copy()
                if not failed_recovered.empty:
                    failed_recovered_customdata = _build_delta_point_customdata(failed_recovered, 'd13C')
                    d13c_summary.add_trace(go.Scatter(
                        x=failed_recovered['x_axis'],
                        y=pd.to_numeric(failed_recovered['d 13C/12C  Mean'], errors='coerce'),
                        mode='markers',
                        name='Partially Failed (Recovered Mean)',
                        marker=dict(
                            size=12,
                            symbol='diamond-open',
                            color='#ff7f0e',
                            line=dict(width=2, color='#ff7f0e')
                        ),
                        showlegend=not d13_legend_partial_shown,
                        legendgroup='collector_status',
                        customdata=failed_recovered_customdata,
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd13C: %{y:.4f}<extra></extra>'
                        )
                    ))
                    d13_legend_partial_shown = True
                if not failed_interp.empty:
                    failed_interp_customdata = _build_delta_point_customdata(failed_interp, 'd13C')
                    d13c_summary.add_trace(go.Scatter(
                        x=failed_interp['x_axis'],
                        y=pd.to_numeric(failed_interp['d 13C/12C  Mean'], errors='coerce'),
                        mode='markers',
                        name='Failed Samples (Interpolated)',
                        marker=dict(
                            size=10,
                            symbol='triangle-down',
                            color='#ff00ff',
                            line=dict(width=1, color='#ff00ff')
                        ),
                        showlegend=not d13_legend_failed_interp_shown,
                        legendgroup='outliers',
                        customdata=failed_interp_customdata,
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd13C: %{y:.4f}<extra></extra>'
                        )
                    ))
                    d13_legend_failed_interp_shown = True
                if not failed_missing.empty:
                    failed_customdata = _build_delta_point_customdata(failed_missing, 'd13C')
                y_vals = pd.to_numeric(species_data['d 13C/12C  Mean'], errors='coerce')
                y_min = y_vals.min()
                y_max = y_vals.max()
                if not np.isfinite(y_min):
                    y_min, y_max = -1.0, 1.0
                y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                if not failed_missing.empty:
                    d13c_summary.add_trace(go.Scatter(
                        x=failed_missing['x_axis'],
                        y=[y_failed] * len(failed_missing),
                        mode='markers',
                        name='Failed Samples (No Values)',
                        marker=dict(
                            size=10,
                            symbol='triangle-down',
                            color='#7f7f7f',
                            line=dict(width=1, color='#7f7f7f')
                        ),
                        showlegend=not d13_legend_failed_no_values_shown,
                        legendgroup='outliers',
                        text=failed_missing['Identifier 2'].astype(str),
                        customdata=failed_customdata,
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd13C: missing (click to edit)<extra></extra>'
                        )
                    ))
                    d13_legend_failed_no_values_shown = True

            # Plot statistical outliers if enabled
            if show_statistical_outliers and not statistical_outliers.empty:
                statistical_customdata = _build_delta_point_customdata(statistical_outliers, 'd13C')
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
                    legendgroup='outliers',
                    customdata=statistical_customdata,
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd13C: %{y:.4f}<extra></extra>'
                    )
                ))

            # Plot range outliers by type if enabled
            if show_range_outliers and not range_outliers.empty:
                # Signal intensity outliers
                signal_mask = (range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                if signal_mask.any():
                    signal_outliers_df = range_outliers[signal_mask]
                    d13c_summary.add_trace(go.Scatter(
                        x=signal_outliers_df['x_axis'],
                        y=signal_outliers_df['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='diamond',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Signal Intensity Range',
                        showlegend=True,
                        legendgroup='outliers',
                        customdata=_build_delta_point_customdata(signal_outliers_df, 'd13C'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd13C: %{y:.4f}<extra></extra>'
                        )
                    ))

                # Leak rate outliers
                leak_mask = (range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (range_outliers['leak_rate'] > st.session_state.leak_range[1])
                if leak_mask.any():
                    leak_outliers_df = range_outliers[leak_mask]
                    d13c_summary.add_trace(go.Scatter(
                        x=leak_outliers_df['x_axis'],
                        y=leak_outliers_df['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='star',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Leak Rate Range',
                        showlegend=True,
                        legendgroup='outliers',
                        customdata=_build_delta_point_customdata(leak_outliers_df, 'd13C'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd13C: %{y:.4f}<extra></extra>'
                        )
                    ))

                # Î´13C range outliers
                d13c_mask = (range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                if d13c_mask.any():
                    d13_range_outliers_df = range_outliers[d13c_mask]
                    d13c_summary.add_trace(go.Scatter(
                        x=d13_range_outliers_df['x_axis'],
                        y=d13_range_outliers_df['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='cross',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='d13C Range',
                        showlegend=True,
                        legendgroup='outliers',
                        customdata=_build_delta_point_customdata(d13_range_outliers_df, 'd13C'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd13C: %{y:.4f}<extra></extra>'
                        )
                    ))

                # Î´18O range outliers
                d18o_mask = (range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
                if d18o_mask.any():
                    d18_range_outliers_df = range_outliers[d18o_mask]
                    d13c_summary.add_trace(go.Scatter(
                        x=d18_range_outliers_df['x_axis'],
                        y=d18_range_outliers_df['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='x',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='d18O Range',
                        showlegend=True,
                        legendgroup='outliers',
                        customdata=_build_delta_point_customdata(d18_range_outliers_df, 'd13C'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd13C: %{y:.4f}<extra></extra>'
                        )
                    ))
        if (
            d13_active_target is not None
            and d13_active_target.get('isotope_key') == 'd13C'
            and d13_active_target.get('row_label') in subset_data_unfiltered.index
        ):
            active_row = subset_data_unfiltered.loc[d13_active_target['row_label']]
            if isinstance(active_row, pd.DataFrame):
                active_row = active_row.iloc[0]
            x_active = active_row.get('x_axis', np.nan)
            if pd.notna(x_active):
                y_active = pd.to_numeric(pd.Series([active_row.get('d 13C/12C  Mean')]), errors='coerce').iloc[0]
                if pd.notna(y_active):
                    y_active_plot = float(y_active)
                else:
                    y_vals = pd.to_numeric(subset_data['d 13C/12C  Mean'], errors='coerce')
                    y_min = y_vals.min()
                    y_max = y_vals.max()
                    if not np.isfinite(y_min):
                        y_min, y_max = -1.0, 1.0
                    y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                    y_active_plot = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                status_val = str(active_row.get('Collector Status', '')).strip()
                marker_symbol = 'triangle-down' if status_val in ('Failed Sample', 'Fully Saturated Collectors') else 'circle'
                d13c_summary.add_trace(go.Scatter(
                    x=[x_active],
                    y=[y_active_plot],
                    mode='markers',
                    name='Active Selection',
                    marker=dict(
                        size=14,
                        symbol=marker_symbol,
                        color='#ff00ff',
                        line=dict(width=2, color='#ff00ff')
                    ),
                    showlegend=False,
                    legendgroup='active_selection'
                ))

        d13c_summary.update_layout(
            title="d13C Summary by Species",
            xaxis_title="Sample Number" if x_axis_option == "By Sequence" else "Identifier 2",
            yaxis_title="d13C",
            showlegend=True,
            height=500,
            clickmode='event+select',
            dragmode='zoom'
        )
        _apply_editor_selection_to_figure(d13c_summary, d13_summary_editor_prefix)
        d13_summary_nonce = int(st.session_state.get(f"{d13_summary_editor_prefix}_chart_nonce", 0))
        d13c_summary_state = st.plotly_chart(
            d13c_summary,
            width='stretch',
            key=f'tab3_d13c_summary_{d13_summary_nonce}',
            on_select='rerun',
            selection_mode='points'
        )
        _render_delta_editor_from_chart_selection(d13c_summary_state, d13_summary_editor_prefix)
        
        # Create summary chart for d18O
        d18o_summary = go.Figure()
        d18_active_target = _get_active_editor_target(d18_summary_editor_prefix)
        d18_legend_partial_shown = False
        d18_legend_failed_full_shown = False
        d18_legend_failed_no_values_shown = False
        d18_legend_failed_interp_shown = False
        d18_legend_edited_shown = False
        for species in unique_species:
            if species == "Unknown":
                continue
            
            species_data = subset_data[subset_data[species_col] == species]
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered[species_col] == species]
            if species_data_unfiltered.empty and species_data.empty:
                continue
            edited_mask_species = pd.Series(species_data.index.map(_is_row_edited), index=species_data.index, dtype=bool)

            status_series = species_data_unfiltered.get('Collector Status', pd.Series(False, index=species_data_unfiltered.index))
            saturated_collectors_mask = status_series == 'Partially Saturated Collectors'
            saturated_samples_mask = status_series == 'Fully Saturated Collectors'
            failed_mask = status_series == 'Failed Sample'
            saturated_samples_idx = species_data_unfiltered[saturated_samples_mask].index
            failed_idx = species_data_unfiltered[failed_mask].index
            
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
            outlier_mask = outlier_mask & ~edited_mask_species
            statistical_outliers = species_data[outlier_mask].copy()
            
            # Calculate range outliers
            if show_range_outliers:
                range_mask_unfiltered = (
                    (species_data_unfiltered['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                    (species_data_unfiltered['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                    (species_data_unfiltered['leak_rate'] < st.session_state.leak_range[0]) |
                    (species_data_unfiltered['leak_rate'] > st.session_state.leak_range[1])
                )
                edited_mask_species_unf = pd.Series(species_data_unfiltered.index.map(_is_row_edited), index=species_data_unfiltered.index, dtype=bool)
                range_mask_unfiltered = range_mask_unfiltered & ~edited_mask_species_unf
                range_outliers = species_data_unfiltered[range_mask_unfiltered].copy()
                range_mask_for_plot = range_mask_unfiltered.reindex(species_data.index, fill_value=False)
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
                range_mask_for_plot = pd.Series(False, index=species_data.index, dtype=bool)

            data_to_plot = species_data[
                ~(outlier_mask | range_mask_for_plot | species_data.index.isin(saturated_samples_idx) | species_data.index.isin(failed_idx))
            ].copy()
            
            # Sort data by x_axis to ensure sequential line connections
            data_to_plot = data_to_plot.sort_values('x_axis')

            # Plot main data
            # Generate unique color for this species
            species_color = f'rgb({hash(species) % 255}, {(hash(species) >> 8) % 255}, {(hash(species) >> 16) % 255})'
            d18_customdata = _build_delta_point_customdata(data_to_plot, 'd18O')
            
            # Plot main data with consistent color
            d18o_summary.add_trace(go.Scatter(
                x=data_to_plot['x_axis'],
                y=data_to_plot['d 18O/16O  Mean'],
                mode='lines+markers',
                name=species,
                marker=dict(
                    size=8,
                    color=data_to_plot[color_param_tab3_value_col],
                    colorscale="Viridis",
                    showscale=False,
                    symbol=species_symbol_map.get(species, 'circle')
                ),
                line=dict(width=1, color=species_color),
                legendgroup=species,
                customdata=d18_customdata,
                hovertemplate=(
                    'Identifier 1: %{customdata[2]}<br>'
                    'Identifier 2: %{customdata[3]}<br>'
                    'd18O: %{y:.4f}<extra></extra>'
                )
            ))
            edited_points = data_to_plot[data_to_plot.index.map(_is_row_edited)]
            if not edited_points.empty:
                edited_customdata = _build_delta_point_customdata(edited_points, 'd18O')
                d18o_summary.add_trace(go.Scatter(
                    x=edited_points['x_axis'],
                    y=edited_points['d 18O/16O  Mean'],
                    mode='markers',
                    name='Edited Samples',
                    marker=dict(
                        size=12,
                        symbol='circle',
                        color='#ff00ff',
                        line=dict(width=1, color='#ff00ff')
                    ),
                    showlegend=not d18_legend_edited_shown,
                    legendgroup='edited_samples',
                    customdata=edited_customdata,
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd18O: %{y:.4f}<extra></extra>'
                    )
                ))
                d18_legend_edited_shown = True

            # Highlight saturated collectors (compromised but valid)
            if show_saturated_collectors and saturated_collectors_mask.any():
                sat_collectors = species_data_unfiltered[saturated_collectors_mask]
                sat_collectors_customdata = _build_delta_point_customdata(sat_collectors, 'd18O')
                d18o_summary.add_trace(go.Scatter(
                    x=sat_collectors['x_axis'],
                    y=sat_collectors['d 18O/16O  Mean'],
                    mode='markers',
                    name='Partially Failed (Recovered Mean)',
                    marker=dict(
                        size=12,
                        symbol='diamond-open',
                        color='#ff7f0e',
                        line=dict(width=2, color='#ff7f0e')
                    ),
                    showlegend=not d18_legend_partial_shown,
                    legendgroup='collector_status',
                    customdata=sat_collectors_customdata,
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd18O: %{y:.4f}<extra></extra>'
                    )
                ))
                d18_legend_partial_shown = True

            # Plot saturated samples as outliers
            if show_saturated_samples and saturated_samples_mask.any():
                sat_samples = species_data_unfiltered[saturated_samples_mask]
                sat_customdata = _build_delta_point_customdata(sat_samples, 'd18O')
                y_vals_sat = pd.to_numeric(sat_samples['d 18O/16O  Mean'], errors='coerce')
                if y_vals_sat.notna().any():
                    y_sat = y_vals_sat.tolist()
                else:
                    y_vals = pd.to_numeric(species_data['d 18O/16O  Mean'], errors='coerce')
                    y_min = y_vals.min()
                    y_max = y_vals.max()
                    if not np.isfinite(y_min):
                        y_min, y_max = -1.0, 1.0
                    y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                    y_sat = [y_min - (0.15 * y_range if y_range > 0 else 0.75)] * len(sat_samples)
                d18o_summary.add_trace(go.Scatter(
                    x=sat_samples['x_axis'],
                    y=y_sat,
                    mode='markers',
                    name='Failed Samples (Fully Saturated)',
                    marker=dict(
                        size=12,
                        symbol='triangle-down',
                        color='#d62728',
                        line=dict(width=2, color='#d62728')
                    ),
                    showlegend=not d18_legend_failed_full_shown,
                    legendgroup='outliers',
                    customdata=sat_customdata,
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd18O: %{y:.4f}<extra></extra>'
                    )
                ))
                d18_legend_failed_full_shown = True

            if show_failed_samples and failed_mask.any():
                failed_samples = species_data_unfiltered[failed_mask]
                failed_vals = pd.to_numeric(failed_samples['d 18O/16O  Mean'], errors='coerce')
                failed_is_edited = pd.Series(failed_samples.index.map(_is_row_edited), index=failed_samples.index)
                failed_interp = failed_samples[failed_vals.notna() & failed_is_edited].copy()
                failed_recovered = failed_samples[failed_vals.notna() & ~failed_is_edited].copy()
                failed_missing = failed_samples[failed_vals.isna()].copy()
                if not failed_recovered.empty:
                    failed_recovered_customdata = _build_delta_point_customdata(failed_recovered, 'd18O')
                    d18o_summary.add_trace(go.Scatter(
                        x=failed_recovered['x_axis'],
                        y=pd.to_numeric(failed_recovered['d 18O/16O  Mean'], errors='coerce'),
                        mode='markers',
                        name='Partially Failed (Recovered Mean)',
                        marker=dict(
                            size=12,
                            symbol='diamond-open',
                            color='#ff7f0e',
                            line=dict(width=2, color='#ff7f0e')
                        ),
                        showlegend=not d18_legend_partial_shown,
                        legendgroup='collector_status',
                        customdata=failed_recovered_customdata,
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: %{y:.4f}<extra></extra>'
                        )
                    ))
                    d18_legend_partial_shown = True
                if not failed_interp.empty:
                    failed_interp_customdata = _build_delta_point_customdata(failed_interp, 'd18O')
                    d18o_summary.add_trace(go.Scatter(
                        x=failed_interp['x_axis'],
                        y=pd.to_numeric(failed_interp['d 18O/16O  Mean'], errors='coerce'),
                        mode='markers',
                        name='Failed Samples (Interpolated)',
                        marker=dict(
                            size=10,
                            symbol='triangle-down',
                            color='#ff00ff',
                            line=dict(width=1, color='#ff00ff')
                        ),
                        showlegend=not d18_legend_failed_interp_shown,
                        legendgroup='outliers',
                        customdata=failed_interp_customdata,
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: %{y:.4f}<extra></extra>'
                        )
                    ))
                    d18_legend_failed_interp_shown = True
                if not failed_missing.empty:
                    failed_customdata = _build_delta_point_customdata(failed_missing, 'd18O')
                y_vals = pd.to_numeric(species_data['d 18O/16O  Mean'], errors='coerce')
                y_min = y_vals.min()
                y_max = y_vals.max()
                if not np.isfinite(y_min):
                    y_min, y_max = -1.0, 1.0
                y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                if not failed_missing.empty:
                    d18o_summary.add_trace(go.Scatter(
                        x=failed_missing['x_axis'],
                        y=[y_failed] * len(failed_missing),
                        mode='markers',
                        name='Failed Samples (No Values)',
                        marker=dict(
                            size=10,
                            symbol='triangle-down',
                            color='#7f7f7f',
                            line=dict(width=1, color='#7f7f7f')
                        ),
                        showlegend=not d18_legend_failed_no_values_shown,
                        legendgroup='outliers',
                        text=failed_missing['Identifier 2'].astype(str),
                        customdata=failed_customdata,
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: missing (click to edit)<extra></extra>'
                        )
                    ))
                    d18_legend_failed_no_values_shown = True

            # Plot statistical outliers if enabled
            if show_statistical_outliers and not statistical_outliers.empty:
                statistical_customdata = _build_delta_point_customdata(statistical_outliers, 'd18O')
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
                    legendgroup='outliers',
                    customdata=statistical_customdata,
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd18O: %{y:.4f}<extra></extra>'
                    )
                ))

            # Plot range outliers by type if enabled
            if show_range_outliers and not range_outliers.empty:
                # Signal intensity outliers
                signal_mask = (range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                if signal_mask.any():
                    signal_outliers_df = range_outliers[signal_mask]
                    d18o_summary.add_trace(go.Scatter(
                        x=signal_outliers_df['x_axis'],
                        y=signal_outliers_df['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='diamond',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Signal Intensity Range',
                        showlegend=True,
                        legendgroup='outliers',
                        customdata=_build_delta_point_customdata(signal_outliers_df, 'd18O'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: %{y:.4f}<extra></extra>'
                        )
                    ))

                # Leak rate outliers
                leak_mask = (range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (range_outliers['leak_rate'] > st.session_state.leak_range[1])
                if leak_mask.any():
                    leak_outliers_df = range_outliers[leak_mask]
                    d18o_summary.add_trace(go.Scatter(
                        x=leak_outliers_df['x_axis'],
                        y=leak_outliers_df['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='star',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Leak Rate Range',
                        showlegend=True,
                        legendgroup='outliers',
                        customdata=_build_delta_point_customdata(leak_outliers_df, 'd18O'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: %{y:.4f}<extra></extra>'
                        )
                    ))

                # Î´13C range outliers
                d13c_mask = (range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                if d13c_mask.any():
                    d13_range_outliers_df = range_outliers[d13c_mask]
                    d18o_summary.add_trace(go.Scatter(
                        x=d13_range_outliers_df['x_axis'],
                        y=d13_range_outliers_df['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='cross',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='d13C Range',
                        showlegend=True,
                        legendgroup='outliers',
                        customdata=_build_delta_point_customdata(d13_range_outliers_df, 'd18O'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: %{y:.4f}<extra></extra>'
                        )
                    ))

                # Î´18O range outliers
                d18o_mask = (range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
                if d18o_mask.any():
                    d18_range_outliers_df = range_outliers[d18o_mask]
                    d18o_summary.add_trace(go.Scatter(
                        x=d18_range_outliers_df['x_axis'],
                        y=d18_range_outliers_df['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='x',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='d18O Range',
                        showlegend=True,
                        legendgroup='outliers',
                        customdata=_build_delta_point_customdata(d18_range_outliers_df, 'd18O'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: %{y:.4f}<extra></extra>'
                        )
                    ))
        if (
            d18_active_target is not None
            and d18_active_target.get('isotope_key') == 'd18O'
            and d18_active_target.get('row_label') in subset_data_unfiltered.index
        ):
            active_row = subset_data_unfiltered.loc[d18_active_target['row_label']]
            if isinstance(active_row, pd.DataFrame):
                active_row = active_row.iloc[0]
            x_active = active_row.get('x_axis', np.nan)
            if pd.notna(x_active):
                y_active = pd.to_numeric(pd.Series([active_row.get('d 18O/16O  Mean')]), errors='coerce').iloc[0]
                if pd.notna(y_active):
                    y_active_plot = float(y_active)
                else:
                    y_vals = pd.to_numeric(subset_data['d 18O/16O  Mean'], errors='coerce')
                    y_min = y_vals.min()
                    y_max = y_vals.max()
                    if not np.isfinite(y_min):
                        y_min, y_max = -1.0, 1.0
                    y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                    y_active_plot = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                status_val = str(active_row.get('Collector Status', '')).strip()
                marker_symbol = 'triangle-down' if status_val in ('Failed Sample', 'Fully Saturated Collectors') else 'circle'
                d18o_summary.add_trace(go.Scatter(
                    x=[x_active],
                    y=[y_active_plot],
                    mode='markers',
                    name='Active Selection',
                    marker=dict(
                        size=14,
                        symbol=marker_symbol,
                        color='#ff00ff',
                        line=dict(width=2, color='#ff00ff')
                    ),
                    showlegend=False,
                    legendgroup='active_selection'
                ))

        d18o_summary.update_layout(
            title="d18O Summary by Species",
            xaxis_title="Sample Number" if x_axis_option == "By Sequence" else "Identifier 2",
            yaxis_title="d18O",
            showlegend=True,
            height=500,
            clickmode='event+select',
            dragmode='zoom'
        )
        # Invert y-axis so increasing d18O plots downward
        d18o_summary.update_yaxes(autorange='reversed')
        _apply_editor_selection_to_figure(d18o_summary, d18_summary_editor_prefix)
        d18_summary_nonce = int(st.session_state.get(f"{d18_summary_editor_prefix}_chart_nonce", 0))
        d18o_summary_state = st.plotly_chart(
            d18o_summary,
            width='stretch',
            key=f'tab3_d18o_summary_{d18_summary_nonce}',
            on_select='rerun',
            selection_mode='points'
        )
        _render_delta_editor_from_chart_selection(d18o_summary_state, d18_summary_editor_prefix)

        # Create cross-plot: d13C vs d18O grouped by Species
        species_scatter = go.Figure()
        scatter_legend_partial_shown = False
        scatter_legend_failed_full_shown = False
        scatter_legend_statistical_shown = False
        for species in unique_species:
            if species == "Unknown":
                continue

            species_data = subset_data[subset_data[species_col] == species]
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered[species_col] == species]
            if species_data_unfiltered.empty and species_data.empty:
                continue

            status_series = species_data_unfiltered.get('Collector Status', pd.Series(False, index=species_data_unfiltered.index))
            saturated_collectors_mask = status_series == 'Partially Saturated Collectors'
            saturated_samples_mask = status_series == 'Fully Saturated Collectors'
            saturated_samples_idx = species_data_unfiltered[saturated_samples_mask].index
            failed_mask = status_series == 'Failed Sample'
            failed_idx = species_data_unfiltered[failed_mask].index

            # Compute statistical thresholds per species
            mean_d13C = species_data['d 13C/12C  Mean'].mean()
            std_d13C = species_data['d 13C/12C  Mean'].std()
            mean_d18O = species_data['d 18O/16O  Mean'].mean()
            std_d18O = species_data['d 18O/16O  Mean'].std()
            edited_mask_species = pd.Series(species_data.index.map(_is_row_edited), index=species_data.index, dtype=bool)

            outlier_mask = (
                (species_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                (species_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                (species_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                (species_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
            )
            outlier_mask = outlier_mask & ~edited_mask_species

            range_mask_unfiltered = (
                (species_data_unfiltered['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (species_data_unfiltered['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                (species_data_unfiltered['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (species_data_unfiltered['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                (species_data_unfiltered['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (species_data_unfiltered['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                (species_data_unfiltered['leak_rate'] < st.session_state.leak_range[0]) |
                (species_data_unfiltered['leak_rate'] > st.session_state.leak_range[1])
            )
            edited_mask_species_unfiltered = pd.Series(
                species_data_unfiltered.index.map(_is_row_edited),
                index=species_data_unfiltered.index,
                dtype=bool
            )
            range_mask_unfiltered = range_mask_unfiltered & ~edited_mask_species_unfiltered
            range_mask_for_plot = range_mask_unfiltered.reindex(species_data.index, fill_value=False)

            # Filter to non-outliers for main scatter
            data_to_plot = species_data[
                ~(outlier_mask | range_mask_for_plot | species_data.index.isin(saturated_samples_idx) | species_data.index.isin(failed_idx))
            ].copy()
            # Drop rows with missing isotope values
            data_to_plot = data_to_plot[
                data_to_plot['d 13C/12C  Mean'].notna() & data_to_plot['d 18O/16O  Mean'].notna()
            ]
            if data_to_plot.empty:
                continue

            # Color and symbol by species, reusing existing mapping for consistency
            species_color = f'rgb({hash(species) % 255}, {(hash(species) >> 8) % 255}, {(hash(species) >> 16) % 255})'
            species_symbol = species_symbol_map.get(species, 'circle')

            species_scatter.add_trace(go.Scatter(
                x=data_to_plot['d 18O/16O  Mean'],
                y=data_to_plot['d 13C/12C  Mean'],
                mode='markers',
                name=species,
                marker=dict(
                    size=10,
                    color=species_color,
                    symbol=species_symbol,
                    line=dict(width=1, color=species_color)
                ),
                hovertemplate=(
                    f'Species: {species}<br>'
                    'd18O: %{x:.4f}<br>'
                    'd13C: %{y:.4f}<br>'
                    'Identifier 2: %{text}<extra></extra>'
                ),
                text=data_to_plot['Identifier 2'].astype(str)
            ))

            # Overlay sigma-based outliers on the species cross-plot.
            if show_statistical_outliers:
                stat_outliers_scatter = species_data[outlier_mask].copy()
                stat_outliers_scatter = stat_outliers_scatter[
                    stat_outliers_scatter['d 13C/12C  Mean'].notna() & stat_outliers_scatter['d 18O/16O  Mean'].notna()
                ]
                if not stat_outliers_scatter.empty:
                    species_scatter.add_trace(go.Scatter(
                        x=stat_outliers_scatter['d 18O/16O  Mean'],
                        y=stat_outliers_scatter['d 13C/12C  Mean'],
                        mode='markers',
                        name='Statistical Outliers',
                        marker=dict(
                            size=12,
                            symbol='x',
                            color='red',
                            line=dict(width=2, color='red')
                        ),
                        showlegend=not scatter_legend_statistical_shown,
                        legendgroup='outliers',
                        text=stat_outliers_scatter['Identifier 2'].astype(str),
                        hovertemplate=(
                            f'Species: {species}<br>'
                            'd18O: %{x:.4f}<br>'
                            'd13C: %{y:.4f}<br>'
                            'Identifier 2: %{text}<extra></extra>'
                        )
                    ))
                    scatter_legend_statistical_shown = True

            # Overlay saturated collectors (valid means)
            if show_saturated_collectors and saturated_collectors_mask.any():
                sat_collectors = species_data_unfiltered[saturated_collectors_mask]
                sat_collectors = sat_collectors[
                    sat_collectors['d 13C/12C  Mean'].notna() & sat_collectors['d 18O/16O  Mean'].notna()
                ]
                if not sat_collectors.empty:
                    species_scatter.add_trace(go.Scatter(
                        x=sat_collectors['d 18O/16O  Mean'],
                        y=sat_collectors['d 13C/12C  Mean'],
                        mode='markers',
                        name='Partially Failed (Recovered Mean)',
                        marker=dict(
                            size=12,
                            symbol='diamond-open',
                            color='#ff7f0e',
                            line=dict(width=2, color='#ff7f0e')
                        ),
                        showlegend=not scatter_legend_partial_shown,
                        legendgroup='collector_status',
                        text=sat_collectors['Identifier 2'].astype(str)
                    ))
                    scatter_legend_partial_shown = True

            # Overlay saturated samples as outliers
            if show_saturated_samples and saturated_samples_mask.any():
                sat_samples = species_data_unfiltered[saturated_samples_mask]
                sat_samples = sat_samples[
                    sat_samples['d 13C/12C  Mean'].notna() & sat_samples['d 18O/16O  Mean'].notna()
                ]
                if not sat_samples.empty:
                    species_scatter.add_trace(go.Scatter(
                        x=sat_samples['d 18O/16O  Mean'],
                        y=sat_samples['d 13C/12C  Mean'],
                        mode='markers',
                        name='Failed Samples (Fully Saturated)',
                        marker=dict(
                            size=12,
                            symbol='triangle-down',
                            color='#d62728',
                            line=dict(width=2, color='#d62728')
                        ),
                        showlegend=not scatter_legend_failed_full_shown,
                        legendgroup='outliers',
                        text=sat_samples['Identifier 2'].astype(str)
                    ))
                    scatter_legend_failed_full_shown = True

        species_scatter.update_layout(
            title="d13C vs d18O by Species",
            xaxis_title="d18O",
            yaxis_title="d13C",
            showlegend=True,
            height=500,
            dragmode='zoom'
        )
        st.plotly_chart(species_scatter, width='stretch')

        # Process individual species
        for species in unique_species:
            # Filter data for this specific species
            species_data = subset_data[subset_data[species_col] == species].copy()
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered[species_col] == species].copy()
            if species_data_unfiltered.empty and species_data.empty:
                continue

            # Skip if Identifier 2 is empty
            if species_data['Identifier 2'].isna().all() and species_data_unfiltered['Identifier 2'].isna().all():
                continue

            status_series = species_data_unfiltered.get('Collector Status', pd.Series(False, index=species_data_unfiltered.index))
            saturated_collectors_mask = status_series == 'Partially Saturated Collectors'
            saturated_samples_mask = status_series == 'Fully Saturated Collectors'
            failed_mask = status_series == 'Failed Sample'
            saturated_samples_idx = species_data_unfiltered[saturated_samples_mask].index
            failed_idx = species_data_unfiltered[failed_mask].index
            edited_mask_species = pd.Series(species_data.index.map(_is_row_edited), index=species_data.index, dtype=bool)
            edited_mask_species_unfiltered = pd.Series(
                species_data_unfiltered.index.map(_is_row_edited),
                index=species_data_unfiltered.index,
                dtype=bool
            )

            col1, col2 = st.columns([3, 1])
            with col1:
                st.subheader(f'Species: {species}')

            # Calculate thresholds for outliers for each comment subset
            mean_d13C = species_data['d 13C/12C  Mean'].mean()
            std_d13C = species_data['d 13C/12C  Mean'].std()
            mean_d18O = species_data['d 18O/16O  Mean'].mean()
            std_d18O = species_data['d 18O/16O  Mean'].std()

            lower_threshold_d13C = mean_d13C - (sigma_level_data * std_d13C)
            upper_threshold_d13C = mean_d13C + (sigma_level_data * std_d13C)
            lower_threshold_d18O = mean_d18O - (sigma_level_data * std_d18O)
            upper_threshold_d18O = mean_d18O + (sigma_level_data * std_d18O)

            # Create x_axis values first for all data
            species_data['x_axis'] = np.nan
            if x_axis_option == "By Identifier 2":
                species_data['x_axis'] = species_data['Identifier 2'].apply(
                    lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                        r'\d+\.?\d*', str(x)) else None
                )
            else:
                species_data['x_axis'] = range(len(species_data))

            # Now identify statistical outliers (after x_axis is created)
            outlier_mask = (
                (species_data['d 13C/12C  Mean'] < lower_threshold_d13C) |
                (species_data['d 13C/12C  Mean'] > upper_threshold_d13C) |
                (species_data['d 18O/16O  Mean'] < lower_threshold_d18O) |
                (species_data['d 18O/16O  Mean'] > upper_threshold_d18O)
            )
            outlier_mask = outlier_mask & ~edited_mask_species
            # Apply mask and include necessary columns (including x_axis)
            statistical_outliers = species_data[outlier_mask].copy()

            # Remove statistical outliers and saturated samples from data_to_plot
            saturated_idx_mask = pd.Series(species_data.index.isin(saturated_samples_idx), index=species_data.index, dtype=bool)
            failed_idx_mask = pd.Series(species_data.index.isin(failed_idx), index=species_data.index, dtype=bool)
            drop_mask = outlier_mask.reindex(species_data.index, fill_value=False) | saturated_idx_mask | failed_idx_mask
            data_to_plot = species_data[~drop_mask].copy()

            # Identify range bar outliers from unfiltered data
            # Identify and process range outliers if enabled
            if show_range_outliers:
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
                range_mask = range_mask & ~edited_mask_species_unfiltered
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
                range_bar_outliers = pd.DataFrame(columns=['Identifier 1', 'Identifier 2', 'd 13C/12C  Mean', 'd 18O/16O  Mean', species_col, 'x_axis'])

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

                has_status_markers = False
                if show_saturated_collectors and not species_data_unfiltered[
                    (species_data_unfiltered['Identifier 1'] == identifier) & saturated_collectors_mask
                ].empty:
                    has_status_markers = True
                if show_saturated_samples and not species_data_unfiltered[
                    (species_data_unfiltered['Identifier 1'] == identifier) & saturated_samples_mask
                ].empty:
                    has_status_markers = True
                if show_failed_samples and not species_data_unfiltered[
                    (species_data_unfiltered['Identifier 1'] == identifier) & failed_mask
                ].empty:
                    has_status_markers = True

                if data_for_identifier.empty and not has_status_markers:
                    continue  # Skip if there is no data to plot for this identifier

                key_suffix_base = re.sub(r'[^0-9A-Za-z_]+', '_', f"{species}_{identifier}")
                key_suffix_base = f"{key_suffix_base}_{abs(hash((species, identifier))) % 10000000}"
                d13_key_suffix = f"{key_suffix_base}_d13"
                d18_key_suffix = f"{key_suffix_base}_d18"
                d13_raw_only_key = f"tab3_d13c_raw_line_only_{d13_key_suffix}"
                d18_raw_only_key = f"tab3_d18o_raw_line_only_{d18_key_suffix}"
                d13_hide_cal_key = f"tab3_d13c_hide_calibrated_{d13_key_suffix}"
                d18_hide_cal_key = f"tab3_d18o_hide_calibrated_{d18_key_suffix}"
                d13_raw_line_only = bool(st.session_state.get(d13_raw_only_key, False))
                d18_raw_line_only = bool(st.session_state.get(d18_raw_only_key, False))
                d13_hide_calibrated = bool(st.session_state.get(d13_hide_cal_key, False))
                d18_hide_calibrated = bool(st.session_state.get(d18_hide_cal_key, False))

                # Plot d13C data for this identifier and comment
                # Create figure for d13C
                fig_d13C = go.Figure()

                # Add statistical outliers as markers if enabled
                if show_statistical_outliers and not statistical_outliers.empty:
                    identifier_stat_outliers = statistical_outliers[statistical_outliers['Identifier 1'] == identifier]
                    if not identifier_stat_outliers.empty:
                        stat_customdata = _build_delta_point_customdata(identifier_stat_outliers, 'd13C')
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
                            name='Statistical Outliers',
                            customdata=stat_customdata,
                            hovertemplate=(
                                'Identifier 1: %{customdata[2]}<br>'
                                'Identifier 2: %{customdata[3]}<br>'
                                'd13C: %{y:.4f}<extra></extra>'
                            )
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
                            signal_df = identifier_range_outliers[signal_range_mask]
                            fig_d13C.add_trace(go.Scatter(
                                x=signal_df['x_axis'],
                                y=signal_df['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='diamond', size=12, line=dict(width=2)),
                                name='Signal Intensity Range',
                                customdata=_build_delta_point_customdata(signal_df, 'd13C'),
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd13C: %{y:.4f}<extra></extra>'
                                )
                            ))
                        if leak_range_mask.any():
                            leak_df = identifier_range_outliers[leak_range_mask]
                            fig_d13C.add_trace(go.Scatter(
                                x=leak_df['x_axis'],
                                y=leak_df['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='star', size=12, line=dict(width=2)),
                                name='Leak Rate Range',
                                customdata=_build_delta_point_customdata(leak_df, 'd13C'),
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd13C: %{y:.4f}<extra></extra>'
                                )
                            ))
                        if d13c_filter_mask.any():
                            d13_df = identifier_range_outliers[d13c_filter_mask]
                            fig_d13C.add_trace(go.Scatter(
                                x=d13_df['x_axis'],
                                y=d13_df['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='cross', size=12, line=dict(width=2)),
                                name='d13C Range',
                                customdata=_build_delta_point_customdata(d13_df, 'd13C'),
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd13C: %{y:.4f}<extra></extra>'
                                )
                            ))
                        if d18o_filter_mask.any():
                            d18_df = identifier_range_outliers[d18o_filter_mask]
                            fig_d13C.add_trace(go.Scatter(
                                x=d18_df['x_axis'],
                                y=d18_df['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='x', size=12, line=dict(width=2)),
                                name='d18O Range',
                                customdata=_build_delta_point_customdata(d18_df, 'd13C'),
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd13C: %{y:.4f}<extra></extra>'
                                )
                            ))

                # Highlight saturated collectors (valid means)
                if show_saturated_collectors:
                    identifier_sat_collectors = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & saturated_collectors_mask
                    ]
                    if not identifier_sat_collectors.empty:
                        sat_collectors_customdata = _build_delta_point_customdata(identifier_sat_collectors, 'd13C')
                        fig_d13C.add_trace(go.Scatter(
                            x=identifier_sat_collectors['x_axis'],
                            y=identifier_sat_collectors['d 13C/12C  Mean'],
                            mode='markers',
                            marker=dict(color='#ff7f0e', symbol='diamond-open', size=12, line=dict(width=2)),
                            name='Partially Failed (Recovered Mean)',
                            customdata=sat_collectors_customdata,
                            hovertemplate=(
                                'Identifier 1: %{customdata[2]}<br>'
                                'Identifier 2: %{customdata[3]}<br>'
                                'd13C: %{y:.4f}<extra></extra>'
                            )
                        ))

                # Show saturated samples as outliers
                if show_saturated_samples:
                    identifier_sat_samples = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & saturated_samples_mask
                    ]
                    if not identifier_sat_samples.empty:
                        sat_customdata = _build_delta_point_customdata(identifier_sat_samples, 'd13C')
                        y_vals_sat = pd.to_numeric(identifier_sat_samples['d 13C/12C  Mean'], errors='coerce')
                        if y_vals_sat.notna().any():
                            y_sat = y_vals_sat.tolist()
                        else:
                            y_vals = pd.to_numeric(data_for_identifier['d 13C/12C  Mean'], errors='coerce')
                            y_min = y_vals.min()
                            y_max = y_vals.max()
                            if not np.isfinite(y_min):
                                y_min, y_max = -1.0, 1.0
                            y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                            y_sat = [y_min - (0.15 * y_range if y_range > 0 else 0.75)] * len(identifier_sat_samples)
                        fig_d13C.add_trace(go.Scatter(
                            x=identifier_sat_samples['x_axis'],
                            y=y_sat,
                            mode='markers',
                            marker=dict(color='#d62728', symbol='triangle-down', size=12, line=dict(width=2)),
                            name='Failed Samples (Fully Saturated)',
                            customdata=sat_customdata,
                            hovertemplate=(
                                'Identifier 1: %{customdata[2]}<br>'
                                'Identifier 2: %{customdata[3]}<br>'
                                'd13C: %{y:.4f}<extra></extra>'
                            )
                        ))

                if show_failed_samples:
                    identifier_failed = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & failed_mask
                    ]
                    if not identifier_failed.empty:
                        failed_vals = pd.to_numeric(identifier_failed['d 13C/12C  Mean'], errors='coerce')
                        failed_is_edited = pd.Series(identifier_failed.index.map(_is_row_edited), index=identifier_failed.index)
                        identifier_failed_interp = identifier_failed[failed_vals.notna() & failed_is_edited].copy()
                        identifier_failed_recovered = identifier_failed[failed_vals.notna() & ~failed_is_edited].copy()
                        identifier_failed_missing = identifier_failed[failed_vals.isna()].copy()
                        if not identifier_failed_recovered.empty:
                            failed_recovered_customdata = _build_delta_point_customdata(identifier_failed_recovered, 'd13C')
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_failed_recovered['x_axis'],
                                y=pd.to_numeric(identifier_failed_recovered['d 13C/12C  Mean'], errors='coerce'),
                                mode='markers',
                                marker=dict(color='#ff7f0e', symbol='diamond-open', size=12, line=dict(width=2)),
                                name='Partially Failed (Recovered Mean)',
                                customdata=failed_recovered_customdata,
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd13C: %{y:.4f}<extra></extra>'
                                )
                            ))
                        if not identifier_failed_interp.empty:
                            failed_interp_customdata = _build_delta_point_customdata(identifier_failed_interp, 'd13C')
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_failed_interp['x_axis'],
                                y=pd.to_numeric(identifier_failed_interp['d 13C/12C  Mean'], errors='coerce'),
                                mode='markers',
                                marker=dict(color='#ff00ff', symbol='triangle-down', size=10, line=dict(width=1)),
                                name='Failed Samples (Interpolated)',
                                customdata=failed_interp_customdata,
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd13C: %{y:.4f}<extra></extra>'
                                )
                            ))
                        if not identifier_failed_missing.empty:
                            failed_customdata = _build_delta_point_customdata(identifier_failed_missing, 'd13C')
                        y_vals = pd.to_numeric(data_for_identifier['d 13C/12C  Mean'], errors='coerce')
                        y_min = y_vals.min()
                        y_max = y_vals.max()
                        if not np.isfinite(y_min):
                            y_min, y_max = -1.0, 1.0
                        y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                        y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                        if not identifier_failed_missing.empty:
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_failed_missing['x_axis'],
                                y=[y_failed] * len(identifier_failed_missing),
                                mode='markers',
                                marker=dict(color='#7f7f7f', symbol='triangle-down', size=10, line=dict(width=1)),
                                name='Failed Samples (No Values)',
                                customdata=failed_customdata,
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd13C: missing (click to edit)<extra></extra>'
                                )
                            ))

                identifier_display_data = display_data[display_data['Identifier 1'] == identifier]
                identifier_curve_data = _augment_curve_with_edited_rows(
                    identifier_display_data,
                    species_data_unfiltered,
                    identifier
                )
                d13_identifier_customdata = _build_delta_point_customdata(identifier_curve_data, 'd13C')

                fig_d13C.add_trace(go.Scatter(
                    x=identifier_curve_data['x_axis'],
                    y=identifier_curve_data['d 13C/12C  Mean'],
                    mode='lines+markers',
                    line=dict(color='blue', dash='dot', width=2),
                    marker=dict(
                        color=identifier_curve_data[color_param_tab3_value_col],
                        colorscale="Viridis",
                        symbol='circle',
                        size=8,
                        showscale=False  # Hide individual colorbar
                    ),
                    name=f'Raw d13C - {identifier}',
                    customdata=d13_identifier_customdata,
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd13C: %{y:.4f}<extra></extra>'
                    )
                ))
                edited_identifier_d13 = identifier_curve_data[identifier_curve_data.index.map(_is_row_edited)]
                if not edited_identifier_d13.empty:
                    edited_customdata = _build_delta_point_customdata(edited_identifier_d13, 'd13C')
                    fig_d13C.add_trace(go.Scatter(
                        x=edited_identifier_d13['x_axis'],
                        y=edited_identifier_d13['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color='#ff00ff',
                            symbol='circle',
                            size=12,
                            line=dict(width=1, color='#ff00ff')
                        ),
                        name='Edited Samples',
                        customdata=edited_customdata,
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd13C: %{y:.4f}<extra></extra>'
                        )
                    ))

                d13_has_calibrated_curve = (
                    'd13C_calibrated' in identifier_curve_data.columns
                    and pd.to_numeric(identifier_curve_data['d13C_calibrated'], errors='coerce').notna().any()
                )
                if d13_has_calibrated_curve and not d13_hide_calibrated and not d13_raw_line_only:
                    fig_d13C.add_trace(go.Scatter(
                        x=identifier_curve_data['x_axis'],
                        y=identifier_curve_data['d13C_calibrated'],
                        mode='lines',
                        line=dict(color='orange', width=2),
                        name=f'Calibrated d13C - {identifier}'
                    ))

                if d13_raw_line_only:
                    fig_d13C = go.Figure()
                    fig_d13C.add_trace(go.Scatter(
                        x=identifier_curve_data['x_axis'],
                        y=identifier_curve_data['d 13C/12C  Mean'],
                        mode='lines',
                        line=dict(color='blue', width=2),
                        name=f'Raw d13C - {identifier}',
                        customdata=d13_identifier_customdata,
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd13C: %{y:.4f}<extra></extra>'
                        )
                    ))

                fig_d13C.update_layout(
                    title=f'{identifier} - d13C for Species: {species}',
                    xaxis_title='X Axis',
                    yaxis_title='d13C (‰)',
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

                fig_d13C.update_layout(clickmode='event+select', dragmode='zoom')
                d13_editor_prefix = f"tab3_d13c_editor_{d13_key_suffix}"
                d13_active_target = _get_active_editor_target(d13_editor_prefix)
                if (
                    not d13_raw_line_only
                    and
                    d13_active_target is not None
                    and d13_active_target.get('isotope_key') == 'd13C'
                    and d13_active_target.get('row_label') in species_data_unfiltered.index
                ):
                    active_row = species_data_unfiltered.loc[d13_active_target['row_label']]
                    if isinstance(active_row, pd.DataFrame):
                        active_row = active_row.iloc[0]
                    x_active = active_row.get('x_axis', np.nan)
                    if pd.notna(x_active):
                        y_active = pd.to_numeric(pd.Series([active_row.get('d 13C/12C  Mean')]), errors='coerce').iloc[0]
                        if pd.notna(y_active):
                            y_active_plot = float(y_active)
                        else:
                            y_vals = pd.to_numeric(data_for_identifier['d 13C/12C  Mean'], errors='coerce')
                            y_min = y_vals.min()
                            y_max = y_vals.max()
                            if not np.isfinite(y_min):
                                y_min, y_max = -1.0, 1.0
                            y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                            y_active_plot = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                        status_val = str(active_row.get('Collector Status', '')).strip()
                        marker_symbol = 'triangle-down' if status_val in ('Failed Sample', 'Fully Saturated Collectors') else 'circle'
                        fig_d13C.add_trace(go.Scatter(
                            x=[x_active],
                            y=[y_active_plot],
                            mode='markers',
                            marker=dict(
                                color='#ff00ff',
                                symbol=marker_symbol,
                                size=14,
                                line=dict(width=2, color='#ff00ff')
                            ),
                            name='Active Selection',
                            showlegend=False,
                            legendgroup='active_selection'
                        ))
                _apply_editor_selection_to_figure(fig_d13C, d13_editor_prefix)
                d13_chart_nonce = int(st.session_state.get(f"{d13_editor_prefix}_chart_nonce", 0))
                d13_chart_col, d13_btn_col = st.columns([12, 1], gap="small")
                with d13_btn_col:
                    d13_btn_label = "Show Full" if d13_raw_line_only else "Raw Line Only"
                    if st.button(d13_btn_label, key=f"{d13_raw_only_key}_btn", use_container_width=True):
                        st.session_state[d13_raw_only_key] = not d13_raw_line_only
                        st.rerun()
                    if d13_has_calibrated_curve:
                        d13_cal_btn_label = "Show calibrated curve" if d13_hide_calibrated else "Hide calibrated curve"
                        if st.button(d13_cal_btn_label, key=f"{d13_hide_cal_key}_btn", use_container_width=True):
                            st.session_state[d13_hide_cal_key] = not d13_hide_calibrated
                            st.rerun()
                with d13_chart_col:
                    d13_chart_state = st.plotly_chart(
                        fig_d13C,
                        width='stretch',
                        height=chart_height,
                        key=f"tab3_d13c_{d13_key_suffix}_{d13_chart_nonce}",
                        on_select='rerun',
                        selection_mode='points'
                    )
                if not d13_raw_line_only:
                    _render_delta_editor_from_chart_selection(d13_chart_state, d13_editor_prefix)

                # Plot Î´18O data for this identifier and comment
                # Create figure for Î´18O
                fig_d18O = go.Figure()

                # Add statistical outliers if enabled
                if show_statistical_outliers:
                    identifier_stat_outliers = statistical_outliers[statistical_outliers['Identifier 1'] == identifier]
                    if not identifier_stat_outliers.empty:
                        stat_customdata = _build_delta_point_customdata(identifier_stat_outliers, 'd18O')
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
                            name='Statistical Outliers',
                            customdata=stat_customdata,
                            hovertemplate=(
                                'Identifier 1: %{customdata[2]}<br>'
                                'Identifier 2: %{customdata[3]}<br>'
                                'd18O: %{y:.4f}<extra></extra>'
                            )
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
                            signal_df = identifier_range_outliers[signal_range_mask]
                            fig_d18O.add_trace(go.Scatter(
                                x=signal_df['x_axis'],
                                y=signal_df['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='diamond', size=12, line=dict(width=2)),
                                name='Signal Intensity Range',
                                customdata=_build_delta_point_customdata(signal_df, 'd18O'),
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd18O: %{y:.4f}<extra></extra>'
                                )
                            ))
                        if leak_range_mask.any():
                            leak_df = identifier_range_outliers[leak_range_mask]
                            fig_d18O.add_trace(go.Scatter(
                                x=leak_df['x_axis'],
                                y=leak_df['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='star', size=12, line=dict(width=2)),
                                name='Leak Rate Range',
                                customdata=_build_delta_point_customdata(leak_df, 'd18O'),
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd18O: %{y:.4f}<extra></extra>'
                                )
                            ))

                    # Add main data trace using display_data
                    fig_d18O.add_trace(go.Scatter(
                        x=identifier_curve_data['x_axis'],
                        y=identifier_curve_data['d 18O/16O  Mean'],
                        mode='lines+markers',
                        line=dict(color='blue', dash='dot', width=2),
                        marker=dict(
                            color=identifier_curve_data[color_param_tab3_value_col],
                            colorscale="Viridis",
                            symbol='circle',
                            size=8,
                            showscale=False  # Hide individual colorbar
                        ),
                        name=f'Raw d18O - {identifier}',
                        customdata=_build_delta_point_customdata(identifier_curve_data, 'd18O'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: %{y:.4f}<extra></extra>'
                        )
                    ))
    
                    d18_has_calibrated_curve_identifier = (
                        'd18O_calibrated' in identifier_curve_data.columns
                        and pd.to_numeric(identifier_curve_data['d18O_calibrated'], errors='coerce').notna().any()
                    )
                    if d18_has_calibrated_curve_identifier:
                        if not d18_hide_calibrated and not d18_raw_line_only:
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_curve_data['x_axis'],
                                y=identifier_curve_data['d18O_calibrated'],
                                mode='lines',
                                line=dict(color='orange', width=2),
                                name=f'Calibrated d18O - {identifier}'
                            ))
                        if d13c_filter_mask.any():
                            d13_df = identifier_range_outliers[d13c_filter_mask]
                            fig_d18O.add_trace(go.Scatter(
                                x=d13_df['x_axis'],
                                y=d13_df['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='cross', size=12, line=dict(width=2)),
                                name='d13C Range',
                                customdata=_build_delta_point_customdata(d13_df, 'd18O'),
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd18O: %{y:.4f}<extra></extra>'
                                )
                            ))
                        if d18o_filter_mask.any():
                            d18_df = identifier_range_outliers[d18o_filter_mask]
                            fig_d18O.add_trace(go.Scatter(
                                x=d18_df['x_axis'],
                                y=d18_df['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='x', size=12, line=dict(width=2)),
                                name='d18O Range',
                                customdata=_build_delta_point_customdata(d18_df, 'd18O'),
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd18O: %{y:.4f}<extra></extra>'
                                )
                            ))

                # Plot main data trace with correct sorting
                sorted_data = identifier_curve_data.sort_values(by='x_axis')
                d18_has_calibrated_curve = (
                    'd18O_calibrated' in sorted_data.columns
                    and pd.to_numeric(sorted_data['d18O_calibrated'], errors='coerce').notna().any()
                )

                # Highlight saturated collectors (valid means)
                if show_saturated_collectors:
                    identifier_sat_collectors = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & saturated_collectors_mask
                    ]
                    if not identifier_sat_collectors.empty:
                        sat_collectors_customdata = _build_delta_point_customdata(identifier_sat_collectors, 'd18O')
                        fig_d18O.add_trace(go.Scatter(
                            x=identifier_sat_collectors['x_axis'],
                            y=identifier_sat_collectors['d 18O/16O  Mean'],
                            mode='markers',
                            marker=dict(color='#ff7f0e', symbol='diamond-open', size=12, line=dict(width=2)),
                            name='Partially Failed (Recovered Mean)',
                            customdata=sat_collectors_customdata,
                            hovertemplate=(
                                'Identifier 1: %{customdata[2]}<br>'
                                'Identifier 2: %{customdata[3]}<br>'
                                'd18O: %{y:.4f}<extra></extra>'
                            )
                        ))

                # Show saturated samples as outliers
                if show_saturated_samples:
                    identifier_sat_samples = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & saturated_samples_mask
                    ]
                    if not identifier_sat_samples.empty:
                        sat_customdata = _build_delta_point_customdata(identifier_sat_samples, 'd18O')
                        y_vals_sat = pd.to_numeric(identifier_sat_samples['d 18O/16O  Mean'], errors='coerce')
                        if y_vals_sat.notna().any():
                            y_sat = y_vals_sat.tolist()
                        else:
                            y_vals = pd.to_numeric(data_for_identifier['d 18O/16O  Mean'], errors='coerce')
                            y_min = y_vals.min()
                            y_max = y_vals.max()
                            if not np.isfinite(y_min):
                                y_min, y_max = -1.0, 1.0
                            y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                            y_sat = [y_min - (0.15 * y_range if y_range > 0 else 0.75)] * len(identifier_sat_samples)
                        fig_d18O.add_trace(go.Scatter(
                            x=identifier_sat_samples['x_axis'],
                            y=y_sat,
                            mode='markers',
                            marker=dict(color='#d62728', symbol='triangle-down', size=12, line=dict(width=2)),
                            name='Failed Samples (Fully Saturated)',
                            customdata=sat_customdata,
                            hovertemplate=(
                                'Identifier 1: %{customdata[2]}<br>'
                                'Identifier 2: %{customdata[3]}<br>'
                                'd18O: %{y:.4f}<extra></extra>'
                            )
                        ))

                if show_failed_samples:
                    identifier_failed = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & failed_mask
                    ]
                    if not identifier_failed.empty:
                        failed_vals = pd.to_numeric(identifier_failed['d 18O/16O  Mean'], errors='coerce')
                        failed_is_edited = pd.Series(identifier_failed.index.map(_is_row_edited), index=identifier_failed.index)
                        identifier_failed_interp = identifier_failed[failed_vals.notna() & failed_is_edited].copy()
                        identifier_failed_recovered = identifier_failed[failed_vals.notna() & ~failed_is_edited].copy()
                        identifier_failed_missing = identifier_failed[failed_vals.isna()].copy()
                        if not identifier_failed_recovered.empty:
                            failed_recovered_customdata = _build_delta_point_customdata(identifier_failed_recovered, 'd18O')
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_failed_recovered['x_axis'],
                                y=pd.to_numeric(identifier_failed_recovered['d 18O/16O  Mean'], errors='coerce'),
                                mode='markers',
                                marker=dict(color='#ff7f0e', symbol='diamond-open', size=12, line=dict(width=2)),
                                name='Partially Failed (Recovered Mean)',
                                customdata=failed_recovered_customdata,
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd18O: %{y:.4f}<extra></extra>'
                                )
                            ))
                        if not identifier_failed_interp.empty:
                            failed_interp_customdata = _build_delta_point_customdata(identifier_failed_interp, 'd18O')
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_failed_interp['x_axis'],
                                y=pd.to_numeric(identifier_failed_interp['d 18O/16O  Mean'], errors='coerce'),
                                mode='markers',
                                marker=dict(color='#ff00ff', symbol='triangle-down', size=10, line=dict(width=1)),
                                name='Failed Samples (Interpolated)',
                                customdata=failed_interp_customdata,
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd18O: %{y:.4f}<extra></extra>'
                                )
                            ))
                        if not identifier_failed_missing.empty:
                            failed_customdata = _build_delta_point_customdata(identifier_failed_missing, 'd18O')
                        y_vals = pd.to_numeric(data_for_identifier['d 18O/16O  Mean'], errors='coerce')
                        y_min = y_vals.min()
                        y_max = y_vals.max()
                        if not np.isfinite(y_min):
                            y_min, y_max = -1.0, 1.0
                        y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                        y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                        if not identifier_failed_missing.empty:
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_failed_missing['x_axis'],
                                y=[y_failed] * len(identifier_failed_missing),
                                mode='markers',
                                marker=dict(color='#7f7f7f', symbol='triangle-down', size=10, line=dict(width=1)),
                                name='Failed Samples (No Values)',
                                customdata=failed_customdata,
                                hovertemplate=(
                                    'Identifier 1: %{customdata[2]}<br>'
                                    'Identifier 2: %{customdata[3]}<br>'
                                    'd18O: missing (click to edit)<extra></extra>'
                                )
                            ))

                fig_d18O.add_trace(go.Scatter(
                    x=sorted_data['x_axis'],
                    y=sorted_data['d 18O/16O  Mean'],
                    mode='lines+markers',
                    line=dict(color='blue', dash='dot', width=2),
                    marker=dict(
                        color=sorted_data[color_param_tab3_value_col],
                        colorscale="Viridis",
                        symbol='circle',
                        size=8,
                        showscale=False  # Hide individual colorbar
                    ),
                    name=f'Raw d18O - {identifier}',
                    customdata=_build_delta_point_customdata(sorted_data, 'd18O'),
                    hovertemplate=(
                        'Identifier 1: %{customdata[2]}<br>'
                        'Identifier 2: %{customdata[3]}<br>'
                        'd18O: %{y:.4f}<extra></extra>'
                    )
                ))
                edited_identifier_d18 = sorted_data[sorted_data.index.map(_is_row_edited)]
                if not edited_identifier_d18.empty:
                    edited_customdata = _build_delta_point_customdata(edited_identifier_d18, 'd18O')
                    fig_d18O.add_trace(go.Scatter(
                        x=edited_identifier_d18['x_axis'],
                        y=edited_identifier_d18['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color='#ff00ff',
                            symbol='circle',
                            size=12,
                            line=dict(width=1, color='#ff00ff')
                        ),
                        name='Edited Samples',
                        customdata=edited_customdata,
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: %{y:.4f}<extra></extra>'
                        )
                    ))

                if d18_has_calibrated_curve and not d18_hide_calibrated and not d18_raw_line_only:
                    fig_d18O.add_trace(go.Scatter(
                        x=sorted_data['x_axis'],
                        y=sorted_data['d18O_calibrated'],
                        mode='lines',
                        line=dict(color='orange', width=2),
                        name=f'Calibrated d18O - {identifier}'
                    ))

                if d18_raw_line_only:
                    fig_d18O = go.Figure()
                    fig_d18O.add_trace(go.Scatter(
                        x=sorted_data['x_axis'],
                        y=sorted_data['d 18O/16O  Mean'],
                        mode='lines',
                        line=dict(color='blue', width=2),
                        name=f'Raw d18O - {identifier}',
                        customdata=_build_delta_point_customdata(sorted_data, 'd18O'),
                        hovertemplate=(
                            'Identifier 1: %{customdata[2]}<br>'
                            'Identifier 2: %{customdata[3]}<br>'
                            'd18O: %{y:.4f}<extra></extra>'
                        )
                    ))

                fig_d18O.update_layout(
                    title=f'{identifier} - d18O for Species: {species}',
                    xaxis_title='X Axis',
                    yaxis_title='d18O (‰)',
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
                # Invert y-axis so increasing d18O plots downward
                fig_d18O.update_yaxes(autorange='reversed')

                fig_d18O.update_layout(clickmode='event+select', dragmode='zoom')
                d18_editor_prefix = f"tab3_d18o_editor_{d18_key_suffix}"
                d18_active_target = _get_active_editor_target(d18_editor_prefix)
                if (
                    not d18_raw_line_only
                    and
                    d18_active_target is not None
                    and d18_active_target.get('isotope_key') == 'd18O'
                    and d18_active_target.get('row_label') in species_data_unfiltered.index
                ):
                    active_row = species_data_unfiltered.loc[d18_active_target['row_label']]
                    if isinstance(active_row, pd.DataFrame):
                        active_row = active_row.iloc[0]
                    x_active = active_row.get('x_axis', np.nan)
                    if pd.notna(x_active):
                        y_active = pd.to_numeric(pd.Series([active_row.get('d 18O/16O  Mean')]), errors='coerce').iloc[0]
                        if pd.notna(y_active):
                            y_active_plot = float(y_active)
                        else:
                            y_vals = pd.to_numeric(data_for_identifier['d 18O/16O  Mean'], errors='coerce')
                            y_min = y_vals.min()
                            y_max = y_vals.max()
                            if not np.isfinite(y_min):
                                y_min, y_max = -1.0, 1.0
                            y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                            y_active_plot = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                        status_val = str(active_row.get('Collector Status', '')).strip()
                        marker_symbol = 'triangle-down' if status_val in ('Failed Sample', 'Fully Saturated Collectors') else 'circle'
                        fig_d18O.add_trace(go.Scatter(
                            x=[x_active],
                            y=[y_active_plot],
                            mode='markers',
                            marker=dict(
                                color='#ff00ff',
                                symbol=marker_symbol,
                                size=14,
                                line=dict(width=2, color='#ff00ff')
                            ),
                            name='Active Selection',
                            showlegend=False,
                            legendgroup='active_selection'
                        ))
                _apply_editor_selection_to_figure(fig_d18O, d18_editor_prefix)
                d18_chart_nonce = int(st.session_state.get(f"{d18_editor_prefix}_chart_nonce", 0))
                d18_chart_col, d18_btn_col = st.columns([12, 1], gap="small")
                with d18_btn_col:
                    d18_btn_label = "Show Full" if d18_raw_line_only else "Raw Line Only"
                    if st.button(d18_btn_label, key=f"{d18_raw_only_key}_btn", use_container_width=True):
                        st.session_state[d18_raw_only_key] = not d18_raw_line_only
                        st.rerun()
                    if d18_has_calibrated_curve:
                        d18_cal_btn_label = "Show calibrated curve" if d18_hide_calibrated else "Hide calibrated curve"
                        if st.button(d18_cal_btn_label, key=f"{d18_hide_cal_key}_btn", use_container_width=True):
                            st.session_state[d18_hide_cal_key] = not d18_hide_calibrated
                            st.rerun()
                with d18_chart_col:
                    d18_chart_state = st.plotly_chart(
                        fig_d18O,
                        width='stretch',
                        height=chart_height,
                        key=f"tab3_d18o_{d18_key_suffix}_{d18_chart_nonce}",
                        on_select='rerun',
                        selection_mode='points'
                    )
                if not d18_raw_line_only:
                    _render_delta_editor_from_chart_selection(d18_chart_state, d18_editor_prefix)

            # Display outliers header for each comment if detected
            if not species_data['Identifier 2'].isna().all():
                st.subheader(f'Outliers Detected for Species: {species}')
            
            # Get outliers data
            stat_outliers_only = statistical_outliers[statistical_outliers[species_col] == species]
            stat_outliers_only = stat_outliers_only[~stat_outliers_only.index.map(_is_row_edited)]
            
            # Get original data for this species before any filtering
            species_data = subset_data_unfiltered[subset_data_unfiltered[species_col] == species]
            
            # Create masks for each range category
            d13c_outliers = species_data[
                (species_data['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (species_data['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
            ]
            d13c_outliers = d13c_outliers[~d13c_outliers.index.map(_is_row_edited)]
            
            d18o_outliers = species_data[
                (species_data['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (species_data['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
            ]
            d18o_outliers = d18o_outliers[~d18o_outliers.index.map(_is_row_edited)]
            
            signal_outliers = species_data[
                (species_data['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (species_data['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
            ]
            signal_outliers = signal_outliers[~signal_outliers.index.map(_is_row_edited)]
            
            leak_outliers = species_data[
                (species_data['leak_rate'] < st.session_state.leak_range[0]) |
                (species_data['leak_rate'] > st.session_state.leak_range[1])
            ]
            leak_outliers = leak_outliers[~leak_outliers.index.map(_is_row_edited)]
        
            # Create two columns for outlier information
            col1, col2 = st.columns(2)

            # Column 1: Isotope Outliers
            with col1:
                st.markdown("### ?? Isotope Outliers")
                st.markdown("---")
                
                # Statistical Outliers
                with st.expander("Statistical Outliers (Sigma-Based)", expanded=True):
                    if not stat_outliers_only.empty:
                        st.markdown("**Based on statistical deviation from the mean**")
                        styled_stats = stat_outliers_only[['Identifier 2', species_col, 'd 13C/12C  Mean', 'd 18O/16O  Mean']].copy()
                        styled_stats = styled_stats.rename(columns={
                            species_col: 'Species',
                            'd 13C/12C  Mean': 'd13C Value (‰)',
                            'd 18O/16O  Mean': 'd18O Value (‰)'
                        })
                        st.dataframe(styled_stats, width='stretch')
                    else:
                        st.info("No statistical outliers detected")

                # Î´13C Outliers
                with st.expander("d13C Range Outliers", expanded=True):
                    if not d13c_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.d13c_range[0]:.2f} to {st.session_state.d13c_range[1]:.2f} ‰")
                        styled_d13c = d13c_outliers[['Identifier 2', species_col, 'd 13C/12C  Mean']].copy()
                        styled_d13c = styled_d13c.rename(columns={
                            species_col: 'Species','d 13C/12C  Mean': 'd13C Value (‰)'})
                        st.dataframe(styled_d13c, width='stretch')
                    else:
                        st.info("No d13C outliers detected")

                # Î´18O Outliers
                with st.expander("d18O Range Outliers", expanded=True):
                    if not d18o_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.d18o_range[0]:.2f} to {st.session_state.d18o_range[1]:.2f} ‰")
                        styled_d18o = d18o_outliers[['Identifier 2', species_col, 'd 18O/16O  Mean']].copy()
                        styled_d18o = styled_d18o.rename(columns={
                            species_col: 'Species','d 18O/16O  Mean': 'd18O Value (‰)'})
                        st.dataframe(styled_d18o, width='stretch')
                    else:
                        st.info("No d18O outliers detected")

            # Column 2: Technical Outliers
            with col2:
                st.markdown("### ?? Technical Outliers")
                st.markdown("---")
                
                # Signal Intensity Outliers
                with st.expander("Signal Intensity Outliers", expanded=True):
                    if not signal_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.signal_range[0]:.2f} to {st.session_state.signal_range[1]:.2f}")
                        styled_signal = signal_outliers[['Identifier 2', species_col, '1  Cycle Int  Samp  44']].copy()
                        styled_signal = styled_signal.rename(columns={
                            species_col: 'Species','1  Cycle Int  Samp  44': 'Signal Intensity'})
                        st.dataframe(styled_signal, width='stretch')
                    else:
                        st.info("No signal intensity outliers detected")
                
                # Leak Rate Outliers
                with st.expander("Leak Rate Outliers", expanded=True):
                    if not leak_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.leak_range[0]:.2f} to {st.session_state.leak_range[1]:.2f}")
                        styled_leak = leak_outliers[['Identifier 2', species_col, 'leak_rate']].copy()
                        styled_leak = styled_leak.rename(columns={
                            species_col: 'Species','leak_rate': 'Leak Rate'})
                        st.dataframe(styled_leak, width='stretch')
                    else:
                        st.info("No leak rate outliers detected")

                # Collector Status (Partial / Full / Failed)
                with st.expander("Collector Status (Partial / Full / Failed)", expanded=True):
                    src_df = species_data_unfiltered if 'species_data_unfiltered' in locals() else species_data
                    status_series = src_df.get('Collector Status', pd.Series(False, index=src_df.index))
                    failed_samples = src_df[status_series == 'Failed Sample'].copy()
                    saturated_samples = src_df[status_series == 'Partially Saturated Collectors'].copy()
                    saturated_all = src_df[status_series == 'Fully Saturated Collectors'].copy()
                    if failed_samples.empty and saturated_samples.empty and saturated_all.empty:
                        st.info("No saturated collectors or failed samples detected")
                    else:
                        if not saturated_samples.empty:
                            st.markdown("**Partially Failed (Recovered Mean)**")
                            cols = ['Identifier 2', species_col, 'd 13C/12C  Mean', 'd 18O/16O  Mean',
                                    'd13C Cycles Excluded', 'd18O Cycles Excluded']
                            cols = [c for c in cols if c in saturated_samples.columns]
                            styled_sat = saturated_samples[cols].copy()
                            styled_sat = styled_sat.rename(columns={species_col: 'Species'})
                            st.dataframe(styled_sat, width='stretch')
                        if not saturated_all.empty:
                            st.markdown("**Failed Samples (Fully Saturated)**")
                            cols = ['Identifier 2', species_col, 'd 13C/12C  Mean', 'd 18O/16O  Mean',
                                    'Cycles Total', 'd13C Cycles Excluded', 'd18O Cycles Excluded']
                            cols = [c for c in cols if c in saturated_all.columns]
                            styled_sat_all = saturated_all[cols].copy()
                            styled_sat_all = styled_sat_all.rename(columns={species_col: 'Species'})
                            st.dataframe(styled_sat_all, width='stretch')
                        if not failed_samples.empty:
                            st.markdown("**Failed Samples (No Values)**")
                            cols = ['Identifier 2', species_col, 'd 13C/12C  Mean', 'd 18O/16O  Mean']
                            cols = [c for c in cols if c in failed_samples.columns]
                            styled_fail = failed_samples[cols].copy()
                            styled_fail = styled_fail.rename(columns={species_col: 'Species'})
                            st.dataframe(styled_fail, width='stretch')

            # with st.expander("Leak Rate Outliers", expanded=True):
            #     if not leak_outliers.empty:
            #         st.markdown(f"Range: {st.session_state.leak_range[0]:.2f} to {st.session_state.leak_range[1]:.2f}")
            #         st.dataframe(leak_outliers[['Identifier 2', species_col, 'leak_rate']])
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


