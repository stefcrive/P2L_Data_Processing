from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from fastapi import UploadFile

from services.irms_api.api import main as api_main
from services.irms_api.session_store import FileSessionStore


def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Identifier 1": ["SampleA", "SampleB"],
            "Identifier 2": ["1", "1"],
            "d 13C/12C  Mean": [1.0, 2.0],
            "d 18O/16O  Mean": [3.0, 4.0],
        }
    )


class SessionLifecycleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = api_main.store
        api_main.store = FileSessionStore(self.temp_dir.name)
        self.session_id = api_main.store.create_session({"source_files": [{"name": "source.xlsx"}]})
        api_main.store.save_frames(self.session_id, sample_df(), pd.DataFrame({"Cycle Number": [1, 2]}))

    def tearDown(self) -> None:
        api_main.store = self.original_store
        self.temp_dir.cleanup()

    def test_open_save_close_and_discard_session(self) -> None:
        listed = api_main.list_sessions(limit=50)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].session_id, self.session_id)

        opened = api_main.open_session(self.session_id)
        self.assertEqual(opened.session_id, self.session_id)
        self.assertTrue(bool(opened.autosave.get("resumed")))

        saved = api_main.save_session(self.session_id)
        self.assertEqual(saved.session_id, self.session_id)
        self.assertEqual(saved.row_count, 2)

        closed = api_main.close_session(self.session_id)
        self.assertEqual(closed.session_id, self.session_id)
        self.assertFalse(bool(closed.autosave.get("resumed")))

        metadata = api_main.store.load_metadata(self.session_id)
        autosave = metadata.get("autosave", {})
        self.assertEqual(int(autosave.get("event_count", 0)), 3)
        self.assertEqual(str(autosave.get("last_action")), "session_closed")
        self.assertIn("snapshot_path", autosave)
        self.assertIn("log_path", autosave)
        self.assertIn("meta_path", autosave)
        self.assertIn("session_state_path", autosave)

        discarded = api_main.discard_session(self.session_id)
        self.assertTrue(bool(discarded.get("deleted")))
        self.assertFalse(api_main.store.session_exists(self.session_id))

    def test_open_session_file_endpoint_registers_manifest(self) -> None:
        api_main.save_session(self.session_id)
        paths = api_main.store._paths(self.session_id)
        manifest_bytes = paths.session_state_path.read_bytes()
        upload = UploadFile(filename="irms_session_state.json", file=io.BytesIO(manifest_bytes))

        payload = asyncio.run(api_main.open_session_file(upload))

        self.assertEqual(payload.session_id, self.session_id)
        self.assertTrue(bool(payload.autosave.get("resumed")))

    def test_open_session_folder_endpoint_restores_uploaded_record(self) -> None:
        api_main.save_session(self.session_id)
        paths = api_main.store._paths(self.session_id)
        uploads = [
            UploadFile(
                filename=f"data/{paths.root.parent.name}/{self.session_id}/metadata.json",
                file=io.BytesIO(paths.metadata_path.read_bytes()),
            ),
            UploadFile(
                filename=f"data/{paths.root.parent.name}/{self.session_id}/snapshot.csv",
                file=io.BytesIO(paths.snapshot_path.read_bytes()),
            ),
            UploadFile(
                filename=f"data/{paths.root.parent.name}/{self.session_id}/cycles_snapshot.csv",
                file=io.BytesIO(paths.cycles_snapshot_path.read_bytes()),
            ),
            UploadFile(
                filename=f"data/{paths.root.parent.name}/{self.session_id}/irms_session_state.json",
                file=io.BytesIO(paths.session_state_path.read_bytes()),
            ),
        ]
        api_main.store = FileSessionStore(Path(self.temp_dir.name) / "portable_store")

        payload = asyncio.run(api_main.open_session_folder(uploads))

        self.assertEqual(payload.session_id, self.session_id)
        self.assertEqual(payload.row_count, 2)
        self.assertTrue(api_main.store.session_exists(self.session_id))


if __name__ == "__main__":
    unittest.main()
