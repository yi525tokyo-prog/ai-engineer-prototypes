"""Remote model providers: Anthropic (Claude), OpenAI, xAI (Grok).

All three share one JSON-contract implementation; only the transport differs.
Outputs are validated against Regent's schemas. Providers propose and
estimate; they never decide -- the evaluator and verifier do.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from regent.models.base import ModelProvider
from regent.schemas import Critique, OperationSpec, RouteEstimates, RouteProposal, VerificationResult

SYSTEM = (
    "You are a strategy and estimation component inside Regent, an operational principal. "
    "You propose and estimate; a separate structured evaluator decides. Be calibrated: give "
    "probabilities you would bet on, name the facts that would change your estimates, and prefer "
    "operations that gather decision-relevant information cheaply. Respond with JSON only."
)


def _extract_json(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return json.loads(text[start:])


class RemoteJSONProvider(ModelProvider):
    kind = "remote"
    env_key = ""

    def missing_credentials(self) -> list[str]:
        return [] if os.environ.get(self.env_key) else [self.env_key]

    def _call(self, system: str, user: str, max_tokens: int = 4000) -> tuple[str, float]:
        raise NotImplementedError

    def _json(self, user: str, max_tokens: int = 4000) -> Any:
        text, _ = self._call(SYSTEM, user, max_tokens)
        return _extract_json(text)

    # ------------------------------------------------------------------ tasks

    def generate_routes(self, mission, world, context) -> list[RouteProposal]:
        schema = RouteProposal.model_json_schema()
        prompt = (
            f"MISSION:\n{json.dumps(mission, default=str)}\n\nWORLD STATE:\n{json.dumps(world, default=str)[:30000]}\n\n"
            f"AVAILABLE TOOLS (tool.action with authority):\n{json.dumps(context.get('tools', []))[:8000]}\n\n"
            f"RELEVANT SKILLS / MEMORY:\n{json.dumps(context.get('skills', []), default=str)[:4000]}\n\n"
            "Generate 3-6 genuinely different routes (include at least one unconventional route). "
            "Do not prefer the easiest route. Every operation must use a listed tool/action. "
            "Use 'sensitivities' to state how estimates change when a named fact becomes known; use "
            "'probe' operations to resolve the most decision-relevant unknowns. expected_upside is in "
            f"the mission's value units (value_scale={mission.get('value_scale')}).\n"
            f'Return {{"routes": [RouteProposal, ...]}} where RouteProposal has JSON schema:\n{json.dumps(schema)}'
        )
        data = self._json(prompt, max_tokens=8000)
        out = []
        for r in data.get("routes", []):
            try:
                out.append(RouteProposal.model_validate(r))
            except Exception:
                continue
        return out

    def criticize_routes(self, mission, world, routes) -> list[Critique]:
        prompt = (
            f"MISSION: {json.dumps(mission, default=str)}\nWORLD: {json.dumps(world, default=str)[:15000]}\n"
            f"ROUTES: {json.dumps(routes, default=str)[:15000]}\n"
            "Criticize each route: unsupported estimates, hidden dependencies, missing information, "
            'irreversibility. Return {"critiques": [{"route_key", "issues": [...], '
            '"adjustments": {field: suggested_value}, "confidence"}]}'
        )
        data = self._json(prompt)
        return [Critique(provider=self.name, **{k: v for k, v in c.items() if k != "provider"})
                for c in data.get("critiques", []) if "route_key" in c]

    def extract_world_state(self, text, world) -> list[dict[str, Any]]:
        prompt = (
            f"Extract world-state events from this text. Known world: {json.dumps(world, default=str)[:6000]}\n"
            f"TEXT:\n{text}\n"
            'Return {"events": [{"type": "entity_upserted"|"relation_added"|"fact_observed", ...payload}]}. '
            "entity_upserted: id, kind, name, attrs. relation_added: src, rel, dst. fact_observed: key, value, confidence."
        )
        return self._json(prompt).get("events", [])

    def estimate_route(self, mission, world, route) -> tuple[RouteEstimates, dict[str, str]]:
        prompt = (
            f"MISSION: {json.dumps(mission, default=str)}\nWORLD: {json.dumps(world, default=str)[:12000]}\n"
            f"ROUTE: {json.dumps(route, default=str)[:6000]}\n"
            f'Independently estimate. Return {{"estimates": {json.dumps(RouteEstimates.model_json_schema())}, '
            '"rationale": {field: reason}}'
        )
        d = self._json(prompt)
        return RouteEstimates.model_validate(d.get("estimates", {})), d.get("rationale", {})

    def summarize_evidence(self, evidence) -> str:
        text, _ = self._call(SYSTEM, "Summarize this evidence in <=5 bullet points, citing ids:\n"
                             + json.dumps(evidence, default=str)[:12000], 800)
        return text

    def generate_operation(self, goal, tools, world) -> OperationSpec:
        d = self._json(f"GOAL: {goal}\nTOOLS: {json.dumps(tools)[:8000]}\nReturn one OperationSpec JSON: "
                       + json.dumps(OperationSpec.model_json_schema()))
        return OperationSpec.model_validate(d)

    def verify_result(self, operation, outputs) -> VerificationResult:
        d = self._json(f"OPERATION: {json.dumps(operation, default=str)[:4000]}\nOUTPUTS: "
                       f"{json.dumps(outputs, default=str)[:8000]}\nDid the operation achieve its goal? "
                       'Return {"verdict": "pass"|"fail"|"inconclusive", "detail": "..."}')
        return VerificationResult(verdict=d.get("verdict", "inconclusive"), method=f"model:{self.name}",
                                  detail=d.get("detail", ""))

    def complete(self, task, context) -> dict[str, Any]:
        text, cost = self._call(SYSTEM.replace("Respond with JSON only.", ""),
                                f"TASK: {task}\nCONTEXT:\n{json.dumps(context, default=str)[:20000]}\n"
                                "Produce the requested artifact directly.", 2000)
        return {"text": text, "cost_usd": cost}

    def generate_code(self, spec) -> dict[str, Any]:
        d = self._json(
            "Write a standalone tool. Contract: tool.py exposes ACTIONS dict and CLI "
            "`python tool.py <action> '<json>'` printing JSON; test_tool.py (pytest) must exercise it. "
            f"SPEC: {json.dumps(spec)}\n"
            'Return {"files": {"tool.py": "...", "test_tool.py": "..."}, "tool_name": "...", '
            '"actions": {name: {"description", "authority", "input_schema", "output_schema"}}, '
            '"capabilities": [...], "test": "test_tool.py"}', max_tokens=6000)
        return d


class AnthropicProvider(RemoteJSONProvider):
    name = "anthropic"
    env_key = "ANTHROPIC_API_KEY"
    cost_per_call_usd = 0.03

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("REGENT_ANTHROPIC_MODEL", "claude-opus-5-5")

    def _call(self, system, user, max_tokens=4000):
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ[self.env_key])
        t = time.time()
        msg = client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        _ = time.time() - t
        return text, self.cost_per_call_usd


class OpenAIProvider(RemoteJSONProvider):
    name = "openai"
    env_key = "OPENAI_API_KEY"
    base_url: str | None = None
    cost_per_call_usd = 0.02

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("REGENT_OPENAI_MODEL", "gpt-5")

    def _call(self, system, user, max_tokens=4000):
        import openai

        client = openai.OpenAI(api_key=os.environ[self.env_key], base_url=self.base_url)
        resp = client.chat.completions.create(
            model=self.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return resp.choices[0].message.content or "", self.cost_per_call_usd


class XAIProvider(OpenAIProvider):
    name = "xai"
    env_key = "XAI_API_KEY"
    base_url = "https://api.x.ai/v1"
    cost_per_call_usd = 0.02

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("REGENT_XAI_MODEL", "grok-4")
