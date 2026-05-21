from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_load_frame_uses_isolated_cached_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileSessionStore(temp_dir)
            session_id = store.create_session()
            store.save_frames(session_id, pd.DataFrame({"a": [1, 2, 3]}))

            first = store.load_frame(session_id)
            first.loc[0, "a"] = 99
            second = store.load_frame(session_id)

            self.assertEqual(second.loc[0, "a"], 1)

    def test_load_frame_refreshes_cache_when_snapshot_changes_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileSessionStore(temp_dir)
            session_id = store.create_session()
            store.save_frames(session_id, pd.DataFrame({"a": [1, 2, 3]}))
            self.assertEqual(len(store.load_frame(session_id)), 3)

            paths = store._paths(session_id)
            paths.snapshot_path.write_text("a\n10\n20\n30\n40\n", encoding="utf-8")
            loaded = store.load_frame(session_id)

            self.assertEqual(loaded["a"].tolist(), [10, 20, 30, 40])

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

    def test_create_session_prefers_source_folder_session_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "workbooks"
            source_root.mkdir(parents=True, exist_ok=True)
            workbook_path = source_root / "source.xlsx"
            workbook_bytes = b"dummy workbook bytes for source matching"
            workbook_path.write_bytes(workbook_bytes)
            source_spec = {
                "name": workbook_path.name,
                "size": len(workbook_bytes),
                "md5": hashlib.md5(workbook_bytes).hexdigest().lower(),
            }
            with patch.dict(os.environ, {"IRMS_SOURCE_SEARCH_ROOTS": str(source_root)}):
                store = FileSessionStore(Path(temp_dir) / "store")
                session_id = store.create_session({"source_files": [source_spec]})

            paths = store._paths(session_id)
            expected_root = (source_root / "Session record" / session_id).resolve()
            self.assertEqual(paths.root, expected_root)
            self.assertTrue(paths.metadata_path.exists())

            index_path = Path(temp_dir) / "store" / "_session_index.json"
            self.assertTrue(index_path.exists())
            index_payload = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(index_payload.get(session_id), str(expected_root))

    def test_source_search_roots_require_explicit_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"IRMS_SOURCE_SEARCH_ROOTS": ""}):
                store = FileSessionStore(temp_dir)
                self.assertEqual(store._iter_source_search_roots(), [])


if __name__ == "__main__":
    unittest.main()
