"""Quick probe: run the arena's core suite against Jev only, to validate the path.

    python3 -m benchmarks.arena_jev_probe
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _candidate in (ROOT.parent / "localdecide", Path("/Volumes/SSD/localdecide")):
    if (_candidate / "localdecide").is_dir():
        sys.path.insert(0, str(_candidate))
        break


def main() -> None:
    from benchmarks.arena_backends import JevBackend
    from benchmarks.model_arena import load_suite
    from localdecide.decider import Decider
    from localdecide.page import build_element_table, table_to_questions

    api_key = ""
    env_path = Path.home() / ".hermes" / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("TYPESAFE_API_KEY="):
            api_key = line.split("=", 1)[1].strip()
    backend = JevBackend(api_key=api_key)
    decider = Decider(backend=backend)

    cases = load_suite("core")
    rows = []
    for case in cases:
        table = build_element_table(case["obs"])
        questions = table_to_questions(table, case["goal"])
        decision = decider.decide(table.state(), questions)
        row = {"id": case["id"], "goal": case["goal"]}
        if decision.ok:
            answers = decision.answers
            operation = answers.choice("operation")
            chosen = None
            q = f"{operation.lower()}_target"
            if q in answers.raw:
                element = table.by_index().get(answers.choice(q))
                chosen = element.label if element is not None else None
            row.update({"operation": operation, "op_p": round(answers.confidence("operation"), 3),
                        "target": chosen,
                        "hit": operation == case["want_op"] and chosen == case["want_target"]})
        else:
            row.update({"error": decision.error, "hit": False})
        rows.append(row)
        print(f"{row['id']:26s} {row.get('operation', '-'):9s} p={row.get('op_p', '-')} "
              f"-> {str(row.get('target'))[:20]:20s} {'HIT' if row['hit'] else ''} "
              f"{row.get('error', '')}")

    hits = sum(1 for row in rows if row["hit"])
    print(f"\nJev: {hits}/{len(rows)} = {hits/len(rows):.0%}")
    out = ROOT / "benchmarks" / "results" / "arena_jev_probe.json"
    out.write_text(json.dumps({"rows": rows, "hits": hits, "cases": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
