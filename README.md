# laya-computer-use

**Local, open-source computer use for macOS.** [Laya](https://github.com/NandhaKishorM/laya) —
the open System One decision model — reads a real window's Accessibility tree and decides
every step. No screenshots, no vision model, no coordinates, no cloud.

```python
from laya_computer_use import CuaDriver, DesktopLoop

driver = CuaDriver(pid=1234, window_id=5678)
run = DesktopLoop().run(driver, "open the display settings")
print(run.summary())
```

A local alternative to [TypeSafe's Jev](https://docs.typesafe.ai) for the desktop-driving
use case: same typed-decision idea, running entirely on your machine, at zero per-step cost.

English | [中文](README.zh-CN.md)

---

## Why a decision model instead of a vision model

Most computer-use agents loop *screenshot → ask a multimodal LLM → click coordinates*. That
loop is expensive, slow, and coordinates are the least reliable thing a small model can
produce. This project takes the other route, the one a screen reader takes:

1. **Observe** — walk the macOS Accessibility (AX) tree: roles, labels, values, enabled and
   selected state, and which actions each element supports.
2. **Decide** — hand the model a numbered table of those controls and a goal. It answers, in
   one forward pass, which operation to run (`CLICK`/`TYPE_TEXT`/`SELECT`/`SCROLL`/`WAIT`/`DONE`)
   and which element index to act on.
3. **Execute** — your code resolves that index to the real AX element and performs the action.

**The model never sees a coordinate and never emits an action.** It picks from what was
observed, so a wrong answer is a wrong *choice*, never an invented one.

| | Screenshot + vision LLM | AX tree + decision model (this project) |
|---|---|---|
| Per step | one multimodal request | one local forward pass (measured below) |
| Cost | per-token, per-step | **$0** |
| Data | screen pixels leave the machine | **nothing leaves the machine** |
| Target resolution | guessed from pixels | **exact** — the element came from the tree |
| Fails at | small text, dense toolbars | custom-drawn surfaces with no AX nodes |

---

## What is reused, and what is new

This project is deliberately thin. The decision loop — guards, confidence gates, history
tracking, goal-aware scoping, the element-table contract, the `/v1/systemone` HTTP layer and
the model backends — is **imported from
[laya-browser-agent](https://github.com/ChenneyZhuang/laya-browser-agent)** (the browser
sibling of this project), not re-implemented. A desktop-only fork of those files would drift
from the browser implementation, and both would then disagree about what a "safe step" means.

| Reused from `laya-browser-agent` | New in this project |
|---|---|
| `page.py` — element table, questions, operation vocabulary | `ax.py` — AX tree → element table |
| `loop.py` — observe/decide/act loop, toggle guard, confidence gate, loop guard | `driver.py` — `CuaDriver` (AX press / type / set_value / scroll) |
| `scope.py` — goal-aware element ranking and truncation | `mcp_server.py` — MCP tools over stdio |
| `decider.py` + backends — validated decisions, MLX/PyTorch/HTTP | `cli.py` — `lcu` command |
| `grounding.py` — cross-script candidate filtering | `models.py` — checkpoint registry |
| | `benchmarks/` — the AX batteries in this repo |

---

## What was measured

Every number below comes from a committed script in `benchmarks/`, on one M4 Mac (16 GB).
The decision layer runs the **official Laya checkpoints** published at
[`convaiinnovations/laya`](https://huggingface.co/convaiinnovations/laya) — all three of
them (English, multilingual, typed-decisions), MLX backend. No weights are redistributed.

**The headline, stated plainly:** the official checkpoints answer the quickstart-style
questions they were trained for, and they do **not** select controls from a desktop AX
tree zero-shot — they answer the operation question with `DONE`/`WAIT` without engaging
with the options. This repo is the harness that measures that gap, and the place a
fine-tuned checkpoint drops into (see [Roadmap](#roadmap)).

### The checkpoints are healthy (control experiment)

Before reading anything into the gap: run the model card's own example through the same
backend path. If the checkpoint is broken, this is where it shows.

```
python3 -m benchmarks.quickstart_sanity

english        department=billing (p=0.926) urgency=1.772 churn=0.88   [874 ms]
multilingual   department=billing (p=1.0)   urgency=1.636 churn=0.008  [1156 ms]
```

Both answer correctly and confidently on email triage (their designed task), in their
designed languages. So the gap below is a task-format gap, not a broken download.

### Control selection — 12 cases, real + synthetic

The battery asks 12 harness-shaped decisions against a real Calculator AX tree and two
synthetic panels: operation (`CLICK`/`TYPE_TEXT`) + which element index.

| Checkpoint | All | English goals,<br>Chinese UI | Chinese goals,<br>Chinese UI | English labels,<br>synthetic | Load |
|---|---|---|---|---|---|
| laya-en (official) | 0/12 | 0/4 | 0/4 | 0/4 | 0.8 s |
| laya-multilingual (official) | 0/12 | 0/4 | 0/4 | 0/4 | 1.0 s |
| laya-typed-decisions (official) | 0/12 | 0/4 | 0/4 | 0/4 | 0.6 s |

`python3 -m benchmarks.checkpoint_battery` reproduces this table. The failure mode is
consistent: `laya-en` answers `DONE`, `laya-multilingual` answers `WAIT` — regardless of
the goal, the labels, or which options were offered.

### Four fairness controls — it is not an artifact of how we ask

A zero like this deserves suspicion, so each plausible explanation was tested:

1. **Option count** (`benchmarks/option_scaling.py`): the same target is planted and the
   option list is grown 1 → 2 → 3 → 5 → 8 → 12 → 16 → 22. The answer never changes
   (`DONE` at p=0.417 for `laya-en`; `WAIT` at p=0.792 for `laya-multilingual`, at every
   size). The checkpoints are not "overwhelmed by options" — they are not reading them.
2. **State layout** (`benchmarks/format_fairness.py`): the element table is served both
   ways — `v3` (table only in the option lists) and `v1` (table also inside the state).
   `laya-en`: 0/8 and 1/8 (the single hit is one lucky pick on "clear"). Not a layout issue.
3. **Question wording** (`benchmarks/direct_question_probe.py`): the same question is asked
   four ways — the harness's structured instructions, plain sentences, the goal text alone,
   and the simplest possible shape ("which button should be pressed to clear the display?").
   All four miss.
4. **Position vs label** (`benchmarks/position_control.py`): option order is shuffled
   (5 seeded trials × 6 goals) to separate "read the label" from "memorised the slot".
   Result: **0 of 30 trials chose any control at all** — there is nothing to be stable
   about until the model engages with element selection.

### The AX bridge works — verified end to end

The adapter, driver and result-verification are exercised against a real app (TextEdit,
real AX tree, real editable document body):

```
python3 -m benchmarks.e2e_loop <pid> <window_id>      # decision loop, official checkpoint
document before: 'test fixture'
  1. TYPE_TEXT  test fixture    p=0.31 executed=True   (text really entered)
  ...
  6. TYPE_TEXT  ...             p=0.41 executed=False  (loop guard: no page change)
document after:  'LOOP-…LOOP-…test fixture'
bridge: PASS (text landed: True) | autonomous: NO (stopped='error', steps=6)

python3 -m benchmarks.e2e_textedit <pid> <window_id>  # AX bridge alone, no model
VERDICT: PASS — text landed in the document
```

Two honest verdicts, because they fail separately: the **bridge** (observe → decide →
execute → effect verified in the document) passes; **autonomous stop** (the model deciding
`DONE` on its own) does not — the official checkpoint re-types instead of stopping, and the
loop's guard is what ends the run. The guards earning their keep is part of the design;
the missing stop is part of the gap.

### Latency

| Stage | Measured |
|---|---|
| AX walk, live window | **~110 ms** median (22 actionable of 169 raw elements; 142 of the raw nodes are menu chrome) |
| One decision, official checkpoint, ≤20 options | **~110 ms** |
| One decision, 40 options | **~230 ms** |
| Total per step (observation + decision) | ~220 ms, **$0** |

Reproduce with `benchmarks/latency_scaling.py` and `benchmarks/ax_walk.py`. The decision
at ≤20 options is about the cost of the AX walk — that balance is why the design caps the
table at 40 elements and ranks rather than enlarges.

---

## Install

```bash
git clone https://github.com/ChenneyZhuang/laya-computer-use
cd laya-computer-use
python3 -m venv .venv && source .venv/bin/activate

pip install -e ".[mlx]"        # Apple Silicon (fastest)
# pip install -e ".[torch]"    # any platform with PyTorch

pip install cua-driver          # from github.com/trycua/cua — the AX reader/executor
cua-driver permissions grant    # one-time: Accessibility + Screen Recording
lcu doctor                      # verify the whole stack
```

`laya-computer-use` depends on [`laya-browser-agent`](https://github.com/ChenneyZhuang/laya-browser-agent)
for the decision loop. Until that is on PyPI, install it from its repo first:

```bash
pip install "git+https://github.com/ChenneyZhuang/laya-browser-agent.git"
```

The first decision for a checkpoint downloads it (~650–850 MB per checkpoint).

### Requirements

- macOS 13+ (Accessibility API and `cua-driver`)
- Python 3.10+
- Apple Silicon for the MLX backend; PyTorch backend works anywhere
- ~1 GB RAM for the checkpoint resident (one model per machine — see below)

---

## Use

### Checkpoints

The decision layer runs an **official Laya checkpoint**; pick one by name, hub id, or local path:

| --model | checkpoint | backbone | size | context |
|---|---|---|---|---|
| `english` *(default)* | `convaiinnovations/laya` | ModernBERT-large | 421M | 512 |
| `multilingual` | `convaiinnovations/laya` subfolder | mmBERT-base | 322M | 1024, up to 8192 |
| `typed-decisions` | `convaiinnovations/laya` subfolder | ModernBERT-large | 421M | 1024 |
| any hub id | e.g. `org/repo` | — | — | — |
| any local dir | a checkpoint trained for *this* task | — | — | — |

A checkpoint fine-tuned for desktop control drops in with no code change:

```bash
lcu decide 1234 5678 "clear the display" --model /path/to/my-desktop-checkpoint
```

### CLI

```bash
lcu windows                              # find a pid + window_id
lcu observe 1234 5678                    # the table the model sees
lcu decide  1234 5678 "显示设置"           # dry run: what would it do?
lcu run     1234 5678 "显示设置"           # full loop, executing steps
lcu decide  1234 5678 "显示设置" --model multilingual
```

### Python

```python
from laya_computer_use import CuaDriver, DesktopLoop
from laya_computer_use.models import resolve as resolve_model

driver = CuaDriver(pid=1234, window_id=5678, max_elements=40)
hub, sub = resolve_model("english")            # or a path to your own checkpoint
loop = DesktopLoop(
    model=hub, subfolder=sub,                  # official Laya checkpoint
    # decider=Decider(model="/path/to/checkpoint"),  # or bring your own
    max_steps=15,
    text_provider=lambda goal, element: "Sydney",   # a text model, a lookup, a regex
    confirm=lambda label, element: input(f"{label}? [y/N] ").lower() == "y",
)
run = loop.run(driver, "fill the destination field with the city")
print(run.summary())
```

### MCP (Claude Desktop, Cursor, any MCP client)

```json
{
  "mcpServers": {
    "laya-computer-use": { "command": "lcu-mcp" }
  }
}
```

Tools: `list_windows`, `desktop_observe`, `desktop_decide` (dry-run by default — an agent
must explicitly pass `execute: true` to click anything; `model` selects the checkpoint).

---

## Design notes

### 142 of 169 elements were the menu bar

A naive AX walk returns the entire OS menu with every window. On the live Calculator
snapshot that is **142 of 169 nodes**, and it is the *same* menu in every app — pure noise
that burns the model's option budget. `ax.py` drops `AXMenuBar`/`AXMenu` subtrees and ranks
what remains (editables, then dropdowns, then buttons; enabled before disabled). The
169-node window becomes a 22-row table.

### The role vocabulary is web-shaped on purpose

The checkpoints learned web roles. Feeding them `AXButton` would be a vocabulary shift for
no benefit, so AX roles map onto the closest web role (`AXButton`→`button`,
`AXTextField`→`textbox`). The executor keeps the real AX role.

### `enabled: false` is data, not noise

Disabled controls stay in the table — the model needs to see that a control exists but is
unavailable, because `WAIT` is a legitimate and often correct answer. They simply rank
below their enabled peers.

### A label alone is not a capability

An element enters the table only if its role *and* its AX actions support the operation
(`AXPress`, `AXPick`, or being editable). A button with no actions cannot be clicked, and
offering it would be a trap.

### page_changed is measured from the AX tree

The browser driver compares URL signatures after acting. A desktop window has no URL, so the
equivalent is an AX signature (control count + label fingerprint) re-read after the action —
about one extra AX walk per step (~110 ms), which is why the driver reuses that snapshot as
the next observation instead of walking twice.

### The guards are inherited, and they matter

The loop refuses to act below a confidence floor, refuses to re-perform a toggle already in
the requested state, refuses repeated actions with no change, and stops for confirmation on
irreversible-looking targets. Those guards exist because the model needed them on the web;
the same failure modes appear on the desktop — the e2e run above is the loop guard stopping
a repeating model, exactly as designed.

---

## Limitations (measured, not guessed)

- **Official checkpoints do not select controls zero-shot.** 0/12 on the battery, 0/30 on
  the shuffle probe, and the four fairness controls rule out option count, layout, and
  wording. The checkpoints are healthy (see the control experiment); the missing piece is
  training data of the shape "goal + numbered controls → pick an index". That is the
  Roadmap's first item, and it is what `--model` is for.
- **Autonomous stopping is not there either.** In the e2e run the checkpoint never decided
  `DONE`; the harness's loop guard ended the run. Do not run this unattended without
  `max_steps` and the guards.
- **Custom-drawn surfaces are invisible.** Calculator's display is not an AX node — the tree
  shows its buttons and nothing else. Anything drawn into a canvas (games, some charting
  apps, parts of Electron apps) cannot be observed. Same class of limitation as "elements
  inside a collapsed menu are not in the DOM".
- **One checkpoint per machine.** Loading two ~1 GB models alongside a browser on a 16 GB
  laptop is how machines swap to death. One model, one moment; the batteries run each
  checkpoint in a subprocess for exactly this reason.
- **No Windows/Linux.** The AX bridge is macOS-only. The loop, guards and model layers are
  portable, so a UIA (Windows) or AT-SPI (Linux) adapter is the natural next step — see
  below.

---

## Roadmap

The honest next steps, in order of value:

1. **A desktop fine-tune.** The data gap is goal↔control pairs over real AX trees. Both this
   repo's `benchmarks/` batteries and the browser sibling's fine-tuning recipe can generate
   it, and the model family is already proven trainable on one 16 GB GPU. A checkpoint
   trained on that data is the difference between "answers questions about text" and
   "selects controls" — `--model` takes it the day it exists.
2. **More AX fixtures.** `benchmarks/capture_ax.py` makes any window a fixture. Settings,
   Finder, Safari and an Electron app each would turn 12 cases into a real suite, and a
   fixture set is half of that fine-tune's dataset.
3. **Other platforms.** Windows UIA and Linux AT-SPI are the same contract with a different
   bridge.

---

## Reference implementations & sources

Built on the work of others, stated plainly:

| Source | What it contributed |
|---|---|
| [`convaiinnovations/laya`](https://github.com/NandhaKishorM/laya) (Apache-2.0) | The decision model itself — and the three official checkpoints every number above was measured with. A non-autoregressive System One encoder that answers typed questions with calibrated probabilities in one forward pass. |
| [`trycua/cua`](https://github.com/trycua/cua) (MIT) | `cua-driver` — the Accessibility-tree reader and the AX/pixel action rung this project executes through. The observation contract here is a thin adapter over its `get_window_state` payload. |
| [`browser-use/jev-ultrafast`](https://github.com/browser-use/jev-ultrafast) (MIT) | The operation vocabulary (`CLICK`/`TYPE_TEXT`/`SELECT`/`SCROLL`/`WAIT`/`DONE`/`BLOCKED`) and the one-round-trip speculative-target design, via the browser sibling. |
| [`FluidInference/FluidUse`](https://github.com/FluidInference/FluidUse) (Apache-2.0) | Prior art proving Laya + macOS AX is the right shape for on-device form work (Swift/Core ML). This project takes it to Python, to general desktop tasks, and to MCP. |
| [ChenneyZhuang/laya-browser-agent](https://github.com/ChenneyZhuang/laya-browser-agent) (Apache-2.0) | The entire decision loop, guards, scoping and element-table contract, imported as a dependency rather than forked. |

No code was copied from these projects: the vocabulary, protocol and adapter shapes were
re-derived here against the AX surface. Model weights are a dependency, not a redistribution.

## License

Apache-2.0.
