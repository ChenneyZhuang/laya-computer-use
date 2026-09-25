"""Backends for the cross-model arena: run the same battery against other models.

The battery's point is an apples-to-apples comparison: the SAME element table, the
same typed questions, the same executor contract — only the model behind the
`Decider` changes. So every backend here speaks the same protocol as the local
Laya backends (`answer(state, questions) -> {"answers": ..., "usage": ...}`).

* `JevBackend`  — TypeSafe's hosted Jev via `https://api.typesafe.ai/v1/systemone`.
  This is the commercial reference point the whole System One class is measured
  against. Needs `TYPESAFE_API_KEY` in the environment (never hard-code a key here).
* `VonBackend`   — the open-weights von model (395M ModernBERT, Apache-2.0) via the
  `von-sdk` package. Runs locally, no key.

Both raise `RuntimeError` on failure; the arena records the failure rather than
silently skipping the case.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL = "jev-latest"


class JevBackend:
    """TypeSafe Jev over the official HTTP API. Same question shapes as Laya."""

    name = "jev"

    def __init__(self, api_key: str | None = None, model: str = TYPESAFE_MODEL,
                 timeout: float = 90.0) -> None:
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("TYPESAFE_API_KEY is not set (export it from your env file first)")
        self.model = model
        self.timeout = timeout

    def answer(self, state: Any, questions: Mapping[str, Any]) -> Dict[str, Any]:
        body = json.dumps({"model": self.model, "state": state,
                           "questions": dict(questions)}).encode()
        request = urllib.request.Request(
            TYPESAFE_URL, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "ignore")[:300]
            raise RuntimeError(f"jev http_{error.code}: {detail}") from None
        except Exception as error:  # network, timeout, malformed
            raise RuntimeError(f"jev network: {str(error)[:200]}") from None
        return {
            "answers": payload.get("answers", {}),
            "usage": payload.get("usage", {}),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "backend": f"jev:{payload.get('model', self.model)}",
        }


class VonBackend:
    """The open-weights von model via `von-sdk` (local, Apache-2.0).

    von is non-autoregressive and returns calibrated distributions, like Laya — but
    its Python API answers ONE question per call (a choice *or* a yes/no), so a
    multi-question state fans out into several calls and the answers are stitched
    back into the wire shape the Decider validates.
    """

    name = "von"

    def __init__(self, **_kwargs: Any) -> None:
        import von

        self._von = von

    def _answer_choice(self, state: Any, question: Mapping[str, Any]) -> Dict[str, Any]:
        criteria = question.get("criteria") or {}
        instructions = question.get("instructions")
        if not isinstance(instructions, str):
            instructions = json.dumps(instructions, ensure_ascii=False)
        result = self._von.decide(state=state, choices=dict(criteria),
                                  instructions=instructions)
        probabilities = {str(k): float(v) for k, v in dict(result.probabilities).items()}
        total = sum(probabilities.values()) or 1.0
        probabilities = {k: v / total for k, v in probabilities.items()}
        chosen = str(result.choice)
        return {"type": "choice", "choice": chosen,
                "probabilities": probabilities,
                "confidence": float(result.confidence)}

    def _answer_noul(self, state: Any, question: Mapping[str, Any]) -> Dict[str, Any]:
        instructions = question.get("instructions")
        if not isinstance(instructions, str):
            instructions = json.dumps(instructions, ensure_ascii=False)
        probability = self._von.judge(state=state, instructions=instructions)
        return {"type": "noul", "noul": float(probability)}

    def answer(self, state: Any, questions: Mapping[str, Any]) -> Dict[str, Any]:
        started = time.monotonic()
        answers: Dict[str, Any] = {}
        for name, question in questions.items():
            kind = question.get("type")
            if kind == "choice":
                answers[name] = self._answer_choice(state, question)
            elif kind == "noul":
                answers[name] = self._answer_noul(state, question)
            else:
                raise RuntimeError(f"von backend does not support question type {kind!r}")
        return {"answers": answers, "usage": {},
                "latency_ms": int((time.monotonic() - started) * 1000),
                "backend": "von"}
