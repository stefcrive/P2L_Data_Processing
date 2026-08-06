from __future__ import annotations

import threading
import time
import unittest

from services.irms_api.jobs import JobQueueFullError, JobRegistry


def wait_for_terminal(registry: JobRegistry, job_id: str, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = registry.get(job_id)
        if snapshot.state in {"succeeded", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish before timeout")


class JobRegistryTests(unittest.TestCase):
    def test_success_reports_progress_and_result(self) -> None:
        registry = JobRegistry(max_workers=1, max_queue=1, max_history=5)
        try:
            def runner(context):
                context.report(45, "working", "Halfway")
                return {"answer": 42}

            submitted = registry.submit("test", runner)
            completed = wait_for_terminal(registry, submitted.job_id)

            self.assertEqual(completed.state, "succeeded")
            self.assertEqual(completed.progress, 100.0)
            self.assertEqual(completed.result, {"answer": 42})
            self.assertFalse(completed.cancellable)
            self.assertGreater(completed.revision, submitted.revision)
        finally:
            registry.shutdown()

    def test_worker_failure_is_captured(self) -> None:
        registry = JobRegistry(max_workers=1, max_queue=0)
        try:
            def runner(_context):
                raise ValueError("broken job")

            submitted = registry.submit("test", runner)
            completed = wait_for_terminal(registry, submitted.job_id)

            self.assertEqual(completed.state, "failed")
            self.assertEqual(completed.error, "broken job")
        finally:
            registry.shutdown()

    def test_queued_job_can_be_cancelled_before_runner_starts(self) -> None:
        registry = JobRegistry(max_workers=1, max_queue=1)
        release_first = threading.Event()
        second_ran = threading.Event()
        try:
            def block_first(_context):
                release_first.wait(2.0)
                return {}

            first = registry.submit("blocking", block_first)
            deadline = time.monotonic() + 2.0
            while registry.get(first.job_id).state == "queued" and time.monotonic() < deadline:
                time.sleep(0.01)

            second = registry.submit("queued", lambda _context: second_ran.set() or {})
            cancelled = registry.cancel(second.job_id)
            self.assertEqual(cancelled.state, "cancel_requested")
            release_first.set()

            completed = wait_for_terminal(registry, second.job_id)
            self.assertEqual(completed.state, "cancelled")
            self.assertFalse(second_ran.is_set())
        finally:
            release_first.set()
            registry.shutdown()

    def test_queue_capacity_is_bounded(self) -> None:
        registry = JobRegistry(max_workers=1, max_queue=0)
        release = threading.Event()
        try:
            def block(_context):
                release.wait(2.0)
                return {}

            registry.submit("blocking", block)
            with self.assertRaises(JobQueueFullError):
                registry.submit("overflow", lambda _context: {})
        finally:
            release.set()
            registry.shutdown()

    def test_artifact_is_retained_with_completed_job(self) -> None:
        registry = JobRegistry(max_workers=1, max_queue=0)
        try:
            def runner(context):
                context.begin_commit(90, "finalizing", "Finalizing")
                context.set_artifact(b"workbook", "output.xlsx", "application/test")
                return {"filename": "output.xlsx"}

            submitted = registry.submit("export", runner)
            completed = wait_for_terminal(registry, submitted.job_id)
            artifact = registry.get_artifact(completed.job_id)

            self.assertEqual(completed.state, "succeeded")
            self.assertIsNotNone(artifact)
            self.assertEqual(artifact.path.read_bytes(), b"workbook")
            self.assertEqual(artifact.filename, "output.xlsx")
        finally:
            registry.shutdown()


if __name__ == "__main__":
    unittest.main()
