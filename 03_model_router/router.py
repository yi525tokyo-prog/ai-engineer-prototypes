"""
AI Model Router -- classifies an incoming task and dispatches it to
whichever backend suits it, through a common adapter interface so the
caller never needs to know which provider is behind it.

Design:
  - AIBackend: common interface both adapters implement -- generate(task)
    -> BackendResult. Swap Claude for Copilot (or add a third provider)
    without touching the router or anything that calls it.
  - classify_task(): rule-based router -- code-shaped tasks (refactors,
    unit tests, autocomplete, specific file/language mentions) go to the
    Copilot lane; conversational/document/multi-step tasks go to the
    Claude lane. Kept rule-based rather than a model call so routing is
    instant, free, and its reasoning is auditable.
  - Every decision is appended to routing_log.jsonl -- the audit trail
    an enterprise rollout needs to show which tasks went where and why.

Note: there's no general-purpose public API for GitHub Copilot itself
(it's IDE-embedded), so CopilotAdapter below is an explicit, clearly
labelled stand-in -- the point being the adapter boundary and routing
logic, which stays the same regardless of which concrete backend sits
behind either lane.

Run:
    python3 demo_tasks.py
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class BackendResult:
    backend: str
    output: str


class ClaudeAdapter:
    """Conversational / agentic / document-oriented tasks."""

    name = "claude"

    def generate(self, task: str) -> BackendResult:
        api_key = os.environ.get("LLM_API_KEY")
        if not api_key:
            return BackendResult(self.name,
                f'[claude-stand-in] Drafted a response for: "{task[:80]}"')

        import urllib.request
        payload = {
            "model": "claude-sonnet-5",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": task}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        return BackendResult(self.name, body["content"][0]["text"])


class CopilotAdapter:
    """Code-completion / IDE-context tasks. Stand-in: Copilot has no
    general REST API to call directly (it's IDE-embedded), so this
    returns a clearly-labelled deterministic suggestion -- the router and
    adapter boundary are the reusable part, not this implementation."""

    name = "copilot"

    def generate(self, task: str) -> BackendResult:
        return BackendResult(self.name,
            f'[copilot-stand-in] Inline suggestion generated for: "{task[:80]}"')


CODE_HINTS = [r"\bfunction\b", r"\brefactor\b", r"\bunit test", r"\bautocomplete\b",
              r"\bboilerplate\b", r"\bbug in this code\b", r"\.py\b", r"\.ts\b",
              r"\.js\b", r"\bregex\b", r"\bsql query\b"]
CLAUDE_HINTS = [r"\bsummari[sz]e\b", r"\bdraft (an? )?email\b", r"\bexplain\b",
                r"\bpolicy\b", r"\bcustomer\b", r"\bmulti-step\b", r"\bagent\b"]


def classify_task(task: str) -> dict:
    lowered = task.lower()
    code_score = sum(1 for pat in CODE_HINTS if re.search(pat, lowered))
    claude_score = sum(1 for pat in CLAUDE_HINTS if re.search(pat, lowered))

    if code_score > claude_score:
        return {"lane": "copilot", "reason": f"{code_score} code-shaped signal(s) matched"}
    if claude_score > 0:
        return {"lane": "claude", "reason": f"{claude_score} conversational signal(s) matched"}
    return {"lane": "claude", "reason": "no strong signal either way -- default to general-purpose lane"}


ADAPTERS = {"claude": ClaudeAdapter(), "copilot": CopilotAdapter()}
LOG_FILE = "routing_log.jsonl"


def route_and_run(task: str) -> dict:
    decision = classify_task(task)
    adapter = ADAPTERS[decision["lane"]]
    result = adapter.generate(task)

    entry = {
        "task": task,
        "lane": decision["lane"],
        "reason": decision["reason"],
        "output": result.output,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    return entry
