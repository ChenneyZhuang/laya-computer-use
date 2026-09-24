"""Prototype: can an official Laya checkpoint decide desktop AX-tree actions?

This is the go/no-go experiment for laya-computer-use. It:
1. reads a live macOS window's Accessibility tree via cua-driver
2. converts it to the element-table contract this project shares with its browser sibling
3. asks an official Laya checkpoint (convaiinnovations/laya) which element to act on

No actions are executed. Dry run only.

    python3 -m benchmarks.diagnostics.prototype_ax <pid> <window_id> [goal]
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

# Repo root (the package lives there); the decision engine resolves separately below.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
try:
    import localdecide
except ImportError:  # dev checkout: the browser project sits next to this repo
    for _candidate in (Path(__file__).resolve().parents[3] / "localdecide",
                       Path("/Volumes/SSD/localdecide")):
        if (_candidate / "localdecide").is_dir():
            sys.path.insert(0, str(_candidate))
            break

from localdecide.page import build_element_table, table_to_questions
from localdecide.decider import Decider

# ── AX roles → the browser contract's vocabulary ─────────────────────────────
# The checkpoints were trained on web roles. Mapping AX roles onto the closest
# web role keeps the option text in-distribution.
ROLE_MAP = {
    "AXButton": "button",
    "AXLink": "link",
    "AXTextField": "textbox",
    "AXTextArea": "textbox",
    "AXSearchField": "searchbox",
    "AXComboBox": "combobox",
    "AXPopUpButton": "combobox",
    "AXCheckBox": "checkbox",
    "AXRadioButton": "radio",
    "AXMenuItem": "menuitem",
    "AXMenuButton": "button",
    "AXTab": "tab",
    "AXCell": "cell",
    "AXRow": "row",
    "AXStaticText": "text",
    "AXImage": "image",
    "AXDisclosureTriangle": "button",
    "AXSlider": "slider",
    "AXTable": "table",
    "AXOutline": "tree",
}

EDITABLE_ROLES = {"AXTextField", "AXTextArea", "AXSearchField"}
CLICKABLE_ROLES = {"AXButton", "AXLink", "AXCheckBox", "AXRadioButton", "AXMenuItem",
                   "AXMenuButton", "AXTab", "AXDisclosureTriangle", "AXPopUpButton"}

# Containers whose whole subtree is OS chrome, not the app's content.
CHROME_SUBTREES = {"AXMenuBar", "AXMenu", "AXApplication"}


def run_driver(tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    out = subprocess.run(["cua-driver", "call", tool, json.dumps(payload)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"cua-driver {tool} failed: {out.stderr[:300]}")
    text = out.stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # cua-driver sometimes prints a human line before JSON
        start = text.find("{")
        if start >= 0:
            return json.loads(text[start:])
        raise


def ancestors(elements: List[Dict[str, Any]], index: int) -> List[str]:
    """Role chain from the element up to the root."""
    by_index = {e["element_index"]: e for e in elements if "element_index" in e}
    chain: List[str] = []
    node = by_index.get(index)
    while node is not None:
        chain.append(node.get("role", ""))
        parent = node.get("parent_index")
        node = by_index.get(parent) if parent is not None else None
    return chain


def observe_window(pid: int, window_id: int) -> Dict[str, Any]:
    """One cua-driver AX snapshot → the browser contract's observation dict."""
    state = run_driver("get_window_state",
                       {"pid": pid, "window_id": window_id, "include_screenshot": False})
    raw = state.get("elements", [])
    items: List[Dict[str, Any]] = []

    for element in raw:
        role = element.get("role", "")
        label = (element.get("label") or "").strip()
        value = str(element.get("value") or "").strip()
        actions = element.get("actions") or []

        # Drop nav/menu subtrees: 121 of 147 elements in Calculator are menu chrome.
        chain = ancestors(raw, element["element_index"])
        if any(r in CHROME_SUBTREES for r in chain[1:]):
            continue

        # Identity: labels are often empty for icon buttons; fall back to value,
        # then to the role so the item is still addressable.
        text = label or value
        if not text:
            continue

        # The element's role must imply it can do something.
        if role in EDITABLE_ROLES:
            kind = "fill"
        elif role in CLICKABLE_ROLES and "AXPress" in actions:
            kind = "click"
        elif role == "AXPopUpButton":
            kind = "select"
        else:
            continue

        items.append({
            "kind": kind,
            "node": element.get("element_token") or str(element["element_index"]),
            "label": text,
            "role": ROLE_MAP.get(role, role.lower()),
            "disabled": element.get("enabled") is False,
            "value": value if value and value != label else "",
            "selected": element.get("selected"),
            "frame": element.get("frame"),
        })

    return {
        "url": "",
        "title": state.get("window_title", ""),
        "text": "",  # AX tree gives us no page prose; labels carry the meaning
        "actions": items,
        "history": [],
    }


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: prototype_ax.py <pid> <window_id> [goal]")
        raise SystemExit(1)
    pid, window_id = int(sys.argv[1]), int(sys.argv[2])
    goal = sys.argv[3] if len(sys.argv) > 3 else "计算 7 加 5，然后按等于"

    started = time.time()
    observation = observe_window(pid, window_id)
    table = build_element_table(observation)
    questions = table_to_questions(table, goal)
    print(f"observed {len(table.elements)} actionable elements "
          f"({len(observation['actions'])} raw items) in {time.time() - started:.2f}s")
    print("\n=== element table ===")
    for element in table.elements:
        print("  " + element.describe())

    print("\n=== questions ===")
    print(json.dumps({k: {kk: (vv if kk != 'criteria' else f'{len(vv)} options')
                          for kk, vv in v.items()}
                      for k, v in questions.items()}, ensure_ascii=False, indent=2))

    started = time.time()
    decision = Decider(model="convaiinnovations/laya").decide(table.state(), questions)
    print(f"\n=== decision ({time.time() - started:.2f}s) ok={decision.ok} ===")
    if not decision.ok:
        print("ERROR:", decision.error)
        raise SystemExit(2)
    answers = decision.answers
    print("operation:", answers.choice("operation"),
          f"p={answers.confidence('operation'):.3f}")
    for name in ("click_target", "type_text_target", "select_target"):
        if name in answers.raw:
            chosen = answers.choice(name)
            element = table.by_index().get(chosen)
            print(f"{name}: [{chosen}] {element.label if element else '???'}"
                  f"  p={answers.confidence(name):.3f}")
    if "select_option" in answers.raw:
        print("select_option:", answers.choice("select_option"))
    print("usage:", getattr(decision, "usage", {}))


if __name__ == "__main__":
    main()
