"""Contract tests: the AX adapter, the observation shape, and the driver protocol.

Everything here runs with no checkpoint, no cua-driver and no live app — so it can gate
CI. Live desktop suites are opt-in, because they need Accessibility permission and a
real window (see tests/test_live_desktop.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from laya_computer_use.ax import (
    AXElement, AXObserver, CHROME_SUBTREES, CLICKABLE_ROLES, EDITABLE_ROLES,
    JUNK_LABELS, ROLE_MAP, ax_to_observation,
)


def make(index, role, label="", value="", actions=None, parent=None, enabled=None,
         selected=None, token=""):
    return AXElement(
        element_index=index, role=role, label=label, value=value,
        actions=list(actions or []), parent_index=parent, enabled=enabled,
        selected=selected, token=token or f"tok{index}",
    )


class TestRoleMapping:
    def test_every_mapped_role_is_lowercase_web_vocabulary(self):
        for ax_role, web_role in ROLE_MAP.items():
            assert ax_role.startswith("AX"), ax_role
            assert web_role == web_role.lower(), web_role
            assert " " not in web_role, web_role

    def test_actionable_role_sets_are_disjoint(self):
        # An element is either edited or clicked; overlapping sets would make the
        # kind inference order-dependent.
        assert not (EDITABLE_ROLES & CLICKABLE_ROLES)


class TestChromeFiltering:
    def test_menu_subtree_is_dropped(self):
        """A real Calculator snapshot carried 121 menu items against 22 buttons."""
        elements = [
            make(0, "AXWindow", "计算器"),
            make(1, "AXMenuBar", "", parent=0),
            make(2, "AXMenuItem", "关于本机", parent=1, actions=["AXPress"]),
            make(3, "AXButton", "等于", parent=0, actions=["AXPress"]),
        ]
        observation = ax_to_observation(elements)
        labels = [item["label"] for item in observation["actions"]]
        assert "关于本机" not in labels
        assert "等于" in labels

    def test_chrome_can_be_kept_on_request(self):
        elements = [
            make(0, "AXWindow", "x"),
            make(1, "AXMenuBar", "", parent=0),
            make(2, "AXMenuItem", "About", parent=1, actions=["AXPress"]),
        ]
        observation = ax_to_observation(elements, include_chrome=True)
        assert [item["label"] for item in observation["actions"]] == ["About"]

    def test_deep_chrome_subtree_is_dropped(self):
        elements = [
            make(0, "AXWindow", "x"),
            make(1, "AXMenuBar", "", parent=0),
            make(2, "AXMenu", "", parent=1),
            make(3, "AXMenu", "", parent=2),
            make(4, "AXMenuItem", "Deep item", parent=3, actions=["AXPress"]),
        ]
        observation = ax_to_observation(elements)
        assert observation["actions"] == []

    def test_chrome_constant_is_the_menu_bar_only(self):
        # Widening this set silently hides user-facing menus (File > Save is a real
        # target). If someone adds a role here, this test forces the conversation.
        assert CHROME_SUBTREES == frozenset({"AXMenuBar", "AXMenu"})


class TestAdaptation:
    def test_text_field_becomes_a_fill_target(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXTextField", "Full name", parent=0)]
        observation = ax_to_observation(elements)
        assert observation["actions"][0]["kind"] == "fill"
        assert observation["actions"][0]["role"] == "textbox"

    def test_button_without_press_action_is_not_actionable(self):
        """A label alone is not a capability: without AXPress there is nothing to do."""
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXButton", "Ghost", parent=0, actions=[])]
        assert ax_to_observation(elements)["actions"] == []

    def test_button_with_press_action_is_clickable(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXButton", "OK", parent=0, actions=["AXPress"])]
        item = ax_to_observation(elements)["actions"][0]
        assert item["kind"] == "click" and item["label"] == "OK"

    def test_popup_button_becomes_a_select(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXPopUpButton", "Country", parent=0, actions=["AXPress"])]
        assert ax_to_observation(elements)["actions"][0]["kind"] == "select"

    def test_junk_framework_labels_are_dropped(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXDisclosureTriangle", "NSOutlineViewDisclosureButtonKey",
                         parent=0, actions=["AXPress"])]
        assert ax_to_observation(elements)["actions"] == []

    def test_unlabeled_container_roles_do_not_appear(self):
        elements = [make(0, "AXWindow", "x"), make(1, "AXGroup", "", parent=0)]
        assert ax_to_observation(elements)["actions"] == []

    def test_value_is_carried_when_it_differs_from_the_label(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXTextField", "Search", value="kettle", parent=0)]
        item = ax_to_observation(elements)["actions"][0]
        assert item["current_value"] == "kettle"

    def test_value_equal_to_label_is_not_duplicated(self):
        """Files in a Finder list carry label == value; repeating it wastes budget."""
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXTextField", "backups", value="backups", parent=0)]
        assert "current_value" not in ax_to_observation(elements)["actions"][0]

    def test_disabled_is_only_set_when_explicitly_false(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXButton", "A", parent=0, actions=["AXPress"]),
                    make(2, "AXButton", "B", parent=0, actions=["AXPress"], enabled=False)]
        items = {item["label"]: item for item in ax_to_observation(elements)["actions"]}
        assert items["A"]["disabled"] is False
        assert items["B"]["disabled"] is True

    def test_element_token_is_the_handle_the_executor_reuses(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXButton", "OK", parent=0, actions=["AXPress"], token="s123:7")]
        assert ax_to_observation(elements)["actions"][0]["node"] == "s123:7"

    def test_selected_state_is_carried(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXRadioButton", "List view", parent=0,
                         actions=["AXPress"], selected=True)]
        assert ax_to_observation(elements)["actions"][0]["selected"] is True


class TestRankingAndTruncation:
    def test_enabled_sort_above_disabled(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXButton", "Disabled", parent=0, actions=["AXPress"], enabled=False),
                    make(2, "AXButton", "Enabled", parent=0, actions=["AXPress"])]
        labels = [item["label"] for item in ax_to_observation(elements)["actions"]]
        assert labels.index("Enabled") < labels.index("Disabled")

    def test_editables_sort_above_clickables(self):
        elements = [make(0, "AXWindow", "x"),
                    make(1, "AXButton", "Button", parent=0, actions=["AXPress"]),
                    make(2, "AXTextField", "Field", parent=0)]
        labels = [item["label"] for item in ax_to_observation(elements)["actions"]]
        assert labels == ["Field", "Button"]

    def test_max_elements_truncates_after_ranking(self):
        elements = [make(0, "AXWindow", "x")]
        elements += [make(i, "AXButton", f"b{i}", parent=0, actions=["AXPress"])
                     for i in range(1, 30)]
        elements.append(make(99, "AXTextField", "IMPORTANT", parent=0))
        observation = ax_to_observation(elements, max_elements=5)
        assert len(observation["actions"]) == 5
        # The editable survives a truncation that drops 25 plain buttons.
        assert "IMPORTANT" in [item["label"] for item in observation["actions"]]

    def test_max_elements_zero_means_no_limit(self):
        elements = [make(0, "AXWindow", "x")]
        elements += [make(i, "AXButton", f"b{i}", parent=0, actions=["AXPress"])
                     for i in range(1, 60)]
        assert len(ax_to_observation(elements, max_elements=0)["actions"]) == 59


class TestSharedContract:
    """The adapter must produce what the browser project's table builder accepts."""

    def test_adapter_output_builds_an_element_table(self):
        from localdecide.page import build_element_table
        elements = [
            make(0, "AXWindow", "Calculator"),
            make(1, "AXButton", "Equals", parent=0, actions=["AXPress"]),
            make(2, "AXTextField", "Display", parent=0, value="7"),
        ]
        observation = ax_to_observation(elements)
        table = build_element_table(observation)
        labels = [element.label for element in table.elements]
        assert "Equals" in labels and "Display" in labels
        # The table is what the loop hands to questions; it must carry both actions.
        operations = {op for element in table.elements for op in element.operations}
        assert operations == {"CLICK", "TYPE_TEXT"}

    def test_history_key_present_for_the_loop(self):
        observation = ax_to_observation([])
        assert observation["history"] == []
        assert observation["url"] == ""  # a desktop window has no URL


