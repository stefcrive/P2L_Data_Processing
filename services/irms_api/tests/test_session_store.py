from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from services.irms_api.session_store import FileSessionStore


class SessionStoreTests(unittest.TestCase):
    def test_create_and_snapshot_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileSessionStore(temp_dir)
            session_id = store.create_session({"source_files": [{"name": "example.xlsx"}]})
            store.save_frames(session_id, pd.DataFrame({"a": [1, 2, 3]}))
            snapshot = store.build_snapshot(session_id)
            self.assertEqual(snapshot["session_id"], session_id)
            self.assertEqual(snapshot["row_count"], 3)
            self.assertEqual(snapshot["source_files"][0]["name"], "example.xlsx")

    def test_load_cycles_frame_handles_empty_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileSessionStore(temp_dir)
            session_id = store.create_session()
            paths = store._paths(session_id)
            paths.cycles_snapshot_path.write_text("", encoding="utf-8")
            loaded = store.load_cycles_frame(session_id)
            self.assertIsInstance(loaded, pd.DataFrame)
            self.assertTrue(loaded.empty)

    def test_list_sessions_returns_recent_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileSessionStore(temp_dir)
            session_id = store.create_session({"source_files": [{"name": "example.xlsx"}]})
            metadata = store.load_metadata(session_id)
            metadata["row_count"] = 3
            metadata["cycles_row_count"] = 2
            store.write_metadata(session_id, metadata)
            store.save_frames(session_id, pd.DataFrame({"a": [1, 2, 3]}), pd.DataFrame({"b": [1, 2]}))

            sessions = store.list_sessions(limit=10)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["session_id"], session_id)
            self.assertEqual(int(sessions[0]["row_count"]), 3)
            self.assertEqual(int(sessions[0]["cycles_row_count"]), 2)

    def test_delete_session_removes_root_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileSessionStore(temp_dir)
            session_id = store.create_session()
            root = Path(temp_dir) / session_id
            self.assertTrue(root.exists())
            self.assertTrue(store.delete_session(session_id))
            self.assertFalse(root.exists())
            self.assertFalse(store.delete_session(session_id))


if __name__ == "__main__":
    unittest.main()
