"""Capture a live window's AX tree as a benchmark fixture.

    python3 benchmarks/capture_ax.py <pid> <window_id> [name]

Writes benchmarks/fixtures/ax_<name>.json in the observation shape that
`build_element_table` accepts, so the battery can run without a live app.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from laya_computer_use.ax import AXObserver


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    pid, window_id = int(sys.argv[1]), int(sys.argv[2])
    name = sys.argv[3] if len(sys.argv) > 3 else f"window_{pid}_{window_id}"

    observer = AXObserver(pid=pid, window_id=window_id, max_elements=0)
    observation = observer.observe()
    fixtures = ROOT / "benchmarks" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    path = fixtures / f"ax_{name}.json"
    observation = {key: value for key, value in observation.items()
                   if key not in ("pid", "window_id", "snapshot_id")}
    path.write_text(json.dumps(observation, ensure_ascii=False, indent=2))
    print(f"wrote {path} ({len(observation['actions'])} actionable elements, "
          f"title={observation['title']!r})")


if __name__ == "__main__":
    main()
