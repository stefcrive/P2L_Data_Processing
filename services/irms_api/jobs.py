from __future__ import annotations

import atexit
import json
import os
import shutil
import threading
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from .domain.contracts import JobSnapshot


TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled"}


class JobQueueFullError(RuntimeError):
    pass


class JobCancelledError(RuntimeError):
    pass


@dataclass(slots=True)
class JobArtifact:
    path: Path
    filename: str
    media_type: str


@dataclass(slots=True)
class _JobRecord:
    job_id: str
    kind: str
    state: str
    progress: float
    phase: str
    message: str
    session_id: str | None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    revision: int = 0
    cancellable: bool = True
    cancel_event: threading.Event = field(default_factory=threading.Event)
    artifact: JobArtifact | None = None


JobRunner = Callable[["JobContext"], dict[str, Any] | None]


class JobContext:
    def __init__(self, registry: "JobRegistry", job_id: str) -> None:
        self._registry = registry
        self.job_id = job_id

    def report(self, progress: float, phase: str, message: str = "") -> None:
        self._registry.report(self.job_id, progress, phase, message)

    def raise_if_cancelled(self) -> None:
        if self._registry.is_cancel_requested(self.job_id):
            raise JobCancelledError("Job cancellation was requested")

    def set_artifact(self, content: bytes, filename: str, media_type: str) -> None:
        self._registry.set_artifact_content(self.job_id, content, filename, media_type)

    def begin_commit(self, progress: float, phase: str, message: str = "") -> None:
        """Enter a non-cancellable section that may persist externally visible state."""

        self._registry.begin_commit(self.job_id, progress, phase, message)


