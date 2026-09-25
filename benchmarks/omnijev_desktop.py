"""OmniJev on a real desktop: can a 0.8B omni-modal Jev point at the right control?

This is the apples-to-apples test for a *vision* decision model. The text batteries
(`model_arena.py`) hand the model a numbered text table; OmniJev was trained to look
at a screen and pick a **region**, so that is how it is asked here:

1. find a real window (Calculator by default) and screenshot the screen
2. read its AX tree — every labelled control with its exact `frame`
3. turn every control into a `region` option (its box, normalized to 0-1000)
4. ask OmniJev one question per goal: "which control should be pressed?"
5. score: did the chosen region's box match the target control's box?

Nothing about this leaks an advantage to the text models: it is the OPPOSITE
trade — the text models get labels without pixels, OmniJev gets pixels with every
control's box drawn from the same AX truth. Either way the answer is resolved
against real AX frames, so a "correct" answer is a correct choice of control.

Run (from the von venv, which has torch + transformers + peft):

    /Volumes/SSD/lcu-experiments/von-env/bin/python -m benchmarks.omnijev_desktop
    /Volumes/SSD/lcu-experiments/von-env/bin/python -m benchmarks.omnijev_desktop --goals 6

Writes `benchmarks/results/omnijev_desktop.json`.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
CKPT = "/Volumes/SSD/lcu-experiments/omnijev-ckpt"
BASE = "/Volumes/SSD/lcu-experiments/qwen-base"
MSO_SRC = "/tmp/omnijev-probe/OmniJev"

# Sizes to score: (name, checkpoint dir, backbone dir). The 4B row runs on the 3080
# (10 GB card, 9.3 GB backbone) — not on this laptop.
SIZES = {
    "0.8B": ("/Volumes/SSD/lcu-experiments/omnijev-ckpt",
             "/Volumes/SSD/lcu-experiments/qwen-base"),
    "2B": ("/Volumes/SSD/lcu-experiments/omnijev-2b-ckpt",
           "/Volumes/SSD/lcu-experiments/qwen-2b-base"),
}

# (goal, expected control label) — labels are the app's own, as they appear in AX.
GOALS: list[tuple[str, str]] = [
    ("Goal: press the number 7 button.", "7"),
    ("Goal: press the number 8 button.", "8"),
    ("Goal: press the number 0 button.", "0"),
    ("Goal: press the equals button.", "="),
    ("Goal: press the plus button.", "+"),
    ("Goal: press the minus button.", "-"),
    ("Goal: press the multiply button.", "*"),
    ("Goal: press the divide button.", "/"),
    ("Goal: clear the display (the all-clear button).", "AC"),
    ("Goal: delete the last digit (the backspace button).", "Backspace"),
    ("Goal: press the percent button.", "Percent"),
    ("Goal: change the sign of the number (plus/minus).", "+/-"),
]

# The macOS Calculator's AX labels are localized; map them to the English names the
# goals use, so the battery can be written in one language regardless of this Mac's UI.
# `清除` (this Mac's basic-mode label) and `全部清除` (the classic label) both map to AC.
LABEL_ALIASES = {
    "7": "7", "8": "8", "9": "9", "4": "4", "5": "5", "6": "6",
    "1": "1", "2": "2", "3": "3", "0": "0", "点": ".",
    "等于": "=", "加": "+", "减": "-", "乘": "*", "除": "/",
    "清除": "AC", "全部清除": "AC", "删除": "Backspace", "百分比": "Percent",
    "更改正负号": "+/-",
}

# Labels that are NOT one of the goals are still offered — they are real buttons,
# and leaving them out would silently shrink the option list.


def driver_call(tool: str, payload: dict) -> dict:
    import shutil

    binary = shutil.which("cua-driver") or "cua-driver"
    proc = subprocess.run([binary, "call", tool, json.dumps(payload)],
                          capture_output=True, text=True, timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"{tool}: {proc.stderr.strip()[:300]}")
    text = proc.stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(text[text.find("{"):])


def find_calculator() -> tuple[int, int]:
    apps = driver_call("list_apps", {}).get("apps", [])
    app = next((a for a in apps if "计算器" in str(a.get("name", "")) or "Calculator" in str(a.get("name", ""))), None)
    if app is None:
        driver_call("launch_app", {"bundle_id": "com.apple.calculator"})
        time.sleep(2.0)
        apps = driver_call("list_apps", {}).get("apps", [])
        app = next((a for a in apps if "计算器" in str(a.get("name", "")) or "Calculator" in str(a.get("name", ""))), None)
    if app is None:
        raise RuntimeError("Calculator is not available")
    pid = app.get("pid")
    windows = [w for w in driver_call("list_windows", {}).get("windows", [])
               if w.get("pid") == pid and w.get("is_on_screen")]
    if not windows:
        raise RuntimeError("Calculator has no on-screen window")
    window = windows[0]
    driver_call("bring_to_front", {"pid": pid, "window_id": int(window["window_id"])})
    time.sleep(1.0)
    return int(pid), int(window["window_id"])


def collect_controls(pid: int, window_id: int) -> tuple[list[dict], tuple[int, int]]:
    """Every labelled AX control with a usable frame, plus the screen size."""
    state = driver_call("get_window_state", {"pid": pid, "window_id": window_id,
                                             "max_nodes": 8000})
    controls = []
    for element in state.get("elements", []):
        frame = element.get("frame")
        label = str(element.get("label") or "").strip()
        if not label or not frame or frame.get("w", 0) <= 0 or frame.get("h", 0) <= 0:
            continue
        english = LABEL_ALIASES.get(label)
        if english is None:
            continue
        controls.append({"label": label, "canonical": english,
                         "frame": frame, "role": element.get("role", ""),
                         "token": element.get("element_token", "")})
    screen = subprocess.run(["screencapture", "-x", "/tmp/omnijev-probe/desktop.png"],
                            capture_output=True, timeout=30)
    if screen.returncode != 0:
        raise RuntimeError("screencapture failed")
    return controls, (2560, 1440)


def to_region(frame: dict, screen: tuple[int, int]) -> dict:
    """AX frame (global screen points) -> OmniJev region box (0-1000)."""
    sw, sh = screen
    x1 = max(0, round(frame["x"] / sw * 1000))
    y1 = max(0, round(frame["y"] / sh * 1000))
    x2 = min(1000, round((frame["x"] + frame["w"]) / sw * 1000))
    y2 = min(1000, round((frame["y"] + frame["h"]) / sh * 1000))
    return {"box": [x1, y1, x2, y2]}


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, MSO_SRC)
    limit = 12
    if "--goals" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--goals") + 1])
    size = "0.8B"
    if "--size" in sys.argv:
        size = sys.argv[sys.argv.index("--size") + 1]
    if size not in SIZES:
        raise SystemExit(f"unknown size {size!r}; have {sorted(SIZES)}")
    ckpt, base = SIZES[size]

    pid, window_id = find_calculator()
    controls, screen = collect_controls(pid, window_id)
    print(f"controls with frames: {len(controls)}", flush=True)
    for c in controls[:8]:
        print(f"   {c['canonical']:10s} {c['frame']}", flush=True)

    # Options: every control, as a region box. The model picks one; the label it maps
    # to is what gets scored. Order is shuffled once so slot position is not a hint.
    import random

    options = [{"key": c["canonical"], "region": to_region(c["frame"], screen)}
               for c in controls]
    random.Random(20260925).shuffle(options)
    # guarantee the goal targets are present
    present = {o["key"] for o in options}
    for goal_target in [g[1] for g in GOALS]:
        if goal_target not in present:
            print(f"   (target {goal_target!r} not offered — its control has no frame)", flush=True)

    from mso.infer import MSO1

    started = time.time()
    model = MSO1(ckpt, base)
    print(f"model {size} loaded in {time.time() - started:.1f}s", flush=True)

    rows = []
    for goal, target in GOALS[:limit]:
        question = {"type": "choice", "instructions": goal,
                    "options": [{"key": o["key"], "text": o["key"],
                                 "region": o["region"]} for o in options]}
        t0 = time.time()
        try:
            answer = model.system_one({"images": ["/tmp/omnijev-probe/desktop.png"]},
                                      {"which": question})["which"]
        except Exception as error:
            rows.append({"goal": goal, "want": target, "hit": False,
                         "error": f"{type(error).__name__}: {error}"[:160]})
            print(f"{goal[:44]:46s} -> ERROR {error}"[:120], flush=True)
            continue
        chosen = str(answer.get("choice"))
        top = sorted(answer.get("probabilities", {}).items(),
                     key=lambda kv: -kv[1])[:3]
        hit = chosen == target
        rows.append({"goal": goal, "want": target, "chosen": chosen, "hit": hit,
                     "confidence": answer.get("confidence"),
                     "top3": [(k, round(v, 3)) for k, v in top],
                     "ms": int((time.time() - t0) * 1000)})
        print(f"{goal[:44]:46s} -> {chosen:10s} {'HIT' if hit else 'miss':4s} "
              f"p={answer.get('confidence', 0):.2f}  want={target}", flush=True)

    hits = sum(1 for r in rows if r.get("hit"))
    summary = {"model": f"OmniJev-{size}", "backend": "screenshot + AX region boxes",
               "controls_offered": len(options), "hits": hits, "cases": len(rows),
               "accuracy": round(hits / len(rows), 3) if rows else 0.0,
               "median_ms": sorted(r.get("ms", 0) for r in rows)[len(rows) // 2] if rows else 0,
               "rows": rows}
    print()
    print(f"OmniJev-{size} region pointing: {hits}/{len(rows)} = {summary['accuracy']:.0%}")
    # Merge per size so the 0.8B and 2B (and a future 4B) rows accumulate.
    path = RESULTS / "omnijev_desktop.json"
    out: dict = {}
    if path.exists():
        try:
            out = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out = {}
    out[f"OmniJev-{size}"] = summary
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
