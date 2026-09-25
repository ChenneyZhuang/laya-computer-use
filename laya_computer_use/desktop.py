"""Whole-computer scope: the desktop itself becomes the observation surface.

The browser project answers "what is on this page". This module answers the question
one level up: **what is on this computer, and what can be done anywhere on it.**

## What "whole computer" means here

A single window is the easy case. The real surface is:

* **every running app** — switch to it, or read its windows
* **every installed app** — not just the running ones; launch any bundle by id,
  even one that is not open yet (that is what "including things this computer
  doesn't have running" means in practice: the launcher is part of the surface)
* **the menu bar** — 121 of a Calculator window's 147 elements are the OS menu, and
  every app carries one. Instead of treating that as noise (the window adapter
  filters it), the desktop scope turns it into a first-class capability: any menu
  item of the frontmost app is invocable by path via `invoke_menu`.
* **keyboard** — shortcuts that have no clickable representation
* **window management** — enumerate, focus, dismiss

## The two-level observation

`DesktopObserver` walks the OS level once (`get_accessibility_tree`: apps + windows)
and then adapts it into the same element-table contract the model already knows. The
model picks indices; the executor maps an index back to a *typed* action (app switch,
app launch, menu path, keystroke, window focus, or a plain AX element click in the
focused window).

Two observations are merged deliberately:

1. the **desktop table** — apps, windows, launcher entries
2. the **focused window table** — the same table `ax.py` builds

...so one decision can choose between "click Save in this window" and "switch to
Finder" without a special mode. The element indices are namespaced (`w12` for window
elements, `a3` for apps) so the executor always knows which surface an index belongs
to and the model never has to.

## Why this is not a screenshot agent

Nothing here locates anything by pixels. App switches are AX actions on the app
element, menu items are AX actions on a resolved menu path, keystrokes go through
the driver's key API, and window elements are AX presses. Coordinates appear only in
the fallback the driver itself documents, never in what the model chooses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ax import AXObserver

# Roles that describe an app or a window at the OS level.
APP_ROLE = "AXApplication"
WINDOW_ROLE = "AXWindow"

# Keys the executor accepts at the desktop level. Kept deliberately small: every key
# here is something the driver can send without a target element.
KEYS = frozenset({
    "return", "tab", "escape", "space", "delete", "home", "end",
    "pageup", "pagedown", "up", "down", "left", "right",
    *[f"f{n}" for n in range(1, 13)],
    *list("abcdefghijklmnopqrstuvwxyz"),
    *list("0123456789"),
})

# Desktop-level actions, as the model sees them.
DESKTOP_OPERATIONS = (
    "SWITCH_APP",   # focus a running app
    "OPEN_APP",     # launch an installed app that is not running
    "MENU",         # invoke a menu path on the frontmost app
    "KEY",          # press a key (combo) that has no clickable form
    "FOCUS_WINDOW", # raise one window of the current or another app
    "CLICK",        # click an element in the focused window (window table)
    "TYPE_TEXT",
    "WAIT",
    "DONE",
    "BLOCKED",
)


@dataclass
class DesktopApp:
    """One app in the desktop table: running or merely installed."""

    index: str          # namespaced, e.g. "a3"
    name: str
    bundle_id: str = ""
    pid: Optional[int] = None
    running: bool = False
    active: bool = False
    windows: List[Dict[str, Any]] = field(default_factory=list)

    def describe(self) -> str:
        flags = []
        if self.running:
            flags.append("running")
        else:
            flags.append("not running")
        if self.active:
            flags.append("frontmost")
        count = len(self.windows)
        if count:
            flags.append(f"{count} window{'s' if count != 1 else ''}")
        return f"{self.name} ({self.bundle_id or 'unknown bundle'}) [{', '.join(flags)}]"


@dataclass
class DesktopWindow:
    """One on-screen window, addressable by index."""

    index: str          # namespaced, e.g. "w214"
    window_id: int
    pid: int
    app_name: str
    title: str = ""
    bounds: Dict[str, float] = field(default_factory=dict)

    def describe(self) -> str:
        title = f' "{self.title}"' if self.title else ""
        return f"{self.app_name}{title} [window]"


class DesktopObserver:
    """Walk the OS surface through cua-driver, without a screenshot.

    `overview()` is the cheap call: apps + on-screen windows, no AX walk.
    `focused_tree()` is the expensive one: the AX tree of one window (the same walk
    `AXObserver` already does). The desktop table merges the two so a single decision
    can act at either level.
    """

    def __init__(self, driver_bin: str = "cua-driver", timeout: float = 60.0) -> None:
        self.driver_bin = driver_bin
        self.timeout = timeout

    def _call(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        binary = shutil.which(self.driver_bin) or self.driver_bin
        proc = subprocess.run(
            [binary, "call", tool, json.dumps(payload)],
            capture_output=True, text=True, timeout=self.timeout,
        )
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

    # ── the two observations ─────────────────────────────────────────────────

    def overview(self) -> Dict[str, Any]:
        """OS level: running apps, installed apps, on-screen windows."""
        apps_payload = self._call("list_apps", {})
        windows_payload = self._call("list_windows", {})
        return {"apps": apps_payload.get("apps", []),
                "windows": [w for w in windows_payload.get("windows", [])
                            if w.get("is_on_screen")]}

    def window_tree(self, pid: int, window_id: int) -> Dict[str, Any]:
        """One window's AX tree, already adapted to the table contract."""
        observer = AXObserver(pid=pid, window_id=window_id,
                              driver_bin=self.driver_bin, timeout=self.timeout)
        return observer.observe()

    def list_windows(self) -> List[Dict[str, Any]]:
        return [w for w in self._call("list_windows", {}).get("windows", [])
                if w.get("is_on_screen")]

    def list_apps(self) -> List[Dict[str, Any]]:
        return self._call("list_apps", {}).get("apps", [])


