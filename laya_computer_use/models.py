"""Which Laya checkpoint to run, and where it comes from.

This repo runs **official Laya checkpoints** (`convaiinnovations/laya`). They are the
open weights the model card publishes, and they are the reference point every number
in the README is measured against. Nothing here redistributes weights — the hub id is
a dependency, exactly like a version pin.

The registry exists because the same call shape covers all three official checkpoints,
and because the useful thing this repo can say is *which* decision layer you plugged in:

    english          ModernBERT-large, 421M, 512 ctx   English text
    multilingual     mmBERT-base, 322M, 1024/8192 ctx  100+ languages, ~2.2x faster
    typed-decisions  ModernBERT-large, 421M, 1024 ctx  the official fine-tuned example

`resolve()` also accepts anything else — another hub id (`org/repo`) or a local
directory from a `git clone` / `hf_hub_download` — so a checkpoint trained for this
task drops in with no code change:

    Decider(model="/path/to/my-desktop-checkpoint")

Measured behaviour of the official checkpoints on desktop element selection is in the
README: zero-shot they answer the operation question with DONE/WAIT regardless of the
options offered, which is a task-format gap rather than an AX-bridge failure. The
bridge itself is verified end to end (text really lands in a real app).
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

# Official hub for the family. The English checkpoint is the repo root; the others are subfolders.
HUB = "convaiinnovations/laya"

# name -> (hub id, subfolder)
OFFICIAL: dict[str, Tuple[str, Optional[str]]] = {
    "english": (HUB, None),
    "multilingual": (HUB, "multilingual"),
    "typed-decisions": (HUB, "typed-decisions"),
}

# What a bare `lcu run` / `DesktopLoop()` uses when nothing is specified.
DEFAULT = "english"

# Environment overrides, so a shell can point the CLI at another checkpoint without
# editing flags: LCU_MODEL=multilingual lcu decide ...
ENV_MODEL = "LCU_MODEL"
ENV_SUBFOLDER = "LCU_SUBFOLDER"


def resolve(name: Optional[str] = None, subfolder: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Turn a registry name, hub id or local path into `(model, subfolder)`.

    * a registry name (`english`) resolves to the official hub id
    * `org/repo` or an absolute/relative path passes through untouched
    * `subfolder` wins over the registry value when given explicitly
    """
    chosen = (name or os.environ.get(ENV_MODEL) or DEFAULT).strip()
    if chosen in OFFICIAL:
        hub, default_sub = OFFICIAL[chosen]
        return hub, (subfolder or default_sub)
    return chosen, (subfolder or os.environ.get(ENV_SUBFOLDER) or None)


def describe(name: Optional[str] = None, subfolder: Optional[str] = None) -> str:
    """A one-line label for logs and benchmark output."""
    model, sub = resolve(name, subfolder)
    return model if sub is None else f"{model}/{sub}"


# Deciders are cached per checkpoint. Loading one costs ~1 GB of RAM and a second of
# wall time, which a long-lived process (the MCP server) must not pay per call — and
# the memory-safety rule is one checkpoint resident per machine, so the cache also
# prevents a second copy appearing. Only successful constructions are cached.
_DECIDERS: dict = {}


def decider_for(model: Optional[str] = None, subfolder: Optional[str] = None, **kwargs):
    """A `Decider` bound to the requested checkpoint (cached per checkpoint)."""
    from localdecide.decider import Decider  # imported here: the engine is optional at import time

    hub, sub = resolve(model, subfolder)
    key = (hub, sub)
    decider = _DECIDERS.get(key)
    if decider is None:
        decider = Decider(model=hub, subfolder=sub, **kwargs)
        _DECIDERS[key] = decider
    return decider


__all__ = ["HUB", "OFFICIAL", "DEFAULT", "ENV_MODEL", "ENV_SUBFOLDER",
           "resolve", "describe", "decider_for"]
