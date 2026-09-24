"""Sanity: do the official Laya checkpoints work, on the task they were trained for?

Everything in `benchmarks/` measures control selection on desktop AX trees — a task
the official checkpoints were NOT trained for. This script is the control group: it
runs the official quickstart example (email triage) through the exact same backend
path, so a reader can see the checkpoints are healthy and the gap measured elsewhere
is a task-format gap, not a broken download or a broken backend.

    python3 -m benchmarks.quickstart_sanity

Result: benchmarks/results/quickstart_sanity.json
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

# The example from the official model card (huggingface.co/convaiinnovations/laya).
EMAIL = ("Hi, we were billed twice for March. Please refund the duplicate today "
         "or we will cancel our plan.")

QUESTIONS = {
    "department": {
        "type": "choice",
        "instructions": "Which department should handle this?",
        "criteria": {
            "billing": "invoices, payments, refunds",
            "technical": "bugs, outages, system errors",
            "other": "everything else",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this?",
        "criteria": ["not urgent", "soon", "blocking"],
    },
    "churn_risk": {
        "type": "noul",
        "instructions": "Does the user threaten to cancel or leave?",
    },
}

# Non-Latin input for the multilingual checkpoint (the card's own example of the
# same question answered in another language).
NON_ENGLISH = "我被重复收费了两次，请把多收的钱退给我。"

CHECKPOINTS = {
    "english": {"model": "convaiinnovations/laya", "subfolder": None, "state": EMAIL},
    "multilingual": {"model": "convaiinnovations/laya", "subfolder": "multilingual",
                     "state": NON_ENGLISH},
}


def run(name: str, spec: dict) -> dict:
    from localdecide.decider import Decider

    decider = Decider(model=spec["model"], subfolder=spec["subfolder"])
    started = time.time()
    decision = decider.decide(spec["state"], QUESTIONS)
    elapsed_ms = int((time.time() - started) * 1000)
    if not decision.ok:
        return {"checkpoint": name, "error": decision.error, "ms": elapsed_ms}
    answers = decision.answers
    return {
        "checkpoint": name,
        "state": spec["state"],
        "ms": elapsed_ms,
        "department": answers.choice("department"),
        "department_p": round(answers.confidence("department"), 3),
        "urgency": round(answers.score("urgency"), 3),
        "churn_risk": round(answers.noul("churn_risk"), 3),
    }


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, spec in CHECKPOINTS.items():
        row = run(name, spec)
        out[name] = row
        if "error" in row:
            print(f"{name:14s} ERROR {row['error']}")
        else:
            print(f"{name:14s} department={row['department']} (p={row['department_p']}) "
                  f"urgency={row['urgency']} churn={row['churn_risk']}  [{row['ms']} ms]")
    (RESULTS / "quickstart_sanity.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nwrote benchmarks/results/quickstart_sanity.json")


if __name__ == "__main__":
    main()