class TestObserverSubprocess:
    def test_observer_calls_the_driver_with_tree_only(self, monkeypatch):
        """The screenshot must be off: AX is the observation, pixels are not."""
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            class Result:
                returncode = 0
                stdout = json.dumps({"elements": [], "window_title": "t", "snapshot_id": "s1"})
                stderr = ""
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)
        observer = AXObserver(pid=1, window_id=2)
        observer.snapshot()
        args = captured["cmd"]
        assert args[0].endswith("cua-driver") or args[0] == "cua-driver"
        payload = json.loads(args[3])
        assert payload["include_screenshot"] is False
        assert payload["pid"] == 1 and payload["window_id"] == 2

    def test_observe_returns_the_pid_and_window_it_read(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            class Result:
                returncode = 0
                stdout = json.dumps({"elements": [], "window_title": "t", "snapshot_id": "s9"})
                stderr = ""
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)
        observation = AXObserver(pid=7, window_id=8).observe()
        assert observation["pid"] == 7 and observation["window_id"] == 8
        assert observation["snapshot_id"] == "s9"

    def test_driver_failure_raises_rather_than_returning_junk(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            class Result:
                returncode = 1
                stdout = ""
                stderr = "window_id_not_found"
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)
        with pytest.raises(RuntimeError):
            AXObserver(pid=1, window_id=2).snapshot()


