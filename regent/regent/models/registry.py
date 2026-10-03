"""Provider registry and selection.

Selection is deliberately simple: an ordered preference list filtered by
availability, with a treasury-aware "tier" (use an expensive provider only
when stakes justify it and the API budget allows). ``all_available()`` is used
where independent outputs should be compared (route generation, critique).
Consensus is never treated as truth; outputs are stored per provider and
the evaluator weighs them against evidence.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from regent.models.base import ModelProvider
from regent.models.local import LocalStrategist
from regent.models.remote import AnthropicProvider, OpenAIProvider, XAIProvider


class ProviderRegistry:
    def __init__(self, providers: list[ModelProvider] | None = None):
        self.providers: dict[str, ModelProvider] = {}
        for p in providers or [AnthropicProvider(), OpenAIProvider(), XAIProvider(), LocalStrategist()]:
            self.providers[p.name] = p
        pref = os.environ.get("REGENT_PROVIDER_PREFERENCE", "anthropic,openai,xai,local")
        self.preference = [x.strip() for x in pref.split(",") if x.strip()]
        self.calls: list[dict[str, Any]] = []  # in-memory buffer drained into model_calls by the loop

    def describe(self) -> list[dict[str, Any]]:
        return [p.describe() for p in self.providers.values()]

    def all_available(self) -> list[ModelProvider]:
        return [p for p in self.providers.values() if p.available()]

    def select(self, task: str, *, stakes: float = 0.5, api_budget_left: float | None = None) -> ModelProvider:
        for name in self.preference:
            p = self.providers.get(name)
            if p is None or not p.available():
                continue
            if p.kind == "remote" and api_budget_left is not None and api_budget_left < p.cost_per_call_usd:
                continue  # treasury: cannot afford a remote call
            if p.kind == "remote" and stakes < 0.2:
                continue  # low-stakes tasks do not justify remote spend
            return p
        return self.providers["local"]

    def call(self, provider: ModelProvider, task: str, fn: Callable[[], Any], *,
             mission_id: str | None = None) -> Any:
        t = time.time()
        rec: dict[str, Any] = {"provider": provider.name, "model": provider.model, "task": task,
                               "mission_id": mission_id, "ok": True, "error": None,
                               "cost_usd": provider.cost_per_call_usd}
        try:
            out = fn()
            rec["output_summary"] = _summarize(out)
            return out
        except Exception as e:
            rec["ok"] = False
            rec["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            rec["latency_ms"] = round((time.time() - t) * 1000, 1)
            self.calls.append(rec)

    def drain_calls(self) -> list[dict[str, Any]]:
        out, self.calls = self.calls, []
        return out


def _summarize(out: Any) -> str:
    if isinstance(out, list):
        return f"{len(out)} item(s): " + ", ".join(getattr(x, "key", type(x).__name__) for x in out[:8])
    if isinstance(out, dict):
        return ", ".join(sorted(out.keys()))[:200]
    return str(out)[:200]
