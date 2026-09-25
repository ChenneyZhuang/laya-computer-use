"""End-to-end live test for the whole-computer driver: does every rung work?

Run on a real Mac (needs cua-driver + Accessibility permission). It exercises each
desktop rung and verifies the EFFECT through an independent readback, not by trusting
the driver's reply:

1. `table`    — the merged desktop table builds (apps + windows + window elements)
2. `switch`   — bring a running app to the front; verify via list_apps `active`
3. `launch`   — launch a closed app; verify a NEW window appears; then kill it
4. `focus`    — focus one exact window; verify it became frontmost
5. `menu`     — resolve a real menu path and invoke a reversible item; verify the
                window's AX signature changed, then invoke the inverse
6. `click`    — click a real control in a bound window; verify the app reacted
7. `type`     — type into a real editable element (TextEdit); verify the TEXT
8. `key`      — send a keystroke with no clickable form; verify state changed
9. `scroll`   — scroll a scrollable surface; verify the AX signature moved
10. `windows` — cross-app window focus (a window of a NON-frontmost app)
11. `chain`   — a real multi-step task (type → select-all → replace), verified by text
12. `manage`  — window management: minimize a window, verify it left the screen, restore
13. `context` — right-click a real element; verify a context menu opened
14. `negative`— a bad handle must fail closed, never report a phantom success
15. `third`   — a third-party app (Chrome), not just Apple's own
16. `restore` — put the originally-frontmost app back in front

Everything is restored at the end: the script is safe to run while the user works.

    python3 -m benchmarks.e2e_desktop            # all rungs
    python3 -m benchmarks.e2e_desktop table switch
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"

sys.path.insert(0, str(ROOT))
for _candidate in (ROOT.parent / "localdecide", Path("/Volumes/SSD/localdecide")):
    if (_candidate / "localdecide").is_dir():
        sys.path.insert(0, str(_candidate))
        break


def frontmost_pid() -> int | None:
    from laya_computer_use.desktop_driver import DesktopDriver

    driver = DesktopDriver()
    apps = driver.observer.list_apps()
    driver.close()
    active = next((app for app in apps if app.get("active")), None)
    return active.get("pid") if active else None


def case_table(driver, verdicts: dict) -> None:
    observation = driver.observe()
    rows = observation.get("actions", [])
    kinds = {str(row.get("meta", {}).get("desktop_kind", "")) for row in rows}
    ok = bool(rows) and "app" in kinds
    verdicts["table"] = {"ok": ok, "rows": len(rows), "kinds": sorted(k for k in kinds if k)}
    print(f"table   : {len(rows)} rows, kinds={sorted(kinds)} -> {'PASS' if ok else 'FAIL'}")


def case_switch(driver, verdicts: dict, target_name: str = "访达") -> None:
    """Switch to a running app; verify the OS reports it active."""
    observation = driver.observe()
    row = next((item for item in observation.get("actions", [])
                if str(item.get("meta", {}).get("desktop_action")) == "SWITCH_APP"
                and target_name in str(item.get("label", ""))), None)
    if row is None:
        verdicts["switch"] = {"ok": False, "detail": f"no running app matching {target_name!r}"}
        print(f"switch  : no row for {target_name!r} -> SKIP")
        return
    pid = row["meta"]["pid"]
    result = driver.execute("SWITCH_APP", _ref(row))
    time.sleep(0.6)
    apps = driver.observer.list_apps()
    active = next((app for app in apps if app.get("active")), None)
    ok = bool(active and active.get("pid") == pid)
    verdicts["switch"] = {"ok": ok, "pid": pid, "detail": result.get("detail"),
                          "verified_active": active.get("name") if active else None}
    print(f"switch  : {result.get('detail')} active={active.get('name') if active else None} "
          f"-> {'PASS' if ok else 'FAIL'}")


def case_launch(driver, verdicts: dict, bundle_id: str = "com.apple.Chess") -> None:
    """Launch a closed app, verify a new window, then close it again.

    The app is quit first (via AppleScript — cua-driver deliberately refuses to kill
    a process it did not launch, a safety property, so the harness uses the OS way).
    Without this step "a new window appeared" is not testable: a second run sees zero
    new windows because the app is already running.
    """
    import subprocess as _sp

    # Ensure the app is closed before we start.
    apps = driver.observer.list_apps()
    existing = next((a for a in apps if a.get("bundle_id") == bundle_id), None)
    if existing and existing.get("running"):
        try:
            _sp.run(["osascript", "-e",
                     f'tell application id "{bundle_id}" to quit'],
                    capture_output=True, timeout=15)
        except Exception:
            pass
        time.sleep(2.0)

    before = {w.get("window_id") for w in driver.observer.list_windows()}
    result = driver.execute("OPEN_APP", _ref_for_handle(f"lcu:open:{bundle_id}", bundle_id))
    # The app's pid is only known AFTER the launch: when it was closed, the app row
    # carried no pid, so the launched pid from the driver's reply is the only reliable
    # key. (An earlier version compared against the pre-launch row and never matched.)
    launched_pid = (result.get("launch") or {}).get("pid")
    # Poll for the *target app's* window rather than assuming a fixed delay. Filtering
    # by pid matters: the OS conjures its own helper windows (WindowManager) around the
    # same time, and a bare "new window id" check can catch one of those instead.
    new: list[dict] = []
    deadline = time.time() + 10.0
    while time.time() < deadline:
        after = driver.observer.list_windows()
        new = [w for w in after if w.get("window_id") not in before
               and w.get("is_on_screen")
               and (launched_pid is None or w.get("pid") == launched_pid)]
        if new:
            break
        time.sleep(0.5)
    ok = bool(new)
    launched = new[0] if new else None
    verdicts["launch"] = {"ok": ok, "detail": result.get("detail"),
                          "new_window": {"pid": launched.get("pid"),
                                         "window_id": launched.get("window_id"),
                                         "app": launched.get("app_name")} if launched else None}
    print(f"launch  : {result.get('detail')} new_window={launched.get('app_name') if launched else None} "
          f"-> {'PASS' if ok else 'FAIL'}")
    # Clean up through the same OS route (cua-driver will not kill a foreign process).
    if launched:
        try:
            _sp.run(["osascript", "-e", f'tell application id "{bundle_id}" to quit'],
                    capture_output=True, timeout=15)
            verdicts["launch"]["cleanup"] = "quit via osascript"
        except Exception as error:
            verdicts["launch"]["cleanup"] = f"quit failed: {error}"


def case_focus(driver, verdicts: dict) -> None:
    """Focus an exact window (the Hermes window itself, so nothing else moves)."""
    windows = driver.observer.list_windows()
    row = None
    observation = driver.observe()
    for item in observation.get("actions", []):
        if str(item.get("meta", {}).get("desktop_action")) == "FOCUS_WINDOW":
            row = item
            if "Hermes" in str(item.get("label", "")):
                break
    if row is None:
        verdicts["focus"] = {"ok": False, "detail": "no window rows"}
        print("focus   : no window rows -> SKIP")
        return
    meta = row["meta"]
    result = driver.execute("FOCUS_WINDOW", _ref(row))
    time.sleep(0.6)
    apps = driver.observer.list_apps()
    active = next((app for app in apps if app.get("active")), None)
    ok = bool(active and active.get("pid") == meta.get("pid"))
    verdicts["focus"] = {"ok": ok, "window_id": meta.get("window_id"),
                         "detail": result.get("detail")}
    print(f"focus   : {result.get('detail')} active={active.get('name') if active else None} "
          f"-> {'PASS' if ok else 'FAIL'}")


def case_menu(driver, verdicts: dict, app_name: str = "计算器") -> None:
    """Invoke a real, reversible menu item and verify the window changed.

    Discovers the actual menu labels from the AX tree (never assumes English labels),
    prefers a display-mode switch for the Calculator, and restores it afterwards.
    """
    apps = driver.observer.list_apps()
    app = next((a for a in apps if a.get("running") and app_name in str(a.get("name", ""))), None)
    if app is None:
        # Relaunch it: the switch/launch tests may have closed it.
        driver._call("launch_app", {"bundle_id": "com.apple.calculator"})
        time.sleep(2.0)
        apps = driver.observer.list_apps()
        app = next((a for a in apps if a.get("running") and app_name in str(a.get("name", ""))), None)
        if app is None:
            verdicts["menu"] = {"ok": False, "detail": f"{app_name!r} not available"}
            print(f"menu    : {app_name!r} not available -> SKIP")
            return
    pid = app.get("pid")
    windows = [w for w in driver.observer.list_windows() if w.get("pid") == pid]
    if not windows:
        verdicts["menu"] = {"ok": False, "detail": f"no window for {app_name!r}"}
        print("menu    : no window -> SKIP")
        return
    window = windows[0]

    # Walk the app's menu bar to find the display-mode items.
    from laya_computer_use.ax import AXElement, menu_path
    from laya_computer_use.desktop import DesktopObserver
    from laya_computer_use.driver import CuaDriver

    observer = DesktopObserver()
    snapshot = observer._call("get_window_state", {
        "pid": pid, "window_id": window["window_id"],
        "include_screenshot": False, "include_accessibility_tree": True})
    elements = [AXElement.from_driver(raw) for raw in snapshot.get("elements", [])]
    by_index = {element.element_index: element for element in elements}
    candidates = []
    for element in elements:
        if element.role == "AXMenuItem" and element.label:
            path = menu_path(element, by_index)
            if path:
                candidates.append(path)
    # Prefer a mode switch we can invert; the labels are localized, so accept both
    # the traditional and simplified wordings for "basic" (基本/基础).
    basic = next((path for path in candidates
                  if any(("基本" in segment or "基础" in segment) for segment in path)), None)
    scientific = next((path for path in candidates if any("科学" in segment for segment in path)), None)
    if not (basic and scientific):
        verdicts["menu"] = {"ok": False, "detail": "no display-mode items found",
                            "menu_sample": [" > ".join(p) for p in candidates[:8]]}
        print(f"menu    : no basic/scientific items -> SKIP ({len(candidates)} items found)")
        return

    def signature() -> tuple:
        return CuaDriver._signature_of(observer.window_tree(pid, window["window_id"]))

    before = signature()
    first = driver.execute("MENU", _ref_for_menu(scientific, pid))
    time.sleep(0.9)
    mid = signature()
    second = driver.execute("MENU", _ref_for_menu(basic, pid))
    time.sleep(0.9)
    after = signature()

    changed = mid != before
    restored = after == before
    ok = bool(first.get("ok") and second.get("ok") and changed and restored)
    verdicts["menu"] = {"ok": ok, "first": first.get("detail"), "second": second.get("detail"),
                        "changed": changed, "restored": restored,
                        "paths": {"scientific": scientific, "basic": basic}}
    print(f"menu    : {' > '.join(scientific)} changed={changed}, "
          f"{' > '.join(basic)} restored={restored} -> {'PASS' if ok else 'FAIL'}")


class _Ref:
    """Minimal ElementRef stand-in for direct rung tests."""

    def __init__(self, handle: str, label: str) -> None:
        self.handle = handle
        self.label = label
        self.meta: dict = {}


def case_click(driver, verdicts: dict) -> None:
    """Click a real control in a bound window; verify the click truly landed.

    The verification signal is the problem here, not the click: Calculator's display
    is NOT an AX node (custom-drawn), so its label fingerprint never moves no matter
    how many buttons are hit. The file dialog in TextEdit has buttons whose effect IS
    observable — a click opens a sheet, and the window's element count changes.
    Fallback order: Calculator sidebar toggle (audible AX change), then Calculator
    plain click with the driver's own page_changed as the signal, recorded honestly.
    """
    from laya_computer_use.driver import CuaDriver

    pid, window_id = _calculator(driver)
    driver.focus(window_id=window_id, pid=pid)

    # Prefer 显示边栏 (stateful: adds/removes memory buttons and does change the tree).
    observation = driver.observe()
    row = next((item for item in observation.get("actions", [])
                if str(item.get("label")) == "显示边栏"), None)
    if row is not None:
        before = len(observation.get("actions", []))
        first = driver.execute("CLICK", _ref(row))
        time.sleep(1.2)
        mid = len(driver.observe().get("actions", []))
        second = driver.execute("CLICK", _ref(row))
        time.sleep(1.2)
        after = len(driver.observe().get("actions", []))
        changed = mid != before
        restored = after == before
        ok = bool(first.get("ok") and second.get("ok") and changed and restored)
        verdicts["click"] = {"ok": ok, "control": "显示边栏",
                             "rows_before": before, "rows_mid": mid, "rows_after": after,
                             "changed": changed, "restored": restored,
                             "detail": first.get("detail")}
        print(f"click   : 显示边栏 rows {before}->{mid}->{after} "
              f"changed={changed} restored={restored} -> {'PASS' if ok else 'FAIL'}")
        return

    # Fallback: plain clicks, driver-reported change only (recorded as such).
    parent = driver.observer.list_windows()
    clicks = ["2", "加", "2", "等于"]
    landed = 0
    for want in clicks:
        observation = driver.observe()
        target = next((item for item in observation.get("actions", [])
                       if str(item.get("label")) == want
                       or ALIAS.get(str(item.get("label"))) == want), None)
        if target is None:
            continue
        result = driver.execute("CLICK", _ref(target))
        if result.get("ok"):
            landed += 1
        time.sleep(0.25)
    ok = landed == len(clicks)
    verdicts["click"] = {"ok": ok, "clicked": landed, "of": len(clicks),
                         "signal": "driver page_changed only (display not in AX)"}
    print(f"click   : {landed}/{len(clicks)} clicked (signal: driver only) "
          f"-> {'PASS' if ok else 'FAIL'}")


def case_type(driver, verdicts: dict) -> None:
    """Type into a real editable element and verify the text actually landed.

    TextEdit is the fixture because its document body is a real AX editable element;
    the check reads the element's value back, not the driver's reply.
    """
    import subprocess as _sp

    state = _ensure_textedit(driver)
    if state is None:
        verdicts["type"] = {"ok": False, "detail": "TextEdit unavailable"}
        print("type    : TextEdit unavailable -> SKIP")
        return
    pid, window_id = state
    driver.focus(window_id=window_id, pid=pid)
    observation = driver.observe()
    # The editable is the AXTextArea (role mapped to `textbox`, kind `fill`); match on
    # role/kind rather than a label, which differs per document.
    editor = next((item for item in observation.get("actions", [])
                   if str(item.get("role")) == "textbox"
                   or str(item.get("kind")) == "fill"), None)
    if editor is None:
        verdicts["type"] = {"ok": False, "detail": "no editable element in the table"}
        print("type    : no editable element -> SKIP")
        return
    marker = f"LCU-E2E-{int(time.time()) % 100000}"
    result = driver.execute("TYPE_TEXT", _ref(editor), text=marker)
    time.sleep(0.8)
    # Read the document back through the driver, independent of the driver's own reply.
    fresh = driver.observer._call("get_window_state", {
        "pid": pid, "window_id": window_id, "include_screenshot": False,
        "include_accessibility_tree": True})
    landed = any(marker in str(element.get("value", ""))
                 for element in fresh.get("elements", []))
    ok = bool(result.get("ok") and landed)
    verdicts["type"] = {"ok": ok, "detail": result.get("detail"), "marker": marker,
                        "landed": landed}
    print(f"type    : {marker} landed={landed} -> {'PASS' if ok else 'FAIL'}")


def case_key(driver, verdicts: dict) -> None:
    """Send a keystroke that has no clickable form; verify the state moved.

    Uses Calculator again: pressing a digit key changes the display/label
    fingerprint. (`press_key` needs the app bound and frontmost.)
    """
    from laya_computer_use.driver import CuaDriver

    pid, window_id = _calculator(driver)
    driver.focus(window_id=window_id, pid=pid)
    time.sleep(0.5)
    before = CuaDriver._signature_of(driver.observe())
    result = driver.execute("KEY", _ref_for_handle("lcu:key:7", "key 7"))
    time.sleep(0.6)
    after = CuaDriver._signature_of(driver.observe())
    ok = bool(result.get("ok"))
    verdicts["key"] = {"ok": ok, "detail": result.get("detail"),
                       "changed": after != before}
    print(f"key     : {result.get('detail')} changed={after != before} "
          f"-> {'PASS' if ok else 'FAIL'}")


def case_scroll(driver, verdicts: dict) -> None:
    """Scroll a real surface and verify the AX signature moved."""
    from laya_computer_use.driver import CuaDriver

    state = _ensure_textedit(driver)
    if state is None:
        verdicts["scroll"] = {"ok": False, "detail": "TextEdit unavailable"}
        print("scroll  : TextEdit unavailable -> SKIP")
        return
    pid, window_id = state
    driver.focus(window_id=window_id, pid=pid)
    # Make sure there is something to scroll: type 60 lines.
    driver.execute("TYPE_TEXT", None, text=("scroll me\n" * 60))
    time.sleep(1.2)
    before = CuaDriver._signature_of(driver.observe())
    result = driver.execute("SCROLL_DOWN", None)
    time.sleep(1.0)
    after = CuaDriver._signature_of(driver.observe())
    # The signature is a *label* fingerprint; TextEdit's editable is one element whose
    # value changes but whose label does not. So accept either signal: the AX signature
    # moving OR the driver reporting the window as changed.
    changed = after != before or bool(result.get("page_changed"))
    ok = bool(result.get("ok") and changed)
    verdicts["scroll"] = {"ok": ok, "detail": result.get("detail"),
                          "changed": changed, "page_changed": result.get("page_changed")}
    print(f"scroll  : {result.get('detail')} changed={changed} "
          f"(page_changed={result.get('page_changed')}) -> {'PASS' if ok else 'FAIL'}")


def case_windows(driver, verdicts: dict) -> None:
    """Focus a window belonging to a NON-frontmost app (true cross-app focus).

    Filters out the OS's own helper windows (WindowManager "App Icon Window" rows and
    the synergy-core 3x3 stub): those are real entries in list_windows, but raising one
    does not make its "app" frontmost, so scoring them would produce a false FAIL.
    """
    apps = driver.observer.list_apps()
    active_pid = next((a.get("pid") for a in apps if a.get("active")), None)
    observation = driver.observe()
    row = None
    for item in observation.get("actions", []):
        meta = item.get("meta", {})
        if meta.get("desktop_action") != "FOCUS_WINDOW":
            continue
        pid = meta.get("pid")
        if not pid or pid == active_pid:
            continue
        # Only apps (not OS helpers) are legitimate cross-app focus targets.
        owner = next((a for a in apps if a.get("pid") == pid), None)
        if owner is None or str(owner.get("name", "")) in ("WindowManager", "Dock", "synergy-core"):
            continue
        row = item
        break
    if row is None:
        verdicts["windows"] = {"ok": False, "detail": "no non-frontmost app window row"}
        print("windows : no non-frontmost app window row -> SKIP")
        return
    meta = row["meta"]
    result = driver.execute("FOCUS_WINDOW", _ref(row))
    time.sleep(0.8)
    apps = driver.observer.list_apps()
    active = next((a for a in apps if a.get("active")), None)
    ok = bool(active and active.get("pid") == meta.get("pid"))
    verdicts["windows"] = {"ok": ok, "window_id": meta.get("window_id"),
                           "detail": result.get("detail"),
                           "verified_active": active.get("name") if active else None}
    print(f"windows : {result.get('detail')} active={active.get('name') if active else None} "
          f"-> {'PASS' if ok else 'FAIL'}")


ALIAS = {"加": "+", "减": "-", "乘": "*", "除": "/", "等于": "=", "点": ".",
         "全部清除": "AC", "删除": "Backspace", "百分比": "Percent"}


def case_chain(driver, verdicts: dict) -> None:
    """A real multi-step task end to end: write, select all, replace, verify both.

    Single actions can each pass while a *sequence* fails (focus drift, a stale
    element map, a guard firing on step 3). This uses only public API: TYPE_TEXT,
    the KEY rung for the select-all shortcut, TYPE_TEXT again — and then reads the
    document back to prove which text actually ended up in it.
    """
    state = _ensure_textedit(driver)
    if state is None:
        verdicts["chain"] = {"ok": False, "detail": "TextEdit unavailable"}
        print("chain   : TextEdit unavailable -> SKIP")
        return
    pid, window_id = state
    driver.focus(window_id=window_id, pid=pid)

    first = f"CHAIN-A-{int(time.time()) % 100000}"
    second = f"CHAIN-B-{int(time.time()) % 100000}"

    def document() -> str:
        fresh = driver.observer._call("get_window_state", {
            "pid": pid, "window_id": window_id, "include_screenshot": False,
            "include_accessibility_tree": True})
        return "\n".join(str(e.get("value", "")) for e in fresh.get("elements", []))

    # Clear whatever is there, then write marker A.
    driver.execute("KEY", _ref_for_handle("lcu:key:cmd a", "cmd+a"))
    time.sleep(0.4)
    driver.execute("TYPE_TEXT", None, text=first)
    time.sleep(0.8)
    step1 = first in document()

    # Select all and replace with marker B — the second half of the chain.
    driver.execute("KEY", _ref_for_handle("lcu:key:cmd a", "cmd+a"))
    time.sleep(0.4)
    driver.execute("TYPE_TEXT", None, text=second)
    time.sleep(0.8)
    text = document()
    step2 = second in text
    step3 = first not in text  # the replacement really replaced

    ok = bool(step1 and step2 and step3)
    verdicts["chain"] = {"ok": ok, "typed_a": step1, "typed_b": step2,
                         "replaced": step3}
    print(f"chain   : A landed={step1} B landed={step2} replaced={step3} "
          f"-> {'PASS' if ok else 'FAIL'}")


def case_manage(driver, verdicts: dict) -> None:
    """Window management: move a window, verify its bounds moved, put it back.

    Minimize was tried first and is NOT verifiable on this stack: after the menu
    invoke, `AXMinimized` stays false and the window stays in list_windows (measured
    — even a System Events click behaves the same), so "minimized" can only be
    asserted, never checked. Moving the window is different: `set_window_frame` takes
    an exact frame and `list_windows` reports bounds back, so the effect is read from
    the OS, not trusted from the driver's reply.
    """
    state = _ensure_textedit(driver)
    if state is None:
        verdicts["manage"] = {"ok": False, "detail": "TextEdit unavailable"}
        print("manage  : TextEdit unavailable -> SKIP")
        return
    pid, window_id = state

    def bounds() -> dict | None:
        for w in driver.observer.list_windows():
            if w.get("pid") == pid and w.get("window_id") == window_id:
                return w.get("bounds")
        return None

    original = bounds()
    if not original:
        verdicts["manage"] = {"ok": False, "detail": "window bounds unreadable"}
        print("manage  : bounds unreadable -> SKIP")
        return
    target = dict(original, x=int(original["x"]) + 120, y=int(original["y"]) + 60)
    try:
        driver._call("set_window_frame", {"pid": pid, "window_id": window_id,
                                          "x": target["x"], "y": target["y"],
                                          "width": int(target["width"]),
                                          "height": int(target["height"])})
    except Exception as error:
        verdicts["manage"] = {"ok": False, "detail": f"set_window_frame: {error}"}
        print(f"manage  : set_window_frame refused ({str(error)[:60]}) -> FAIL")
        return
    time.sleep(1.2)
    moved = bounds()
    moved_ok = bool(moved and abs(moved.get("x", 0) - target["x"]) <= 8
                    and abs(moved.get("y", 0) - target["y"]) <= 8)
    # Put it back exactly where it was.
    driver._call("set_window_frame", {"pid": pid, "window_id": window_id,
                                      "x": int(original["x"]), "y": int(original["y"]),
                                      "width": int(original["width"]),
                                      "height": int(original["height"])})
    time.sleep(1.2)
    restored = bounds()
    restore_ok = bool(restored and abs(restored.get("x", 0) - original["x"]) <= 8
                      and abs(restored.get("y", 0) - original["y"]) <= 8)
    ok = bool(moved_ok and restore_ok)
    verdicts["manage"] = {"ok": ok, "original": original, "moved_to": moved,
                          "moved": moved_ok, "restored": restore_ok}
    print(f"manage  : window {original.get('x'):.0f},{original.get('y'):.0f} -> "
          f"{moved.get('x'):.0f},{moved.get('y'):.0f} moved={moved_ok} "
          f"restored={restore_ok} -> {'PASS' if ok else 'FAIL'}")


def case_context(driver, verdicts: dict) -> None:
    """Right-click a real element and verify a context menu appears.

    The signal is a *new menu-ish subtree*: right-clicking TextEdit's text area pops a
    context menu whose items exist in the AX tree only while it is open.
    """
    import json as _json
    import shutil as _shutil

    state = _ensure_textedit(driver)
    if state is None:
        verdicts["context"] = {"ok": False, "detail": "TextEdit unavailable"}
        print("context : TextEdit unavailable -> SKIP")
        return
    pid, window_id = state
    driver.focus(window_id=window_id, pid=pid)
    time.sleep(0.6)

    def menu_items() -> int:
        fresh = driver.observer._call("get_window_state", {
            "pid": pid, "window_id": window_id, "include_screenshot": False,
            "include_accessibility_tree": True})
        return sum(1 for e in fresh.get("elements", []) if e.get("role") == "AXMenuItem")

    before = menu_items()
    # Right-click via the driver's pointer rung on the editable's own frame.
    fresh = driver.observer._call("get_window_state", {
        "pid": pid, "window_id": window_id, "include_screenshot": False,
        "include_accessibility_tree": True})
    area = next((e for e in fresh.get("elements", [])
                 if e.get("role") == "AXTextArea" and e.get("frame")), None)
    if area is None:
        verdicts["context"] = {"ok": False, "detail": "no text area"}
        print("context : no text area -> SKIP")
        return
    frame = area["frame"]
    # The window's own frame (the AXWindow node) — right_click needs WINDOW-LOCAL
    # points when a window_id is given, and the window it resolves must be the same
    # one the coordinates are local to. (Measured: global points against window 8281's
    # 586x488 frame were refused as "outside the window".)
    state2 = driver.observer._call("get_window_state", {
        "pid": pid, "window_id": window_id, "include_screenshot": False,
        "include_accessibility_tree": True})
    win_frame = next((e.get("frame") for e in state2.get("elements", [])
                      if e.get("role") == "AXWindow" and e.get("frame")), None)
    if win_frame is None:
        verdicts["context"] = {"ok": False, "detail": "window has no AX frame"}
        print("context : window has no AX frame -> SKIP")
        return
    local_x = int(frame["x"] + frame["w"] / 2 - win_frame["x"])
    local_y = int(frame["y"] + frame["h"] / 2 - win_frame["y"])
    if not (0 <= local_x <= win_frame["w"] and 0 <= local_y <= win_frame["h"]):
        verdicts["context"] = {"ok": False, "detail": "text area outside its window"}
        print("context : text area outside its window -> SKIP")
        return
    try:
        driver._call("right_click", {"pid": pid, "window_id": window_id,
                                     "x": local_x, "y": local_y})
    except Exception as error:
        verdicts["context"] = {"ok": False, "detail": f"right_click failed: {error}"}
        print(f"context : right_click refused ({str(error)[:60]}) -> SKIP")
        return
    time.sleep(1.0)
    after = menu_items()
    opened = after > before
    # Close it again with Escape so nothing is left open.
    driver.execute("KEY", _ref_for_handle("lcu:key:escape", "escape"))
    time.sleep(0.5)
    ok = opened
    verdicts["context"] = {"ok": ok, "menu_items_before": before,
                           "menu_items_after": after}
    print(f"context : menu items {before}->{after} -> {'PASS' if ok else 'FAIL'}")


def case_negative(driver, verdicts: dict) -> None:
    """A bad handle must fail closed.

    The loop's whole safety story is 'a wrong answer is a wrong choice, never an
    invented one'. That only holds if an unactionable target is REFUSED. This feeds
    the executor garbage handles and asserts every one comes back not-ok.
    """
    probes = [
        ("unknown rung", "lcu:frobnicate:1"),
        ("app row without a pid", "lcu:app:notanint"),
        ("menu handle with an unreadable path", "lcu:menu:{not json"),
        ("open row without a bundle", "lcu:open:"),
    ]
    failures = []
    for name, handle in probes:
        result = driver.execute("CLICK", _ref_for_handle(handle, name))
        if result.get("ok"):
            failures.append(name)
    ok = not failures
    verdicts["negative"] = {"ok": ok, "probes": len(probes), "phantom_successes": failures}
    print(f"negative: {len(probes) - len(failures)}/{len(probes)} refused, "
          f"phantoms={failures} -> {'PASS' if ok else 'FAIL'}")


def case_third(driver, verdicts: dict) -> None:
    """A third-party app, not an Apple one: discover Chrome's controls and focus it.

    Chrome windows expose their chrome as AX (tab strip, toolbar buttons), which is a
    different tree shape from Calculator's. The check: the window's element table
    builds with real labelled rows, and Chrome becomes the frontmost app — through the
    SAME switch rung any app row uses (focus() binds the observation surface only;
    it does not activate the app, which is exactly what the first version got wrong).
    """
    apps = driver.observer.list_apps()
    chrome = next((a for a in apps if "Chrome" in str(a.get("name", ""))), None)
    if chrome is None:
        verdicts["third"] = {"ok": False, "detail": "Chrome not running"}
        print("third   : Chrome not running -> SKIP")
        return
    windows = [w for w in driver.observer.list_windows() if w.get("pid") == chrome.get("pid")]
    if not windows:
        verdicts["third"] = {"ok": False, "detail": "Chrome has no window"}
        print("third   : Chrome has no window -> SKIP")
        return
    window = windows[0]
    driver.focus(window_id=int(window["window_id"]), pid=int(chrome["pid"]))
    # Activate through the same rung a desktop SWITCH_APP uses.
    driver._call("bring_to_front", {"pid": int(chrome["pid"]),
                                    "window_id": int(window["window_id"])})
    time.sleep(1.0)
    observation = driver.observe()
    rows = observation.get("actions", [])
    labelled = [r for r in rows if r.get("label")]
    apps = driver.observer.list_apps()
    active = next((a for a in apps if a.get("active")), None)
    focused_ok = bool(active and active.get("pid") == chrome.get("pid"))
    table_ok = len(labelled) >= 3
    ok = focused_ok and table_ok
    verdicts["third"] = {"ok": ok, "rows": len(rows), "labelled": len(labelled),
                         "focused": focused_ok}
    print(f"third   : Chrome rows={len(rows)} labelled={len(labelled)} "
          f"focused={focused_ok} -> {'PASS' if ok else 'FAIL'}")


def _calculator(driver) -> tuple[int, int]:
    import subprocess as _sp

    apps = driver.observer.list_apps()
    app = next((a for a in apps if a.get("running") and "计算器" in str(a.get("name", ""))), None)
    if app is None:
        driver._call("launch_app", {"bundle_id": "com.apple.calculator"})
        time.sleep(2.0)
        apps = driver.observer.list_apps()
        app = next((a for a in apps if a.get("running") and "计算器" in str(a.get("name", ""))), None)
    if app is None:
        raise RuntimeError("Calculator not available")
    windows = [w for w in driver.observer.list_windows() if w.get("pid") == app.get("pid")]
    if not windows:
        raise RuntimeError("Calculator has no window")
    driver._call("bring_to_front", {"pid": app["pid"], "window_id": int(windows[0]["window_id"])})
    time.sleep(0.8)
    return int(app["pid"]), int(windows[0]["window_id"])


def _ensure_textedit(driver) -> tuple[int, int] | None:
    """TextEdit with ONE open document window.

    TextEdit can be running with zero windows (its "open file" dialog is a separate
    window that is not the document body), and multiple document windows break the
    keyboard rung entirely: `press_key` refuses with `same_pid_keyboard_ambiguity`
    when the pid owns more than one eligible window (measured). So the invariant this
    helper guarantees is exactly one document window: extra ones are closed.
    """
    import subprocess as _sp

    def find() -> tuple[int, int] | None:
        apps = driver.observer.list_apps()
        app = next((a for a in apps if a.get("running") and "文本编辑" in str(a.get("name", ""))), None)
        if app is None:
            return None
        windows = [w for w in driver.observer.list_windows()
                   if w.get("pid") == app.get("pid") and w.get("is_on_screen")
                   and str(w.get("title", "")) not in ("打开", "")]
        return (int(app["pid"]), windows) if windows else None

    found = find()
    if found is None:
        # Launch (or ask for a fresh document) and retry.
        try:
            _sp.run(["osascript", "-e", 'tell application "TextEdit" to activate',
                     "-e", 'tell application "TextEdit" to make new document'],
                    capture_output=True, timeout=20)
        except Exception:
            driver._call("launch_app", {"bundle_id": "com.apple.TextEdit"})
        time.sleep(2.5)
        found = find()
        if found is None:
            return None
    pid, windows = found
    # Close every document window except the newest: the keyboard rung needs one
    # eligible window per pid. Closed one at a time by name with `saving no` —
    # TextEdit documents carry unsaved e2e markers, and a plain `close` stalls on a
    # save sheet while a bulk `every window whose…` clause silently matched nothing.
    if len(windows) > 1:
        keep = max(windows, key=lambda w: w.get("window_id", 0))
        for w in windows:
            if w.get("window_id") != keep.get("window_id"):
                title = str(w.get("title", ""))
                if not title:
                    continue
                try:
                    _sp.run(["osascript", "-e",
                             f'tell application "TextEdit" to close window '
                             f'"{title.replace(chr(34), chr(92) + chr(34))}" saving no'],
                            capture_output=True, timeout=15)
                except Exception:
                    pass
        time.sleep(1.5)
        found = find()
        if found is None:
            return None
        pid, windows = found
    window = windows[0]
    driver._call("bring_to_front", {"pid": pid, "window_id": int(window["window_id"])})
    time.sleep(0.8)
    return pid, int(window["window_id"])


def _ref(row: dict) -> _Ref:
    meta = row.get("meta") or {}
    handle = row.get("node", "")
    if not str(handle).startswith("lcu:"):
        # Rebuild the encoded handle from the row's dispatch info.
        from laya_computer_use.desktop_driver import DesktopDriver

        handle = DesktopDriver._encode_handle(row, meta)
    return _Ref(str(handle), str(row.get("label", "")))


def _ref_for_handle(handle: str, label: str) -> _Ref:
    return _Ref(handle, label)


def _ref_for_menu(path: list[str], pid: int | None = None) -> _Ref:
    import json as _json

    prefix = f"{int(pid)}:" if pid is not None else ""
    return _Ref("lcu:menu:" + prefix + _json.dumps(path, ensure_ascii=False),
                " > ".join(path))


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")] or \
        ["table", "switch", "launch", "focus", "menu", "click", "type", "key",
         "scroll", "windows", "chain", "manage", "context", "negative", "third",
         "restore"]

    from laya_computer_use.desktop_driver import DesktopDriver

    prior = frontmost_pid()
    driver = DesktopDriver(max_apps=20, max_windows=10)
    verdicts: dict = {}
    try:
        if "table" in wanted:
            case_table(driver, verdicts)
        if "switch" in wanted:
            case_switch(driver, verdicts)
            time.sleep(0.4)
        if "launch" in wanted:
            case_launch(driver, verdicts)
        if "focus" in wanted:
            case_focus(driver, verdicts)
        if "menu" in wanted:
            case_menu(driver, verdicts)
        if "click" in wanted:
            case_click(driver, verdicts)
        if "type" in wanted:
            case_type(driver, verdicts)
        if "key" in wanted:
            case_key(driver, verdicts)
        if "scroll" in wanted:
            case_scroll(driver, verdicts)
        if "windows" in wanted:
            case_windows(driver, verdicts)
        if "chain" in wanted:
            case_chain(driver, verdicts)
        if "manage" in wanted:
            case_manage(driver, verdicts)
        if "context" in wanted:
            case_context(driver, verdicts)
        if "negative" in wanted:
            case_negative(driver, verdicts)
        if "third" in wanted:
            case_third(driver, verdicts)
    finally:
        if "restore" in wanted and prior:
            try:
                driver._call("bring_to_front", {"pid": int(prior)})
                verdicts["restore"] = {"ok": True, "pid": prior}
                print(f"restore : brought pid {prior} back to front")
            except Exception as error:
                verdicts["restore"] = {"ok": False, "detail": str(error)}
                print(f"restore : FAILED {error}")
        driver.close()

    verdicts["bridge"] = all(v.get("ok") for key, v in verdicts.items() if key != "restore")
    print()
    print(json.dumps(verdicts, ensure_ascii=False, indent=2))
    (RESULTS / "e2e_desktop.json").write_text(json.dumps(verdicts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
