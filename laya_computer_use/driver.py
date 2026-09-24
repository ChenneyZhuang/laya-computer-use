"""The executor: turn a chosen element index into a real macOS action.

This mirrors the browser project's driver contract exactly — `observe()`,
`execute()`, `close()` — so the loop, guards, scoping and history tracking all run
unchanged. What differs is only the rung used to perform the action:

* `click`      -> cua-driver AX press on the element token (works on backgrounded
                  windows; no cursor move, no focus steal)
* `type_text`  -> cua-driver `type_text` against the element
* `select`     -> cua-driver `set_value` on a popup button; the option list comes
                  from the AX tree, so the model picked a real value
* scroll/wait  -> keystroke path (`scroll`) / short sleep

## page_changed, honestly

The browser driver computes a URL signature after acting. A desktop window has no
URL, so the equivalent signal is an **AX signature** — visible control count plus a
label fingerprint — measured by re-walking the tree after the action. It matters for
the same reason it does in the browser: an async UI update changes the screen without
changing the window title, and a loop that cannot see that change will punish real
progress as if it were stuck.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any, Dict, Optional

from .ax import AXObserver


class CuaDriver:
    """Drive one macOS window through cua-driver. Implements the loop's Driver protocol.

    Example:
        from laya_computer_use import CuaDriver, DesktopLoop
        driver = CuaDriver(pid=1234, window_id=5678)
        run = DesktopLoop().run(driver, "open the Displays settings")
    """

    def __init__(
        self,
        pid: int,
        window_id: int,
        *,
        driver_bin: str = "cua-driver",
        max_elements: int = 40,
        timeout: float = 60.0,
        delivery_mode: str = "background",
        settle_ms: int = 250,
    ) -> None:
        self.pid = pid
        self.window_id = window_id
        self.driver_bin = driver_bin
        self.timeout = timeout
        self.delivery_mode = delivery_mode
        self.settle_ms = settle_ms
        self.observer = AXObserver(pid=pid, window_id=window_id,
                                   driver_bin=driver_bin, timeout=timeout,
                                   max_elements=max_elements)
        self._closed = False
        # The executor's own index -> observed item map, replaced by every observation.
        # The model's answer is an index; only this map turns it into a real target.
        self._by_index: Dict[str, Dict[str, Any]] = {}
        self._snapshot_id: str = ""
        self._signature: Optional[tuple] = None
        self._cached: Optional[Dict[str, Any]] = None

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _call(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        binary = shutil.which(self.driver_bin) or self.driver_bin
        proc = subprocess.run(
            [binary, "call", tool, json.dumps(payload)],
            capture_output=True, text=True, timeout=self.timeout,
        )
        text = (proc.stdout or "").strip()
        if proc.returncode != 0:
            detail = (proc.stderr or text or "").strip()[:300]
            raise RuntimeError(f"{tool} failed ({proc.returncode}): {detail}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            return json.loads(text[start:]) if start >= 0 else {}

    @staticmethod
    def _signature_of(observation: Dict[str, Any]) -> tuple:
        """A cheap fingerprint of what the window currently shows.

        Count plus a label fingerprint — enough to notice a changed form, a new
        dialog or a completed navigation without hashing the whole tree.
        """
        actions = observation.get("actions", [])
        labels = tuple(sorted(str(item.get("label", ""))[:40] for item in actions))
        return (len(actions), labels[:12])

    def _absorb(self, observation: Dict[str, Any]) -> None:
        self._by_index = {str(position): item
                          for position, item in enumerate(observation.get("actions", []), start=1)}
        self._snapshot_id = observation.get("snapshot_id", "") or ""
        self._signature = self._signature_of(observation)

    # ── the loop's Driver protocol ───────────────────────────────────────────

    def observe(self) -> Dict[str, Any]:
        # execute() already re-walked the tree to judge page_changed; reuse that
        # snapshot instead of paying a second AX walk per step.
        if self._cached is not None:
            observation, self._cached = self._cached, None
            return observation
        observation = self.observer.observe()
        self._absorb(observation)
        return observation

    def execute(self, operation: str, element: Optional[Any], text: Optional[str] = None) -> Dict[str, Any]:
        """Perform one operation. Returns {"ok", "detail", "page_changed"}."""
        if self._closed:
            return {"ok": False, "detail": "driver closed"}

        # The loop hands back the ElementRef it built from our observation. `handle`
        # carries the cua-driver element token, which is what identifies one exact
        # snapshot element on the AX action path.
        token = getattr(element, "handle", None) if element is not None else None
        label = getattr(element, "label", "") if element is not None else ""

        try:
            if operation == "CLICK":
                if not token:
                    return {"ok": False, "detail": "no element handle for CLICK"}
                self._call("click", {"pid": self.pid, "window_id": self.window_id,
                                     "element_token": str(token)})

            elif operation == "TYPE_TEXT":
                if not text:
                    return {"ok": False, "detail": "TYPE_TEXT with no text"}
                payload: Dict[str, Any] = {"pid": self.pid, "text": text,
                                           "delivery_mode": self.delivery_mode}
                if token:
                    payload["window_id"] = self.window_id
                    payload["element_token"] = str(token)
                self._call("type_text", payload)

            elif operation == "SELECT":
                if not text:
                    return {"ok": False, "detail": "SELECT with no option value"}
                if not token:
                    return {"ok": False, "detail": "no element handle for SELECT"}
                self._call("set_value", {"pid": self.pid, "window_id": self.window_id,
                                         "element_token": str(token), "value": str(text)})

            elif operation in ("SCROLL_DOWN", "SCROLL_UP"):
                self._call("scroll", {"pid": self.pid,
                                      "direction": "down" if operation == "SCROLL_DOWN" else "up",
                                      "by": "page", "amount": 1})

            elif operation == "WAIT":
                time.sleep(0.4)

            else:
                return {"ok": False, "detail": f"unsupported operation {operation!r}"}
        except Exception as error:  # a driver failure is not the model's fault
            return {"ok": False, "detail": f"driver error: {error}"}

        # Judge whether the action changed anything, and keep the fresh observation
        # for the loop's next observe() call.
        if self.settle_ms:
            time.sleep(self.settle_ms / 1000)
        changed: Optional[bool] = None
        try:
            fresh = self.observer.observe()
            changed = self._signature_of(fresh) != self._signature
            self._absorb(fresh)
            self._cached = fresh
        except Exception:
            changed = None
        detail = f"ok {label}".strip() if label else "ok"
        return {"ok": True, "detail": detail, "page_changed": changed}

    def close(self) -> None:
        """Idempotent: the loop's finally block and the caller's may both call it."""
        self._closed = True

    def __enter__(self) -> "CuaDriver":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def DesktopLoop(*args: Any, decider: Any = None, model: Optional[str] = None,
                subfolder: Optional[str] = None, **kwargs: Any):
    """Construct the browser project's loop, targeting a desktop driver.

    The browser loop is already surface-agnostic (`observe` / `execute` / `close`),
    so there is nothing to subclass. Importing it lazily keeps this package
    importable without the browser extra installed, and keeps ONE implementation of
    the guards — a desktop-only fork of the loop would drift from it.

    With no `decider`, the loop binds to the **official Laya checkpoint** (see
    `models.py`) through a lazy shim: the checkpoint is not touched until the first
    decision, so `DesktopLoop()` still constructs in an environment with no model
    runtime. Pass `decider=` to bind something else — e.g. a checkpoint trained for
    this desktop task — or `model=`/`subfolder=` to pick another official checkpoint.
    """
    if args:
        # BrowserDecider's only positional parameter is its decider; everything else
        # is keyword-only. Keep a positional decider working.
        decider, args = args[0], args[1:]
    if decider is None:
        decider = OfficialDecider(model, subfolder)
    try:
        from localdecide.loop import BrowserDecider
    except ImportError as error:  # pragma: no cover
        raise ImportError(
            "laya-computer-use reuses the decision loop from laya-browser-agent. "
            "Install it with: pip install laya-browser-agent (or set PYTHONPATH)"
        ) from error
    return BrowserDecider(*args, decider=decider, **kwargs)


class OfficialDecider:
    """Lazy `Decider` bound to an official Laya checkpoint (the repo's default).

    A loop constructed without an explicit decider must not fall through to whatever
    default the engine happens to ship — this project runs the official checkpoints.
    The inner `Decider` is built on first use, so nothing here needs a model runtime
    at import or construction time.
    """

    def __init__(self, model: Optional[str] = None, subfolder: Optional[str] = None) -> None:
        self.model = model
        self.subfolder = subfolder
        self._inner: Any = None

    def decide(self, state: Any, questions: Any) -> Any:
        if self._inner is None:
            from .models import decider_for

            self._inner = decider_for(self.model, self.subfolder)
        return self._inner.decide(state, questions)
