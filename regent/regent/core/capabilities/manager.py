"""Capability Manager: "cannot" is not a terminal state.

When a route needs a capability Regent lacks, the manager opens a
capability-acquisition sub-mission whose competing routes are the
acquisition strategies, evaluated by the same evaluator as everything else:

1. existing tool           5. browser automation
2. another service         6. generate code (build a tool)
3. a better-suited model   7. bounded human action
4. build a connector       8. buy the capability
                           9. avoid the dependency (re-route)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.goals.missions import MissionGraph
from regent.core.observe.events import EventStore
from regent.core.world.state import WorldView
from regent.db import BuiltTool, Capability, Mission, Route
from regent.ids import new_id
from regent.models.codegen_templates import TEMPLATES
from regent.schemas import (
    CostEstimate,
    OperationSpec,
    RouteEstimates,
    RouteProposal,
    Sensitivity,
    VerificationSpec,
)
from regent.tools.builtin import built_tool

STRATEGIES = ("existing_tool", "alternative_service", "better_model", "build_connector", "browser_automation",
              "generate_code", "human_action", "purchase", "avoid_dependency")


class CapabilityManager:
    def __init__(self, db: Session, services: Any):
        self.db = db
        self.services = services
        self.events = EventStore(db)

    # ------------------------------------------------------------- registry

    def sync_from_registry(self) -> None:
        for cap, info in self.services.tools.capability_status().items():
            status = {"available": "available", "degraded": "degraded"}.get(info["status"], "missing")
            c = self.db.get(Capability, cap)
            if c is None or c.status != status or sorted(c.provided_by or []) != sorted(info["providers"]):
                if c is not None and c.status == "acquiring" and status == "missing":
                    continue
                self.events.append("capability_changed", {"id": cap, "name": cap, "status": status,
                                                          "provided_by": info["providers"]}, source="registry")

    def missing_for(self, route: Route, world: WorldView) -> list[str]:
        return [c for c in (route.required_capabilities or []) if not world.capability_available(c)]

    # ---------------------------------------------------------- acquisition

    def open_acquisition(self, parent: Mission, route: Route, cap_id: str) -> Mission:
        c = self.db.get(Capability, cap_id)
        if c is not None and c.acquisition_mission_id:
            m = self.db.get(Mission, c.acquisition_mission_id)
            if m is not None:
                return m
        graph = MissionGraph(self.db)
        m = graph.create(
            title=f"Acquire capability: {cap_id}", objective=f"Make '{cap_id}' available so '{route.title}' can proceed",
            success_criteria=[{"id": "available", "statement": f"{cap_id} is available",
                               "condition": {"fact": f"capability.{cap_id}", "op": "in",
                                             "value": ["available", "degraded"]}}],
            tags=["capability_acquisition"], value_scale=1.0, horizon_days=7, parent_id=parent.id,
            attrs={"capability": cap_id, "for_route": route.id, "for_mission": parent.id}, source="regent")
        self.events.append("capability_missing", {"capability": cap_id, "route_id": route.id,
                                                  "acquisition_mission_id": m.id}, source="regent", mission_id=parent.id)
        self.events.append("capability_changed", {"id": cap_id, "name": cap_id, "status": "acquiring",
                                                  "acquisition_mission_id": m.id}, source="regent")
        return m

    def acquisition_options(self, cap_id: str, world: WorldView) -> list[RouteProposal]:
        cap = self.db.get(Capability, cap_id)
        attrs = (cap.attrs if cap else {}) or {}
        reg = self.services.tools
        out: list[RouteProposal] = []
        providers = reg.providers_of(cap_id)
        live = [(t, a) for t, a in providers if t.backend in ("live", "local")]
        credential_gated = [(t, a) for t, a in providers if t.backend == "unavailable"]

        def rp(strategy: str, title: str, thesis: str, est: dict, ops: list[OperationSpec], tags=None,
               sens: list[Sensitivity] | None = None) -> RouteProposal:
            return RouteProposal(key=f"acquire-{strategy}", archetype=strategy, title=title, thesis=thesis,
                                 estimates=RouteEstimates(expected_upside=est.pop("upside", 1.0), **est),
                                 operations=ops, tags=tags or [strategy], sensitivities=sens or [])

        if live:
            t, a = live[0]
            out.append(rp("existing_tool", f"Use existing {t.name}.{a}", "Capability already provided by a registered tool.",
                          dict(success_probability=0.97, time_cost_hours=0, reversibility=1, optionality=0.9,
                               risk=0.05, information_gain=0.1), []))
        alt = [(t, a) for t, a in reg.providers_of(attrs.get("alternative_capability", "__none__"))]
        if alt:
            t, a = alt[0]
            out.append(rp("alternative_service", f"Substitute via {t.name}.{a}",
                          f"Use {attrs.get('alternative_capability')} as a substitute.",
                          dict(success_probability=0.6, time_cost_hours=1, reversibility=0.9, optionality=0.7,
                               risk=0.3), []))
        remote_missing = [p for p in self.services.providers.providers.values()
                          if p.kind == "remote" and not p.available()]
        if cap_id.startswith("language.") and remote_missing:
            out.append(rp("better_model", "Enable a frontier model provider",
                          "Configure an API key so a stronger model can perform this.",
                          dict(success_probability=0.9, time_cost_hours=0.1, authority_cost=0.6, reversibility=1,
                               optionality=0.9, risk=0.1),
                          [self._human_op(cap_id, f"Add one of {[p.missing_credentials()[0] for p in remote_missing]} "
                                                  "to the environment", 120, "credential")]))
        for t, a in credential_gated:
            creds = ", ".join(t.missing_credentials) or "credentials"
            out.append(rp("build_connector", f"Activate the {t.name} connector ({creds})",
                          f"The integration exists ({t.name}.{a}) but lacks {creds}. One bounded human action "
                          "(create account / paste key) activates it permanently.",
                          dict(success_probability=0.85, time_cost_hours=0.3, money_cost=float(attrs.get("setup_cost", 0)),
                               authority_cost=0.6, reversibility=0.9, optionality=0.9, risk=0.2, information_gain=0.2),
                          [self._human_op(cap_id, f"Create/obtain {creds} for {t.name} and add it to Regent's "
                                                  "environment", 300, "credential")]))
        if attrs.get("web_url"):
            out.append(rp("browser_automation", f"Operate {attrs['web_url']} via browser",
                          "Drive the service's web UI with Playwright; human only for identity walls.",
                          dict(success_probability=0.45, time_cost_hours=1, reversibility=0.8, optionality=0.6,
                               risk=0.45, authority_cost=0.3),
                          [OperationSpec(key="acq.browse", tool="browser", action="run", kind="probe",
                                         goal=f"Inspect {attrs['web_url']} for an automatable flow",
                                         inputs={"steps": [{"do": "navigate", "url": attrs["web_url"]},
                                                           {"do": "inspect"}]},
                                         verification=VerificationSpec(method="schema", required_keys=["page"]))]))
        template = cap_id in TEMPLATES
        remote_ok = any(p.kind == "remote" and p.available() for p in self.services.providers.providers.values())
        p_build = 0.9 if template else (0.6 if remote_ok else 0.05)
        out.append(rp("generate_code", f"Build a '{cap_id}' tool",
                      "Generate the tool, test it in the sandbox, register it. Fully automatic (AUTO).",
                      dict(success_probability=p_build, time_cost_hours=0.2, reversibility=1, optionality=0.95,
                           risk=0.15, information_gain=0.3, authority_cost=0.0),
                      [OperationSpec(key="acq.build", tool="code", action="build_tool", kind="acquire",
                                     goal=f"Generate, test and register a tool providing {cap_id}",
                                     inputs={"capability": cap_id, "description": (cap.description if cap else "")},
                                     verification=VerificationSpec(method="predicate",
                                                                   predicate={"path": "tests_passed", "op": "eq",
                                                                              "value": True}),
                                     cost_estimate=CostEstimate(api_usd=0.0 if template else 0.2, minutes=1))],
                      tags=["generate_code", "self_sufficient"]))
        out.append(rp("human_action", "Have the principal do it manually when needed",
                      "No new capability; each use becomes a bounded human action.",
                      dict(success_probability=0.9, time_cost_hours=0.5, authority_cost=0.9, reversibility=1,
                           optionality=0.5, risk=0.1),
                      [self._human_op(cap_id, f"Perform '{cap_id}' manually once", 600, "physical")]))
        if attrs.get("purchase_cost"):
            out.append(rp("purchase", f"Buy {attrs.get('purchase_vendor', 'a service')} for {cap_id}",
                          "Spend money to acquire the capability immediately.",
                          dict(success_probability=0.85, time_cost_hours=0.2, money_cost=float(attrs["purchase_cost"]),
                               reversibility=0.5, optionality=0.6, risk=0.2, authority_cost=0.3),
                          [OperationSpec(key="acq.buy", tool="commerce", action="purchase",
                                         goal=f"Buy access providing {cap_id}",
                                         inputs={"vendor": attrs.get("purchase_vendor", "vendor"), "item": cap_id,
                                                 "amount": float(attrs["purchase_cost"]),
                                                 "currency": attrs.get("currency", "JPY")},
                                         verification=VerificationSpec(method="schema", required_keys=["order_id"]))]))
        out.append(rp("avoid_dependency", "Avoid the dependency: re-route the parent mission",
                      "Drop this capability; parent routes that need it lose value and competitors take over.",
                      dict(upside=0.3, success_probability=1.0, time_cost_hours=0, reversibility=1, optionality=0.6,
                           risk=0.05), []))
        return out

    def _human_op(self, cap_id: str, action: str, secs: int, kind: str) -> OperationSpec:
        return OperationSpec(key=f"acq.human.{kind}", tool="human", action="perform", goal=action,
                             inputs={"required_action": action, "reason": f"capability {cap_id} requires it",
                                     "estimated_time_seconds": secs, "kind": "identity" if kind == "credential" else kind,
                                     "response_schema": {"done": {"type": "boolean"}}},
                             verification=VerificationSpec(method="human_confirmed"))

    # --------------------------------------------------------------- results

    def register_built_tool(self, outputs: dict[str, Any], mission_id: str | None) -> list[str]:
        caps = outputs.get("capabilities", [])
        bt = BuiltTool(id=new_id("bt"), capability_id=caps[0] if caps else "?", code_path=outputs["path"],
                       spec={"tool_name": outputs["tool_name"], "actions": outputs["actions"], "capabilities": caps},
                       verified=bool(outputs.get("tests_passed")))
        self.db.add(bt)
        self.db.flush()
        if bt.verified:
            self.services.tools.register(built_tool(outputs["tool_name"], outputs["path"], outputs["actions"], caps))
            for c in caps:
                self.events.append("capability_changed", {
                    "id": c, "name": c, "status": "available",
                    "provided_by": [f"{outputs['tool_name']}.{a}" for a in outputs["actions"]],
                    "attrs": {"built_tool_id": bt.id, "built_by": outputs.get("generated_by")}},
                    source="capability_manager", mission_id=mission_id)
        return caps

    def acquisition_state(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.db.scalars(select(Capability).order_by(Capability.id))]
