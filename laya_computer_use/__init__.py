"""laya-computer-use: desktop decisions with Laya, the open System One model.

Same contract as the browser project: the model picks an index from a numbered
table of observed controls; your code resolves that index to a real element and
performs the action. The model never sees coordinates, never emits actions, and
cannot hallucinate a click.

The only new surface is the observation source: a macOS Accessibility (AX) tree
from `cua-driver` instead of a DOM snapshot.

The decision layer runs the **official Laya checkpoints** (`convaiinnovations/laya`)
by default — see `laya_computer_use.models` for the registry and how to point it at
another checkpoint or a locally trained one.
"""

from .ax import AXObserver, ax_to_observation
from .driver import CuaDriver, DesktopLoop
from .models import DEFAULT, HUB, OFFICIAL, describe, resolve

__all__ = [
    "AXObserver", "ax_to_observation", "CuaDriver", "DesktopLoop",
    "HUB", "OFFICIAL", "DEFAULT", "resolve", "describe",
]