class JobRegistry:
    """Bounded in-process worker pool with revisioned progress snapshots.

    Jobs for the same session are serialized to protect file-backed session state.
    Completed job metadata and download artifacts are retained for a bounded time.
    """

    def __init__(
        self,
        *,
        max_workers: int | None = None,
        max_queue: int | None = None,
        max_history: int | None = None,
        retention_seconds: int | None = None,
    ) -> None:
        workers = max(1, int(max_workers or os.getenv("IRMS_JOB_WORKERS", "2")))
        queue_size = max(0, int(max_queue if max_queue is not None else os.getenv("IRMS_JOB_QUEUE_SIZE", "24")))
        self.max_history = max(1, int(max_history or os.getenv("IRMS_JOB_HISTORY_SIZE", "100")))
        self.retention = timedelta(
            seconds=max(60, int(retention_seconds or os.getenv("IRMS_JOB_RETENTION_SECONDS", "3600")))
        )
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="irms-job")
        self._capacity = threading.BoundedSemaphore(workers + queue_size)
        self._records: dict[str, _JobRecord] = {}
        self._session_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()
        self._artifact_root = Path(tempfile.mkdtemp(prefix="irms-job-artifacts-"))
        atexit.register(shutil.rmtree, self._artifact_root, ignore_errors=True)

    def submit(self, kind: str, runner: JobRunner, *, session_id: str | None = None) -> JobSnapshot:
        if not self._capacity.acquire(blocking=False):
            raise JobQueueFullError("The background job queue is full; retry shortly")
        now = datetime.now(timezone.utc)
        record = _JobRecord(
            job_id=uuid.uuid4().hex,
            kind=str(kind),
            state="queued",
            progress=0.0,
            phase="queued",
            message="Waiting for a worker",
            session_id=str(session_id) if session_id is not None else None,
            created_at=now,
        )
        with self._lock:
            self._prune_locked(now)
            self._records[record.job_id] = record
            snapshot = self._snapshot_locked(record)
        try:
            self._executor.submit(self._run, record.job_id, runner)
        except Exception:
            with self._lock:
                self._records.pop(record.job_id, None)
            self._capacity.release()
            raise
        return snapshot

    def _run(self, job_id: str, runner: JobRunner) -> None:
        try:
            record = self._record(job_id)
            session_lock = self._session_lock(record.session_id) if record.session_id else None
            if session_lock is not None:
                with self._lock:
                    self._update_locked(record, phase="waiting_for_session", message="Waiting for session access")
                session_lock.acquire()
            try:
                self._start(record)
                context = JobContext(self, job_id)
                context.raise_if_cancelled()
                result = runner(context) or {}
                context.raise_if_cancelled()
                with self._lock:
                    self._update_locked(
                        record,
                        state="succeeded",
                        progress=100.0,
                        phase="complete",
                        message="Completed",
                        result=result,
                        completed_at=datetime.now(timezone.utc),
                    )
            finally:
                if session_lock is not None:
                    session_lock.release()
        except JobCancelledError:
            with self._lock:
                record = self._records.get(job_id)
                if record is not None:
                    self._update_locked(
                        record,
                        state="cancelled",
                        phase="cancelled",
                        message="Cancelled",
                        completed_at=datetime.now(timezone.utc),
                    )
        except Exception as exc:  # pragma: no cover - exact worker failures are runner-dependent
            with self._lock:
                record = self._records.get(job_id)
                if record is not None:
                    self._update_locked(
                        record,
                        state="failed",
                        phase="failed",
                        message="Failed",
                        error=self._format_error(exc),
                        completed_at=datetime.now(timezone.utc),
                    )
        finally:
            self._capacity.release()

    def _start(self, record: _JobRecord) -> None:
        with self._lock:
            if record.cancel_event.is_set():
                raise JobCancelledError("Job was cancelled before it started")
            self._update_locked(
                record,
                state="running",
                progress=max(1.0, record.progress),
                phase="starting",
                message="Starting",
                started_at=datetime.now(timezone.utc),
            )

    def report(self, job_id: str, progress: float, phase: str, message: str = "") -> None:
        with self._lock:
            record = self._record_locked(job_id)
            if record.cancel_event.is_set():
                raise JobCancelledError("Job cancellation was requested")
            if record.state in TERMINAL_JOB_STATES:
                return
            self._update_locked(
                record,
                progress=max(record.progress, min(99.0, max(0.0, float(progress)))),
                phase=str(phase),
                message=str(message),
            )

    def cancel(self, job_id: str) -> JobSnapshot:
        with self._lock:
            record = self._record_locked(job_id)
            if record.state in TERMINAL_JOB_STATES or not record.cancellable:
                return self._snapshot_locked(record)
            record.cancel_event.set()
            self._update_locked(
                record,
                state="cancel_requested",
                phase="cancelling",
                message="Cancellation requested",
            )
            return self._snapshot_locked(record)

    def begin_commit(self, job_id: str, progress: float, phase: str, message: str = "") -> None:
        with self._lock:
            record = self._record_locked(job_id)
            if record.cancel_event.is_set():
                raise JobCancelledError("Job cancellation was requested")
            record.cancellable = False
            self._update_locked(
                record,
                progress=max(record.progress, min(99.0, max(0.0, float(progress)))),
                phase=str(phase),
                message=str(message),
            )

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            return self._record_locked(job_id).cancel_event.is_set()

    def get(self, job_id: str) -> JobSnapshot:
        with self._lock:
            return self._snapshot_locked(self._record_locked(job_id))

    def set_artifact_content(self, job_id: str, content: bytes, filename: str, media_type: str) -> None:
        target = self._artifact_root / f"{job_id}.artifact"
        target.write_bytes(content)
        artifact = JobArtifact(path=target, filename=filename, media_type=media_type)
        with self._lock:
            try:
                record = self._record_locked(job_id)
            except KeyError:
                target.unlink(missing_ok=True)
                raise
            record.artifact = artifact
            record.revision += 1

    def get_artifact(self, job_id: str) -> JobArtifact | None:
        with self._lock:
            return self._record_locked(job_id).artifact

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)
        shutil.rmtree(self._artifact_root, ignore_errors=True)

    def _record(self, job_id: str) -> _JobRecord:
        with self._lock:
            return self._record_locked(job_id)

    def _record_locked(self, job_id: str) -> _JobRecord:
        record = self._records.get(str(job_id))
        if record is None:
            raise KeyError(str(job_id))
        return record

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._lock:
            return self._session_locks.setdefault(str(session_id), threading.Lock())

    def _update_locked(self, record: _JobRecord, **changes: Any) -> None:
        for key, value in changes.items():
            setattr(record, key, value)
        record.revision += 1

    def _snapshot_locked(self, record: _JobRecord) -> JobSnapshot:
        return JobSnapshot(
            job_id=record.job_id,
            kind=record.kind,
            state=record.state,
            progress=record.progress,
            phase=record.phase,
            message=record.message,
            session_id=record.session_id,
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            result=record.result,
            error=record.error,
            revision=record.revision,
            cancellable=record.cancellable and record.state not in TERMINAL_JOB_STATES,
        )

    def _prune_locked(self, now: datetime) -> None:
        terminal = sorted(
            (record for record in self._records.values() if record.state in TERMINAL_JOB_STATES),
            key=lambda record: record.completed_at or record.created_at,
        )
        expired_before = now - self.retention
        remove_ids = {
            record.job_id
            for record in terminal
            if (record.completed_at or record.created_at) < expired_before
        }
        retained_terminal = [record for record in terminal if record.job_id not in remove_ids]
        if len(retained_terminal) > self.max_history:
            remove_ids.update(record.job_id for record in retained_terminal[: -self.max_history])
        for job_id in remove_ids:
            record = self._records.pop(job_id, None)
            if record is not None and record.artifact is not None:
                record.artifact.path.unlink(missing_ok=True)

    @staticmethod
    def _format_error(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, str):
                return detail
            try:
                return json.dumps(detail, default=str)
            except Exception:
                return str(detail)
        return str(exc) or exc.__class__.__name__
