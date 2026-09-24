"""The AX layer: a macOS Accessibility tree becomes the same numbered table the
browser project feeds its decision model.

## Why an AX tree and not a screenshot

A screenshot forces a vision model to locate a target, and coordinates are the least
reliable thing a small model can produce. An AX tree hands you the same information
a screen reader gets: roles, labels, values, enabled/selected state, and the list of
actions an element supports. That maps one-to-one onto the element-table contract -
no pixels, no vision, no coordinates.

Measured on a live 147-element Calculator snapshot: **121 of those elements are the
menu bar** (every app carries the full OS menu), so a naive walk buries the 22
buttons that matter under 5x as much chrome. Filtering by subtree is therefore not
an optimisation, it is what makes the observation usable at all.

## The role bridge

The official checkpoints are browser-adjacent: they learned web roles (`button`, `textbox`,
`searchbox`). Feeding them `AXButton` directly would be a small vocabulary shift for
no benefit, so AX roles are mapped onto the closest web role. That keeps the option
text in-distribution for the model while the executor keeps the real AX role.

    AX tree ──filter──► items ──map──► element table ──► questions ──► Decider ──► index
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── role mapping ─────────────────────────────────────────────────────────────
# AX role -> the web role the checkpoints were trained on. Keep this close to the
# browser vocabulary; anything unmapped falls through to lowercase AX role text.
ROLE_MAP: Dict[str, str] = {
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
    "AXDisclosureTriangle": "button",
    "AXTab": "tab",
    "AXCell": "cell",
    "AXRow": "row",
    "AXColumn": "column",
    "AXOutline": "tree",
    "AXTable": "table",
    "AXSlider": "slider",
    "AXStaticText": "text",
    "AXImage": "image",
    "AXToolbar": "toolbar",
    "AXSheet": "dialog",
    "AXWindow": "region",
    "AXGroup": "group",
    "AXList": "list",
}

# Roles a decision model may act on. Everything else is context, and context is
# paid for in tokens — dropping it is what keeps the option list short enough to
# decide quickly (see the browser project's latency-vs-element-count table).
EDITABLE_ROLES = frozenset({"AXTextField", "AXTextArea", "AXSearchField"})
CLICKABLE_ROLES = frozenset({
    "AXButton", "AXLink", "AXCheckBox", "AXRadioButton", "AXMenuItem", "AXMenuButton",
    "AXDisclosureTriangle", "AXTab", "AXPopUpButton", "AXCell", "AXRow", "AXStaticText",
})

# Subtrees that are OS chrome rather than the app's content. The menu bar alone was
# 121/147 elements on Calculator, and it is identical in every app.
CHROME_SUBTREES = frozenset({"AXMenuBar", "AXMenu"})

# Elements whose AX label is a framework internal, not a human-visible name.
JUNK_LABELS = frozenset({"NSOutlineViewDisclosureButtonKey", "AXGroup", "AXUnknown"})


@dataclass
class AXElement:
    """One raw AX node, with the fields the adapter and executor need."""

    element_index: int
    role: str = ""
    label: str = ""
    value: str = ""
    enabled: Optional[bool] = None
    selected: Optional[bool] = None
    expanded: Optional[bool] = None
    actions: List[str] = field(default_factory=list)
    token: str = ""
    frame: Optional[Dict[str, float]] = None
    parent_index: Optional[int] = None
    depth: int = 0

    @classmethod
    def from_driver(cls, raw: Dict[str, Any]) -> "AXElement":
        return cls(
            element_index=int(raw.get("element_index", -1)),
            role=str(raw.get("role", "") or ""),
            label=str(raw.get("label", "") or "").strip(),
            value=str(raw.get("value", "") or "").strip(),
            enabled=raw.get("enabled"),
            selected=raw.get("selected"),
            expanded=raw.get("expanded"),
            actions=list(raw.get("actions") or []),
            token=str(raw.get("element_token", "") or ""),
            frame=raw.get("frame"),
            parent_index=raw.get("parent_index"),
            depth=int(raw.get("depth", 0) or 0),
        )


@dataclass
class AXObserver:
    """Pull one window's AX tree through cua-driver and adapt it to the table contract.

    The driver is reached over its CLI (`cua-driver call <tool> <json>`), so nothing
    here imports a driver SDK — the same tree could come from any AX source.
    """

    pid: int
    window_id: int
    driver_bin: str = "cua-driver"
    timeout: float = 60.0
    # Cap the option list. The browser project measured 20 elements ≈ 330 ms and
    # 120 elements ≈ 1.2 s with degrading accuracy; prefer ranking over raising this.
    max_elements: int = 40
    include_chrome: bool = False

    def _call(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        binary = shutil.which(self.driver_bin) or self.driver_bin
        proc = subprocess.run(
            [binary, "call", tool, json.dumps(payload)],
            capture_output=True, text=True, timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"{tool} failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
        text = proc.stdout.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            if start < 0:
                raise RuntimeError(f"{tool} returned no JSON: {text[:200]}") from None
            return json.loads(text[start:])

    def snapshot(self) -> Dict[str, Any]:
        """Raw get_window_state payload, tree only (no screenshot)."""
        return self._call("get_window_state", {
            "pid": self.pid,
            "window_id": self.window_id,
            "include_screenshot": False,
        })

    def observe(self) -> Dict[str, Any]:
        """The observation dict `build_element_table` accepts."""
        snapshot = self.snapshot()
        elements = [AXElement.from_driver(raw) for raw in snapshot.get("elements", [])]
        observation = ax_to_observation(
            elements,
            title=str(snapshot.get("window_title", "") or ""),
            include_chrome=self.include_chrome,
            max_elements=self.max_elements,
        )
        observation["pid"] = self.pid
        observation["window_id"] = self.window_id
        observation["snapshot_id"] = snapshot.get("snapshot_id", "")
        return observation


def ax_to_observation(
    elements: List[AXElement],
    *,
    title: str = "",
    include_chrome: bool = False,
    max_elements: int = 40,
) -> Dict[str, Any]:
    """Filter and adapt an AX walk into the browser contract's observation shape.

    Two jobs, in this order:

    1. **Drop what cannot be acted on or is OS chrome.** Menu subtrees, unlabelled
       containers, disabled-but-unlabeled rows, framework-internal labels.
    2. **Rank what remains** so the most goal-useful controls survive a truncation.
       Ranking here is generic (editables first, then pressable, then labelled
       statics); goal-aware ranking happens later in `scope`, exactly as the browser
       project does it.

    The output carries both vocabularies: `kind`/`role` for the model, `ax_role`/
    `element_token` for the executor. The model never sees the AX side.
    """
    by_index = {element.element_index: element for element in elements}

    def in_chrome(element: AXElement) -> bool:
        seen = 0
        node: Optional[AXElement] = element
        while node is not None and seen < 64:
            parent = node.parent_index
            if parent is None:
                break
            node = by_index.get(parent)
            if node is not None and node.role in CHROME_SUBTREES:
                return True
            seen += 1
        return False

    items: List[Dict[str, Any]] = []
    for element in elements:
        if element.role in ("AXWindow",):
            continue
        if not include_chrome and in_chrome(element):
            continue

        actions = element.actions
        text = element.label or element.value
        if not text or element.label in JUNK_LABELS:
            continue

        if element.role in EDITABLE_ROLES:
            kind = "fill"
        elif element.role == "AXPopUpButton":
            kind = "select"
        elif element.role in CLICKABLE_ROLES and ("AXPress" in actions or "AXPick" in actions):
            kind = "click"
        else:
            continue

        # `enabled` is absent on many nodes; only an explicit False means disabled.
        disabled = element.enabled is False
        item: Dict[str, Any] = {
            "kind": kind,
            "node": element.token or str(element.element_index),
            "label": text,
            "role": ROLE_MAP.get(element.role, element.role.lower()),
            "disabled": disabled,
            "ax_role": element.role,
            "meta": {"element_index": element.element_index, "element_token": element.token},
        }
        if element.value and element.value != element.label:
            item["current_value"] = element.value
        if element.selected is not None:
            item["selected"] = element.selected
        if element.expanded is not None:
            item["expanded"] = element.expanded
        if element.frame:
            item["bbox"] = [element.frame.get("x", 0), element.frame.get("y", 0),
                            element.frame.get("w", 0), element.frame.get("h", 0)]
        items.append(item)

    # Rank before truncating. Disabled controls stay visible (the model needs to see
    # that the thing exists but is unavailable - WAIT is a legitimate answer), they
    # just rank below their enabled peers.
    def rank(item: Dict[str, Any]) -> tuple:
        return (
            0 if not item.get("disabled") else 1,
            {"fill": 0, "select": 1, "click": 2}.get(item.get("kind", ""), 3),
        )

    items.sort(key=rank)
    if max_elements and len(items) > max_elements:
        items = items[:max_elements]

    return {
        "url": "",
        "title": title,
        "text": "",
        "actions": items,
        "history": [],
    }
