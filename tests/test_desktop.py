"""Contract tests for the whole-computer layer — no driver, no model, no window.

Same discipline as `test_contract.py`: everything here runs on synthetic
observation dicts so it is safe in CI on every OS. The live rungs are exercised by
`benchmarks/e2e_desktop.py` on a real Mac.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from laya_computer_use.ax import AXElement, ax_to_observation, menu_path
from laya_computer_use.desktop import (
    DesktopApp,
    DesktopObserver,
    DesktopWindow,
    build_desktop_observation,
    split_desktop_element,
)


# ── the namespace ────────────────────────────────────────────────────────────


def test_namespace_splits_apps_windows_and_elements():
    assert split_desktop_element("a3") == ("app", "3")
    assert split_desktop_element("w214") == ("window", "214")
    assert split_desktop_element("12") == ("element", "12")
    assert split_desktop_element("s0000000c:1") == ("element", "s0000000c:1")


def test_namespace_does_not_mistake_label_like_tokens():
    # "a" followed by digits is an app; "abc" is not.
    assert split_desktop_element("abc") == ("element", "abc")
    assert split_desktop_element("a3b") == ("element", "a3b")


# ── the merged observation ───────────────────────────────────────────────────


def _apps():
    return [
        {"name": "Google Chrome", "bundle_id": "com.google.Chrome", "pid": 440,
         "running": True, "active": True},
        {"name": "访达", "bundle_id": "com.apple.finder", "pid": 459, "running": True,
         "active": False},
        {"name": "Safari", "bundle_id": "com.apple.Safari", "pid": None,
         "running": False, "active": False},
        {"name": "Calculator", "bundle_id": "com.apple.calculator", "pid": None,
         "running": False, "active": False},
    ]


def _windows():
    return [
        {"window_id": 141, "pid": 440, "app_name": "Google Chrome",
         "title": "Trip", "bounds": {"x": 0, "y": 0, "width": 100, "height": 100}},
        {"window_id": 2167, "pid": 459, "app_name": "访达", "title": "NoahsArk",
         "bounds": {}},
    ]


def test_merged_observation_carries_apps_windows_and_window_elements():
    window_obs = {"url": "", "title": "Calculator", "text": "",
                  "actions": [{"kind": "click", "node": "tok1", "label": "7", "role": "button"}]}
    obs = build_desktop_observation(_apps(), _windows(), window_observation=window_obs)
    labels = [item.get("label") for item in obs["actions"]]
    assert any("Google Chrome" in str(label) for label in labels)
    assert any("Safari" in str(label) for label in labels)
    assert any("NoahsArk" in str(label) for label in labels)
    assert any(item.get("label") == "7" for item in obs["actions"])


def test_running_apps_come_before_installed_ones():
    obs = build_desktop_observation(_apps(), _windows())
    rows = obs["actions"]
    chrome = next(i for i, item in enumerate(rows) if "Google Chrome" in str(item.get("label")))
    safari = next(i for i, item in enumerate(rows) if "Safari" in str(item.get("label")))
    assert chrome < safari, "a running app must rank above a closed one"


def test_installed_apps_can_be_excluded():
    obs = build_desktop_observation(_apps(), [], include_installed=False)
    labels = " ".join(str(item.get("label")) for item in obs["actions"])
    assert "Safari" not in labels
    assert "Google Chrome" in labels


def test_closed_app_rows_are_launch_targets():
    obs = build_desktop_observation(_apps(), [])
    safari = next(item for item in obs["actions"] if "Safari" in str(item.get("label")))
    assert safari["meta"]["desktop_action"] == "OPEN_APP"
    assert safari["meta"]["bundle_id"] == "com.apple.Safari"


def test_running_app_rows_are_switch_targets_with_a_pid():
    obs = build_desktop_observation(_apps(), [])
    chrome = next(item for item in obs["actions"] if "Google Chrome" in str(item.get("label")))
    assert chrome["meta"]["desktop_action"] == "SWITCH_APP"
    assert chrome["meta"]["pid"] == 440


def test_window_rows_carry_both_ids():
    obs = build_desktop_observation(_apps(), _windows())
    row = next(item for item in obs["actions"] if "NoahsArk" in str(item.get("label")))
    assert row["meta"]["desktop_action"] == "FOCUS_WINDOW"
    assert row["meta"]["window_id"] == 2167
    assert row["meta"]["pid"] == 459


def test_describe_lines_are_informative():
    app = DesktopApp(index="a1", name="Safari", bundle_id="com.apple.Safari",
                     running=False)
    text = app.describe()
    assert "Safari" in text and "not running" in text and "com.apple.Safari" in text

    window = DesktopWindow(index="w1", window_id=1, pid=2, app_name="Notes",
                           title="Shopping")
    assert "Notes" in window.describe() and "Shopping" in window.describe()


# ── the menu rung ────────────────────────────────────────────────────────────


def _menu_elements() -> list[AXElement]:
    """Apple menu > 关于本机, the plainest real menu shape."""
    return [
        AXElement(element_index=100, role="AXMenuBar", label="", parent_index=None, depth=0),
        AXElement(element_index=101, role="AXMenuBarItem", label="Apple",
                  parent_index=100, depth=1),
        AXElement(element_index=102, role="AXMenu", label="", parent_index=101, depth=2),
        AXElement(element_index=103, role="AXMenuItem", label="关于本机",
                  parent_index=102, depth=3, actions=["AXPress"]),
    ]


def test_menu_path_resolves_through_the_bar():
    elements = _menu_elements()
    by_index = {element.element_index: element for element in elements}
    target = elements[-1]
    assert menu_path(target, by_index) == ["Apple", "关于本机"]


def test_menu_path_is_empty_outside_the_bar():
    elements = [
        AXElement(element_index=1, role="AXWindow", label="", parent_index=None, depth=0),
        AXElement(element_index=2, role="AXMenu", label="", parent_index=1, depth=1),
        AXElement(element_index=3, role="AXMenuItem", label="Cut", parent_index=2, depth=2),
    ]
    by_index = {element.element_index: element for element in elements}
    # A context menu is not an app menu: it must not become an invocable path.
    assert menu_path(elements[-1], by_index) == []


def test_menu_items_are_offered_only_when_asked():
    elements = _menu_elements()
    quiet = ax_to_observation(elements)
    loud = ax_to_observation(elements, menu_items=True)
    assert all(item.get("role") != "menuitem" for item in quiet["actions"])
    menu_rows = [item for item in loud["actions"] if item.get("role") == "menuitem"]
    assert len(menu_rows) == 1
    assert menu_rows[0]["meta"]["menu"] == ["Apple", "关于本机"]
    assert menu_rows[0]["meta"]["desktop_action"] == "MENU"


def test_menu_observation_keeps_the_window_controls():
    """Opting into menus must not push the window's own controls out of the table."""
    elements = _menu_elements() + [
        AXElement(element_index=200, role="AXButton", label="7", parent_index=1,
                  depth=2, actions=["AXPress"]),
    ]
    loud = ax_to_observation(elements, menu_items=True)
    labels = [item.get("label") for item in loud["actions"]]
    assert "7" in labels and "关于本机" in labels


