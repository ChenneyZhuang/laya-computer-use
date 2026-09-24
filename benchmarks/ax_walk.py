"""What does one observation cost, and how much of it is OS chrome?

The AX walk is the other half of per-step latency (the decision is the first half),
and the menu bar dominates the raw element count — it is the same menu in every app.
This script measures both on a live window.

    python3 -m benchmarks.ax_walk <pid> <window_id> [repeats]

Result: benchmarks/results/ax_walk.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from laya_computer_use.ax import AXObserver, CHROME_SUBTREES

RESULTS = ROOT / "benchmarks" / "results"


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    pid, window_id = int(sys.argv[1]), int(sys.argv[2])
    repeats = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    observer = AXObserver(pid=pid, window_id=window_id, max_elements=40)
    times = []
    observation = None
    for _ in range(repeats):
        started = time.time()
        observation = observer.observe()
        times.append(int((time.time() - started) * 1000))
    assert observation is not None

    raw = observer.snapshot()
    elements = raw.get("elements", [])
    by_index = {element["element_index"]: element for element in elements
                if "element_index" in element}
    chrome = 0
    for element in elements:
        node, chain = by_index.get(element.get("element_index")), []
        while node is not None:
            chain.append(node.get("role", ""))
            parent = node.get("parent_index")
            node = by_index.get(parent) if parent is not None else None
        if any(role in CHROME_SUBTREES for role in chain[1:]):
            chrome += 1

    out = {
        "pid": pid, "window_id": window_id, "title": observation.get("title"),
        "ax_walk_ms": times, "ax_walk_median_ms": statistics.median(times),
        "actionable_elements": len(observation.get("actions", [])),
        "raw_elements": len(elements), "menu_chrome_elements": chrome,
    }
    (RESULTS / "ax_walk.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"AX walk: {out['ax_walk_median_ms']:.0f} ms median over {repeats} runs "
          f"({times})")
    print(f"actionable: {out['actionable_elements']} | "
          f"raw: {out['raw_elements']} | inside menu subtrees: {chrome}")
    print("wrote benchmarks/results/ax_walk.json")


if __name__ == "__main__":
    main()
