from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from services.irms_api.api import main as api_main
from services.irms_api.domain.contracts import ScientificChatRequest
from services.irms_api.runtime_secrets import clear_runtime_openai_api_key, get_openai_api_key
from services.irms_api.scientific_chat_assistant import ScientificDataTools, run_scientific_chat
from services.irms_api.session_store import FileSessionStore


class _FunctionCall:
    type = "function_call"

    def __init__(self, name: str, arguments: dict[str, object]) -> None:
        self.name = name
        self.arguments = json.dumps(arguments)
        self.call_id = "call_1"

    def model_dump(self, **_kwargs):
        return {
            "type": self.type,
            "name": self.name,
            "arguments": self.arguments,
            "call_id": self.call_id,
        }


class _FakeResponses:
    def __init__(self, session_id: str) -> None:
        self.requests: list[dict[str, object]] = []
        self.session_id = session_id

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            return SimpleNamespace(
                output=[_FunctionCall("summarize_session_data", {
                    "session_id": self.session_id,
                    "dataset": "measurements",
                    "columns": ["d 13C/12C  Mean"],
                    "group_by": None,
                    "search": None,
                    "filter_column": None,
                    "filter_operator": None,
                    "filter_value": None,
                })],
                output_text="",
                model="gpt-test",
                usage=None,
            )
        return SimpleNamespace(
            output=[], output_text="The mean is grounded in the measurement snapshot.", model="gpt-test", usage=None
        )


class ScientificChatAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = FileSessionStore(Path(self.temp_dir.name))
        self.session_id = self.store.create_session(
            {
                "session_id": "session-1",
                "session_name": "Carbonates",
                "source_files": [{"name": "run.xlsx", "size": 120, "md5": "a" * 32}],
                "processing": {"config": {"d13c_range": [-10, 10]}},
            }
        )
        self.store.save_frames(
            self.session_id,
            pd.DataFrame(
                {
                    "Identifier 1": ["STD-1", "SAMPLE-2", "SAMPLE-3"],
                    "d 13C/12C  Mean": [-2.0, -1.0, 3.0],
                    "Status": ["ok", "failed", "ok"],
                    "api_token": ["secret-a", "secret-b", "secret-c"],
                }
            ),
            pd.DataFrame({"Identifier 1": ["STD-1"], "Cycle Number": [1], "Signal 44": [9.2]}),
        )

    def tearDown(self) -> None:
        clear_runtime_openai_api_key()
        self.temp_dir.cleanup()

    def test_query_filters_pages_and_redacts_secret_named_fields(self) -> None:
        result = ScientificDataTools(self.store).query_session_data(
            self.session_id,
            "measurements",
            ["Identifier 1", "d 13C/12C  Mean", "api_token"],
            None,
            "d 13C/12C  Mean",
            "gt",
            "-1.5",
            "d 13C/12C  Mean",
            "desc",
            0,
            10,
        )
        self.assertEqual(result["matched_rows"], 2)
        self.assertEqual(result["rows"][0]["Identifier 1"], "SAMPLE-3")
        # Redaction is applied when evidence is bounded for model/UI exposure.
        from services.irms_api.scientific_chat_assistant import _bounded_result

        bounded, _ = _bounded_result(result, 30_000)
        self.assertEqual(bounded["rows"][0]["api_token"], "[redacted]")
        self.assertIn("record_version", result["source"])

    def test_diagnostics_reports_explicit_failure(self) -> None:
        result = ScientificDataTools(self.store).get_diagnostic_summary(self.session_id, 10)
        self.assertEqual(result["flagged_row_count"], 1)
        self.assertEqual(result["flagged_rows"][0]["Identifier 1"], "SAMPLE-2")

    def test_uploaded_excel_can_be_inspected_and_compared_to_platform_data(self) -> None:
        workbook = io.BytesIO()
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            pd.DataFrame(
                {
                    "Sample ID": ["STD-1", "SAMPLE-2", "SAMPLE-3", "UPLOAD-ONLY"],
                    "Carbon": [-2.0, -1.02, 4.0, 8.0],
                }
            ).to_excel(writer, sheet_name="Results", index=False)

        tools = ScientificDataTools(
            self.store, [("client-results.xlsx", workbook.getvalue())]
        )
        context = tools.get_uploaded_workbook_context()
        self.assertEqual(context["workbooks"][0]["sheets"][0]["columns"], ["Sample ID", "Carbon"])

        result = tools.compare_session_to_uploaded_data(
            session_id=self.session_id,
            dataset="measurements",
            file_name="client-results.xlsx",
            sheet_name="Results",
            platform_key="Identifier 1",
            uploaded_key="Sample ID",
            column_pairs=[
                {"platform_column": "d 13C/12C  Mean", "uploaded_column": "Carbon"}
            ],
            numeric_tolerance=0.05,
            case_sensitive=False,
        )
        self.assertEqual(result["matched_rows"], 3)
        self.assertEqual(result["uploaded_only_rows"], 1)
        self.assertEqual(result["column_comparisons"][0]["equal_or_within_tolerance"], 2)
        self.assertEqual(result["column_comparisons"][0]["different"], 1)
        self.assertEqual(result["discrepancies"][0]["key"], "sample-3")

    def test_chat_file_endpoint_rejects_non_excel_attachments(self) -> None:
        client = TestClient(api_main.app)
        response = client.post(
            "/chat/scientific-assistant-with-files",
            data={"message": "Compare this", "history": "[]"},
            files={"files": ("notes.csv", b"a,b\n1,2", "text/csv")},
        )
        self.assertEqual(response.status_code, 415)
        self.assertIn(".xls or .xlsx", response.json()["detail"])

    def test_chat_file_endpoint_passes_excel_bytes_to_agent(self) -> None:
        workbook = io.BytesIO()
        pd.DataFrame({"Sample": ["A"], "Value": [1.0]}).to_excel(
            workbook, index=False, engine="openpyxl"
        )
        agent_response = {
            "message": "Compared the workbook.",
            "model": "test-model",
            "tools_used": [],
            "usage": {},
            "generated_at": datetime.now(timezone.utc),
            "read_only": True,
            "processing_environment": {},
            "tool_activity": [],
            "reasoning_summary": None,
        }
        client = TestClient(api_main.app)
        with patch.object(
            api_main, "run_scientific_chat", return_value=agent_response
        ) as run_chat:
            response = client.post(
                "/chat/scientific-assistant-with-files",
                data={"message": "Compare this", "history": "[]"},
                files={
                    "files": (
                        "comparison.xlsx",
                        workbook.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
        self.assertEqual(response.status_code, 200)
        uploaded = run_chat.call_args.args[3]
        self.assertEqual(uploaded[0][0], "comparison.xlsx")
        self.assertEqual(uploaded[0][1], workbook.getvalue())

    def test_agent_executes_tool_and_returns_same_evidence_for_ui(self) -> None:
        responses = _FakeResponses(self.session_id)
        request = ScientificChatRequest(
            message="What is the mean carbon isotope value?",
            history=[],
            current_session_id=self.session_id,
        )
        result = run_scientific_chat(request, self.store, SimpleNamespace(responses=responses))
        self.assertEqual(result["model"], "gpt-test")
        self.assertEqual(result["tools_used"], ["summarize_session_data"])
        self.assertEqual(result["tool_activity"][0]["status"], "completed")
        self.assertEqual(len(responses.requests), 2)
        self.assertFalse(responses.requests[0]["parallel_tool_calls"])
        function_output = responses.requests[1]["input"][-1]
        self.assertEqual(json.loads(function_output["output"]), result["tool_activity"][0]["result"])

    def test_api_key_setting_is_backend_only_and_never_returned(self) -> None:
        replacement = "sk-test-" + "x" * 48
        client = TestClient(api_main.app)
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False),
            patch(
                "services.irms_api.runtime_secrets._write_windows_user_openai_api_key"
            ) as write_user_key,
            patch(
                "services.irms_api.runtime_secrets._delete_windows_user_openai_api_key"
            ) as delete_user_key,
            patch(
                "services.irms_api.runtime_secrets._read_windows_user_openai_api_key",
                return_value=None,
            ) as read_user_key,
        ):
            response = client.put("/settings/openai-api-key", json={"api_key": replacement})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"configured": True, "source": "user_environment"})
            self.assertNotIn(replacement, response.text)
            self.assertEqual(get_openai_api_key(), replacement)
            write_user_key.assert_called_once_with(replacement)

            status = client.get("/settings/openai-api-key")
            self.assertEqual(status.json(), {"configured": True, "source": "user_environment"})
            self.assertNotIn(replacement, status.text)

            clear_runtime_openai_api_key()
            read_user_key.return_value = replacement
            restarted_status = client.get("/settings/openai-api-key")
            self.assertEqual(
                restarted_status.json(),
                {"configured": True, "source": "user_environment"},
            )
            self.assertEqual(get_openai_api_key(), replacement)

            delete_user_key.side_effect = lambda: setattr(read_user_key, "return_value", None)
            cleared = client.delete("/settings/openai-api-key")
            self.assertEqual(cleared.json(), {"configured": False, "source": "not_configured"})
            delete_user_key.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
