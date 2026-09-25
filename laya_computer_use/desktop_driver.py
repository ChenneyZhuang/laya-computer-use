"""The desktop executor: turn a namespaced index into a real whole-computer action.

`driver.py` executes inside one window. This executes across the computer:

* an app row       -> bring it to the front (or launch it when it is closed)
* a window row     -> focus that exact window, optionally cross-app
* a menu item      -> resolve and invoke the app-menu path, no pixels
* a key            -> send a keystroke the UI has no clickable form for
* a window element -> the same AX press `CuaDriver` already does

The model still only ever picks an index from a table it was shown. The index
namespace (`a3` app, `w214` window, `12` element) is what tells the executor which
rung to use, and it is the only thing that can — the model's answer can never turn
into a bundle id, a coordinate, or a shell string.

## Verify, don't assume

Every action re-reads state through the driver and reports what changed:

* app switch/launch -> the window list is diffed (did a new window appear? did the
  frontmost pid change?)
* menu invoke       -> `invoke_menu` already fails closed on a missing or disabled
  path, and the result is recorded verbatim
* key press         -> the AX signature of the focused window is compared before and
  after, the same signal `driver.py` uses for page_changed

A step that reports `ok` but changes nothing is exactly the failure mode this
project exists to surface, so "changed" is a first-class field, never inferred.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from .desktop import DesktopObserver
from .driver import CuaDriver

# Same fingerprint semantics as the single-window driver: visible control count plus
# a label sample. Enough to notice a changed form or a new dialog without hashing
# the whole tree.
_signature_of = CuaDriver._signature_of


class DesktopDriver:
    """Whole-computer driver. Implements the loop's Driver protocol.

    `DesktopDriver()` starts with no focused window: the first observation is the
    desktop table (apps + windows). `focus(window_id, pid)` binds the window surface
    so subsequent observations merge the desktop and that window's element table —
    which is how one decision can choose either a window control or another app.
    """

    def __init__(
        self,
        *,
        driver_bin: str = "cua-driver",
        max_elements: int = 40,
        timeout: float = 60.0,
        max_apps: int = 30,
        max_windows: int = 12,
        include_installed: bool = True,
        menu_items: bool = True,
        delivery_mode: str = "background",
        settle_ms: int = 250,
    ) -> None:
        self.driver_bin = driver_bin
        self.timeout = timeout
        self.max_elements = max_elements
        self.max_apps = max_apps
        self.max_windows = max_windows
        self.include_installed = include_installed
        self.menu_items = menu_items
        self.delivery_mode = delivery_mode
        self.settle_ms = settle_ms
        self.observer = DesktopObserver(driver_bin=driver_bin, timeout=timeout)
        self._window: Optional[CuaDriver] = None
        self._focused_pid: Optional[int] = None
        self._by_index: Dict[str, Dict[str, Any]] = {}
        self._signature: Optional[tuple] = None
        self._cached: Optional[Dict[str, Any]] = None
        self._closed = False
        self._last_window_state: Dict[str, Any] = {}

    # ── window binding ────────────────────────────────────────────────────────

    @property
    def window(self) -> Optional[CuaDriver]:
        return self._window

    def focus(self, window_id: int, pid: int) -> None:
        """Bind the window surface. Cheap: constructs nothing until a call."""
        self._window = CuaDriver(pid=pid, window_id=window_id, driver_bin=self.driver_bin,
                                 max_elements=self.max_elements, timeout=self.timeout,
                                 delivery_mode=self.delivery_mode, settle_ms=self.settle_ms)
        self._focused_pid = pid

    def _focus_observation(self) -> Dict[str, Any]:
        """The bound window's element table (menu-aware), or an empty table."""
        if self._window is None:
            return {"url": "", "title": "", "text": "", "actions": [], "history": []}
        # Rebuild through a fresh observer so `menu_items` is honoured.
        from .ax import AXObserver

        observer = AXObserver(pid=self._window.pid, window_id=self._window.window_id,
                              driver_bin=self.driver_bin, timeout=self.timeout,
                              max_elements=self.max_elements,
                              menu_items=self.menu_items)
        observation = observer.observe()
        self._last_window_state = observation
        return observation

    # ── Driver protocol ──────────────────────────────────────────────────────

    def observe(self) -> Dict[str, Any]:
        if self._cached is not None:
            observation, self._cached = self._cached, None
            self._absorb(observation)
            return observation
        observation = self._build_observation()
        self._absorb(observation)
        return observation

    def _build_observation(self) -> Dict[str, Any]:
        overview = self.observer.overview()
        window_observation = self._focus_observation() if self._window is not None else None
        from .desktop import build_desktop_observation

        observation = build_desktop_observation(
            overview.get("apps", []), overview.get("windows", []),
            window_observation=window_observation,
            focused_pid=self._focused_pid,
            include_installed=self.include_installed,
            max_apps=self.max_apps,
            max_windows=self.max_windows,
        )
        # Re-index: the merged table's `node` values are the namespaced handles the
        # executor dispatches on, but the model must see dense 1..N indices (it picks
        # an index; the code owns the mapping). Renumber and encode the dispatch rung
        # into the handle, which is the one field that survives scoping and the table
        # build unchanged (`node` -> ElementRef.handle -> execute()).
        renumbered = []
        for position, item in enumerate(observation.get("actions", []), start=1):
            meta = dict(item.get("meta") or {})
            meta["desktop_kind"] = str(item.get("role", "")).split(":", 1)[0]
            handle = self._encode_handle(item, meta)
            merged = {**item, "node": handle, "index": str(position), "meta": meta}
            renumbered.append(merged)
        observation["actions"] = renumbered
        observation["snapshot_id"] = time.strftime("%Y%m%d%H%M%S")
        return observation

    @staticmethod
    def _encode_handle(item: Dict[str, Any], meta: Dict[str, Any]) -> str:
        """Encode how to dispatch this row, in the handle string.

        The element table keeps only a few meta keys, so the dispatch information has
        to ride in the one field that survives: `node`. Format `lcu:<rung>:<payload>`:

        * `lcu:app:<pid>`            focus a running app
        * `lcu:open:<bundle id>`     launch an installed app
        * `lcu:win:<pid>:<window>`   focus one exact window
        * `lcu:menu:["File","New"]`  invoke an app-menu path

        Anything without the prefix is a window element token and is pressed through
        the AX rung, exactly as `driver.py` does it.
        """
        rung = str(meta.get("desktop_action", "") or "")
        if rung == "SWITCH_APP" and meta.get("pid") is not None:
            return f"lcu:app:{int(meta['pid'])}"
        if rung == "OPEN_APP" and meta.get("bundle_id"):
            return f"lcu:open:{meta['bundle_id']}"
        if rung == "FOCUS_WINDOW" and meta.get("window_id") is not None:
            return f"lcu:win:{int(meta.get('pid', 0))}:{int(meta['window_id'])}"
        if rung == "MENU" and meta.get("menu"):
            # Carry the owning pid so a menu can be invoked without depending on the
            # driver's current focus (measured: a bare path call fails with "no app").
            pid = meta.get("pid")
            prefix = f"{int(pid)}:" if pid is not None else ""
            return "lcu:menu:" + prefix + json.dumps(meta["menu"], ensure_ascii=False)
        token = meta.get("element_token") or ""
        return str(token or item.get("node", ""))

    def _absorb(self, observation: Dict[str, Any]) -> None:
        self._by_index = {str(item.get("index")): item
                          for item in observation.get("actions", [])}
        self._signature = _signature_of(observation)

    def snapshot_view(self) -> List[Dict[str, str]]:
        """What the model can currently choose among. For logs and tests."""
        return [{"index": index, "label": str(item.get("label", ""))[:70],
                 "role": str(item.get("role", ""))}
                for index, item in sorted(self._by_index.items(), key=lambda kv: int(kv[0]))]

    # ── execute ──────────────────────────────────────────────────────────────

    def execute(self, operation: str, element: Optional[Any], text: Optional[str] = None) -> Dict[str, Any]:
        if self._closed:
            return {"ok": False, "detail": "driver closed"}

        handle = str(getattr(element, "handle", "") or "") if element is not None else ""
        label = getattr(element, "label", "") if element is not None else ""

        try:
            if handle.startswith("lcu:"):
                return self._execute_desktop_handle(operation, handle, label, text)
            # No desktop handle: a window element or an operation without a target.
            if operation == "CLICK":
                return self._click_window(element, handle, label)
            if operation == "TYPE_TEXT":
                if not text:
                    return {"ok": False, "detail": "TYPE_TEXT with no text"}
                return self._type(element, text, label)
            if operation in ("SCROLL_DOWN", "SCROLL_UP"):
                return self._scroll(operation)
            if operation == "WAIT":
                time.sleep(0.4)
                return {"ok": True, "detail": "waited", "page_changed": None}
            return {"ok": False, "detail": f"unsupported desktop operation {operation!r}"}
        except Exception as error:  # a driver failure is not the model's fault
            return {"ok": False, "detail": f"driver error: {error}"}

    def _execute_desktop_handle(self, operation: str, handle: str, label: str,
                                text: Optional[str]) -> Dict[str, Any]:
        """Dispatch on the encoded handle. CLICK and the explicit rungs are aliases.

        The model answers with an operation + a target index; for desktop rows the
        operation will be CLICK (that is how they are described). SWITCH_APP/OPEN_APP/
        FOCUS_WINDOW/MENU remain executable directly so a future prompt can name them
        explicitly, and so the tests can drive each rung in isolation.
        """
        rung, _, payload = handle.partition(":")[2].partition(":")
        if rung == "app":
            if operation not in ("CLICK", "SWITCH_APP"):
                return {"ok": False, "detail": f"{operation} is not valid on an app row"}
            return self._switch_app(payload, label)
        if rung == "open":
            if operation not in ("CLICK", "OPEN_APP"):
                return {"ok": False, "detail": f"{operation} is not valid on an app row"}
            return self._open_app(payload, label)
        if rung == "win":
            if operation not in ("CLICK", "FOCUS_WINDOW"):
                return {"ok": False, "detail": f"{operation} is not valid on a window row"}
            pid_text, _, window_text = payload.partition(":")
            return self._focus_window(int(window_text), int(pid_text or 0), label)
        if rung == "menu":
            if operation not in ("CLICK", "MENU"):
                return {"ok": False, "detail": f"{operation} is not valid on a menu row"}
            # Two shapes exist: with an owning pid (`<pid>:<json>`) and without
            # (`<json>`). Split only when the head really is a pid, so a JSON path
            # whose first label contains a colon is not mangled.
            pid: Optional[int] = None
            path_text = payload
            head, sep, rest = payload.partition(":")
            if sep and head.isdigit():
                pid, path_text = int(head), rest
            try:
                path = json.loads(path_text)
            except json.JSONDecodeError:
                return {"ok": False, "detail": "menu handle carries an unreadable path"}
            return self._menu(path, label, pid=pid)
        if rung == "key":
            return self._key(payload)
        return {"ok": False, "detail": f"unknown desktop handle {handle[:40]!r}"}

    # ── the rungs ────────────────────────────────────────────────────────────

    def _call(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        binary = shutil.which(self.driver_bin) or self.driver_bin
        proc = subprocess.run([binary, "call", tool, json.dumps(payload)],
                              capture_output=True, text=True, timeout=self.timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"{tool} failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
        text = proc.stdout.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            if start < 0:
                raise RuntimeError(f"{tool} returned no JSON: {text[:200]}") from None
            return json.loads(text[start:])

    def _click_window(self, element: Optional[Any], handle: str, label: str) -> Dict[str, Any]:
        """A window element: press it through the bound window's AX rung."""
        if self._window is None:
            return {"ok": False, "detail": "no window bound; focus a window first"}
        if not handle:
            return {"ok": False, "detail": "no element handle for CLICK"}
        self._call("click", {"pid": self._window.pid, "window_id": self._window.window_id,
                             "element_token": str(handle)})
        return self._changed(label)

    def _type(self, element: Optional[Any], text: str, label: str) -> Dict[str, Any]:
        if self._window is None:
            return {"ok": False, "detail": "no window bound for TYPE_TEXT"}
        handle = str(getattr(element, "handle", "") or "") if element is not None else ""
        # Text only lands in FOREGROUND delivery: the background mode's synthetic
        # events reported ok while the document stayed empty (measured — a marker
        # typed in background mode never appeared in the AXTextArea value).
        self._call("bring_to_front", {"pid": self._window.pid,
                                      "window_id": self._window.window_id})
        time.sleep(self.settle_ms / 1000 or 0.3)
        payload: Dict[str, Any] = {"pid": self._window.pid, "text": text,
                                   "delivery_mode": "foreground"}
        # An exact window + element token makes delivery deterministic. With a bare
        # pid the call only returns a candidate list and types NOTHING (measured —
        # same silent failure family as bring_to_front/invoke_menu/press_key).
        payload["window_id"] = self._window.window_id
        if handle and not handle.startswith("lcu:"):
            payload["element_token"] = handle
        # A foreground type can come back `type_text_incomplete` with
        # delivered_chars=0 while marking itself retryable (measured — it happens
        # when the focus raise has not fully settled). Retry once or twice before
        # giving up; each retry re-raises the window first.
        last: Optional[Dict[str, Any]] = None
        for attempt in range(3):
            last = self._call("type_text", payload)
            delivered = (last.get("delivery") or {}).get("delivered_count",
                                                         last.get("delivered_chars"))
            if last.get("code") != "type_text_incomplete" and delivered != 0:
                break
            if attempt < 2:
                time.sleep(0.5)
                self._call("bring_to_front", {"pid": self._window.pid,
                                              "window_id": self._window.window_id})
                time.sleep(0.4)
        return self._changed(label)

    def _scroll(self, operation: str) -> Dict[str, Any]:
        # `scroll` needs an exact window: called with a bare pid it only returns
        # candidates and scrolls nothing (same measured failure mode as
        # bring_to_front/invoke_menu). Bind the window when we have one.
        payload: Dict[str, Any] = {"pid": self._focused_pid or 0,
                                   "direction": "down" if operation == "SCROLL_DOWN" else "up",
                                   "by": "page", "amount": 1}
        if self._window is not None:
            payload["pid"] = self._window.pid
            payload["window_id"] = self._window.window_id
        self._call("scroll", payload)
        return self._changed(operation)

    def _menu(self, path: Any, label: str, *, pid: Optional[int] = None) -> Dict[str, Any]:
        if not path:
            return {"ok": False, "detail": "menu item without a resolved path"}
        target_pid = pid if pid is not None else self._focused_pid
        if target_pid is None and self._window is not None:
            target_pid = self._window.pid
        if target_pid is None:
            return {"ok": False, "detail": "no app to invoke a menu on"}
        payload: Dict[str, Any] = {"pid": target_pid, "path": [str(segment) for segment in path]}
        # invoke_menu requires a window_id as well — called with only a pid it replies
        # `invalid_arguments` and does nothing (measured). Find the app's frontmost
        # on-screen window when the caller did not bind one.
        window_id = self._window.window_id if self._window is not None and self._window.pid == target_pid else None
        if window_id is None:
            windows = [w for w in self.observer.list_windows() if w.get("pid") == target_pid]
            if windows:
                window_id = int(max(windows, key=lambda w: w.get("z_index", 0))["window_id"])
        if window_id is not None:
            payload["window_id"] = window_id
        result = self._call("invoke_menu", payload)
        return {"ok": True, "detail": f"menu {' > '.join(str(s) for s in path)}",
                "page_changed": None, "driver": result}

    def _key(self, combo: str) -> Dict[str, Any]:
        parts = [part.strip().lower() for part in str(combo).replace("+", " ").split() if part.strip()]
        if not parts:
            return {"ok": False, "detail": "KEY with no key"}
        key = parts[-1]
        modifiers = [part for part in parts[:-1] if part in ("cmd", "shift", "option", "alt", "ctrl", "fn")]
        if self._window is not None:
            self._call("bring_to_front", {"pid": self._window.pid,
                                          "window_id": self._window.window_id})
            time.sleep(self.settle_ms / 1000 or 0.3)
        payload: Dict[str, Any] = {"pid": self._focused_pid or 0, "key": key,
                                   "delivery_mode": "foreground"}
        if modifiers:
            payload["modifiers"] = ["option" if m == "alt" else m for m in modifiers]
        # An exact window makes the delivery deterministic: with a bare pid (and more
        # than one eligible window on that pid) the driver refuses with
        # `same_pid_keyboard_ambiguity`, and a foreground keystroke would land on
        # whatever the OS has focused instead.
        if self._window is not None:
            payload["pid"] = self._window.pid
            payload["window_id"] = self._window.window_id
        self._call("press_key", payload)
        # cua-driver 0.28.2 bug (measured): a MODIFIED keystroke reports
        # `confirmed` but does not reach the app — cmd+a left the document
        # untouched while the same keystroke through System Events selected all.
        # Plain keys work. So fall back to osascript `keystroke` whenever
        # modifiers are involved, and verify nothing was claimed by the broken path.
        if modifiers:
            if self._keystroke_fallback(key, modifiers):
                return self._changed(f"key {combo} (osascript)")
        return self._changed(f"key {combo}")

    def _keystroke_fallback(self, key: str, modifiers: list[str]) -> bool:
        """Send a modified keystroke through System Events (the reliable path)."""
        import shutil as _shutil

        osascript = _shutil.which("osascript")
        if osascript is None:
            return False
        name_map = {"cmd": "command down", "ctrl": "control down",
                    "option": "option down", "alt": "option down", "shift": "shift down",
                    "fn": "function down"}
        mod_text = ", ".join(name_map[m] for m in modifiers if m in name_map)
        key_text = {"return": "return", "escape": "escape", "tab": "tab",
                    "space": "space", "delete": "delete"}.get(key, key)
        script = f'tell application "System Events" to keystroke "{key_text}"'
        if mod_text:
            script += f" using {{{mod_text}}}"
        proc = subprocess.run([osascript, "-e", script],
                              capture_output=True, text=True, timeout=15)
        return proc.returncode == 0

    def _switch_app(self, pid_text: str, label: str) -> Dict[str, Any]:
        try:
            pid = int(pid_text)
        except ValueError:
            return {"ok": False, "detail": "app row without a numeric pid"}
        before = self._window_titles()
        # `bring_to_front` needs an exact window_id to actually activate: called with a
        # bare pid it only returns candidates and leaves the front app unchanged
        # (measured — it reported success while the OS still had the prior app
        # frontmost). Pick the app's most recent on-screen window and pass it.
        windows = [w for w in self.observer.list_windows() if w.get("pid") == pid]
        payload: Dict[str, Any] = {"pid": pid}
        chosen: Optional[Dict[str, Any]] = None
        if windows:
            chosen = max(windows, key=lambda w: w.get("z_index", 0))
            payload["window_id"] = int(chosen["window_id"])
        result = self._call("bring_to_front", payload)
        after = self._window_titles()
        # Verify rather than trust: the OS says which app is frontmost.
        active_pid = None
        try:
            apps = self.observer.list_apps()
            active_pid = next((app.get("pid") for app in apps if app.get("active")), None)
        except Exception:
            pass
        activated = result.get("activated") if isinstance(result, dict) else None
        if chosen is not None:
            self.focus(window_id=int(chosen["window_id"]), pid=pid)
        self._focused_pid = pid
        verified = active_pid == pid or (activated is True and not windows)
        return {"ok": bool(verified), "detail": f"switched to {label}",
                "page_changed": before != after,
                "verified_active_pid": active_pid,
                "focus": {"pid": pid, "window_id": chosen.get("window_id") if chosen else None}}

    def _open_app(self, bundle: str, label: str) -> Dict[str, Any]:
        if not bundle:
            return {"ok": False, "detail": "app row without a bundle id"}
        before = self._window_titles()
        result = self._call("launch_app", {"bundle_id": bundle})
        # Give the app a beat to create its window; re-poll rather than assume.
        deadline = time.time() + 6.0
        new_window = None
        while time.time() < deadline:
            after = self._window_titles()
            if after != before:
                new_window = next((title for title in after if title not in before), None)
                break
            time.sleep(0.4)
        launched_pid = result.get("pid") or ((result.get("windows") or [{}])[0].get("pid"))
        if launched_pid:
            self._focused_pid = int(launched_pid)
            self._adopt_focus(pid=int(launched_pid))
        return {"ok": True, "detail": f"launched {label}", "page_changed": new_window is not None,
                "launch": {"pid": launched_pid, "window": new_window}}

    def _focus_window(self, window_id: int, pid: int, label: str) -> Dict[str, Any]:
        if not window_id or not pid:
            return {"ok": False, "detail": "window row without an id"}
        before = self._window_titles()
        result = self._call("bring_to_front", {"pid": pid, "window_id": window_id})
        after = self._window_titles()
        self.focus(window_id=window_id, pid=pid)
        self._focused_pid = pid
        activated = bool(result.get("activated")) if isinstance(result, dict) else False
        return {"ok": activated or before != after, "detail": f"focused {label}",
                "page_changed": before != after,
                "focus": {"pid": pid, "window_id": window_id}}

    # ── helpers ──────────────────────────────────────────────────────────────

    def _adopt_focus(self, *, pid: int) -> None:
        """After switching apps, bind that app's most recent window, if any."""
        try:
            windows = [w for w in self.observer.list_windows() if w.get("pid") == pid]
        except Exception:
            return
        if windows:
            chosen = max(windows, key=lambda w: w.get("z_index", 0))
            self.focus(window_id=int(chosen["window_id"]), pid=pid)

    def _window_titles(self) -> List[str]:
        try:
            return sorted(f"{w.get('pid')}:{w.get('window_id')}:{w.get('title', '')}"
                          for w in self.observer.list_windows())
        except Exception:
            return []

    def _changed(self, label: str) -> Dict[str, Any]:
        """Re-read the bound window and report whether its signature moved."""
        if self.settle_ms:
            time.sleep(self.settle_ms / 1000)
        changed: Optional[bool] = None
        try:
            fresh = self._focus_observation()
            changed = _signature_of(fresh) != self._signature
            self._cached = None  # the next observe() rebuilds with the desktop merge
        except Exception:
            changed = None
        return {"ok": True, "detail": f"ok {label}".strip(), "page_changed": changed}

    def close(self) -> None:
        self._closed = True
        if self._window is not None:
            self._window.close()

    def __enter__(self) -> "DesktopDriver":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
