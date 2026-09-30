"""Competing routes for a software need.

Routes are generated from what was *observed* (the inventory), never from the benchmark:

* ``reuse``            a capability Regent already has answers the same need
* ``compose-public``   compose a capability now from sources anyone can read
* ``compose+<conn>``   the same, plus a platform source that needs one credential from the
                       principal (delivered first without it; upgraded when it arrives)
* ``external-<conn>``  use an analytics product already running in the product
* ``add-analytics``    add a client-side analytics script to the product
* ``instrument``       change the product's own code to count usage first-party
* ``delegate``         have a coding agent build a bespoke app on the same sources
* ``manual``           the principal looks it up themselves on the platform's dashboard

Estimates come from the inventory: coverage of the need's core questions each route can
reach (by the honest form of its best source), the human seconds it needs, the authority it
consumes, what it changes in the product. The product's own public commitments become hard
constraints: a route that would break one is not a candidate.
"""

from __future__ import annotations

from typing import Any

from regent.schemas import (
    CostEstimate,
    Effect,
    OperationSpec,
    RouteEstimates,
    RouteProposal,
    Sensitivity,
    Uncertainty,
    VerificationSpec,
)
from regent.software.compose import coverage_estimate


def _ops_compose(include: list[str]) -> list[OperationSpec]:
    return [
        OperationSpec(key="sw.compose", goal="Compose the capability from verified sources", tool="software",
                      action="compose", inputs={"include": include}, timeout_s=300,
                      verification=VerificationSpec(method="schema", required_keys=["capability_id", "version"]),
                      cost_estimate=CostEstimate(minutes=1)),
        OperationSpec(key="sw.verify", goal="Run Regent's acceptance suite on the capability", tool="software",
                      action="verify", depends_on=["sw.compose"], timeout_s=600,
                      inputs={"capability_id": "{{ops.sw.compose.outputs.capability_id}}"},
                      verification=VerificationSpec(method="predicate",
                                                    predicate={"path": "passed", "op": "eq", "value": True}),
                      cost_estimate=CostEstimate(minutes=2)),
        OperationSpec(key="sw.activate", goal="Register the capability as a tool, a view and world facts",
                      tool="software", action="activate", depends_on=["sw.verify"],
                      inputs={"capability_id": "{{ops.sw.compose.outputs.capability_id}}"},
                      verification=VerificationSpec(method="schema", required_keys=["tool", "status", "view"])),
    ]


def _unlock_ops(p: dict[str, Any], mission_id: str) -> list[OperationSpec]:
    cred = p["credentials"][0]
    host = p.get("host") or ""
    return [
        OperationSpec(key=f"sw.unlock.{p['id']}", goal=f"Obtain {cred} from the principal", tool="human",
                      action="perform", depends_on=["sw.activate"],
                      inputs={"required_action": p["human_action"].format(zone=host, site=f"https://{host}/"),
                              "reason": f"{p['title']} measures {p['measures']}; it is the smallest step from the "
                                        f"current answer to a count of people. Read-only: {p['footprint']}",
                              "estimated_time_seconds": p["human_seconds"], "kind": "credential",
                              "response_schema": {cred: {"type": "secret", "label": cred}},
                              "resume_condition": {"type": "fact", "fact": f"credential.{cred}", "op": "exists"},
                              "context": {"connector": p["id"], "credential": cred, "mission_id": mission_id}},
                      verification=VerificationSpec(method="human_confirmed")),
        OperationSpec(key=f"sw.upgrade.{p['id']}", goal=f"Read {p['title']} and re-verify the capability",
                      tool="software", action="upgrade", depends_on=[f"sw.unlock.{p['id']}"], timeout_s=600,
                      inputs={"capability_id": "{{ops.sw.compose.outputs.capability_id}}", "connector": p["id"]},
                      verification=VerificationSpec(method="predicate",
                                                    predicate={"path": "passed", "op": "eq", "value": True})),
    ]


