"""Is a correct pick label-based or position-based?

A checkpoint that always picks option #20 has "learned" the Calculator layout, not
the task: it would collapse the moment a real app moved its buttons. This test
shuffles the option order (seeded, so it is reproducible) and asks again. A pick
that tracks the LABEL across shuffles is robust; a pick that tracks the SLOT is not.

Also covers the same question for goals in a different language, which is where the
official Laya checkpoints are weakest.

    python3 -m benchmarks.position_control laya-en
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Path resolution: installed dependency first, sibling checkout as fallback.
try:
    import localdecide
except ImportError:  # dev checkout: the browser project sits next to this repo
    for _candidate in (ROOT.parent / "localdecide", Path("/Volumes/SSD/localdecide")):
        if (_candidate / "localdecide").is_dir():
            sys.path.insert(0, str(_candidate))
            break

FIXTURES = ROOT / "benchmarks" / "fixtures"
RESULTS = ROOT / "benchmarks" / "results"

# Official Laya checkpoints only.
SPECS = {
    "laya-en": {"model": "convaiinnovations/laya", "subfolder": None},
    "laya-multilingual": {"model": "convaiinnovations/laya", "subfolder": "multilingual"},
    "laya-typed-decisions": {"model": "convaiinnovations/laya", "subfolder": "typed-decisions"},
}

CASES = [
    {"id": "en-equals", "goal": "press equals",         "want": "等于"},
    {"id": "en-clear",  "goal": "clear the display",    "want": "全部清除"},
    {"id": "en-7",      "goal": "click the number 7",   "want": "7"},
    {"id": "zh-equals", "goal": "按等于",               "want": "等于"},
    {"id": "zh-clear",  "goal": "清空显示",             "want": "全部清除"},
    {"id": "zh-7",      "goal": "点击数字7",            "want": "7"},
]

TRIALS = 5


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "laya-en"
    spec = SPECS[name]

    from localdecide.decider import Decider
    from localdecide.page import build_element_table, table_to_questions

    decider = Decider(model=spec["model"], subfolder=spec["subfolder"])
    tree = json.loads((FIXTURES / "ax_calculator.json").read_text(encoding="utf-8"))

    rows = []
    for case in CASES:
        picks, slots = [], []
        for trial in range(TRIALS):
            rng = random.Random(1000 + trial)
            observation = dict(tree)
            actions = list(tree["actions"])
            rng.shuffle(actions)
            observation["actions"] = actions

            table = build_element_table(observation)
            questions = table_to_questions(table, case["goal"])
            decision = decider.decide(table.state(), questions)
            if not decision.ok:
                picks.append(f"ERR:{decision.error}")
                slots.append(-1)
                continue
            answers = decision.answers
            operation = answers.choice("operation")
            question = f"{operation.lower()}_target"
            if question in answers.raw:
                index = answers.choice(question)
                element = table.by_index().get(index)
                picks.append(element.label if element else "?")
                slots.append(int(index))
            else:
                # No target question was answered (DONE/WAIT/BLOCKED): there is no slot,
                # so record None rather than a fake 0 — a checkpoint that never picks a
                # control must not be reported as "slot-fixed".
                picks.append(f"[{operation}]")
                slots.append(None)

        hits = sum(1 for pick in picks if pick == case["want"])
        chosen_slots = [slot for slot in slots if slot is not None]
        slot_spread = len(set(chosen_slots))
        rows.append({
            "id": case["id"], "goal": case["goal"], "want": case["want"],
            "picks": picks, "hit_rate": f"{hits}/{TRIALS}",
            "slots": slots, "slot_spread": slot_spread,
            "targets_chosen": len(chosen_slots),
        })
        print(f"  {case['id']:10s} want={case['want']:6s} hits={hits}/{TRIALS} "
              f"slot_spread={slot_spread} targets_chosen={len(chosen_slots)}  picks={picks}")

    label_stable = sum(1 for row in rows if row["hit_rate"].split("/")[0] == str(TRIALS))
    slot_fixed = sum(1 for row in rows
                     if row["targets_chosen"] >= 2 and row["slot_spread"] == 1)
    chosen_total = sum(row["targets_chosen"] for row in rows)
    print(f"\n{name}: {label_stable}/{len(rows)} cases label-stable across shuffles; "
          f"{slot_fixed}/{len(rows)} pinned to one slot; "
          f"targets chosen in {chosen_total}/{len(rows) * TRIALS} trials")
    (RESULTS / f"position_control_{name}.json").write_text(
        json.dumps({"checkpoint": name, "trials": TRIALS, "rows": rows,
                    "label_stable_cases": label_stable, "slot_fixed_cases": slot_fixed,
                    "targets_chosen_trials": chosen_total},
                   ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
