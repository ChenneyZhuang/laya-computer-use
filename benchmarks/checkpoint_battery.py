"""Checkpoint battery: how do the official Laya checkpoints do on desktop AX trees?

Runs the official `convaiinnovations/laya` checkpoints (zero-shot — none of them was
trained on desktop AX trees or on this task format) against a real macOS AX tree.

One checkpoint resident at a time (16 GB M4) — each runs in its own subprocess.

    python3 -m benchmarks.checkpoint_battery                  # all checkpoints
    python3 -m benchmarks.checkpoint_battery laya-en          # one

Results land in benchmarks/results/<checkpoint>.json, plus _summary.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
FIXTURES = ROOT / "benchmarks" / "fixtures"

# Path resolution: the browser project is an installed dependency normally; in a dev
# checkout it sits next to this repo. Resolve at import time so every entry point works.
try:
    import localdecide
except ImportError:  # pragma: no cover - dev checkout only
    for _candidate in (ROOT.parent / "localdecide", Path("/Volumes/SSD/localdecide")):
        if (_candidate / "localdecide").is_dir():
            sys.path.insert(0, str(_candidate))
            break

# Official Laya checkpoints only. `convaiinnovations/laya` is the hub for the family:
# the repo root is the English checkpoint; `multilingual` and `typed-decisions` are
# bundled subfolders. None of them was trained to pick a control index.
CHECKPOINTS: dict[str, dict] = {
    "laya-en": {"model": "convaiinnovations/laya", "subfolder": None},
    "laya-multilingual": {"model": "convaiinnovations/laya", "subfolder": "multilingual"},
    "laya-typed-decisions": {"model": "convaiinnovations/laya", "subfolder": "typed-decisions"},
}


def calc_cases(tree: dict) -> list[dict]:
    """Real Calculator AX tree. Same targets, goals in both languages — this is how
    'AX semantics' and 'language' get separated as failure causes."""
    return [
        {"id": "calc-en-7",       "obs": tree, "goal": "click the number 7",   "want_target": "7",       "want_op": "CLICK"},
        {"id": "calc-en-equals",  "obs": tree, "goal": "press equals",         "want_target": "等于",     "want_op": "CLICK"},
        {"id": "calc-en-clear",   "obs": tree, "goal": "clear the display",    "want_target": "全部清除", "want_op": "CLICK"},
        {"id": "calc-en-sidebar", "obs": tree, "goal": "show the sidebar",     "want_target": "显示边栏", "want_op": "CLICK"},
        {"id": "calc-zh-7",       "obs": tree, "goal": "点击数字7",             "want_target": "7",       "want_op": "CLICK"},
        {"id": "calc-zh-equals",  "obs": tree, "goal": "按等于",               "want_target": "等于",     "want_op": "CLICK"},
        {"id": "calc-zh-clear",   "obs": tree, "goal": "清空显示",             "want_target": "全部清除", "want_op": "CLICK"},
        {"id": "calc-zh-sidebar", "obs": tree, "goal": "显示侧边栏",            "want_target": "显示边栏", "want_op": "CLICK"},
    ]


def synthetic_cases() -> list[dict]:
    """English-label desktops: isolates AX semantics from language."""
    settings = {"url": "", "title": "System Settings", "text": "", "actions": [
        {"kind": "click", "node": "d1", "label": "Appearance", "role": "button"},
        {"kind": "click", "node": "d2", "label": "Control Center", "role": "button"},
        {"kind": "click", "node": "d3", "label": "Desktop & Dock", "role": "button"},
        {"kind": "click", "node": "d4", "label": "Displays", "role": "button"},
        {"kind": "click", "node": "d5", "label": "Sound", "role": "button"},
        {"kind": "click", "node": "d6", "label": "Wi-Fi", "role": "button"},
        {"kind": "click", "node": "d7", "label": "Bluetooth", "role": "button"},
        {"kind": "fill", "node": "d8", "label": "Search", "role": "searchbox"},
    ]}
    form = {"url": "", "title": "Passport Renewal", "text": "", "actions": [
        {"kind": "fill", "node": "f1", "label": "Full name", "role": "textbox"},
        {"kind": "fill", "node": "f2", "label": "Date of birth", "role": "textbox"},
        {"kind": "fill", "node": "f3", "label": "Email address", "role": "textbox"},
        {"kind": "click", "node": "f4", "label": "Next", "role": "button"},
        {"kind": "click", "node": "f5", "label": "Cancel", "role": "button"},
    ]}
    return [
        {"id": "syn-settings-displays", "obs": settings, "goal": "Open the Displays settings", "want_target": "Displays", "want_op": "CLICK"},
        {"id": "syn-settings-search",   "obs": settings, "goal": "Find the city in Settings",  "want_target": "Search", "want_op": "TYPE_TEXT"},
        {"id": "syn-form-name",         "obs": form, "goal": "Fill in the full name John Smith", "want_target": "Full name", "want_op": "TYPE_TEXT"},
        {"id": "syn-form-dob",          "obs": form, "goal": "Put 1985-03-14 into the date of birth", "want_target": "Date of birth", "want_op": "TYPE_TEXT"},
    ]


def run_one_case(decider, case: dict) -> dict:
    from localdecide.page import build_element_table, table_to_questions
    table = build_element_table(case["obs"])
    questions = table_to_questions(table, case["goal"])
    started = time.time()
    decision = decider.decide(table.state(), questions)
    elapsed_ms = int((time.time() - started) * 1000)
    row = {"id": case["id"], "goal": case["goal"], "ms": elapsed_ms}
    if not decision.ok:
        row.update({"error": decision.error, "hit": False})
        return row
    answers = decision.answers
    operation = answers.choice("operation")
    chosen = None
    question = f"{operation.lower()}_target"
    if question in answers.raw:
        element = table.by_index().get(answers.choice(question))
        chosen = element.label if element is not None else None
    row.update({"operation": operation,
                "op_p": round(answers.confidence("operation"), 3),
                "target": chosen})
    op_ok = operation == case["want_op"]
    target_ok = chosen == case["want_target"]
    row["op_ok"], row["target_ok"] = op_ok, target_ok
    row["hit"] = op_ok and target_ok
    return row


def run_checkpoint(name: str, spec: dict) -> dict:
    from localdecide.decider import Decider
    tree = json.loads((FIXTURES / "ax_calculator.json").read_text(encoding="utf-8"))
    cases = calc_cases(tree) + synthetic_cases()

    decider = Decider(model=spec["model"], subfolder=spec["subfolder"])
    # Warm-up: force the checkpoint to load before the timed cases.
    load_started = time.time()
    decider.decide({"page": {"url": "", "title": "warm", "text": ""}},
                   {"operation": {"type": "choice", "instructions": "warm up",
                                  "criteria": {"a": "a", "b": "b"}}})
    load_ms = int((time.time() - load_started) * 1000)

    rows = [run_one_case(decider, case) for case in cases]
    hits = sum(1 for row in rows if row.get("hit"))

    def bucket(predicate) -> dict:
        selected = [row for row in rows if predicate(row)]
        return {"hits": sum(1 for row in selected if row.get("hit")), "total": len(selected)}

    return {
        "checkpoint": name,
        "model": spec["model"],
        "subfolder": spec["subfolder"],
        "load_ms": load_ms,
        "cases": len(rows),
        "hits": hits,
        "accuracy": round(hits / len(rows), 3) if rows else 0.0,
        "by_language": {
            "calc_en_goals": bucket(lambda r: r["id"].endswith(("-en-7", "-en-equals", "-en-clear", "-en-sidebar"))),
            "calc_zh_goals": bucket(lambda r: "-zh-" in r["id"]),
            "synthetic_en": bucket(lambda r: r["id"].startswith("syn-")),
        },
        "rows": rows,
    }


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    wanted = sys.argv[1:] or list(CHECKPOINTS)
    summary: dict[str, dict] = {}
    for name in wanted:
        spec = CHECKPOINTS[name]
        origin = spec["model"] if spec["subfolder"] is None else f"{spec['model']}/{spec['subfolder']}"
        print(f"-- {name} ({origin}) ...", flush=True)
        env = dict(os.environ, LCU_CHECKPOINT=name, LCU_SPEC=json.dumps(spec))
        proc = subprocess.run([sys.executable, str(Path(__file__)), "--child"],
                              capture_output=True, text=True, timeout=1800, env=env)
        if proc.returncode != 0:
            print(f"   FAILED: {proc.stderr[-400:]}")
            summary[name] = {"error": proc.stderr[-400:]}
            continue
        payload = json.loads(proc.stdout[proc.stdout.find("{"):])
        (RESULTS / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        language = payload["by_language"]
        summary[name] = {
            "accuracy": payload["accuracy"],
            "hits": f"{payload['hits']}/{payload['cases']}",
            "calc_en": f"{language['calc_en_goals']['hits']}/{language['calc_en_goals']['total']}",
            "calc_zh": f"{language['calc_zh_goals']['hits']}/{language['calc_zh_goals']['total']}",
            "synthetic_en": f"{language['synthetic_en']['hits']}/{language['synthetic_en']['total']}",
            "load_ms": payload["load_ms"],
        }
        print(f"   {payload['hits']}/{payload['cases']} = {payload['accuracy']:.0%}  "
              f"(calc-en {summary[name]['calc_en']}, calc-zh {summary[name]['calc_zh']}, "
              f"syn-en {summary[name]['synthetic_en']})")

    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    (RESULTS / "_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--child" in sys.argv:
        spec = json.loads(os.environ["LCU_SPEC"])
        print(json.dumps(run_checkpoint(os.environ["LCU_CHECKPOINT"], spec), ensure_ascii=False))
    else:
        main()