def strategies(mission: dict[str, Any], need: dict[str, Any], inv: dict[str, Any]) -> list[RouteProposal]:
    mid = mission["id"]
    scale = float(mission.get("value_scale") or 1.0)
    routes: list[RouteProposal] = []
    hosts = [d["host"] for d in inv.get("deployments", [])]
    host = hosts[0] if hosts else "product"
    forbids = {f for c in inv.get("constraints", []) if c.get("evidence_found") for f in c.get("forbids", [])}
    promise_text = "; ".join(c["statement"] for c in inv.get("constraints", []) if c.get("evidence_found"))
    public_cov = coverage_estimate(need, inv, [])
    usable_platform = [p for p in inv.get("platform_sources", []) if not p.get("adds_client_code")
                       and p.get("collect", True) is not False]
    readable = [p for p in usable_platform if p["id"] in {str(f.get("endpoint", "")).split(":", 1)[-1]
                                                          for f in inv.get("public_fields", [])
                                                          if str(f.get("endpoint", "")).startswith("connector:")}]

    for cap in inv.get("existing_capabilities", []):
        routes.append(RouteProposal(
            key=f"software-reuse-{cap['slug']}", archetype="reuse", title=f"Use the existing capability '{cap['title']}'",
            thesis="Regent already built and verified a capability answering this need; reading it costs nothing.",
            tags=["software", "reuse"],
            estimates=RouteEstimates(expected_upside=scale * max(cap.get("coverage") or 0.3, 0.3),
                                     success_probability=0.95, time_cost_hours=0.0, reversibility=1.0,
                                     optionality=0.9, risk=0.05, authority_cost=0.0, information_gain=0.2),
            operations=[OperationSpec(key="sw.reuse", goal=f"Read {cap['title']}", tool="software", action="reuse",
                                      inputs={"capability_id": cap["id"]},
                                      verification=VerificationSpec(method="predicate", predicate={
                                          "path": "passed", "op": "eq", "value": True}))]))

    if public_cov > 0 or not readable:
        routes.append(RouteProposal(
            key="software-compose-public", archetype="compose",
            title=f"Compose a live answer from what {host} already publishes",
            thesis=(f"Read the product's own public endpoints (GET only) and turn the fields that bear on the "
                    f"question into metrics with honest forms. Answers {public_cov:.0%} of the core question now; "
                    "anything it cannot answer is shown as unknown, never estimated."),
            tags=["software", "compose", "read_only"],
            estimates=RouteEstimates(expected_upside=scale * max(public_cov, 0.05), success_probability=0.9,
                                     time_cost_hours=0.1, reversibility=1.0, optionality=0.9, risk=0.08,
                                     authority_cost=0.0, information_gain=0.4),
            estimate_rationale={"expected_upside": f"coverage of core questions by public fields: {public_cov:.2f}"},
            operations=_ops_compose([])))

    for p in readable:
        cov = coverage_estimate(need, inv, [p["id"]])
        if cov <= public_cov + 0.01:
            continue
        cred = p["credentials"][0]
        p_cred = 0.8          # the principal provides a read-only credential when asked for one
        expected = public_cov + p_cred * 0.85 * (cov - public_cov)
        routes.append(RouteProposal(
            key=f"software-compose-{p['id']}", archetype="compose+credential",
            title=f"Compose now, then add {p['title']} (one credential)",
            thesis=(f"Deliver the public answer immediately; wire in {p['title']} ({p['footprint']}), which measures "
                    f"{p['measures']}. One bounded action from the principal ({cred}, ~{p['human_seconds'] // 60} min) "
                    f"raises coverage from {public_cov:.0%} to {cov:.0%}. Until then the view says 'not connected'."),
            tags=["software", "compose", "read_only", "credential"],
            estimates=RouteEstimates(expected_upside=scale * expected, success_probability=0.88,
                                     time_cost_hours=0.1 + p["human_seconds"] / 3600, reversibility=1.0,
                                     optionality=0.9, risk=0.1, authority_cost=min(1.0, p["human_seconds"] / 900),
                                     information_gain=0.6),
            estimate_rationale={"expected_upside": f"{public_cov:.2f} now + P(credential)={p_cred} x "
                                                   f"P(source works)=0.85 x ({cov:.2f}-{public_cov:.2f})",
                                "authority_cost": f"{p['human_seconds']}s of the principal's time, read-only access"},
            sensitivities=[Sensitivity(fact=f"credential.{cred}", op="exists",
                                       rationale="with the credential in hand the upgrade no longer waits on anyone",
                                       effects={"expected_upside": Effect(set=scale * (public_cov + 0.85 * (cov - public_cov))),
                                                "authority_cost": Effect(set=0.0)})],
            uncertainty=[Uncertainty(question=f"Will the principal provide {cred}?", fact_key=f"credential.{cred}",
                                     affects=["expected_upside"], resolvable_by="human")],
            operations=_ops_compose([p["id"]]) + _unlock_ops(p, mid)))

    for p in inv.get("platform_sources", []):
        if p.get("adds_client_code"):
            tags = ["software", "changes_product", f"adds_client_code:{host}", f"third_party_tracking:{host}"]
            routes.append(RouteProposal(
                key="software-add-analytics", archetype="integrate",
                title="Add a client-side analytics script to the product",
                thesis=(f"The conventional answer: install an analytics provider in every page. {p['footprint']}."
                        + (f" The product publicly promises: {promise_text}." if promise_text else "")),
                tags=tags,
                estimates=RouteEstimates(expected_upside=scale * 0.85, success_probability=0.7,
                                         time_cost_hours=2.0 + p["human_seconds"] / 3600, reversibility=0.6,
                                         optionality=0.5, risk=0.5 + (0.3 if forbids else 0),
                                         authority_cost=0.7, information_gain=0.6),
                operations=[OperationSpec(key="sw.human.analytics", goal=p["human_action"], tool="human",
                                          action="perform", inputs={"required_action": p["human_action"],
                                                                     "estimated_time_seconds": p["human_seconds"],
                                                                     "kind": "physical"},
                                          verification=VerificationSpec(method="human_confirmed"))]))

    routes.append(RouteProposal(
        key="software-instrument", archetype="build",
        title="Change the product to count its own readers first-party",
        thesis=("Add aggregate, cookieless counting to the product's server code and deploy it. Most direct "
                "measure, but it needs the product's source and deploy credentials, changes production, and every "
                "reader's visit becomes something the product records."),
        tags=["software", "changes_product", f"writes_to_product:{host}", "commit"],
        estimates=RouteEstimates(expected_upside=scale * 0.95, success_probability=0.35, time_cost_hours=6.0,
                                 reversibility=0.5, optionality=0.5, risk=0.45, authority_cost=0.85,
                                 information_gain=0.5),
        estimate_rationale={"success_probability": "the product's source is not reachable from here; deploy "
                                                   "credentials and a production change need the principal"},
        operations=[OperationSpec(key="sw.human.source", goal="Grant access to the product's source and deployment",
                                  tool="human", action="perform",
                                  inputs={"required_action": f"Give Regent write access to {host}'s source repository"
                                                             " and a deploy token", "estimated_time_seconds": 900,
                                          "kind": "identity"},
                                  verification=VerificationSpec(method="human_confirmed"))]))

    best = max(readable, key=lambda p: coverage_estimate(need, inv, [p["id"]]), default=None)
    routes.append(RouteProposal(
        key="software-delegate", archetype="delegate",
        title="Have a coding agent build a bespoke usage app on the same sources",
        thesis=("Brief a coding agent with the same sources and acceptance tests; Regent verifies its output the "
                "same way. Same data, so the same coverage as composing, at the cost of an agent run and the "
                "principal's authorization to let an agent write and run code."),
        tags=["software", "delegate", "coding_agent"],
        estimates=RouteEstimates(expected_upside=scale * max(public_cov, 0.05), success_probability=0.6,
                                 time_cost_hours=1.0, money_cost=0.0, reversibility=1.0, optionality=0.8, risk=0.25,
                                 authority_cost=0.4, information_gain=0.3),
        operations=[OperationSpec(key="sw.delegate", goal="Delegate the build to a coding agent", tool="software",
                                  action="delegate_build", inputs={"include": [best["id"]] if best else []},
                                  timeout_s=3600,
                                  verification=VerificationSpec(method="predicate", predicate={
                                      "path": "passed", "op": "eq", "value": True}))]))

    if best is not None:
        routes.append(RouteProposal(
            key="software-manual", archetype="human",
            title=f"Look it up yourself on the {best['title'].split(' (')[0]} dashboard",
            thesis="No software: each time the principal wants the answer they sign in and read the platform's own "
                   "dashboard. Not 'at a glance' and costs attention every time.",
            tags=["software", "human"],
            estimates=RouteEstimates(expected_upside=scale * 0.4, success_probability=0.9, time_cost_hours=0.1,
                                     reversibility=1.0, optionality=0.7, risk=0.1, authority_cost=0.9,
                                     information_gain=0.1),
            operations=[OperationSpec(key="sw.human.manual", goal="Read the platform dashboard", tool="human",
                                      action="perform", inputs={"required_action": "Sign in and read the usage "
                                                                                   "numbers on the dashboard",
                                                                "estimated_time_seconds": 120, "kind": "physical"},
                                      verification=VerificationSpec(method="human_confirmed"))]))
    return routes
