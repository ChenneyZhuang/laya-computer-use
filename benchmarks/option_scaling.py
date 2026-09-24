"""Where does the official Laya checkpoint stop working? Option-count gradient.

The 12-case battery scored the official checkpoints 0/12 on a 22-element Calculator
tree, yet a 1-element TextEdit tree decided correctly. That gap points at the option
count, not at the task: this script varies ONLY the number of offered options and
holds the planted target fixed, so the failure point is visible.

    python3 -m benchmarks.option_scaling
"""

from __future__ import annotations

import json
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

CHECKPOINTS = {
    "laya-en": {"model": "convaiinnovations/laya", "subfolder": None},
    "laya-multilingual": {"model": "convaiinnovations/laya", "subfolder": "multilingual"},
    "laya-typed-decisions": {"model": "convaiinnovations/laya", "subfolder": "typed-decisions"},
}

# Real Calculator buttons; the test keeps the first N and plants the target inside.
BUTTONS = ["7", "8", "9", "4", "5", "6", "1", "2", "3", "0", "点", "等于",
           "加", "减", "乘", "除", "百分比", "更改正负号", "删除", "全部清除",
           "显示边栏", "模式"]
TARGET = "全部清除"
GOAL = "clear the display"


def build_observation(labels: list[str]) -> dict:
    return {"url": "", "title": "Calculator", "text": "", "actions": [
        {"kind": "click", "node": f"b{i}", "label": label, "role": "button"}
        for i, label in enumerate(labels)
    ]}


def main() -> None:
    from localdecide.decider import Decider
    from localdecide.page import build_element_table, table_to_questions

    sizes = [1, 2, 3, 5, 8, 12, 16, 22]
    out = {}
    print(f"goal: {GOAL!r}   target: {TARGET!r}   (target is present at every size)\n")
    print(f"{'checkpoint':20s} " + " ".join(f"{n:>4d}" for n in sizes))
    for name, spec in CHECKPOINTS.items():
        decider = Decider(model=spec["model"], subfolder=spec["subfolder"])
        row = {}
        line = []
        for size in sizes:
            labels = BUTTONS[:size]
            if TARGET not in labels:
                labels = labels[:-1] + [TARGET]
            table = build_element_table(build_observation(labels))
            questions = table_to_questions(table, GOAL)
            decision = decider.decide(table.state(), questions)
            if not decision.ok:
                line.append(" ERR")
                row[size] = {"hit": False, "error": decision.error}
                continue
            answers = decision.answers
            operation = answers.choice("operation")
            chosen = None
            question = f"{operation.lower()}_target"
            if question in answers.raw:
                element = table.by_index().get(answers.choice(question))
                chosen = element.label if element is not None else None
            hit = operation == "CLICK" and chosen == TARGET
            line.append("  ok" if hit else f"{chosen or operation[:4]:>4.4s}")
            row[size] = {"hit": hit, "operation": operation, "target": chosen,
                         "op_p": round(answers.confidence("operation"), 3)}
        out[name] = row
        print(f"{name:20s} " + " ".join(line))

    (RESULTS / "option_scaling.json").write_text(
        json.dumps({"goal": GOAL, "target": TARGET, "results": out},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nwrote benchmarks/results/option_scaling.json")


if __name__ == "__main__":
    main()
