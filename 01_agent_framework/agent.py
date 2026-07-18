"""
Enterprise AI Agent Framework -- a small, reusable scaffold for building
Claude-powered agents with tool use, structured tracing, and graceful
degradation when no API key is configured.

Design:
  - Tool: a callable exposed to the agent, described by name/description/
    JSON schema, so it's introspectable the same way it'd be declared to
    a real LLM's tool-use API.
  - Agent: runs a bounded think -> act -> observe loop. Every step (LLM
    decision, tool call, tool result) is written to a structured trace --
    the piece that matters most for enterprise use, since it's what makes
    an agent debuggable and auditable in production.
  - call_llm(): the production path, calling Claude Enterprise via the
    Messages API with tool definitions attached. Falls back to a
    deterministic, rule-based planner when no API key is configured, so
    the full loop -- tool selection, execution, multi-turn reasoning --
    is runnable and verifiable with zero setup and zero API cost.

Run:
    python3 demo.py
"""

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional


# ---------------------------------------------------------------- tools ----

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON-schema-style, for the real LLM's tool spec
    fn: Callable[..., dict]   # actual python implementation


POLICY_KB = {
    "expense": "Expenses under NZD $100 are self-approved; over that needs "
               "manager sign-off in the finance portal within 14 days.",
    "leave": "20 days annual leave/year, accrued monthly. Requests need 2 "
             "weeks notice via the HR portal except in emergencies.",
    "remote": "Hybrid by default: 3 days/week in-office, rest remote. Fully "
              "remote needs written manager approval.",
    "security": "All laptops must have disk encryption + MFA enabled. "
                "Report lost devices to IT Security within 1 hour.",
}


def lookup_policy(topic: str) -> dict:
    """Stand-in for a real internal knowledge-base / RAG lookup tool."""
    topic_norm = topic.lower().strip()
    for key, text in POLICY_KB.items():
        if key in topic_norm or topic_norm in key:
            return {"found": True, "topic": key, "text": text}
    return {"found": False, "topic": topic_norm,
            "text": "No matching policy found -- escalate to HR."}


TICKETS_FILE = "tickets.json"


