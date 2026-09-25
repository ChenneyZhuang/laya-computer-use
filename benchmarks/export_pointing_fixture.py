#!/usr/bin/env python3
"""Capture the pointing fixture on the Mac: a screenshot + every control's AX region.

This is the Mac half of the 4B run. The Windows box cannot see the Mac's AX tree, so
the window (Calculator by default) is captured here as:

* `image`    — a PNG of the window, cropped to its bounds
* `controls` — every labelled control with its box normalized to 0-1000
* `goals`    — the battery's questions, each naming the control it wants

Then `run_omnijev_4b.py` on the 3080 reads that JSON and scores the model.

    python3 -m benchmarks.export_pointing_fixture --out /tmp/pointing_fixture.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CROP = (620, 664, 1064, 1096)  # Calculator window incl. chrome, in screen points

GOALS = [
    ("Goal: press the number 7 button.", "7"),
    ("Goal: press the number 8 button.", "8"),
    ("Goal: press the number 9 button.", "9"),
    ("Goal: press the number 4 button.", "4"),
    ("Goal: press the number 0 button.", "0"),
    ("Goal: press the equals button.", "="),
    ("Goal: press the plus button.", "+"),
    ("Goal: press the minus button.", "-"),
    ("Goal: press the multiply button.", "*"),
    ("Goal: press the divide button.", "/"),
    ("Goal: clear the display (the all-clear button).", "AC"),
    ("Goal: delete the last digit (the backspace button).", "Backspace"),
]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/pointing_fixture.json")
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--window-id", type=int, default=None)
    args = parser.parse_args()

    from laya_computer_use.desktop_driver import DesktopDriver

    driver = DesktopDriver()
    apps = driver.observer.list_apps()
    app = next((a for a in apps if "计算器" in str(a.get("name", ""))
                or "Calculator" in str(a.get("name", ""))), None)
    if app is None:
        driver._call("launch_app", {"bundle_id": "com.apple.calculator"})
        time.sleep(2.0)
        apps = driver.observer.list_apps()
        app = next((a for a in apps if "计算器" in str(a.get("name", ""))), None)
    pid = args.pid or int(app["pid"])
    windows = [w for w in driver.observer.list_windows() if w.get("pid") == pid]
    window_id = args.window_id or int(windows[0]["window_id"])
    driver._call("bring_to_front", {"pid": pid, "window_id": window_id})
    time.sleep(1.2)

    # Screenshot the window with screencapture, cropped to its real bounds.
    full = subprocess.run(["screencapture", "-x", "/tmp/_fixture_full.png"],
                          capture_output=True, timeout=40)
    if full.returncode != 0:
        raise RuntimeError("screencapture failed")
    from PIL import Image

    image = Image.open("/tmp/_fixture_full.png").convert("RGB")
    # Use the AX window frame (the true window bounds), falling back to CROP.
    state = driver.observer._call("get_window_state", {
        "pid": pid, "window_id": window_id, "max_nodes": 8000})
    window_frame = next((e.get("frame") for e in state.get("elements", [])
                         if e.get("role") == "AXWindow" and e.get("frame")), None)
    if window_frame:
        box = (int(window_frame["x"]), int(window_frame["y"]),
               int(window_frame["x"] + window_frame["w"]),
               int(window_frame["y"] + window_frame["h"]))
    else:
        box = CROP
    cropped = image.crop(box)
    out_png = "/tmp/pointing_window.png"
    cropped.save(out_png)
    print(f"window crop {box} -> {cropped.size}")

    # Controls: labelled AX elements with frames, in region coordinates (0-1000
    # relative to the CROPPED image, which is what the model sees). The canonical
    # name is the localized label run through the same alias table the on-Mac
    # battery uses, so the English goals match regardless of this Mac's UI language.
    from benchmarks.omnijev_desktop import LABEL_ALIASES

    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    controls = []
    for element in state.get("elements", []):
        frame = element.get("frame")
        label = str(element.get("label") or "").strip()
        if not label or not frame or frame.get("w", 0) <= 0 or frame.get("h", 0) <= 0:
            continue
        canonical = LABEL_ALIASES.get(label)
        if canonical is None:
            continue  # not a goal-addressable control; skip rather than confuse
        cx1 = max(0, round((frame["x"] - x0) / width * 1000))
        cy1 = max(0, round((frame["y"] - y0) / height * 1000))
        cx2 = min(1000, round((frame["x"] + frame["w"] - x0) / width * 1000))
        cy2 = min(1000, round((frame["y"] + frame["h"] - y0) / height * 1000))
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        controls.append({"label": label, "canonical": canonical,
                         "region": {"box": [cx1, cy1, cx2, cy2]}})
    print(f"controls: {len(controls)}")
    for c in controls[:10]:
        print(f"   {c['label']:12s} {c['region']['box']}")

    payload = {"image": out_png, "controls": controls,
               "goals": [[g, t] for g, t in GOALS]}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")
    driver.close()


if __name__ == "__main__":
    main()
