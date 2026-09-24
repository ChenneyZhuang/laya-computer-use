"""Full-loop test on a real app: the model decides, the harness executes, we verify.

TextEdit is the fixture because its document is a real editable AX element, so the
test can assert the *result* (text in the document) and not merely that calls returned.

    python3 -m benchmarks.e2e_loop <pid> <window_id>
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Path resolution: installed dependency first, sibling checkout as fallback.
try:
    import localdecide
except ImportError:  # dev checkout: the browser project sits next to this repo
    import sys as _sys
    from pathlib import Path as _Path
    for _candidate in (_Path(__file__).resolve().parents[3] / "localdecide",
                       _Path("/Volumes/SSD/localdecide")):
        if (_candidate / "localdecide").is_dir():
            _sys.path.insert(0, str(_candidate))
            break


def read_document(pid: int, window_id: int) -> str:
    proc = subprocess.run(
        ["cua-driver", "call", "get_window_state",
         json.dumps({"pid": pid, "window_id": window_id, "include_screenshot": False})],
        capture_output=True, text=True, timeout=60)
    payload = json.loads(proc.stdout[proc.stdout.find("{"):])
    return "\n".join(str(element.get("value") or "")
                     for element in payload.get("elements", [])
                     if element.get("role") in ("AXTextArea", "AXTextField")
                     and element.get("value"))


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    pid, window_id = int(sys.argv[1]), int(sys.argv[2])
    model = sys.argv[3] if len(sys.argv) > 3 else "convaiinnovations/laya"
    subfolder = sys.argv[4] if len(sys.argv) > 4 else None
    from localdecide.decider import Decider
    decider = Decider(model=model, subfolder=subfolder)
    print(f"model: {model}" + (f"/{subfolder}" if subfolder else ""))

    from laya_computer_use.driver import CuaDriver, DesktopLoop

    marker = f"LOOP-{int(time.time())}"
    driver = CuaDriver(pid=pid, window_id=window_id, max_elements=40)

    before = read_document(pid, window_id)
    print(f"document before: {before[:120]!r}")

    loop = DesktopLoop(
        decider=decider,
        max_steps=6,
        text_provider=lambda goal, element: marker,
        min_confidence=0.15,
    )
    goal = "type the specified text into the document body"
    print(f"goal: {goal!r}  (text provider will supply {marker!r})")

    seen = []
    run = loop.run(driver, goal)
    for step in run.steps:
        seen.append({"n": step.n, "op": step.operation, "label": step.label[:40],
                     "conf": round(step.confidence, 3), "executed": step.executed,
                     "detail": step.detail[:70]})
        print(f"  {step.n}. {step.operation:<10} {step.label[:40]:<40} "
              f"p={step.confidence:.2f} executed={step.executed} {step.detail[:50]}")

    time.sleep(0.6)
    after = read_document(pid, window_id)
    print(f"\ndocument after: {after[:200]!r}")
    landed = marker in after
    # Two separate verdicts, because they fail separately:
    #   bridge      - did the AX observation → decision → execution path actually work?
    #   autonomous  - did the model stop itself (DONE) instead of looping until a guard fired?
    bridge = landed and any(step.executed for step in run.steps)
    print(f"\n=== bridge: {'PASS' if bridge else 'FAIL'} (text landed: {landed}) | "
          f"autonomous: {'PASS' if run.solved else 'NO'} "
          f"(stopped={run.stopped!r}, steps={len(run.steps)}) ===")
    (ROOT / "benchmarks" / "results" / "e2e_loop.json").write_text(json.dumps({
        "pid": pid, "window_id": window_id, "goal": goal, "marker": marker,
        "model": model, "subfolder": subfolder,
        "stopped": run.stopped, "solved": run.solved, "text_landed": landed,
        "bridge_pass": bridge,
        "document_before": before[:200], "document_after": after[:200],
        "steps": seen,
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if bridge else 3)


if __name__ == "__main__":
    main()
