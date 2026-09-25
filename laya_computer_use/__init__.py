"""laya-computer-use: desktop decisions with Laya, the open System One model.

Same contract as the browser project: the model picks an index from a numbered
table of observed controls; your code resolves that index to a real element and
performs the action. The model never sees coordinates, never emits actions, and
cannot hallucinate a click.

The only new surface is the observation source: a macOS Accessibility (AX) tree
from `cua-driver` instead of a DOM snapshot.

Two scopes ship:

* **window** — one window's controls (`AXObserver`, `CuaDriver`, `DesktopLoop`).
* **desktop** — the whole computer: every running app, every installed app (launch
  it), every on-screen window (focus it, cross-app), and the app menu bar as
  invocable paths (`DesktopObserver`, `DesktopDriver`).

The decision layer runs the **official Laya checkpoints** (`convaiinnovations/laya`)
by default — see `laya_computer_use.models` for the registry and how to point it at
another checkpoint or a locally trained one. Other System One models plug in through
the same `Decider` (see `benchmarks/arena_backends.py` for the Jev and von backends
used in the model comparison).
"""

from .ax import AXObserver, ax_to_observation, menu_path
from .desktop import DesktopObserver, DesktopWindow, DesktopApp, build_desktop_observation
from .desktop_driver import DesktopDriver
from .driver import CuaDriver, DesktopLoop
from .models import DEFAULT, HUB, OFFICIAL, describe, resolve

__all__ = [
    "AXObserver", "ax_to_observation", "menu_path",
    "CuaDriver", "DesktopLoop",
    "DesktopObserver", "DesktopDriver", "build_desktop_observation",
    "DesktopApp", "DesktopWindow",
    "HUB", "OFFICIAL", "DEFAULT", "resolve", "describe",
]
