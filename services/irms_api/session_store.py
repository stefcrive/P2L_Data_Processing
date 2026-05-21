from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .domain.constants import DEFAULT_SESSION_DATA_DIR, SESSION_RECORD_DIRNAME


@dataclass(slots=True)
class SessionPaths:
    root: Path
    uploads_dir: Path
    metadata_path: Path
    snapshot_path: Path
    cycles_snapshot_path: Path
    log_path: Path


@dataclass(slots=True)
class _CachedFrame:
    mtime_ns: int
    size: int
    frame: pd.DataFrame


class FileSessionStore:
    def __init__(self, root_dir: str | Path | None = None) -> None:
        base = Path(root_dir or os.getenv("IRMS_API_DATA_DIR", DEFAULT_SESSION_DATA_DIR))
        self.root_dir = base.resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root_dir / "_session_index.json"
        self._session_roots = self._load_index()
        self._frame_cache: dict[tuple[str, str], _CachedFrame] = {}
        self._cache_lock = threading.RLock()

    def _load_index(self) -> dict[str, str]:
        if not self.index_path.exists():
            return {}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        roots: dict[str, str] = {}
        for raw_session_id, raw_root in payload.items():
            session_id = str(raw_session_id).strip()
            if session_id == "" or not isinstance(raw_root, str) or raw_root.strip() == "":
                continue
            try:
                roots[session_id] = str(Path(raw_root).resolve())
            except Exception:
                continue
        return roots

    def _write_index(self) -> None:
        self.index_path.write_text(json.dumps(self._session_roots, indent=2), encoding="utf-8")

    def _register_session_root(self, session_id: str, root: Path) -> None:
        key = str(session_id)
        value = str(root.resolve())
        if self._session_roots.get(key) == value:
            return
        self._session_roots[key] = value
        self._write_index()

    def _unregister_session_root(self, session_id: str) -> None:
        if self._session_roots.pop(str(session_id), None) is not None:
            self._write_index()

    def _paths_for_root(self, root: Path) -> SessionPaths:
        uploads_dir = root / "uploads"
        return SessionPaths(
            root=root,
            uploads_dir=uploads_dir,
            metadata_path=root / "metadata.json",
            snapshot_path=root / "snapshot.csv",
            cycles_snapshot_path=root / "cycles_snapshot.csv",
            log_path=root / "events.jsonl",
        )

    def _session_root(self, session_id: str) -> Path:
        mapped = self._session_roots.get(str(session_id))
        if mapped:
            try:
                return Path(mapped).resolve()
            except Exception:
                pass
        return (self.root_dir / session_id).resolve()

    def _normalize_source_file_spec(self, source_item: Any) -> dict[str, Any] | None:
        raw_name = source_item
        raw_size = None
        raw_md5 = None
        if isinstance(source_item, dict):
            raw_name = source_item.get("raw_name") or source_item.get("name")
            raw_size = source_item.get("size")
            raw_md5 = source_item.get("md5")

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
            if len(text) == 32 and all(ch in "0123456789abcdef" for ch in text):
                md5_val = text

        return {
            "raw_name": raw_name_text,
            "name": Path(raw_name_text).name,
            "size": size_val,
            "md5": md5_val,
        }

    def _iter_source_search_roots(self) -> list[Path]:
        roots: list[Path] = []
        seen: set[str] = set()

        def _add(path_obj: Path | None) -> None:
            if path_obj is None:
                return
            try:
                resolved = Path(path_obj).resolve()
            except Exception:
                return
            key = str(resolved).lower()
            if key in seen or not resolved.exists() or not resolved.is_dir():
                return
            seen.add(key)
            roots.append(resolved)

        raw_env = os.getenv("IRMS_SOURCE_SEARCH_ROOTS", "").strip()
        if raw_env:
            for raw in raw_env.split(os.pathsep):
                value = raw.strip()
                if value:
                    _add(Path(value).expanduser())
        return roots

    def _file_md5(self, path_obj: Path, chunk_size: int = 1024 * 1024) -> str:
        digest = hashlib.md5()
        with Path(path_obj).open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _find_upload_matches(
        self,
        *,
        name: str,
        size: int | None = None,
        md5: str | None = None,
        max_matches: int = 40,
    ) -> list[Path]:
        matches: list[Path] = []
        for root in self._iter_source_search_roots():
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
                            if self._file_md5(path).lower() != str(md5).lower():
                                continue
                        except Exception:
                            continue
                    matches.append(path.resolve())
                    if len(matches) >= int(max_matches):
                        return matches
            except Exception:
                continue
        return matches

    def _candidate_source_directories(self, source_item: Any) -> list[Path]:
        spec = self._normalize_source_file_spec(source_item)
        if spec is None:
            return []

        directories: list[Path] = []
        seen: set[str] = set()

        def _add(path_obj: Path | None) -> None:
            if path_obj is None:
                return
            try:
                resolved = Path(path_obj).resolve()
            except Exception:
                return
            key = str(resolved).lower()
            if key in seen:
                return
            seen.add(key)
            directories.append(resolved)

        raw_path = Path(str(spec["raw_name"]))
        if raw_path.is_absolute() and raw_path.exists() and raw_path.is_file():
            _add(raw_path.parent)

        name = str(spec.get("name") or "").strip()
        if name == "":
            return directories
        size = spec.get("size")
        md5 = spec.get("md5")
        if size is None and not md5:
            # Avoid broad recursive scans when we only have a filename.
            return directories

        matches = self._find_upload_matches(name=name, size=size, md5=md5)
        if not matches and size is not None:
            matches = self._find_upload_matches(name=name, size=None, md5=md5)
        if not matches and md5:
            matches = self._find_upload_matches(name=name, size=size, md5=None)
        for match in matches:
            _add(match.parent)
        return directories

    def _resolve_source_directory(self, source_files: list[Any] | None) -> Path | None:
        directories: list[Path] = []
        for item in source_files or []:
            directories.extend(self._candidate_source_directories(item))
        if len(directories) == 0:
            return None

        counts: dict[str, int] = {}
        for directory in directories:
            key = str(directory.resolve())
            counts[key] = counts.get(key, 0) + 1
        cwd_key = str(Path.cwd().resolve()).lower()
        best = max(
            counts.items(),
            key=lambda item: (item[1], 0 if str(item[0]).lower() == cwd_key else 1),
        )[0]
        return Path(best)

    def _resolve_session_root(self, session_id: str, metadata: dict[str, Any]) -> Path:
        source_directory = self._resolve_source_directory(metadata.get("source_files", []))
        if source_directory is None:
            return (self.root_dir / session_id).resolve()
        return (source_directory / SESSION_RECORD_DIRNAME / session_id).resolve()

    def _paths(self, session_id: str) -> SessionPaths:
        return self._paths_for_root(self._session_root(session_id))

    def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        session_id = uuid.uuid4().hex
        payload = {
            "session_id": session_id,
            "session_name": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source_files": [],
            "errors": [],
            "calibration": {},
            "processing": {},
            "autosave": {},
            "edit_state": {
                "edited_rows": [],
                "original_delta_values": {},
                "original_missing_delta_tokens": [],
                "original_std_values": {},
                "original_missing_std_tokens": [],
                "manual_outlier_overrides": {},
                "restored_delta_tokens": [],
            },
        }
        if metadata:
            payload.update(metadata)

        session_root = self._resolve_session_root(session_id, payload)
        self._register_session_root(session_id, session_root)
        paths = self._paths(session_id)
        paths.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.write_metadata(session_id, payload)
        if not paths.log_path.exists():
            paths.log_path.write_text("", encoding="utf-8")
        return session_id

    def session_exists(self, session_id: str) -> bool:
        return self._paths(session_id).metadata_path.exists()

    def write_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        paths = self._paths(session_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        payload = dict(metadata)
        payload["session_id"] = session_id
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        paths.metadata_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def load_metadata(self, session_id: str) -> dict[str, Any]:
        paths = self._paths(session_id)
        if not paths.metadata_path.exists():
            raise FileNotFoundError(f"Unknown session {session_id}")
        return json.loads(paths.metadata_path.read_text(encoding="utf-8"))

    def append_log(self, session_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
        paths = self._paths(session_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "payload": payload or {},
        }
        with paths.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")

    def save_upload(self, session_id: str, filename: str, content: bytes) -> Path:
        paths = self._paths(session_id)
        paths.uploads_dir.mkdir(parents=True, exist_ok=True)
        normalized = str(filename or "").replace("\\", "/").strip()
        parts = [part for part in Path(normalized).parts if part not in ("", ".", "..") and not part.endswith(":")]
        target = paths.uploads_dir / Path(*parts) if parts else paths.uploads_dir / "upload.xlsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def save_frames(
        self,
        session_id: str,
        df: pd.DataFrame,
        cycles_df: pd.DataFrame | None = None,
    ) -> None:
        paths = self._paths(session_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        df.to_csv(paths.snapshot_path, index=False)
        self._refresh_cached_csv_frame(session_id, "snapshot", paths.snapshot_path)
        if cycles_df is not None:
            cycles_df.to_csv(paths.cycles_snapshot_path, index=False)
            self._refresh_cached_csv_frame(session_id, "cycles", paths.cycles_snapshot_path)

    def _refresh_cached_csv_frame(self, session_id: str, frame_key: str, path: Path) -> None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            with self._cache_lock:
                self._frame_cache.pop((str(session_id), frame_key), None)
            return
        try:
            frame = pd.read_csv(path, low_memory=False)
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        cached = _CachedFrame(
            mtime_ns=int(stat.st_mtime_ns),
            size=int(stat.st_size),
            frame=frame,
        )
        with self._cache_lock:
            self._frame_cache[(str(session_id), frame_key)] = cached

    def _load_cached_csv_frame(self, session_id: str, frame_key: str, path: Path) -> pd.DataFrame:
        if not path.exists():
            with self._cache_lock:
                self._frame_cache.pop((str(session_id), frame_key), None)
            raise FileNotFoundError(f"Session {session_id} has no snapshot")

        stat = path.stat()
        cache_key = (str(session_id), frame_key)
        with self._cache_lock:
            cached = self._frame_cache.get(cache_key)
            if (
                cached is not None
                and cached.mtime_ns == int(stat.st_mtime_ns)
                and cached.size == int(stat.st_size)
            ):
                return cached.frame.copy(deep=True)

        frame = pd.read_csv(path, low_memory=False)
        with self._cache_lock:
            self._frame_cache[cache_key] = _CachedFrame(
                mtime_ns=int(stat.st_mtime_ns),
                size=int(stat.st_size),
                frame=frame.copy(deep=True),
            )
        return frame

    def load_frame(self, session_id: str) -> pd.DataFrame:
        paths = self._paths(session_id)
        return self._load_cached_csv_frame(session_id, "snapshot", paths.snapshot_path)

    def load_cycles_frame(self, session_id: str) -> pd.DataFrame | None:
        paths = self._paths(session_id)
        if not paths.cycles_snapshot_path.exists():
            with self._cache_lock:
                self._frame_cache.pop((str(session_id), "cycles"), None)
            return None
        try:
            return self._load_cached_csv_frame(session_id, "cycles", paths.cycles_snapshot_path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()

    def build_snapshot(self, session_id: str, preview_rows: int = 25) -> dict[str, Any]:
        metadata = self.load_metadata(session_id)
        df = self.load_frame(session_id)
        cycles_df = self.load_cycles_frame(session_id)
        preview_frame = df.head(preview_rows).replace({pd.NA: None})
        preview_frame = preview_frame.where(pd.notnull(preview_frame), None)
        metadata["row_count"] = int(len(df))
        metadata["cycles_row_count"] = int(len(cycles_df)) if cycles_df is not None else 0
        metadata["preview"] = preview_frame.to_dict(orient="records")
        return metadata

    def _csv_row_count(self, path: Path) -> int:
        if not path.exists():
            return 0
        try:
            frame = pd.read_csv(path, low_memory=False)
        except pd.errors.EmptyDataError:
            return 0
        return int(len(frame))

    def list_sessions(self, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.root_dir.exists():
            return []
        candidate_ids: set[str] = set(self._session_roots.keys())
        for candidate in self.root_dir.iterdir():
            if candidate.is_dir():
                candidate_ids.add(candidate.name)

        snapshots: list[dict[str, Any]] = []
        seen_session_ids: set[str] = set()
        for session_id in candidate_ids:
            paths = self._paths(session_id)
            if not paths.metadata_path.exists():
                continue
            try:
                metadata = json.loads(paths.metadata_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            actual_session_id = str(metadata.get("session_id") or session_id)
            if actual_session_id in seen_session_ids:
                continue
            seen_session_ids.add(actual_session_id)
            self._register_session_root(actual_session_id, paths.root)
            metadata.setdefault("source_files", [])
            metadata.setdefault("session_name", None)
            metadata.setdefault("errors", [])
            metadata.setdefault("calibration", {})
            metadata.setdefault("processing", {})
            metadata.setdefault("autosave", {})
            metadata.setdefault("preview", [])
            metadata["session_id"] = actual_session_id
            if "row_count" not in metadata:
                metadata["row_count"] = self._csv_row_count(paths.snapshot_path)
            if "cycles_row_count" not in metadata:
                metadata["cycles_row_count"] = self._csv_row_count(paths.cycles_snapshot_path)
            snapshots.append(metadata)
        snapshots.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        if limit is not None and int(limit) >= 0:
            return snapshots[: int(limit)]
        return snapshots

    def delete_session(self, session_id: str) -> bool:
        paths = self._paths(session_id)
        if not paths.root.exists():
            self._unregister_session_root(session_id)
            self._clear_session_frame_cache(session_id)
            return False
        shutil.rmtree(paths.root)
        self._unregister_session_root(session_id)
        self._clear_session_frame_cache(session_id)
        if paths.root.parent.name == SESSION_RECORD_DIRNAME:
            try:
                if not any(paths.root.parent.iterdir()):
                    paths.root.parent.rmdir()
            except Exception:
                pass
        return True

    def _clear_session_frame_cache(self, session_id: str) -> None:
        session_key = str(session_id)
        with self._cache_lock:
            for cache_key in list(self._frame_cache):
                if cache_key[0] == session_key:
                    self._frame_cache.pop(cache_key, None)
