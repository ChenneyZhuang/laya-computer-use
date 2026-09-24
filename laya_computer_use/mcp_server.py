"""MCP server: let any MCP client drive a desktop window through local Laya decisions.

Speaks JSON-RPC over stdio, stdlib only (no `mcp` SDK required) — the same shape the
browser project ships, so one client config style covers both.

Tools:
    list_windows   - running apps and their on-screen windows (pick a target)
    desktop_decide - read a window's AX tree, decide, and optionally execute one step
    desktop_observe - the AX observation as the model sees it (debugging/grounding)

Wire it into a client (Claude Desktop / Cursor / any MCP client):

    {
      "mcpServers": {
        "laya-computer-use": {
          "command": "lcu-mcp"
        }
      }
    }
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "laya-computer-use", "version": "0.1.0"}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_windows",
        "description": ("List running macOS apps and their windows. Use it to find the "
                        "pid and window_id to drive."),
        "inputSchema": {"type": "object", "properties": {
            "on_screen_only": {"type": "boolean", "default": True,
                               "description": "Only return windows currently visible."},
        }},
    },
    {
        "name": "desktop_observe",
        "description": ("Read one window's Accessibility tree as an observation: the "
                        "numbered table of controls the decision model sees."),
        "inputSchema": {
            "type": "object",
            "required": ["pid", "window_id"],
            "properties": {
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
                "max_elements": {"type": "integer", "default": 40},
            },
        },
    },
    {
        "name": "desktop_decide",
        "description": ("Decide the next step for a goal on a window, and optionally "
                        "execute it. Returns the operation, the chosen control, its "
                        "confidence, and (with execute=true) whether it ran."),
        "inputSchema": {
            "type": "object",
            "required": ["pid", "window_id", "goal"],
            "properties": {
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
                "goal": {"type": "string", "description": "What to accomplish, in any language."},
                "execute": {"type": "boolean", "default": False,
                            "description": "Run the chosen operation. Default false = dry run."},
                "text": {"type": "string",
                         "description": "Payload for TYPE_TEXT/SELECT when the caller "
                                        "supplies it instead of a text provider."},
                "max_elements": {"type": "integer", "default": 40},
                "model": {"type": "string",
                          "description": ("Checkpoint to decide with: a registry name "
                                          "(english, multilingual, typed-decisions), a hub id, "
                                          "or a local checkpoint directory. Default: official English Laya.")},
            },
        },
    },
]


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, payload: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _text_result(payload: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


def _driver_call(tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """One cua-driver CLI call, shared by every MCP tool."""
    import shutil
    import subprocess

    binary = shutil.which("cua-driver") or "cua-driver"
    proc = subprocess.run([binary, "call", tool, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    text = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or text or "driver call failed").strip()[:300])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        return json.loads(text[start:]) if start >= 0 else {}


def _list_windows(on_screen_only: bool = True) -> Dict[str, Any]:
    raw = _driver_call("list_windows", {})
    items = []
    for window in raw.get("windows", []):
        if on_screen_only and not window.get("is_on_screen"):
            continue
        if not (window.get("title") or "").strip() and window.get("layer", 0) != 0:
            continue
        items.append({
            "pid": window.get("pid"),
            "window_id": window.get("window_id"),
            "app": window.get("app_name"),
            "title": window.get("title"),
            "bounds": window.get("bounds"),
            "on_screen": window.get("is_on_screen"),
        })
    return {"windows": items}


def _observe(pid: int, window_id: int, max_elements: int = 40) -> Dict[str, Any]:
    from .ax import AXObserver
    observation = AXObserver(pid=pid, window_id=window_id, max_elements=max_elements).observe()
    return {
        "title": observation.get("title"),
        "elements": len(observation.get("actions", [])),
        "table": [
            {"n": position, "label": item.get("label"), "kind": item.get("kind"),
             "role": item.get("role"), "disabled": item.get("disabled")}
            for position, item in enumerate(observation.get("actions", []), start=1)
        ],
    }


def _decide(pid: int, window_id: int, goal: str, *, execute: bool = False,
            text: Optional[str] = None, max_elements: int = 40,
            model: Optional[str] = None, subfolder: Optional[str] = None) -> Dict[str, Any]:
    from localdecide.page import build_element_table, table_to_questions

    from .driver import CuaDriver
    from .models import decider_for

    driver = CuaDriver(pid=pid, window_id=window_id, max_elements=max_elements)
    observation = driver.observe()
    table = build_element_table(observation)
    questions = table_to_questions(table, goal)

    decider = decider_for(model, subfolder)
    decision = decider.decide(table.state(), questions)
    if not decision.ok:
        return {"ok": False, "error": decision.error, "elements": len(table.elements)}

    answers = decision.answers
    operation = answers.choice("operation")
    payload: Dict[str, Any] = {
        "ok": True,
        "operation": operation,
        "confidence": round(answers.confidence("operation"), 3),
        "elements": len(table.elements),
    }
    from localdecide.loop import ElementRef
    element: Optional[ElementRef] = None
    question = f"{operation.lower()}_target"
    if question in answers.raw:
        index = answers.choice(question)
        found = table.by_index().get(index)
        if found is not None:
            element = ElementRef(found.index, found.label, found.role, found.handle,
                                 {**found.meta, "checked": found.checked}, options=list(found.options))
            payload["target"] = {"n": int(index), "label": found.label, "role": found.role}
            payload["target_confidence"] = round(answers.confidence(question), 3)

    option: Optional[str] = None
    if operation == "SELECT" and element is not None:
        option_question = (f"select_option_{element.index}"
                           if f"select_option_{element.index}" in answers.raw else "select_option")
        if option_question in answers.raw:
            key = answers.choice(option_question)
            for candidate in element.options:
                if str(candidate.get("index")) == str(key):
                    option = str(candidate.get("value") or candidate.get("label") or "")
                    break
            payload["option"] = option

    if execute:
        if operation in ("DONE", "BLOCKED", "WAIT"):
            payload["executed"] = False
            payload["detail"] = f"{operation} needs no action"
        else:
            result = driver.execute(operation, element, text if operation != "SELECT" else option)
            payload["executed"] = bool(result.get("ok"))
            payload["detail"] = result.get("detail", "")
            payload["page_changed"] = result.get("page_changed")
    return payload


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One JSON-RPC message in, one out (or None for notifications)."""
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        client_version = str(params.get("protocolVersion", ""))
        # Negotiate per the MCP spec: echo a version we support, otherwise answer with ours.
        version = client_version if client_version in {"2025-06-18", "2024-11-05"} else PROTOCOL_VERSION
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "ping":
        return _result(request_id, {})
    if method != "tools/call":
        return _error(request_id, -32601, f"method not found: {method}")

    name = params.get("name")
    arguments = params.get("arguments") or {}
    try:
        if name == "list_windows":
            payload = _list_windows(bool(arguments.get("on_screen_only", True)))
        elif name == "desktop_observe":
            payload = _observe(int(arguments["pid"]), int(arguments["window_id"]),
                               int(arguments.get("max_elements", 40)))
        elif name == "desktop_decide":
            payload = _decide(int(arguments["pid"]), int(arguments["window_id"]),
                              str(arguments["goal"]),
                              execute=bool(arguments.get("execute", False)),
                              text=arguments.get("text"),
                              max_elements=int(arguments.get("max_elements", 40)),
                              model=arguments.get("model"))
        else:
            return _error(request_id, -32602, f"unknown tool: {name}")
    except KeyError as missing:
        return _error(request_id, -32602, f"missing argument: {missing}")
    except Exception as error:
        return _result(request_id, {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False)}],
            "isError": True})
    return _result(request_id, _text_result(payload))


def main() -> None:
    """stdio loop: one JSON object per line in, one per line out."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
