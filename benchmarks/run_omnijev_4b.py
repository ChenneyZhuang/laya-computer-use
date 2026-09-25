#!/usr/bin/env python3
"""OmniJev-4B region pointing on the 3080 box (CUDA).

Mirrors benchmarks/omnijev_desktop.py on the Mac, but the "screen" comes from a
captured fixture instead of a live window, because the Mac's AX tree is not visible
from the Windows box. The screenshot + control frames are captured on the Mac by
`benchmarks/export_pointing_fixture.py` and shipped as one JSON file.

Run on the 3080 (WSL side):

    /home/ichenney/omnijev-env/bin/python run_omnijev_4b.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CKPT = os.environ.get("OMNIJEV_CKPT", str(HERE / "omnijev-4b-ckpt"))
BASE = os.environ.get("OMNIJEV_BASE", str(HERE / "qwen-4b-base"))
MSO_SRC = os.environ.get("OMNIJEV_SRC", str(HERE / "OmniJev"))
FIXTURE = Path(os.environ.get("OMNIJEV_FIXTURE", str(HERE / "pointing_fixture.json")))
OUT = Path(os.environ.get("OMNIJEV_OUT", str(HERE / "omnijev_4b.json")))

sys.path.insert(0, MSO_SRC)


def main() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    goals = fixture["goals"]
    controls = fixture["controls"]
    image = fixture["image"]
    print(f"fixture: {len(controls)} controls, {len(goals)} goals, image={image}", flush=True)

    options = [{"key": c["canonical"], "text": c["canonical"], "region": c["region"]}
               for c in controls]
    present = {o["key"] for o in options}

    import torch

    from mso.infer import MSO1

    started = time.time()
    model = MSO1(CKPT, BASE)
    print(f"model loaded in {time.time() - started:.1f}s | cuda={torch.cuda.is_available()}", flush=True)

    rows = []
    for goal, target in goals:
        if target not in present:
            continue
        question = {"type": "choice", "instructions": goal, "options": options}
        t0 = time.time()
        try:
            answer = model.system_one({"images": [image]}, {"which": question})["which"]
        except Exception as error:
            rows.append({"goal": goal, "want": target, "hit": False,
                         "error": f"{type(error).__name__}: {error}"[:160]})
            print(f"{goal[:44]:46s} -> ERROR {error}"[:120], flush=True)
            continue
        chosen = str(answer.get("choice"))
        hit = chosen == target
        rows.append({"goal": goal, "want": target, "chosen": chosen, "hit": hit,
                     "confidence": answer.get("confidence"),
                     "ms": int((time.time() - t0) * 1000)})
        print(f"{goal[:44]:46s} -> {chosen:10s} {'HIT' if hit else 'miss':4s} "
              f"p={answer.get('confidence', 0):.2f}  want={target}", flush=True)

    hits = sum(1 for r in rows if r.get("hit"))
    payload = {"model": "OmniJev-4B", "backend": "screenshot + AX region boxes (3080 CUDA)",
               "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
               "controls_offered": len(options), "hits": hits, "cases": len(rows),
               "accuracy": round(hits / len(rows), 3) if rows else 0.0,
               "median_ms": sorted(r.get("ms", 0) for r in rows)[len(rows) // 2] if rows else 0,
               "rows": rows}
    print()
    print(f"OmniJev-4B region pointing: {hits}/{len(rows)} = {payload['accuracy']:.0%}")
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
