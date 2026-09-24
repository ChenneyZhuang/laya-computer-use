"""Format fairness check: is 0/12 on control selection a layout artifact?

This harness can serve the element table two ways: `v3` — the modern layout where the
table lives ONLY in the option lists and the state carries the window title and text —
and `v1`, the original Jev-style layout where the table is also copied into the state.

Before reporting "official Laya scores 0/12 on desktop", check whether that zero is a
layout mismatch rather than a capability gap. Same cases, same checkpoints, both layouts.

    python3 -m benchmarks.format_fairness
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

FIXTURES = ROOT / "benchmarks" / "fixtures"
RESULTS = ROOT / "benchmarks" / "results"

CASES = [
    {"id": "calc-en-7",      "goal": "click the number 7",  "want_target": "7",        "want_op": "CLICK"},
    {"id": "calc-en-equals", "goal": "press equals",        "want_target": "等于",      "want_op": "CLICK"},
    {"id": "calc-en-clear",  "goal": "clear the display",   "want_target": "全部清除",  "want_op": "CLICK"},
    {"id": "calc-en-sidebar", "goal": "show the sidebar",   "want_target": "显示边栏",  "want_op": "CLICK"},
    {"id": "calc-zh-7",      "goal": "点击数字7",            "want_target": "7",        "want_op": "CLICK"},
    {"id": "calc-zh-equals", "goal": "按等于",              "want_target": "等于",      "want_op": "CLICK"},
    {"id": "calc-zh-clear",  "goal": "清空显示",            "want_target": "全部清除",  "want_op": "CLICK"},
    {"id": "calc-zh-sidebar", "goal": "显示侧边栏",          "want_target": "显示边栏",  "want_op": "CLICK"},
]

CHECKPOINTS = {
    "laya-en": {"model": "convaiinnovations/laya", "subfolder": None},
    "laya-multilingual": {"model": "convaiinnovations/laya", "subfolder": "multilingual"},
    "laya-typed-decisions": {"model": "convaiinnovations/laya", "subfolder": "typed-decisions"},
}


def run_layout(decider, tree: dict, layout: str) -> list[dict]:
    from localdecide.page import build_element_table, table_to_questions

    rows = []
    for case in CASES:
        table = build_element_table(tree)
        questions = table_to_questions(table, case["goal"])
        started = time.time()
        decision = decider.decide(table.state(layout=layout), questions)
        ms = int((time.time() - started) * 1000)
        row = {"id": case["id"], "layout": layout, "ms": ms}
        if not decision.ok:
            row.update({"error": decision.error, "hit": False})
            rows.append(row)
            continue
        answers = decision.answers
        operation = answers.choice("operation")
        chosen = None
        question = f"{operation.lower()}_target"
        if question in answers.raw:
            element = table.by_index().get(answers.choice(question))
            chosen = element.label if element is not None else None
        row.update({"operation": operation, "target": chosen,
                    "hit": operation == case["want_op"] and chosen == case["want_target"]})
        rows.append(row)
    return rows


def main() -> None:
    from localdecide.decider import Decider

    tree = json.loads((FIXTURES / "ax_calculator.json").read_text(encoding="utf-8"))
    out = {}
    print("Official Laya checkpoints, same 8 cases, two state layouts:\n")
    print(f"{'checkpoint':20s} {'layout':8s} {'hits':>7s}   sample decisions")
    for name, spec in CHECKPOINTS.items():
        decider = Decider(model=spec["model"], subfolder=spec["subfolder"])
        per_layout = {}
        for layout in ("v3", "v1"):
            rows = run_layout(decider, tree, layout)
            hits = sum(1 for row in rows if row.get("hit"))
            per_layout[layout] = {"hits": hits, "total": len(rows), "rows": rows}
            sample = ", ".join(f"{r['id'][5:]}→{r.get('target') or r.get('operation')}"
                               for r in rows[:4])
            print(f"{name:20s} {layout:8s} {hits:>3d}/{len(rows):<3d}  {sample}")
        out[name] = per_layout
        print()

    (RESULTS / "format_fairness.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote benchmarks/results/format_fairness.json")


if __name__ == "__main__":
    main()
