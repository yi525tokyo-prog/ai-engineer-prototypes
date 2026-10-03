"""Structured reasoning as a delegated resource.

Regent asks a reasoning worker narrow, schema-bound questions ("what information does this
intent actually need?", "what does this field of this public endpoint measure?") and treats
every answer as a *claim* to be checked, never as a decision. Decisions (route choice,
verification verdicts, whether a capability is usable) stay in Regent's own code.

Backends
--------
``claude-code``  the Claude Code CLI in headless mode (``claude -p``), tool-less: the worker
                 only reasons over what Regent hands it and returns JSON matching a schema.
                 It runs in an empty scratch directory with a minimal environment.
``replay:<dir>`` recorded answers only (tests). A question that was never recorded fails
                 loudly instead of being guessed.
``off``          no reasoning worker; callers fall back to their local heuristics and say so.

Every live answer is recorded to ``<workspace>/reasoning`` (prompt hash -> answer, cost), so a
run can be replayed exactly and audited later.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from regent.config import settings
from regent.software.claude_env import claude_env

DEFAULT_MODEL = os.environ.get("REGENT_REASONER_MODEL", "claude-sonnet-5-5")

class ReasonerUnavailable(RuntimeError):
    pass


@dataclass
class Answer:
    output: dict[str, Any]
    task: str
    provider: str
    model: str
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cached: bool = False
    key: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _key(task: str, instructions: str, payload: Any, schema: dict[str, Any]) -> str:
    blob = json.dumps({"t": task, "i": instructions, "p": payload, "s": schema}, sort_keys=True, ensure_ascii=False,
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


class Reasoner:
    def __init__(self, mode: str | None = None, *, record_dir: Path | None = None, model: str | None = None):
        self.mode = (mode or os.environ.get("REGENT_REASONER", "auto")).strip()
        self.model = model or DEFAULT_MODEL
        self.record_dir = record_dir or (settings.workspace / "reasoning")
        self.replay_dir: Path | None = Path(self.mode.split(":", 1)[1]) if self.mode.startswith("replay:") else None
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------ status

    @property
    def backend(self) -> str:
        if self.replay_dir is not None:
            return "replay"
        if self.mode in ("off", "none"):
            return "off"
        if self.mode in ("auto", "api") and _api_ready():
            return "api"                    # an API key is set: call Claude directly, no CLI start-up per call
        if self.mode in ("auto", "claude-code"):
            return "claude-code" if shutil.which("claude") else "off"
        return "off"

    def available(self) -> bool:
        return self.backend != "off"

    def describe(self) -> dict[str, Any]:
        return {"resource": "reasoner", "backend": self.backend, "model": self.model if self.backend != "off" else None,
                "available": self.available(),
                "note": {"claude-code": "Claude Code CLI, headless and tool-less; answers are claims Regent checks",
                         "api": "Claude API with structured output; answers are claims Regent checks",
                         "replay": f"recorded answers from {self.replay_dir}",
                         "off": "no reasoning worker: local heuristics only"}[self.backend]}

    # --------------------------------------------------------------- ask

    def ask(self, task: str, instructions: str, payload: Any, schema: dict[str, Any], *,
            budget_usd: float = 0.6, mission_id: str | None = None, effort: str | None = None) -> Answer:
        key = _key(task, instructions, payload, schema)
        if self.backend == "replay":
            p = self.replay_dir / f"{task}-{key}.json"
            if not p.exists():
                raise ReasonerUnavailable(f"no recorded answer for {task} ({key})")
            rec = json.loads(p.read_text())
            return Answer(output=rec["output"], task=task, provider="replay", model=rec.get("model", ""),
                          cost_usd=0.0, cached=True, key=key)
        if self.backend == "off":
            raise ReasonerUnavailable("no reasoning worker configured (REGENT_REASONER=off or claude CLI absent)")
        cached = self.record_dir / f"{task}-{key}.json"
        if cached.exists() and mission_id not in FRESH:
            rec = json.loads(cached.read_text())
            return Answer(output=rec["output"], task=task, provider="claude-code", model=rec.get("model", ""),
                          cached=True, key=key)
        prompt = self._prompt(instructions, payload)
        t0 = time.time()
        provider = self.backend
        try:
            if provider == "api":
                out, raw = self._api(prompt, schema, effort)
            else:
                out, raw = self._claude(prompt, schema, budget_usd)
        except ReasonerUnavailable as e:
            LAST_FAILURE.update({"at": time.time(), "error": str(e)[:300]})
            raise
        LAST_FAILURE.clear()
        ans = Answer(output=out, task=task, provider=provider, model=self.model,
                     cost_usd=float(raw.get("total_cost_usd") or 0), latency_ms=round((time.time() - t0) * 1000, 1),
                     key=key, raw={k: raw.get(k) for k in ("num_turns", "session_id", "subtype")})
        self.record_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps({"task": task, "key": key, "model": self.model, "cost_usd": ans.cost_usd,
                                      "instructions": instructions, "payload": payload, "schema": schema,
                                      "output": out, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                     ensure_ascii=False, indent=1, default=str))
        with self._lock:
            self.calls.append({"provider": provider, "model": self.model, "task": task[:40],
                               "mission_id": mission_id, "ok": True, "cost_usd": ans.cost_usd,
                               "latency_ms": ans.latency_ms, "output_summary": ", ".join(sorted(out))[:200]})
        return ans

    def drain_calls(self) -> list[dict[str, Any]]:
        with self._lock:
            out, self.calls = self.calls, []
        return out

    @staticmethod
    def _prompt(instructions: str, payload: Any) -> str:
        return (instructions.strip() + "\n\nEverything you need is below; you have no tools. Answer only with JSON "
                "matching the schema. Say 'unknown' (or use null) rather than guessing; your answer is checked "
                "against live observations.\n\n<input>\n"
                + json.dumps(payload, ensure_ascii=False, indent=1, default=str)[:60000] + "\n</input>")

    def _api(self, prompt: str, schema: dict[str, Any], effort: str | None) -> tuple[dict[str, Any], dict]:
        import anthropic

        config: dict[str, Any] = {"format": {"type": "json_schema", "schema": _strict(schema)}}
        if effort:
            config["effort"] = effort
        try:
            msg = _client().messages.create(model=self.model, max_tokens=16000, output_config=config,
                                            messages=[{"role": "user", "content": prompt}])
        except anthropic.BadRequestError as e:
            raise ReasonerUnavailable(f"reasoning request rejected: {str(e)[:300]}") from e
        except anthropic.APIError as e:
            raise ReasonerUnavailable(f"reasoning service error: {str(e)[:300]}") from e
        if msg.stop_reason == "refusal":
            raise ReasonerUnavailable("the reasoning service declined this request")
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            out = json.loads(text)
        except ValueError as e:
            raise ReasonerUnavailable(f"reasoning service returned no JSON ({msg.stop_reason})") from e
        return out, {"total_cost_usd": _cost(self.model, msg.usage), "subtype": msg.stop_reason}

    def stream_text(self, system: str, text: str, on_text, *, effort: str | None = None,
                    max_tokens: int = 4000) -> str:
        """Write a plain-text answer, handing it over as it is written (direct API only)."""
        import anthropic

        if self.backend != "api":
            raise ReasonerUnavailable("streaming needs the direct API backend")
        kwargs: dict[str, Any] = {"output_config": {"effort": effort}} if effort else {}
        out = ""
        try:
            with _client().messages.stream(model=self.model, max_tokens=max_tokens, system=system,
                                           messages=[{"role": "user", "content": text}], **kwargs) as stream:
                for piece in stream.text_stream:
                    out += piece
                    on_text(out)
                final = stream.get_final_message()
        except anthropic.APIError as e:
            raise ReasonerUnavailable(f"reasoning service error: {str(e)[:300]}") from e
        if final.stop_reason == "refusal":
            raise ReasonerUnavailable("the reasoning service declined this request")
        return out

    def _claude(self, prompt: str, schema: dict[str, Any], budget_usd: float) -> tuple[dict[str, Any], dict]:
        with tempfile.TemporaryDirectory(prefix="regent-reason-") as tmp:
            env, isolate = claude_env(Path(tmp))
            cmd = ["claude", "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(schema),
                   "--max-budget-usd", f"{budget_usd:.2f}", "--model", self.model, "--tools", "", *isolate]
            try:
                proc = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired as e:
                raise ReasonerUnavailable("reasoning worker timed out after 600s") from e
        try:
            raw = json.loads(proc.stdout)
        except ValueError as e:
            raise ReasonerUnavailable(f"reasoning worker returned no JSON (exit {proc.returncode}): "
                                      f"{(proc.stderr or proc.stdout)[:300]}") from e
        out = raw.get("structured_output")
        if raw.get("is_error") or not isinstance(out, dict):
            raise ReasonerUnavailable(f"reasoning worker failed: {raw.get('subtype')} {str(raw.get('result'))[:300]}")
        return out, raw


_PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-4-5": (1.0, 5.0)}
_CLIENT = None


def _api_ready() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _client():
    global _CLIENT
    if _CLIENT is None:
        import anthropic

        _CLIENT = anthropic.Anthropic(max_retries=2, timeout=600.0)
    return _CLIENT


def _cost(model: str, usage: Any) -> float:
    pin, pout = _PRICES.get(model, (0.0, 0.0))
    return round(((getattr(usage, "input_tokens", 0) or 0) * pin + (getattr(usage, "output_tokens", 0) or 0) * pout)
                 / 1e6, 6)


def _strict(schema: Any) -> Any:
    """Structured output wants every object closed (additionalProperties: false)."""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items()}
        if out.get("type") == "object" or (isinstance(out.get("type"), list) and "object" in out["type"]) \
                or "properties" in out:
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [_strict(x) for x in schema]
    return schema


_REASONER: Reasoner | None = None


LAST_FAILURE: dict[str, Any] = {}      # the most recent time the worker could not answer (shown to the person)
FRESH: set[str] = set()                # missions the person asked to redo: think again, do not reuse past answers


def get_reasoner() -> Reasoner:
    global _REASONER
    if _REASONER is None:
        _REASONER = Reasoner()
    return _REASONER


def set_reasoner(r: Reasoner | None) -> None:
    global _REASONER
    _REASONER = r
