"""Format matrix: does the layout decide whether a model can answer at all?

Discovered the hard way: the arena's core suite runs the browser-trained layout
(`v3`: elements live only in option lists, questions carry the browser rules text).
Against that layout TypeSafe's Jev answered `BLOCKED` on all 12 cases — while a
hand probe using the Jev-native layout (`v1`: elements inside the state) answered
`CLICK` with the right element at p=1.0.

So "which model caps out" and "which format cap fits the model" are two different
questions and this script separates them:

* layout `v3`     — elements only in options (browser checkpoints' training format)
* layout `v1`     — elements as JSON in the state (Jev's documented style)
* layout `native` — elements as plain text lines in the state, short questions
                    (the hand-probe shape that Jev answered correctly)

Run for any contender:

    python3 -m benchmarks.format_matrix jev
    python3 -m benchmarks.format_matrix von
    python3 -m benchmarks.format_matrix laya-en
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"

sys.path.insert(0, str(ROOT))
for _candidate in (ROOT.parent / "localdecide", Path("/Volumes/SSD/localdecide")):
    if (_candidate / "localdecide").is_dir():
        sys.path.insert(0, str(_candidate))
        break


def native_state(observation: dict, goal: str) -> dict:
    """Elements as text lines in the state, goals as short instructions."""
    lines = []
    for position, item in enumerate(observation.get("actions", []), start=1):
        index = item.get("index", str(position))
        text = f"[{index}] {item.get('label')}"
        if item.get("role"):
            text += f" ({item['role']})"
        lines.append(text)
    return {"app": observation.get("title", ""), "controls": lines, "task": goal}


def native_questions(observation: dict, goal: str) -> dict:
    """Short, direct questions — no browser rules text.

    Indices come from the raw observation, which may carry `node` handles ("d1", a
    driver token) instead of numeric `index` values. Renumber here so the native
    layout still offers dense `1..N` indices, exactly like the table does.
    """
    actions = observation.get("actions", [])
    by_op: dict[str, list[dict]] = {"CLICK": [], "TYPE_TEXT": [], "SELECT": []}
    for position, item in enumerate(actions, start=1):
        kind = "CLICK" if item.get("kind") == "click" else (
            "TYPE_TEXT" if item.get("kind") == "fill" else "SELECT")
        by_op.setdefault(kind, []).append({**item, "index": str(position)})
    questions: dict = {
        "operation": {
            "type": "choice",
            "instructions": f"Goal: {goal}. Which single action should be performed next?",
            "criteria": {"CLICK": "Click one of the visible controls.",
                         "TYPE_TEXT": "Type text into a field.",
                         "SCROLL_DOWN": "Scroll down.", "WAIT": "Wait.",
                         "DONE": "The goal is already complete."},
        }
    }
    for op, items in by_op.items():
        if not items or op in ("SCROLL_DOWN", "WAIT"):
            continue
        questions[f"{op.lower()}_target"] = {
            "type": "choice",
            "instructions": f"Goal: {goal}. If the next action is {op}, which control should it act on?",
            "criteria": {item["index"]: f"[{item['index']}] {item.get('label')}"
                         for item in items},
        }
    return questions


def run(contender: str) -> dict:
    from benchmarks.model_arena import CONTENDERS, load_suite
    from localdecide.decider import Decider
    from localdecide.page import build_element_table, table_to_questions

    spec = CONTENDERS[contender]
    cases = load_suite("core")

    if spec["kind"] == "jev":
        from benchmarks.arena_backends import JevBackend

        api_key = ""
        env_path = Path.home() / ".hermes" / ".env"
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("TYPESAFE_API_KEY="):
                api_key = line.split("=", 1)[1].strip()
        decider = Decider(backend=JevBackend(api_key=api_key, model=spec["model"]))
    elif spec["kind"] == "von":
        from benchmarks.arena_backends import VonBackend

        decider = VonBackend()
    else:
        decider = Decider(model=spec["model"], subfolder=spec.get("subfolder"))

    rows = []
    for case in cases:
        table = build_element_table(case["obs"])
        row = {"id": case["id"]}
        for layout in ("v3", "v1", "native"):
            if layout == "native":
                state = native_state(case["obs"], case["goal"])
                questions = native_questions(case["obs"], case["goal"])
            else:
                state = table.state(layout=layout)
                questions = table_to_questions(table, case["goal"])
            started = time.time()
            try:
                if spec["kind"] == "von":
                    op_answer = decider._answer_choice(state, questions["operation"])
                    operation = op_answer["choice"]
                    target = None
                    qname = f"{operation.lower()}_target"
                    if qname in questions:
                        target_answer = decider._answer_choice(state, questions[qname])
                        element = table.by_index().get(target_answer["choice"])
                        target = element.label if element else None
                    hit = operation == case["want_op"] and target == case["want_target"]
                    row[layout] = {"op": operation, "target": target, "hit": hit,
                                   "ms": int((time.time() - started) * 1000)}
                    continue
                decision = decider.decide(state, questions)
            except Exception as error:
                row[layout] = {"error": f"{type(error).__name__}: {error}"[:120]}
                continue
            if not decision.ok:
                row[layout] = {"error": (decision.error or "")[:120]}
                continue
            answers = decision.answers
            operation = answers.choice("operation")
            chosen = None
            qname = f"{operation.lower()}_target"
            if qname in answers.raw:
                element = table.by_index().get(answers.choice(qname))
                chosen = element.label if element is not None else None
            hit = operation == case["want_op"] and chosen == case["want_target"]
            row[layout] = {"op": operation, "p": round(answers.confidence("operation"), 3),
                           "target": chosen, "hit": hit,
                           "ms": int((time.time() - started) * 1000)}
        rows.append(row)
        marks = {layout: ("HIT" if row[layout].get("hit") else
                          (row[layout].get("op") or row[layout].get("error", "?")[:12]))
                 for layout in ("v3", "v1", "native")}
        print(f"{row['id']:26s} v3={marks['v3']:12s} v1={marks['v1']:12s} native={marks['native']}")

    summary = {}
    for layout in ("v3", "v1", "native"):
        hits = sum(1 for row in rows if row[layout].get("hit"))
        summary[layout] = {"hits": hits, "cases": len(rows),
                           "accuracy": round(hits / len(rows), 3)}
    print()
    for layout, stats in summary.items():
        print(f"{contender} {layout:7s} {stats['hits']}/{stats['cases']} = {stats['accuracy']:.0%}")
    return {"contender": contender, "summary": summary, "rows": rows}


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or ["jev"]
    path = RESULTS / "format_matrix.json"
    # Merge into any existing file rather than overwriting: the five contenders are
    # run as separate processes (one ~1 GB checkpoint resident at a time), so an
    # overwrite would leave only the last model in the committed artifact.
    out: dict = {}
    if path.exists():
        try:
            out = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out = {}
    for name in names:
        print(f"== {name} ==", flush=True)
        out[name] = run(name)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