def create_ticket(summary: str, priority: str = "medium") -> dict:
    """Stand-in for creating an IT/HR ticket in an enterprise system."""
    tickets = []
    if os.path.exists(TICKETS_FILE):
        with open(TICKETS_FILE, encoding="utf-8") as f:
            tickets = json.load(f)
    ticket = {
        "id": f"TCK-{len(tickets) + 1:04d}",
        "summary": summary,
        "priority": (priority or "medium").lower(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tickets.append(ticket)
    with open(TICKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(tickets, f, indent=2)
    return ticket


TOOLS = [
    Tool(
        name="lookup_policy",
        description="Look up an internal company policy by topic "
                    "(expense, leave, remote work, security).",
        parameters={"topic": {"type": "string"}},
        fn=lookup_policy,
    ),
    Tool(
        name="create_ticket",
        description="Raise an IT/HR support ticket for something that "
                    "needs human follow-up.",
        parameters={"summary": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]}},
        fn=create_ticket,
    ),
]


# ------------------------------------------------------------- planning ----

POLICY_HINTS = ["policy", "leave", "expense", "remote", "wfh", "security", "how many days"]
TICKET_HINTS = ["ticket", "broken", "not working", "raise a", "log a", "report an issue", "issue with"]


def plan_fallback(user_input: str) -> Optional[dict]:
    """Deterministic stand-in for an LLM's tool-selection reasoning --
    used whenever no API key is configured. Keyword-matches the request
    onto one of the two tools, or returns None to answer directly."""
    lowered = user_input.lower()

    if any(h in lowered for h in TICKET_HINTS):
        return {"tool": "create_ticket",
                "args": {"summary": user_input.strip()[:140], "priority": "medium"}}

    if any(h in lowered for h in POLICY_HINTS):
        for topic in POLICY_KB:
            if topic in lowered:
                return {"tool": "lookup_policy", "args": {"topic": topic}}
        return {"tool": "lookup_policy", "args": {"topic": lowered}}

    return None  # no tool needed -- answer directly


# ------------------------------------------------------------ llm calls ----

SYSTEM_PROMPT = (
    "You are an internal enterprise assistant. Use the lookup_policy tool "
    "for policy questions, and create_ticket for anything that needs human "
    "follow-up. Otherwise answer directly and briefly."
)


def call_llm(messages: list, api_key: Optional[str] = None) -> dict:
    """Production path: Claude Enterprise via the Messages API with tool
    definitions attached. Falls back to the deterministic planner above
    when no key is configured, so the agent loop is fully runnable and
    testable without one."""
    api_key = api_key or os.environ.get("LLM_API_KEY")
    if not api_key:
        last_user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        decision = plan_fallback(last_user)
        if decision is None:
            return {"type": "final", "text": (
                "I can help with company policy questions (expense, leave, "
                "remote work, security) or raising a support ticket -- "
                "could you say a bit more about what you need?")}
        return {"type": "tool_call", **decision}

    import urllib.request
    payload = {
        "model": "claude-sonnet-5",
        "max_tokens": 500,
        "system": SYSTEM_PROMPT,
        "messages": messages,
        "tools": [{"name": t.name, "description": t.description,
                   "input_schema": {"type": "object", "properties": t.parameters}}
                  for t in TOOLS],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    block = body["content"][0]
    if block["type"] == "tool_use":
        return {"type": "tool_call", "tool": block["name"], "args": block["input"]}
    return {"type": "final", "text": block["text"]}


# ----------------------------------------------------------------- agent ----

class Agent:
    """Bounded think -> act -> observe loop with a structured, inspectable
    trace -- the part of an agent framework that actually matters for
    enterprise use: knowing exactly what it did and why."""

    def __init__(self, tools=None, max_turns: int = 4):
        self.tools = {t.name: t for t in (tools or TOOLS)}
        self.max_turns = max_turns

    def run(self, user_input: str) -> dict:
        run_id = str(uuid.uuid4())[:8]
        trace = [{"step": "user_input", "content": user_input}]
        messages = [{"role": "user", "content": user_input}]

        for _ in range(self.max_turns):
            decision = call_llm(messages)

            if decision["type"] == "final":
                trace.append({"step": "final_answer", "content": decision["text"]})
                return {"run_id": run_id, "answer": decision["text"], "trace": trace}

            tool_name, args = decision["tool"], decision["args"]
            trace.append({"step": "tool_call", "tool": tool_name, "args": args})

            tool = self.tools.get(tool_name)
            result = {"error": f"unknown tool: {tool_name}"} if tool is None else tool.fn(**args)
            trace.append({"step": "tool_result", "tool": tool_name, "result": result})

            messages.append({"role": "assistant",
                              "content": f"[called {tool_name} with {args}]"})
            messages.append({"role": "user",
                              "content": f"[tool result: {json.dumps(result)}] "
                                         f"Now answer the original request using this."})

            # fallback path: synthesize a final answer directly from the tool result
            if not os.environ.get("LLM_API_KEY"):
                answer = self._synthesize_fallback(tool_name, result)
                trace.append({"step": "final_answer", "content": answer})
                return {"run_id": run_id, "answer": answer, "trace": trace}

        trace.append({"step": "max_turns_exceeded"})
        return {"run_id": run_id, "answer": "Couldn't resolve this in time -- escalating.",
                "trace": trace}

    @staticmethod
    def _synthesize_fallback(tool_name: str, result: dict) -> str:
        if tool_name == "lookup_policy":
            return result["text"]
        if tool_name == "create_ticket":
            return (f"Logged ticket {result['id']} (priority: {result['priority']}). "
                    f"Someone will follow up.")
        return json.dumps(result)