# ── the driver's handle encoding (pure, no driver needed) ────────────────────


def test_handle_encoding_round_trips_every_rung():
    from laya_computer_use.desktop_driver import DesktopDriver

    cases = [
        ({"desktop_action": "SWITCH_APP", "pid": 440}, "lcu:app:440"),
        ({"desktop_action": "OPEN_APP", "bundle_id": "com.apple.Safari"}, "lcu:open:com.apple.Safari"),
        ({"desktop_action": "FOCUS_WINDOW", "pid": 459, "window_id": 2167},
         "lcu:win:459:2167"),
        ({"desktop_action": "MENU", "menu": ["File", "New Window"]},
         'lcu:menu:["File", "New Window"]'),
    ]
    for meta, expected in cases:
        handle = DesktopDriver._encode_handle({"node": "x"}, meta)
        assert handle == expected


def test_handle_encoding_falls_back_to_the_element_token():
    from laya_computer_use.desktop_driver import DesktopDriver

    handle = DesktopDriver._encode_handle({"node": "tok1"}, {"element_token": "s0000000c:1"})
    assert handle == "s0000000c:1"


def test_execute_dispatches_on_the_encoded_handle():
    """The registry of rungs must accept CLICK (how the rows are described) and the
    explicit operation, and refuse mismatches without calling the driver."""
    from laya_computer_use.desktop_driver import DesktopDriver

    driver = DesktopDriver()
    calls = {}

    def fake_switch(pid_text, label):
        calls["switch"] = (pid_text, label)
        return {"ok": True, "detail": "switched"}

    def fake_menu(path, label, *, pid=None):
        calls["menu"] = (path, label)
        return {"ok": True, "detail": "menu"}

    driver._switch_app = fake_switch  # type: ignore[method-assign]
    driver._menu = fake_menu  # type: ignore[method-assign]

    class Ref:
        def __init__(self, handle, label):
            self.handle = handle
            self.label = label
            self.meta = {}

    assert driver.execute("CLICK", Ref("lcu:app:440", "Chrome"))["ok"]
    assert calls["switch"] == ("440", "Chrome")
    assert driver.execute("MENU", Ref('lcu:menu:["File"]', "New"))["ok"]
    assert calls["menu"] == (["File"], "New")
    # With an owning pid: `lcu:menu:<pid>:<json>` must split at the pid, not the JSON.
    assert driver.execute("MENU", Ref('lcu:menu:459:["显示", "科学"]', "科学"))["ok"]
    assert calls["menu"] == (["显示", "科学"], "科学")

    bad = driver.execute("TYPE_TEXT", Ref("lcu:app:440", "Chrome"), "hi")
    assert not bad["ok"] and "not valid" in bad["detail"]
    driver.close()


def test_unknown_handle_is_refused():
    from laya_computer_use.desktop_driver import DesktopDriver

    driver = DesktopDriver()
    class Ref:
        handle = "lcu:bogus:x"
        label = "?"
        meta = {}
    result = driver.execute("CLICK", Ref())
    assert not result["ok"] and "unknown desktop handle" in result["detail"]
    driver.close()


# ── observer construction does not touch a real driver ───────────────────────


def test_observer_constructs_without_calling_the_driver(tmp_path: Path):
    observer = DesktopObserver(driver_bin=str(tmp_path / "nope"))
    assert observer.driver_bin.endswith("nope")


def test_fixture_if_present_has_the_right_shape():
    fixture = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures" / "desktop_table.json"
    if not fixture.exists():
        pytest.skip("no captured desktop fixture on this machine")
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert data.get("actions"), "a captured desktop table must have rows"
    for row in data["actions"]:
        assert row.get("label"), "every desktop row needs a label"
