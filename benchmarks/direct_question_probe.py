"""Direct-question probe: is the official checkpoint's failure an instruction artifact?

The battery feeds the harness's decision contract (dict-shaped instructions, a goal
plus rules, options rendered as "index: label"). The official Laya checkpoints were
trained on plain-sentence questions. Before reporting a capability gap, this script
checks the gap is not a wording artifact: it asks the SAME model the SAME underlying
question four ways, each one closer to plain language.

    python3 -m benchmarks.direct_question_probe            # both checkpoints
    python3 -m benchmarks.direct_question_probe en         # one

A: dict instructions        (what the battery sends)
B: plain-sentence rules     (same criteria, natural wording)
C: goal text only
D: no operation question at all — "which button clears the display?" over the raw
   labels, the simplest possible question shape.

Result: benchmarks/results/direct_question_probe.json
"""

from __future__ import annotations

import json
import sys
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

GOAL = "click the number 7"
CLEAR_GOAL = "clear the display"
CLEAR_TARGET = "全部清除"

CHECKPOINTS = {
    "laya-en": {"model": "convaiinnovations/laya", "subfolder": None},
    "laya-multilingual": {"model": "convaiinnovations/laya", "subfolder": "multilingual"},
    "laya-typed-decisions": {"model": "convaiinnovations/laya", "subfolder": "typed-decisions"},
}


def _answer(decider, table, questions, name):
    decision = decider.decide(table.state(), questions)
    if not decision.ok:
        return {"error": decision.error}
    answers = decision.answers
    row = {"operation": answers.choice("operation"),
           "op_p": round(answers.confidence("operation"), 3)}
    question = f"{row['operation'].lower()}_target"
    if question in answers.raw:
        index = answers.choice(question)
        element = table.by_index().get(index)
        row["target"] = element.label if element is not None else None
        row["target_index"] = index
    return row


def run(name: str, spec: dict) -> dict:
    from localdecide.decider import Decider
    from localdecide.page import build_element_table, table_to_questions

    tree = json.loads((FIXTURES / "ax_calculator.json").read_text(encoding="utf-8"))
    table = build_element_table(tree)
    base = table_to_questions(table, GOAL)
    decider = Decider(model=spec["model"], subfolder=spec["subfolder"])

    out = {}

    # A: the harness's own dict instructions
    out["A_dict_instructions"] = _answer(decider, table, base, "A")

    # B: plain-sentence wording, same criteria
    plain = dict(base)
    plain["operation"] = dict(
        base["operation"],
        instructions=f"Goal: {GOAL}. Choose the single next operation to execute on this window.",
    )
    out["B_plain_instructions"] = _answer(decider, table, plain, "B")

    # C: goal text only
    goal_only = dict(base)
    goal_only["operation"] = dict(base["operation"], instructions=GOAL)
    out["C_goal_only"] = _answer(decider, table, goal_only, "C")

    # D: no operation question — ask directly which element to press
    targets = table.targets_for("CLICK")
    direct = {"element": {
        "type": "choice",
        "criteria": {index: element.label for index, element in targets.items()},
        "instructions": f"Which button should be pressed to {CLEAR_GOAL}?",
    }}
    decision = decider.decide({"page": {"url": "", "title": "Calculator", "text": ""}}, direct)
    if not decision.ok:
        out["D_direct_element"] = {"error": decision.error}
    else:
        answers = decision.answers
        index = answers.choice("element")
        element = targets.get(index)
        out["D_direct_element"] = {
            "picked_index": index,
            "picked_label": element.label if element is not None else None,
            "wanted": CLEAR_TARGET,
            "hit": element is not None and element.label == CLEAR_TARGET,
        }

    return out


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    wanted = sys.argv[1:] or list(CHECKPOINTS)
    out: dict = {}
    for name in wanted:
        spec = CHECKPOINTS[name]
        print(f"-- {name} --")
        rows = run(name, spec)
        out[name] = rows
        for probe, row in rows.items():
            if "error" in row:
                print(f"  {probe:24s} ERROR {row['error']}")
            elif probe == "D_direct_element":
                print(f"  {probe:24s} picked {row['picked_label']!r} "
                      f"(wanted {row['wanted']!r}) hit={row['hit']}")
            else:
                line = f"  {probe:24s} op={row['operation']} p={row['op_p']}"
                if row.get("target"):
                    line += f" target={row['target']!r}"
                print(line)
        print()
    (RESULTS / "direct_question_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote benchmarks/results/direct_question_probe.json")


if __name__ == "__main__":
    main()