def build_desktop_observation(
    apps: List[Dict[str, Any]],
    windows: List[Dict[str, Any]],
    *,
    window_observation: Optional[Dict[str, Any]] = None,
    focused_pid: Optional[int] = None,
    include_installed: bool = True,
    max_apps: int = 30,
    max_windows: int = 12,
) -> Dict[str, Any]:
    """Merge OS-level apps/windows with one window's element table.

    Index namespaces keep the two surfaces apart in one option list:

    * `aN` — an app row (switch, or launch when not running)
    * `wN` — a window row (focus it)
    * plain `N` — an element inside the focused window (the existing contract)

    The model chooses from one list; the executor dispatches on the namespace.
    """
    actions: List[Dict[str, Any]] = []

    running = [a for a in apps if a.get("running")]
    installed = [a for a in apps if not a.get("running")] if include_installed else []

    # Running apps first (they are the likely targets), then closed ones, then windows.
    for position, app in enumerate(running[:max_apps], start=1):
        name = str(app.get("name", "") or "")
        if not name:
            continue
        actions.append({
            "kind": "click",
            "node": f"a{position}",
            "label": DesktopApp(index=f"a{position}", name=name,
                                bundle_id=str(app.get("bundle_id", "") or ""),
                                pid=app.get("pid"), running=True,
                                active=bool(app.get("active"))).describe(),
            "role": "app",
            "meta": {"desktop_action": "SWITCH_APP", "pid": app.get("pid"),
                     "bundle_id": app.get("bundle_id", "")},
        })

    for offset, app in enumerate(installed[:max_apps], start=1):
        name = str(app.get("name", "") or "")
        if not name:
            continue
        index = f"a{len(running) + offset}"
        actions.append({
            "kind": "click",
            "node": index,
            "label": DesktopApp(index=index, name=name,
                                bundle_id=str(app.get("bundle_id", "") or ""),
                                running=False).describe(),
            "role": "app",
            "meta": {"desktop_action": "OPEN_APP", "bundle_id": app.get("bundle_id", "")},
        })

    for position, window in enumerate(windows[:max_windows], start=1):
        window_id = window.get("window_id")
        actions.append({
            "kind": "click",
            "node": f"w{window_id}",
            "label": DesktopWindow(index=f"w{window_id}", window_id=int(window_id),
                                   pid=int(window.get("pid", 0)),
                                   app_name=str(window.get("app_name", "") or ""),
                                   title=str(window.get("title", "") or ""),
                                   bounds=dict(window.get("bounds") or {})).describe(),
            "role": "window",
            "meta": {"desktop_action": "FOCUS_WINDOW", "window_id": window_id,
                     "pid": window.get("pid")},
        })

    # Window elements come last: the OS rows exist so the model can *get to* a
    # window; once it is focused, its controls are the fine-grained options.
    if window_observation:
        for item in window_observation.get("actions", []):
            merged = dict(item)
            merged["role"] = f"element:{item.get('role', '')}"
            actions.append(merged)

    return {
        "url": "",
        "title": "Desktop",
        "text": "",
        "actions": actions,
        "history": list((window_observation or {}).get("history", [])),
        "focused_pid": focused_pid,
        "window_table": window_observation or {},
    }


def split_desktop_element(node: str) -> tuple[str, str]:
    """Which surface does this index belong to? ("app" | "window" | "element", id)."""
    text = str(node)
    if text.startswith("a") and text[1:].isdigit():
        return "app", text[1:]
    if text.startswith("w") and text[1:].isdigit():
        return "window", text[1:]
    return "element", text
