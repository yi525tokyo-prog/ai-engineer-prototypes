"""Provider-neutral model interface.

Regent logic never depends on a specific provider. Providers *estimate* and
*propose*; Regent's evaluator, verifier and evidence ledger decide.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from regent.schemas import Critique, OperationSpec, RouteEstimates, RouteProposal, VerificationResult

TASKS = (
    "generate_routes", "criticize_routes", "extract_world_state", "estimate_route",
    "summarize_evidence", "generate_operation", "verify_result", "complete", "generate_code",
)


class ModelProvider(ABC):
    name: str = "base"
    model: str = ""
    kind: str = "remote"  # remote | local
    cost_per_call_usd: float = 0.0

    def missing_credentials(self) -> list[str]:
        return []

    def available(self) -> bool:
        return not self.missing_credentials()

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "model": self.model, "kind": self.kind, "available": self.available(),
                "missing_credentials": self.missing_credentials(), "cost_per_call_usd": self.cost_per_call_usd}

    @abstractmethod
    def generate_routes(self, mission: dict[str, Any], world: dict[str, Any], context: dict[str, Any]) -> list[RouteProposal]: ...

    @abstractmethod
    def criticize_routes(self, mission: dict[str, Any], world: dict[str, Any], routes: list[dict[str, Any]]) -> list[Critique]: ...

    @abstractmethod
    def extract_world_state(self, text: str, world: dict[str, Any]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def estimate_route(self, mission: dict[str, Any], world: dict[str, Any], route: dict[str, Any]) -> tuple[RouteEstimates, dict[str, str]]: ...

    @abstractmethod
    def summarize_evidence(self, evidence: list[dict[str, Any]]) -> str: ...

    @abstractmethod
    def generate_operation(self, goal: str, tools: list[dict[str, Any]], world: dict[str, Any]) -> OperationSpec: ...

    @abstractmethod
    def verify_result(self, operation: dict[str, Any], outputs: dict[str, Any]) -> VerificationResult: ...

    @abstractmethod
    def complete(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        """Free-form work item (draft, analyze, summarize, extract). Returns {"text": ..., ...}."""

    @abstractmethod
    def generate_code(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Return {"files": {relative_path: source}, "entrypoint": ..., "test": ...}."""
