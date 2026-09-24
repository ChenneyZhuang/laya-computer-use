"""End-to-end desktop test: observe a real window's AX tree, run one action through the
driver, and verify the effect in the actual document.

This is the go/no-go for the AX bridge (no decision model involved): it drives TextEdit's
text area — a real app with a real AX tree — and checks the *document* changed, not just
that a call returned success.

Safety: the target is a scratch file in /tmp opened in TextEdit. Nothing else is
touched, no UI outside the test document is clicked, and every step is verified by
re-reading the window.

    python3 -m benchmarks.e2e_textedit <pid> <window_id>
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Path resolution: installed dependency first, sibling checkout as fallback.
try:
    import localdecide
except ImportError:  # dev checkout: the browser project sits next to this repo
    import sys as _sys
    from pathlib import Path as _Path
    for _candidate in (_Path(__file__).resolve().parents[3] / "localdecide",
                       _Path("/Volumes/SSD/localdecide")):
        if (_candidate / "localdecide").is_dir():
            _sys.path.insert(0, str(_candidate))
            break


def read_window_text(pid: int, window_id: int) -> str:
    """Whatever the window's AX tree says is in an editable field."""
    proc = subprocess.run(
        ["cua-driver", "call", "get_window_state",
         json.dumps({"pid": pid, "window_id": window_id, "include_screenshot": False})],
        capture_output=True, text=True, timeout=60)
    payload = json.loads(proc.stdout[proc.stdout.find("{"):])
    parts = []
    for element in payload.get("elements", []):
        role = element.get("role", "")
        if role in ("AXTextArea", "AXTextField"):
            value = str(element.get("value") or "")
            if value:
                parts.append(value)
    return "\n".join(parts)


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    pid, window_id = int(sys.argv[1]), int(sys.argv[2])

    from laya_computer_use.ax import AXObserver
    from laya_computer_use.driver import CuaDriver
    from localdecide.page import build_element_table, table_to_questions

    print("=== observation ===")
    observer = AXObserver(pid=pid, window_id=window_id, max_elements=40)
    observation = observer.observe()
    table = build_element_table(observation)
    for element in table.elements:
        print(f"  [{element.index:>2}] {element.label[:50]:<50} ({element.role}) {element.operations}")

    before = read_window_text(pid, window_id)
    print(f"\ndocument before: {before[:120]!r}")

    # The one editable control should be the document body.
    editable = [element for element in table.elements if "TYPE_TEXT" in element.operations]
    if not editable:
        print("\nNO EDITABLE CONTROL — TextEdit's body is not exposed as AXTextArea here.")
        raise SystemExit(2)
    target = editable[0]
    print(f"target: [{target.index}] {target.label!r}")

    driver = CuaDriver(pid=pid, window_id=window_id, max_elements=40)
    from localdecide.loop import ElementRef
    fresh = driver.observe()
    fresh_table = build_element_table(fresh)
    ref = None
    for element in fresh_table.elements:
        if "TYPE_TEXT" in element.operations:
            ref = ElementRef(element.index, element.label, element.role, element.handle,
                             {**element.meta, "checked": element.checked},
                             options=list(element.options))
            break
    if ref is None:
        print("no editable element on the fresh observation")
        raise SystemExit(2)

    marker = f"lcu-e2e-{int(time.time())}"
    print(f"\n=== executing TYPE_TEXT {marker!r} ===")
    result = driver.execute("TYPE_TEXT", ref, text=marker)
    print(f"  result: {json.dumps(result, ensure_ascii=False)}")

    time.sleep(0.6)
    after = read_window_text(pid, window_id)
    print(f"\ndocument after: {after[:200]!r}")
    landed = marker in after
    print(f"\n=== VERDICT: {'PASS — text landed in the document' if landed else 'FAIL — marker missing'} ===")
    driver.close()
    raise SystemExit(0 if landed else 3)


if __name__ == "__main__":
    main()
