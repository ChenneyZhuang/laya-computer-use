"""MCP protocol tests: version negotiation, tool listing, and the error paths.

No checkpoint and no live app: the decision path is exercised through a fake backend
by monkeypatching the driver boundary. These run in CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from laya_computer_use import mcp_server


def call(**request):
    return mcp_server.handle_request(request)


class TestLifecycle:
    def test_initialize_echoes_a_supported_client_version(self):
        response = call(id=1, method="initialize",
                        params={"protocolVersion": "2025-06-18"})
        assert response["result"]["protocolVersion"] == "2025-06-18"

    def test_initialize_still_supports_the_older_version(self):
        response = call(id=1, method="initialize",
                        params={"protocolVersion": "2024-11-05"})
        assert response["result"]["protocolVersion"] == "2024-11-05"

    def test_initialize_falls_back_to_our_version_for_an_unknown_one(self):
        """Spec: reply with a version the server supports when the client's is unknown."""
        response = call(id=1, method="initialize",
                        params={"protocolVersion": "1999-01-01"})
        assert response["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION

    def test_server_info_is_present_and_named(self):
        response = call(id=1, method="initialize", params={"protocolVersion": "2025-06-18"})
        assert response["result"]["serverInfo"]["name"] == "laya-computer-use"

    def test_initialized_notification_produces_no_response(self):
        assert mcp_server.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    def test_ping_answers_with_an_empty_result(self):
        assert call(id=9, method="ping")["result"] == {}


class TestToolListing:
    def test_all_three_tools_are_listed(self):
        response = call(id=2, method="tools/list")
        names = {tool["name"] for tool in response["result"]["tools"]}
        assert names == {"list_windows", "desktop_observe", "desktop_decide"}

    def test_desktop_decide_declares_its_required_arguments(self):
        response = call(id=2, method="tools/list")
        tool = next(t for t in response["result"]["tools"] if t["name"] == "desktop_decide")
        assert set(tool["inputSchema"]["required"]) == {"pid", "window_id", "goal"}

    def test_execute_defaults_to_dry_run(self):
        """A tool that can click for you must not do so unless the caller says so."""
        response = call(id=2, method="tools/list")
        tool = next(t for t in response["result"]["tools"] if t["name"] == "desktop_decide")
        assert tool["inputSchema"]["properties"]["execute"]["default"] is False


class TestErrorPaths:
    def test_unknown_method_is_a_jsonrpc_error(self):
        response = call(id=3, method="does/not/exist")
        assert response["error"]["code"] == -32601

    def test_unknown_tool_is_an_error(self):
        response = call(id=4, method="tools/call",
                        params={"name": "no_such_tool", "arguments": {}})
        assert response["error"]["code"] == -32602

    def test_missing_argument_is_an_error_naming_it(self):
        response = call(id=5, method="tools/call",
                        params={"name": "desktop_decide", "arguments": {"pid": 1}})
        assert response["error"]["code"] == -32602
        assert "window_id" in response["error"]["message"]

    def test_driver_exception_becomes_a_tool_error_not_a_crash(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("window_id_not_found")

        monkeypatch.setattr(mcp_server, "_list_windows", boom)
        response = call(id=6, method="tools/call",
                        params={"name": "list_windows", "arguments": {}})
        assert response["result"]["isError"] is True
        assert "window_id_not_found" in response["result"]["content"][0]["text"]


class TestToolBehaviour:
    def test_list_windows_filters_off_screen_windows(self, monkeypatch):
        def fake_driver(tool, payload):
            return {"windows": [
                {"pid": 1, "window_id": 11, "app_name": "App", "title": "On", "is_on_screen": True, "layer": 0},
                {"pid": 1, "window_id": 12, "app_name": "App", "title": "Off", "is_on_screen": False, "layer": 0},
            ]}

        monkeypatch.setattr(mcp_server, "_driver_call", fake_driver)
        response = call(id=7, method="tools/call",
                        params={"name": "list_windows", "arguments": {"on_screen_only": True}})
        payload = json.loads(response["result"]["content"][0]["text"])
        assert [w["window_id"] for w in payload["windows"]] == [11]

    def test_desktop_observe_reports_the_numbered_table(self, monkeypatch):
        observation = {
            "title": "Calculator",
            "actions": [
                {"label": "Equals", "kind": "click", "role": "button", "disabled": False},
                {"label": "Display", "kind": "fill", "role": "textbox", "disabled": False},
            ],
        }

        class FakeObserver:
            def __init__(self, **kwargs):
                pass

            def observe(self):
                return observation

        monkeypatch.setattr("laya_computer_use.ax.AXObserver", FakeObserver)
        response = call(id=8, method="tools/call",
                        params={"name": "desktop_observe",
                                "arguments": {"pid": 1, "window_id": 2}})
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["title"] == "Calculator"
        assert [row["n"] for row in payload["table"]] == [1, 2]
        assert payload["table"][0]["label"] == "Equals"
