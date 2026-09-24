"""How does decision latency scale with the number of offered controls?

The lever this measures is the observation size: a decision hands the model one
question per operation, each listing every control that can perform it. More controls
means more options, and options share a fixed token budget, so both latency and
accuracy are expected to move with it.

    python3 -m benchmarks.latency_scaling

Result: benchmarks/results/latency_scaling.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    import localdecide
except ImportError:  # pragma: no cover - dev checkout only
    for _candidate in (ROOT.parent / "localdecide", Path("/Volumes/SSD/localdecide")):
        if (_candidate / "localdecide").is_dir():
            sys.path.insert(0, str(_candidate))
            break

RESULTS = ROOT / "benchmarks" / "results"

SIZES = [5, 10, 20, 40]
REPEATS = 3
GOAL = "clear the display"


def synthetic_tree(count: int) -> dict:
    labels = ["7", "8", "9", "4", "5", "6", "1", "2", "3", "0", "点", "等于",
              "加", "减", "乘", "除", "百分比", "更改正负号", "删除", "全部清除",
              "显示边栏", "模式", "mc", "m+", "m-", "mr", "x²", "x³", "xʸ", "eˣ"]
    labels = (labels * ((count // len(labels)) + 1))[:count]
    return {"url": "", "title": "Synthetic", "text": "", "actions": [
        {"kind": "click", "node": f"b{i}", "label": label, "role": "button"}
        for i, label in enumerate(labels)
    ]}


def main() -> None:
    from localdecide.decider import Decider
    from localdecide.page import build_element_table, table_to_questions

    decider = Decider(model="convaiinnovations/laya", subfolder=None)
    out = {}
    print(f"official English checkpoint — {REPEATS} repeats per size, median reported\n")
    print(f"{'elements':>9s} {'median ms':>10s}")
    for size in SIZES:
        tree = synthetic_tree(size)
        table = build_element_table(tree)
        questions = table_to_questions(table, GOAL)
        samples = []
        for _ in range(REPEATS):
            started = time.time()
            decision = decider.decide(table.state(), questions)
            samples.append(int((time.time() - started) * 1000))
            if not decision.ok:
                samples[-1] = -1
        median = statistics.median(samples)
        out[size] = {"samples_ms": samples, "median_ms": median}
        print(f"{size:>9d} {median:>10.0f}")
    (RESULTS / "latency_scaling.json").write_text(
        json.dumps({"goal": GOAL, "repeats": REPEATS, "results": out},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nwrote benchmarks/results/latency_scaling.json")


if __name__ == "__main__":
    main()