class TestDriverContract:
    def _driver(self, monkeypatch, responses):
        from laya_computer_use.driver import CuaDriver
        calls = []

        def fake_run(cmd, **kwargs):
            tool = cmd[2]
            calls.append((tool, json.loads(cmd[3])))
            class Result:
                returncode = 0
                stdout = json.dumps(responses.get(tool, {"elements": [], "window_title": "t"}))
                stderr = ""
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)
        return CuaDriver(pid=10, window_id=20), calls

    def test_click_uses_the_element_token(self, monkeypatch):
        driver, calls = self._driver(monkeypatch, {})
        from localdecide.loop import ElementRef
        element = ElementRef(index="3", label="OK", handle="s1:3")
        result = driver.execute("CLICK", element)
        assert result["ok"] is True
        tool, payload = calls[0]
        assert tool == "click"
        assert payload["element_token"] == "s1:3"
        assert payload["pid"] == 10 and payload["window_id"] == 20

    def test_click_without_a_handle_fails_closed(self, monkeypatch):
        driver, calls = self._driver(monkeypatch, {})
        from localdecide.loop import ElementRef
        result = driver.execute("CLICK", ElementRef(index="3", label="OK", handle=None))
        assert result["ok"] is False
        assert calls == []  # nothing was attempted

    def test_type_text_requires_text(self, monkeypatch):
        driver, calls = self._driver(monkeypatch, {})
        from localdecide.loop import ElementRef
        result = driver.execute("TYPE_TEXT", ElementRef(index="1", label="F", handle="t"),
                                text="")
        assert result["ok"] is False and calls == []

    def test_type_text_background_delivery_is_the_default(self, monkeypatch):
        driver, calls = self._driver(monkeypatch, {})
        from localdecide.loop import ElementRef
        driver.execute("TYPE_TEXT", ElementRef(index="1", label="F", handle="t"), text="hi")
        tool, payload = calls[0]
        assert tool == "type_text"
        assert payload["text"] == "hi" and payload["delivery_mode"] == "background"

    def test_select_uses_set_value(self, monkeypatch):
        driver, calls = self._driver(monkeypatch, {})
        from localdecide.loop import ElementRef
        driver.execute("SELECT", ElementRef(index="2", label="Country", handle="t2"),
                       text="Australia")
        tool, payload = calls[0]
        assert tool == "set_value"
        assert payload["value"] == "Australia" and payload["element_token"] == "t2"

    def test_unsupported_operation_fails_closed(self, monkeypatch):
        driver, calls = self._driver(monkeypatch, {})
        result = driver.execute("LAUNCH_MISSILE", None)
        assert result["ok"] is False and calls == []

    def test_close_is_idempotent(self, monkeypatch):
        driver, _ = self._driver(monkeypatch, {})
        driver.close()
        driver.close()  # the loop's finally + the caller's finally both run
        assert driver.execute("CLICK", None) == {"ok": False, "detail": "driver closed"}


class TestPageChangedSignal:
    def test_signature_notices_a_changed_control_set(self):
        from laya_computer_use.driver import CuaDriver
        before = {"actions": [{"label": "A"}, {"label": "B"}]}
        after = {"actions": [{"label": "A"}, {"label": "C"}]}
        assert CuaDriver._signature_of(before) != CuaDriver._signature_of(after)

    def test_signature_notices_a_vanished_control(self):
        """A dialog closing removes controls; the loop must see that as progress."""
        from laya_computer_use.driver import CuaDriver
        before = {"actions": [{"label": "OK"}, {"label": "Cancel"}]}
        after = {"actions": []}
        assert CuaDriver._signature_of(before) != CuaDriver._signature_of(after)

    def test_signature_is_stable_for_an_unchanged_screen(self):
        from laya_computer_use.driver import CuaDriver
        screen = {"actions": [{"label": "A"}, {"label": "B"}]}
        assert CuaDriver._signature_of(screen) == CuaDriver._signature_of(screen)


class TestFixtures:
    """The committed fixtures must stay loadable — the battery depends on them."""

    def test_calculator_fixture_builds_a_table(self):
        from localdecide.page import build_element_table
        path = ROOT / "benchmarks" / "fixtures" / "ax_calculator.json"
        observation = json.loads(path.read_text(encoding="utf-8"))
        table = build_element_table(observation)
        labels = {element.label for element in table.elements}
        # The controls the battery's goals depend on.
        for expected in ("等于", "全部清除", "显示边栏", "7"):
            assert expected in labels, f"{expected} missing from the fixture"

    def test_calculator_fixture_has_no_menu_chrome(self):
        path = ROOT / "benchmarks" / "fixtures" / "ax_calculator.json"
        observation = json.loads(path.read_text(encoding="utf-8"))
        for item in observation["actions"]:
            assert item.get("ax_role") != "AXMenuItem", item["label"]


class TestMCPModuleSurface:
    def test_mcp_server_is_importable_without_the_sdk(self):
        """The server speaks stdlib JSON over stdio; no mcp package is required."""
        import importlib.util
        path = ROOT / "laya_computer_use" / "mcp_server.py"
        if not path.exists():
            pytest.skip("mcp server not present")
        spec = importlib.util.spec_from_file_location("lcu_mcp", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert hasattr(module, "handle_request")
