"""Tool abstraction.

A tool exposes named actions. Every action declares its authority level, cost,
latency, reliability prior and input/output schema. Implementations must return
a real ``ToolResult`` -- a tool never "pretends" an action succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from regent.schemas import AuthorityLevel, ToolResult

EXECUTORS = ("llm", "search", "browser", "code", "api", "connector", "os", "agent", "human")


class MissingCredential(RuntimeError):
    def __init__(self, credential: str, detail: str = ""):
        super().__init__(f"missing credential {credential}{': ' + detail if detail else ''}")
        self.credential = credential


@dataclass
class ToolContext:
    """What a tool may touch while running. No DB session: tools are side-effect
    boundaries and run concurrently; persistence happens in the executor."""

    mission_id: str
    operation_id: str
    workspace: Any
    facts: dict[str, Any] = field(default_factory=dict)
    world: dict[str, Any] = field(default_factory=dict)
    services: Any = None  # regent.runtime.Services (providers, connectors, browser)


@dataclass
class ActionSpec:
    name: str
    description: str
    authority: AuthorityLevel = "AUTO"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_s: float = 0.5
    reliability: float = 0.95
    capabilities: list[str] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    executor: str
    description: str
    actions: dict[str, ActionSpec]
    handler: Callable[[str, dict[str, Any], ToolContext], ToolResult]
    backend: str = "local"  # which backend is live: "live", "local", "mock"
    missing_credentials: list[str] = field(default_factory=list)
    built_by_regent: bool = False

    def __post_init__(self) -> None:
        assert self.executor in EXECUTORS, self.executor

    @property
    def capabilities(self) -> list[str]:
        caps: list[str] = []
        for a in self.actions.values():
            caps.extend(a.capabilities)
        return sorted(set(caps))

    def invoke(self, action: str, inputs: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if action not in self.actions:
            return ToolResult(status="failed", error=f"{self.name} has no action '{action}'")
        try:
            res = self.handler(action, inputs, ctx)
        except MissingCredential as e:
            return ToolResult(status="failed", error=str(e), outputs={"missing_credential": e.credential})
        except Exception as e:  # tools must never crash the loop
            return ToolResult(status="failed", error=f"{type(e).__name__}: {e}")
        if self.backend != "live":
            res.degraded = True
        return res

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "executor": self.executor,
            "description": self.description,
            "backend": self.backend,
            "missing_credentials": self.missing_credentials,
            "built_by_regent": self.built_by_regent,
            "capabilities": self.capabilities,
            "actions": {
                n: {
                    "description": a.description, "authority": a.authority, "cost_usd": a.cost_usd,
                    "latency_s": a.latency_s, "reliability": a.reliability,
                    "input_schema": a.input_schema, "output_schema": a.output_schema,
                    "capabilities": a.capabilities,
                }
                for n, a in self.actions.items()
            },
        }


def get_path(data: Any, path: str, default: Any = None) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return default
    return cur
