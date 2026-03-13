from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .domain.constants import DEFAULT_SESSION_DATA_DIR


@dataclass(slots=True)
class SessionPaths:
    root: Path
    uploads_dir: Path
    metadata_path: Path
    snapshot_path: Path
    cycles_snapshot_path: Path
    log_path: Path


class FileSessionStore:
    def __init__(self, root_dir: str | Path | None = None) -> None:
        base = Path(root_dir or os.getenv("IRMS_API_DATA_DIR", DEFAULT_SESSION_DATA_DIR))
        self.root_dir = base.resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, session_id: str) -> SessionPaths:
        root = self.root_dir / session_id
        uploads_dir = root / "uploads"
        return SessionPaths(
            root=root,
            uploads_dir=uploads_dir,
            metadata_path=root / "metadata.json",
            snapshot_path=root / "snapshot.csv",
            cycles_snapshot_path=root / "cycles_snapshot.csv",
            log_path=root / "events.jsonl",
        )

    def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        session_id = uuid.uuid4().hex
        paths = self._paths(session_id)
        paths.uploads_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session_id,
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
                "manual_outlier_overrides": {},
            },
        }
        if metadata:
            payload.update(metadata)
        self.write_metadata(session_id, payload)
        if not paths.log_path.exists():
            paths.log_path.write_text("", encoding="utf-8")
        return session_id

    def session_exists(self, session_id: str) -> bool:
        return self._paths(session_id).root.exists()

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
        target = paths.uploads_dir / filename
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
        if cycles_df is not None:
            cycles_df.to_csv(paths.cycles_snapshot_path, index=False)

    def load_frame(self, session_id: str) -> pd.DataFrame:
        paths = self._paths(session_id)
        if not paths.snapshot_path.exists():
            raise FileNotFoundError(f"Session {session_id} has no snapshot")
        return pd.read_csv(paths.snapshot_path, low_memory=False)

    def load_cycles_frame(self, session_id: str) -> pd.DataFrame | None:
        paths = self._paths(session_id)
        if not paths.cycles_snapshot_path.exists():
            return None
        try:
            return pd.read_csv(paths.cycles_snapshot_path, low_memory=False)
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
        snapshots: list[dict[str, Any]] = []
        for candidate in self.root_dir.iterdir():
            if not candidate.is_dir():
                continue
            session_id = candidate.name
            paths = self._paths(session_id)
            if not paths.metadata_path.exists():
                continue
            try:
                metadata = self.load_metadata(session_id)
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            metadata.setdefault("source_files", [])
            metadata.setdefault("errors", [])
            metadata.setdefault("calibration", {})
            metadata.setdefault("processing", {})
            metadata.setdefault("autosave", {})
            metadata.setdefault("preview", [])
            metadata["session_id"] = session_id
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
            return False
        shutil.rmtree(paths.root)
        return True
