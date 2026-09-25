"""CLI: `lcu` — observe, decide and execute desktop steps from a shell.

    lcu windows                         # list driveable windows
    lcu observe 1234 5678               # the AX table the model sees
    lcu decide  1234 5678 "打开显示设置"  # dry run: what would it do?
    lcu run     1234 5678 "打开显示设置"  # full loop, executing steps
    lcu desktop                         # whole-computer table: apps + windows
    lcu desktop-run "open Calculator"   # whole-computer loop (switch, launch, menus)
    lcu doctor                          # environment check

The decision layer runs an official Laya checkpoint by default
(`convaiinnovations/laya`, the English one). Pick another with `--model`:

    lcu decide 1234 5678 "显示设置" --model multilingual      # official, 100+ languages
    lcu decide 1234 5678 "显示设置" --model /path/to/checkpoint  # your own, trained for this

`--layout` picks the state format: `v3` (default; elements only in the option
lists — what browser-trained checkpoints expect) or `v1` (elements also inside the
state — the Jev-style layout). Measured: the layout decides whether some hosted
models can answer at all, so it is a first-class flag, not a hidden default.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

from .models import DEFAULT as DEFAULT_MODEL, decider_for, describe as describe_model


def _cua_call(tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    binary = shutil.which("cua-driver") or "cua-driver"
    proc = subprocess.run([binary, "call", tool, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    text = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise SystemExit(f"cua-driver {tool} failed: {(proc.stderr or text).strip()[:300]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        return json.loads(text[start:]) if start >= 0 else {}


def _make_decider(model: Optional[str], subfolder: Optional[str]):
    """Decider bound to the requested checkpoint (official by default)."""
    return decider_for(model, subfolder)


def cmd_windows(args: argparse.Namespace) -> int:
    raw = _cua_call("list_windows", {})
    rows = [window for window in raw.get("windows", [])
            if (not args.on_screen or window.get("is_on_screen"))]
    for window in rows:
        title = (window.get("title") or "").strip()
        if not title and window.get("layer", 0) != 0:
            continue
        print(f"pid={window.get('pid'):<8} window_id={window.get('window_id'):<8} "
              f"{window.get('app_name', '?'):<28} {title[:60]}")
    return 0


def cmd_observe(args: argparse.Namespace) -> int:
    from .ax import AXObserver
    observation = AXObserver(pid=args.pid, window_id=args.window_id,
                             max_elements=args.max_elements).observe()
    print(f"window: {observation.get('title')!r}  "
          f"({len(observation.get('actions', []))} actionable elements)")
    for position, item in enumerate(observation.get("actions", []), start=1):
        flags = [item.get("kind", "")]
        if item.get("disabled"):
            flags.append("disabled")
        if item.get("selected") is not None:
            flags.append(f"selected={item['selected']}")
        print(f"  [{position:>2}] {item.get('label', '')[:60]:<60} ({', '.join(flags)})")
    return 0


def _decide_once(pid: int, window_id: int, goal: str, max_elements: int,
                 text: Optional[str], execute: bool,
                 model: Optional[str] = None, subfolder: Optional[str] = None) -> Dict[str, Any]:
    from localdecide.loop import ElementRef
    from localdecide.page import build_element_table, table_to_questions

    from .driver import CuaDriver

    driver = CuaDriver(pid=pid, window_id=window_id, max_elements=max_elements)
    observation = driver.observe()
    table = build_element_table(observation)
    questions = table_to_questions(table, goal)

    decider = _make_decider(model, subfolder)
    decision = decider.decide(table.state(), questions)
    if not decision.ok:
        return {"ok": False, "error": decision.error}

    answers = decision.answers
    operation = answers.choice("operation")
    payload: Dict[str, Any] = {
        "ok": True,
        "model": describe_model(model, subfolder),
        "operation": operation,
        "confidence": round(answers.confidence("operation"), 3),
        "elements": len(table.elements),
    }
    element: Optional[ElementRef] = None
    question = f"{operation.lower()}_target"
    if question in answers.raw:
        index = answers.choice(question)
        found = table.by_index().get(index)
        if found is not None:
            element = ElementRef(found.index, found.label, found.role, found.handle,
                                 {**found.meta, "checked": found.checked},
                                 options=list(found.options))
            payload["target"] = {"n": int(index), "label": found.label}
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

    if execute and operation not in ("DONE", "BLOCKED", "WAIT"):
        result = driver.execute(operation, element, text if operation != "SELECT" else option)
        payload["executed"] = bool(result.get("ok"))
        payload["detail"] = result.get("detail", "")
        payload["page_changed"] = result.get("page_changed")
    return payload


def cmd_decide(args: argparse.Namespace) -> int:
    payload = _decide_once(args.pid, args.window_id, args.goal, args.max_elements,
                           args.text, execute=False,
                           model=args.model, subfolder=args.subfolder)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


def cmd_run(args: argparse.Namespace) -> int:
    from .driver import CuaDriver, DesktopLoop

    print(f"model: {describe_model(args.model, args.subfolder)}")
    driver = CuaDriver(pid=args.pid, window_id=args.window_id,
                       max_elements=args.max_elements)
    loop = DesktopLoop(
        decider=_make_decider(args.model, args.subfolder),
        max_steps=args.max_steps,
        text_provider=(lambda _g, _e: args.text) if args.text else None,
        min_confidence=args.min_confidence,
    )
    run = loop.run(driver, args.goal)
    summary = run.summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for step in run.steps:
        mark = "->" if step.executed else "  "
        print(f"  {mark} {step.n:>2}. {step.operation:<10} {step.label[:40]:<40} "
              f"p={step.confidence:.2f} {step.detail[:60]}")
    return 0 if run.solved else 1


def cmd_desktop(args: argparse.Namespace) -> int:
    """The whole-computer table: apps (running and installed) + on-screen windows."""
    from .desktop_driver import DesktopDriver

    driver = DesktopDriver(max_apps=args.max_apps, max_windows=args.max_windows,
                           include_installed=not args.running_only,
                           menu_items=not args.no_menu)
    observation = driver.observe()
    print(f"desktop: {len(observation.get('actions', []))} rows "
          f"({args.max_apps} apps max, {args.max_windows} windows max)")
    for item in observation.get("actions", []):
        meta = item.get("meta") or {}
        rung = meta.get("desktop_kind", "")
        flags = [rung] if rung else [item.get("kind", "")]
        if item.get("disabled"):
            flags.append("disabled")
        path = meta.get("menu")
        if path:
            flags.append("menu: " + " > ".join(str(p) for p in path))
        print(f"  [{item.get('index'):>3}] {str(item.get('label'))[:66]:<66} ({', '.join(flags)})")
    driver.close()
    return 0


def cmd_desktop_run(args: argparse.Namespace) -> int:
    """Whole-computer loop: the model may switch apps, launch them, use menus."""
    from .desktop_driver import DesktopDriver
    from .driver import DesktopLoop

    print(f"model: {describe_model(args.model, args.subfolder)}")
    driver = DesktopDriver(max_apps=args.max_apps, max_windows=args.max_windows,
                           include_installed=not args.running_only,
                           menu_items=not args.no_menu)
    loop = DesktopLoop(
        decider=_make_decider(args.model, args.subfolder),
        max_steps=args.max_steps,
        text_provider=(lambda _g, _e: args.text) if args.text else None,
        min_confidence=args.min_confidence,
    )
    run = loop.run(driver, args.goal)
    summary = run.summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for step in run.steps:
        mark = "->" if step.executed else "  "
        print(f"  {mark} {step.n:>2}. {step.operation:<10} {step.label[:40]:<40} "
              f"p={step.confidence:.2f} {step.detail[:60]}")
    driver.close()
    return 0 if run.solved else 1


def cmd_doctor(args: argparse.Namespace) -> int:

    print(f"python      : {sys.version.split()[0]} ({platform.machine()})")
    print(f"cua-driver  : {shutil.which('cua-driver') or 'NOT FOUND — install from trycua/cua'}")
    perms: Dict[str, Any] = {}
    if shutil.which("cua-driver"):
        try:
            proc = subprocess.run(["cua-driver", "permissions", "status", "--json"],
                                  capture_output=True, text=True, timeout=30)
            perms = json.loads(proc.stdout or "{}")
        except Exception as error:
            perms = {"error": str(error)}
    print(f"accessibility: {perms.get('accessibility', 'unknown')}")
    print(f"screen record: {perms.get('screen_recording', 'unknown')}")
    for module, label in (("laya_mlx", "laya-mlx (Apple Silicon)"), ("laya", "laya (PyTorch)")):
        try:
            __import__(module)
            print(f"runtime     : {label} ok")
        except ImportError:
            print(f"runtime     : {label} not installed")
    try:
        from localdecide.page import build_element_table  # noqa: F401
        print("engine      : laya-browser-agent loop ok")
    except ImportError as error:
        print(f"engine      : MISSING ({error})")
    print(f"checkpoint  : {describe_model(args.model, args.subfolder)} (official Laya)")
    return 0


def _add_model_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model", default=None,
        help=(f"checkpoint to decide with: a registry name ({DEFAULT_MODEL}, multilingual, "
              "typed-decisions), a hub id (org/repo), or a local checkpoint directory. "
              "Default: the official English Laya checkpoint."))
    parser.add_argument("--subfolder", default=None,
                        help="subfolder inside the checkpoint repo (registry names set this for you)")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="lcu", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    windows = sub.add_parser("windows", help="list driveable windows")
    windows.add_argument("--on-screen", action="store_true", default=False)
    windows.set_defaults(func=cmd_windows)

    observe = sub.add_parser("observe", help="print the AX table for a window")
    observe.add_argument("pid", type=int)
    observe.add_argument("window_id", type=int)
    observe.add_argument("--max-elements", type=int, default=40)
    observe.set_defaults(func=cmd_observe)

    decide = sub.add_parser("decide", help="dry run: what would the model do?")
    decide.add_argument("pid", type=int)
    decide.add_argument("window_id", type=int)
    decide.add_argument("goal")
    decide.add_argument("--max-elements", type=int, default=40)
    decide.add_argument("--text", default=None, help="payload for TYPE_TEXT")
    _add_model_flags(decide)
    decide.set_defaults(func=cmd_decide)

    run = sub.add_parser("run", help="run the full loop, executing steps")
    run.add_argument("pid", type=int)
    run.add_argument("window_id", type=int)
    run.add_argument("goal")
    run.add_argument("--max-elements", type=int, default=40)
    run.add_argument("--max-steps", type=int, default=15)
    run.add_argument("--min-confidence", type=float, default=0.15)
    run.add_argument("--text", default=None)
    _add_model_flags(run)
    run.set_defaults(func=cmd_run)

    doctor = sub.add_parser("doctor", help="check the environment")
    _add_model_flags(doctor)
    doctor.set_defaults(func=cmd_doctor)

    desktop = sub.add_parser("desktop", help="print the whole-computer table (apps + windows)")
    desktop.add_argument("--max-apps", type=int, default=30)
    desktop.add_argument("--max-windows", type=int, default=12)
    desktop.add_argument("--running-only", action="store_true",
                         help="exclude installed-but-closed apps")
    desktop.add_argument("--no-menu", action="store_true",
                         help="do not offer the app menu bar as invocable items")
    desktop.set_defaults(func=cmd_desktop)

    desktop_run = sub.add_parser("desktop-run", help="whole-computer loop: apps, windows, menus")
    desktop_run.add_argument("goal")
    desktop_run.add_argument("--max-apps", type=int, default=30)
    desktop_run.add_argument("--max-windows", type=int, default=12)
    desktop_run.add_argument("--running-only", action="store_true")
    desktop_run.add_argument("--no-menu", action="store_true")
    desktop_run.add_argument("--max-steps", type=int, default=15)
    desktop_run.add_argument("--min-confidence", type=float, default=0.15)
    desktop_run.add_argument("--text", default=None)
    _add_model_flags(desktop_run)
    desktop_run.set_defaults(func=cmd_desktop_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
