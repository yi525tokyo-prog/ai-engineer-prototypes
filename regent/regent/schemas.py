"""Provider-neutral schemas shared by providers, tools, the loop and the API.

Model providers must emit ``RouteProposal`` objects; tools must return
``ToolResult`` objects. Nothing outside these contracts is trusted.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AuthorityLevel = Literal["AUTO", "COMMIT", "IDENTITY"]
AUTHORITY_ORDER = {"AUTO": 0, "COMMIT": 1, "IDENTITY": 2}

ESTIMATE_FIELDS = (
    "expected_upside", "success_probability", "time_cost_hours", "money_cost",
    "information_gain", "reversibility", "optionality", "risk", "authority_cost",
)


class RouteEstimates(BaseModel):
    expected_upside: float = Field(0.0, description="Upside if the route succeeds, in mission value units")
    success_probability: float = Field(0.5, ge=0, le=1)
    time_cost_hours: float = Field(0.0, ge=0)
    money_cost: float = Field(0.0, ge=0, description="Cash cost, in the money resource unit")
    information_gain: float = Field(0.0, ge=0, le=1)
    reversibility: float = Field(0.5, ge=0, le=1)
    optionality: float = Field(0.5, ge=0, le=1)
    risk: float = Field(0.3, ge=0, le=1)
    authority_cost: float = Field(0.0, ge=0, le=1, description="How much human authority/attention the route consumes")


class Effect(BaseModel):
    set: float | None = None
    mul: float | None = None
    add: float | None = None
    from_fact: bool = False  # set the field to the (numeric) value of the triggering fact

    def apply(self, value: float, fact_value: Any = None) -> float:
        if self.from_fact:
            try:
                value = float(fact_value)
            except (TypeError, ValueError):
                pass
        if self.set is not None:
            value = self.set
        if self.mul is not None:
            value = value * self.mul
        if self.add is not None:
            value = value + self.add
        return value


class Condition(BaseModel):
    fact: str
    op: Literal["eq", "ne", "lt", "gt", "le", "ge", "in", "exists", "missing", "truthy", "falsy"] = "eq"
    value: Any = None

    def holds(self, facts: dict[str, Any], present: set[str]) -> bool | None:
        """True/False if decidable from current facts, None if the fact is unknown."""
        if self.op == "exists":
            return self.fact in present
        if self.op == "missing":
            return self.fact not in present
        if self.fact not in present:
            return None
        v = facts.get(self.fact)
        try:
            if self.op == "eq":
                return v == self.value
            if self.op == "ne":
                return v != self.value
            if self.op == "lt":
                return float(v) < float(self.value)
            if self.op == "gt":
                return float(v) > float(self.value)
            if self.op == "le":
                return float(v) <= float(self.value)
            if self.op == "ge":
                return float(v) >= float(self.value)
            if self.op == "in":
                return v in (self.value or [])
            if self.op == "truthy":
                return bool(v)
            if self.op == "falsy":
                return not bool(v)
        except (TypeError, ValueError):
            return False
        return None


class Sensitivity(Condition):
    """How a route's estimates respond to a fact about the world."""

    effects: dict[str, Effect] = Field(default_factory=dict)
    rationale: str = ""


class Blocker(BaseModel):
    kind: Literal["fact", "capability"] = "fact"
    fact: str | None = None
    op: str = "eq"
    value: Any = None
    capability: str | None = None
    reason: str = ""

    def condition(self) -> Condition | None:
        if self.kind != "fact" or not self.fact:
            return None
        return Condition(fact=self.fact, op=self.op, value=self.value)  # type: ignore[arg-type]


class Uncertainty(BaseModel):
    question: str
    fact_key: str
    affects: list[str] = Field(default_factory=list)
    resolvable_by: str | None = None


class FallbackSpec(BaseModel):
    tool: str
    action: str
    inputs: dict[str, Any] | None = None
    reason: str = ""


class RetryPolicy(BaseModel):
    max_attempts: int = 2
    backoff_s: float = 0.0
    fallback: list[FallbackSpec] = Field(default_factory=list)


class VerificationSpec(BaseModel):
    method: Literal["none", "schema", "predicate", "state_check", "model", "human_confirmed"] = "schema"
    required_keys: list[str] = Field(default_factory=list)
    predicate: dict[str, Any] | None = None  # {"path": "a.b", "op": "eq", "value": ...}
    check: dict[str, Any] | None = None      # {"tool": ..., "action": ..., "inputs": ..., "expect": {...}}


class CostEstimate(BaseModel):
    money: float = 0.0
    api_usd: float = 0.0
    minutes: float = 0.0
    attention_s: float = 0.0


class OperationSpec(BaseModel):
    key: str
    goal: str
    tool: str
    action: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    kind: Literal["step", "probe", "acquire"] = "step"
    depends_on: list[str] = Field(default_factory=list)
    resolves: list[str] = Field(default_factory=list)
    emits: dict[str, str] = Field(default_factory=dict, description="output path -> fact key")
    verification: VerificationSpec = Field(default_factory=VerificationSpec)
    timeout_s: float = 60.0
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    cost_estimate: CostEstimate = Field(default_factory=CostEstimate)
    authority: AuthorityLevel | None = None


class RouteProposal(BaseModel):
    key: str
    title: str
    thesis: str
    archetype: str = "direct"
    tags: list[str] = Field(default_factory=list)
    estimates: RouteEstimates = Field(default_factory=RouteEstimates)
    estimate_rationale: dict[str, str] = Field(default_factory=dict)
    sensitivities: list[Sensitivity] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    operations: list[OperationSpec] = Field(default_factory=list)
    uncertainty: list[Uncertainty] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _slug(cls, v: str) -> str:
        return "".join(c if c.isalnum() or c in "-_." else "-" for c in v.lower())[:110]


class Critique(BaseModel):
    route_key: str
    provider: str
    issues: list[str] = Field(default_factory=list)
    adjustments: dict[str, float] = Field(default_factory=dict, description="field -> suggested value")
    confidence: float = 0.5


class Blocked(BaseModel):
    type: str  # captcha|login|payment|biometric|signature|physical|rate_limit|unknown
    detail: str = ""
    url: str | None = None
    selector: str | None = None


class ToolResult(BaseModel):
    status: Literal["ok", "failed", "blocked"] = "ok"
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    blocker: Blocked | None = None
    cost: CostEstimate = Field(default_factory=CostEstimate)
    facts: list[dict[str, Any]] = Field(default_factory=list)  # [{key, value, confidence}]
    claims: list[str] = Field(default_factory=list)
    degraded: bool = False  # produced by a local/mock backend


class VerificationResult(BaseModel):
    verdict: Literal["pass", "fail", "inconclusive"]
    method: str
    detail: str = ""
    checks: list[dict[str, Any]] = Field(default_factory=list)
