"""The model arena: how does every candidate decision model do on desktop control?

This is the benchmark that answers the question the README's honest-number rule
demands: **when the same element table is put in front of different System One
models, which ones can actually pick the control?**

Contenders (all measured through the SAME question contract — no special-casing):

* `laya-*`      — the official Laya checkpoints, run locally (0/12 as shipped)
* `jev`         — TypeSafe's hosted Jev 1.13, the commercial reference; needs
                  `TYPESAFE_API_KEY`
* `von`         — the open-weights von model (Apache-2.0), run locally

Each contender runs in its OWN subprocess: one ~1 GB checkpoint resident at a time
(the memory-safety rule), and a crash in one contender cannot poison the rest.

    python3 -m benchmarks.model_arena                 # every contender that is available
    python3 -m benchmarks.model_arena jev von         # a subset
    python3 -m benchmarks.model_arena von --suite wide

Results: benchmarks/results/model_arena.json
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

try:
    import localdecide
except ImportError:  # pragma: no cover - dev checkout only
    for _candidate in (ROOT.parent / "localdecide", Path("/Volumes/SSD/localdecide")):
        if (_candidate / "localdecide").is_dir():
            sys.path.insert(0, str(_candidate))
            break

# The contenders. `kind` decides how the child process builds the decider.
CONTENDERS: dict[str, dict] = {
    "laya-en": {"kind": "laya", "model": "convaiinnovations/laya", "subfolder": None},
    "laya-multilingual": {"kind": "laya", "model": "convaiinnovations/laya", "subfolder": "multilingual"},
    "laya-typed-decisions": {"kind": "laya", "model": "convaiinnovations/laya", "subfolder": "typed-decisions"},
    "jev": {"kind": "jev", "model": "jev-latest"},
    "von": {"kind": "von", "model": "von-latest"},
}


def load_suite(name: str) -> list[dict]:
    """The battery cases. `core` mirrors checkpoint_battery.py so the numbers are
    comparable across the two scripts; `wide` adds whole-computer cases."""
    tree = json.loads((FIXTURES / "ax_calculator.json").read_text(encoding="utf-8"))
    core = [
        ("calc-en-7", tree, "click the number 7", "7", "CLICK"),
        ("calc-en-equals", tree, "press equals", "等于", "CLICK"),
        ("calc-en-clear", tree, "clear the display", "全部清除", "CLICK"),
        ("calc-en-sidebar", tree, "show the sidebar", "显示边栏", "CLICK"),
        ("calc-zh-7", tree, "点击数字7", "7", "CLICK"),
        ("calc-zh-equals", tree, "按等于", "等于", "CLICK"),
        ("calc-zh-clear", tree, "清空显示", "全部清除", "CLICK"),
        ("calc-zh-sidebar", tree, "显示侧边栏", "显示边栏", "CLICK"),
    ]
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
    synthetic = [
        ("syn-settings-displays", settings, "Open the Displays settings", "Displays", "CLICK"),
        ("syn-settings-search", settings, "Find the city in Settings", "Search", "TYPE_TEXT"),
        ("syn-form-name", form, "Fill in the full name John Smith", "Full name", "TYPE_TEXT"),
        ("syn-form-dob", form, "Put 1985-03-14 into the date of birth", "Date of birth", "TYPE_TEXT"),
    ]
    other = [
        ("laya-en", tree, "click the number 7", "7", "CLICK"),
    ]
    wide = [
        # Whole-computer cases: the desktop table itself (apps + windows) is the
        # option list. Goals name an app or a window, never a coordinate.
        ("desk-switch-finder", "desktop", "Switch to Finder", "访达", "CLICK"),
        ("desk-switch-safari", "desktop", "Bring Safari to the front", "Safari", "CLICK"),
        ("desk-open-calculator", "desktop", "Open the Calculator app", "Calculator", "OPEN_APP"),
        ("desk-window-notes", "desktop", "Focus the Notes window", "Notes", "FOCUS_WINDOW"),
    ]
    if name == "core":
        return _rows(core + synthetic)
    if name == "wide":
        return _rows(core + synthetic) + _desktop_rows()
    raise ValueError(f"unknown suite {name!r}")


def _desktop_rows() -> list[dict]:
    """Whole-computer cases built from a captured desktop table (real apps/windows).

    Falls back to a synthetic desktop table when no fixture exists, so the suite still
    runs on a machine that never captured one.
    """
    path = FIXTURES / "desktop_table.json"
    if path.exists():
        obs = json.loads(path.read_text(encoding="utf-8"))
    else:
        obs = {"url": "", "title": "Desktop", "text": "", "actions": [
            {"kind": "click", "node": "lcu:app:101", "label": "Google Chrome (com.google.Chrome) [running, frontmost, 2 windows]", "role": "app"},
            {"kind": "click", "node": "lcu:app:102", "label": "访达 (com.apple.finder) [running, 1 window]", "role": "app"},
            {"kind": "click", "node": "lcu:app:103", "label": "Safari (com.apple.Safari) [running]", "role": "app"},
            {"kind": "click", "node": "lcu:app:104", "label": "Calculator (com.apple.calculator) [not running]", "role": "app"},
            {"kind": "click", "node": "lcu:app:105", "label": "Notes (com.apple.Notes) [not running]", "role": "app"},
            {"kind": "click", "node": "lcu:win:101:11", "label": 'Google Chrome "Project Alpha" [window]', "role": "window"},
            {"kind": "click", "node": "lcu:win:102:21", "label": '访达 "Documents" [window]', "role": "window"},
            {"kind": "click", "node": "lcu:win:101:12", "label": 'Google Chrome "Inbox" [window]', "role": "window"},
        ]}
    return [
        {"id": "desk-switch-finder", "obs": obs, "goal": "Switch to Finder", "want_target": "访达", "want_op": "CLICK"},
        {"id": "desk-switch-safari", "obs": obs, "goal": "Bring Safari to the front", "want_target": "Safari", "want_op": "CLICK"},
        {"id": "desk-open-calculator", "obs": obs, "goal": "Open the Calculator app", "want_target": "Calculator", "want_op": "CLICK"},
    ]


def _rows(cases: list[tuple]) -> list[dict]:
    return [{"id": rid, "obs": obs, "goal": goal, "want_target": want, "want_op": op}
            for rid, obs, goal, want, op in cases]


def run_one(build_decider, case: dict, *, layout: str = "v3") -> dict:
    from localdecide.page import build_element_table, table_to_questions

    decider = build_decider()
    table = build_element_table(case["obs"])
    questions = table_to_questions(table, case["goal"])
    started = time.time()
    try:
        decision = decider.decide(table.state(layout=layout), questions)
    except Exception as error:
        return {"id": case["id"], "hit": False, "error": f"{type(error).__name__}: {error}",
                "ms": int((time.time() - started) * 1000)}
    row = {"id": case["id"], "goal": case["goal"], "ms": int((time.time() - started) * 1000)}
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
    op_ok = operation == case["want_op"]
    target_ok = chosen == case["want_target"]
    row.update({"operation": operation, "op_p": round(answers.confidence("operation"), 3),
                "target": chosen, "op_ok": op_ok, "target_ok": target_ok,
                "hit": bool(op_ok and target_ok)})
    return row


def run_contender(name: str, kind: str, spec: dict, suite: str, layout: str = "v3") -> dict:
    """Everything a contender needs, run inside its child process."""
    from localdecide.decider import Decider

    if kind == "laya":
        decider = Decider(model=spec["model"], subfolder=spec.get("subfolder"))
    elif kind == "jev":
        from benchmarks.arena_backends import JevBackend

        api_key = ""
        env_path = Path.home() / ".hermes" / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("TYPESAFE_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
        decider = Decider(backend=JevBackend(api_key=api_key, model=spec["model"]))
    elif kind == "von":
        from benchmarks.arena_backends import VonBackend

        decider = VonBackend()
    else:
        raise ValueError(f"unknown contender kind {kind!r}")

    cases = load_suite(suite)
    if kind == "von":
        # von's Python API answers one question per call; run it through the same
        # table/questions builder but answer each question with its own call.
        rows = [_run_von_case(decider, case, layout=layout) for case in cases]
    else:
        rows = [run_one(lambda: decider, case, layout=layout) for case in cases]

    hits = sum(1 for row in rows if row.get("hit"))
    return {
        "contender": name, "kind": kind, "model": spec.get("model"),
        "suite": suite, "layout": layout, "cases": len(rows), "hits": hits,
        "accuracy": round(hits / len(rows), 3) if rows else 0.0,
        "median_ms": sorted(row.get("ms", 0) for row in rows)[len(rows) // 2] if rows else 0,
        "rows": rows,
    }


def _run_von_case(backend, case: dict, *, layout: str = "v3") -> dict:
    """Drive von through the element-table contract, one question per call."""
    from localdecide.page import build_element_table, table_to_questions

    table = build_element_table(case["obs"])
    questions = table_to_questions(table, case["goal"])
    state = table.state(layout=layout)
    started = time.time()
    row = {"id": case["id"], "goal": case["goal"]}
    try:
        operation_answer = backend._answer_choice(state, questions["operation"])
        operation = operation_answer["choice"]
    except Exception as error:
        row.update({"hit": False, "error": f"{type(error).__name__}: {error}",
                    "ms": int((time.time() - started) * 1000)})
        return row
    chosen = None
    question_name = f"{operation.lower()}_target"
    if question_name in questions:
        try:
            target_answer = backend._answer_choice(state, questions[question_name])
            element = table.by_index().get(target_answer["choice"])
            chosen = element.label if element is not None else None
        except Exception as error:
            row.update({"hit": False, "error": f"target: {type(error).__name__}: {error}",
                        "ms": int((time.time() - started) * 1000), "operation": operation})
            return row
    op_ok = operation == case["want_op"]
    target_ok = chosen == case["want_target"]
    row.update({"operation": operation, "op_p": round(operation_answer["confidence"], 3),
                "target": chosen, "op_ok": op_ok, "target_ok": target_ok,
                "hit": bool(op_ok and target_ok), "ms": int((time.time() - started) * 1000)})
    return row


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    suite = "core"
    if "--suite" in sys.argv:
        suite = sys.argv[sys.argv.index("--suite") + 1]
    layout = "v3"
    if "--layout" in sys.argv:
        layout = sys.argv[sys.argv.index("--layout") + 1]
    wanted = args or list(CONTENDERS)

    summary: dict[str, dict] = {}
    for name in wanted:
        spec = CONTENDERS[name]
        print(f"-- {name} ({spec['kind']}:{spec.get('model')}) suite={suite} layout={layout} ...",
              flush=True)
        env = dict(os.environ, ARENA_CHILD=name, ARENA_KIND=spec["kind"],
                   ARENA_SPEC=json.dumps(spec), ARENA_SUITE=suite, ARENA_LAYOUT=layout)
        # von lives in its own venv (torch + transformers 5.x); use its interpreter
        # when it exists so the arena venv stays independent of the laya runtime.
        executable = sys.executable
        if spec["kind"] == "von":
            candidate = Path("/Volumes/SSD/lcu-experiments/von-env/bin/python")
            if candidate.exists():
                executable = str(candidate)
        proc = subprocess.run([executable, str(Path(__file__)), "--child"],
                              capture_output=True, text=True, timeout=3600, env=env)
        if proc.returncode != 0:
            print(f"   FAILED rc={proc.returncode}: {proc.stderr[-500:]}")
            summary[name] = {"error": proc.stderr[-500:]}
            continue
        stdout = proc.stdout
        payload = json.loads(stdout[stdout.find("{"):])
        (RESULTS / f"arena_{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        summary[name] = {"accuracy": payload["accuracy"],
                         "hits": f"{payload['hits']}/{payload['cases']}",
                         "median_ms": payload["median_ms"],
                         "by_case": {row["id"]: row.get("hit", False) for row in payload["rows"]}}
        print(f"   {payload['hits']}/{payload['cases']} = {payload['accuracy']:.0%}  "
              f"({payload['median_ms']} ms median)")

    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    (RESULTS / f"model_arena_{suite}_{layout}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--child" in sys.argv:
        name = os.environ["ARENA_CHILD"]
        result = run_contender(name, os.environ["ARENA_KIND"],
                               json.loads(os.environ["ARENA_SPEC"]), os.environ["ARENA_SUITE"],
                               os.environ.get("ARENA_LAYOUT", "v3"))
        print(json.dumps(result, ensure_ascii=False))
    else:
        main()
