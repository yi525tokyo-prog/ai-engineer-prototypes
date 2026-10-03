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

import re
from typing import Any

from regent.schemas import (
    RetryPolicy,
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


def _ops_compose(include: list[str], use: tuple[str, ...] | None = None) -> list[OperationSpec]:
    return [
        OperationSpec(key="sw.compose", goal="Compose the capability from verified sources", tool="software",
                      action="compose", inputs={"include": include, **({"use": list(use)} if use else {})},
                      timeout_s=300,
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
    # the world was not re-examined: an existing capability answers
    reuse_only = "public_fields" not in inv and inv.get("kind") != "tool"
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
        rel = cap.get("relation", "same_need")
        if cap.get("implementation") == "application" and rel == "same_need":
            rel = "can_do"            # an application answers a need by being used through its API
        elif cap.get("implementation") != "application" and rel in ("can_do", "extend"):
            rel = "same_need"         # a dashboard is read, not called or rebuilt as an app
        if rel in ("can_do", "extend"):
            routes += _app_reuse_routes(cap, rel, need, scale)
            continue
        routes.append(RouteProposal(
            key=f"software-reuse-{cap['slug']}", archetype="reuse", title=f"Use the existing capability '{cap['title']}'",
            thesis=(f"Regent already built and verified a capability answering this need (judged by "
                    f"{cap.get('judged_by')}; it answered with {cap.get('live_values')} live values just now). "
                    "Reading it costs nothing; gaps: " + ("; ".join(cap.get("gaps") or []) or "none stated")),
            tags=["software", "reuse"],
            estimates=RouteEstimates(expected_upside=scale * max(cap.get("coverage") or 0.3, 0.3),
                                     success_probability=0.95, time_cost_hours=0.0, reversibility=1.0,
                                     optionality=0.9, risk=0.05, authority_cost=0.0, information_gain=0.2),
            operations=[OperationSpec(key="sw.reuse", goal=f"Read {cap['title']}", tool="software", action="reuse",
                                      inputs={"capability_id": cap["id"]},
                                      verification=VerificationSpec(method="predicate", predicate={
                                          "path": "passed", "op": "eq", "value": True}))]))

    if reuse_only:
        return routes
    if inv.get("kind") == "tool":
        return routes + tool_strategies(mission, need, inv, scale)
    origins = {f.get("origin", "product") for f in inv.get("public_fields", [])
               if f.get("path_exists") and f.get("relation_to_need") != "unrelated"
               and not str(f.get("endpoint", "")).startswith("connector:")}
    pages = [p for p in inv.get("proposals", {}).get("pages", [])]
    if "page" in origins and origins - {"page"}:
        data = tuple(o for o in ("product", "api") if o in origins)
        own_cov = coverage_estimate(need, inv, [], data)
        page_cov = coverage_estimate(need, inv, [], ("page",))
        both_cov = max(public_cov, own_cov, page_cov)
        names = ", ".join(p.get("name") or "" for p in pages)
        routes.append(RouteProposal(
            key="software-use-existing", archetype="use_existing",
            title=f"Use what {names} already shows people",
            thesis=(f"An existing service already answers this ({names}); Regent reads its answer from its page with "
                    f"extraction patterns verified against the live page. Nothing to build, but the answer is that "
                    f"service's own judgement and breaks if its page layout changes. Coverage {page_cov:.0%}."),
            tags=["software", "use_existing", "read_only"],
            estimates=RouteEstimates(expected_upside=scale * max(page_cov, 0.05), success_probability=0.85,
                                     time_cost_hours=0.05, reversibility=1.0, optionality=0.8, risk=0.25,
                                     authority_cost=0.0, information_gain=0.3),
            estimate_rationale={"risk": "third-party page read by pattern: layout changes break it",
                                "information_gain": "the service's own rule is not visible"},
            operations=_ops_compose([], ("page",))))
        routes.append(RouteProposal(
            key="software-compose-data", archetype="compose",
            title="Build the answer from public data with a stated rule",
            thesis=(f"Read public data services directly and decide with an explicit, inspectable rule. Coverage "
                    f"{own_cov:.0%}; no dependence on another service's judgement."),
            tags=["software", "compose", "read_only"],
            estimates=RouteEstimates(expected_upside=scale * max(own_cov, 0.05), success_probability=0.88,
                                     time_cost_hours=0.1, reversibility=1.0, optionality=0.9, risk=0.12,
                                     authority_cost=0.0, information_gain=0.5),
            operations=_ops_compose([], data)))
        routes.append(RouteProposal(
            key="software-compose-crosscheck", archetype="hybrid",
            title=f"Build from public data and cross-check against {names}",
            thesis=("Decide with Regent's own stated rule over public data and show the existing service's answer "
                    "next to it: two independent sources, so a broken source or a disagreement is visible instead "
                    f"of silently wrong. Coverage {both_cov:.0%}."),
            tags=["software", "compose", "use_existing", "read_only"],
            estimates=RouteEstimates(expected_upside=scale * max(both_cov, 0.05), success_probability=0.93,
                                     time_cost_hours=0.12, reversibility=1.0, optionality=0.9, risk=0.08,
                                     authority_cost=0.0, information_gain=0.6),
            estimate_rationale={"success_probability": "either source alone still answers; disagreement is shown"},
            operations=_ops_compose([], tuple(sorted(origins)))))
    elif public_cov > 0 or not readable:
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
        # the principal's time is spent on a credential only for what earns an answer, not for more proxies
        earned = [f for f in inv.get("public_fields", []) if f.get("endpoint") == f"connector:{p['id']}"
                  and f.get("relation_to_need") in ("measure", "lower_bound", "upper_bound", "at_least_one", "direct")]
        if not earned:
            continue
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

    if hosts:
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
        title="Have a coding agent build a bespoke usage application on the same sources",
        thesis=("Design an application on the same sources, have a coding agent build it, and accept it only after "
                "Regent's own build, tests, browser scenarios and restart pass. Same data, so at best the same "
                "coverage as composing, at the cost of an agent run, a process to keep alive and the principal's "
                "authorization to let an agent write code Regent runs."),
        tags=["software", "delegate", "coding_agent"],
        estimates=RouteEstimates(expected_upside=scale * max(public_cov, 0.05), success_probability=0.6,
                                 time_cost_hours=1.0, money_cost=0.0, reversibility=1.0, optionality=0.8, risk=0.25,
                                 authority_cost=0.4, information_gain=0.3),
        operations=[OperationSpec(key="sw.design", goal="Design the application", tool="software",
                                  action="design_app", timeout_s=600,
                                  verification=VerificationSpec(method="schema", required_keys=["designed"])),
                    OperationSpec(key="sw.delegate", goal="Delegate the build, then accept it independently",
                                  tool="software", action="build_app", retry=RetryPolicy(max_attempts=1), depends_on=["sw.design"], timeout_s=7200,
                                  verification=_verify_passed())]))

    if best is not None:
        # by hand the principal sees what the platform shows -- the same audited forms, not at a glance
        manual_cov = 0.5 * coverage_estimate(need, inv, [best["id"]])
        routes.append(RouteProposal(
            key="software-manual", archetype="human",
            title=f"Look it up yourself on the {best['title'].split(' (')[0]} dashboard",
            thesis="No software: each time the principal wants the answer they sign in and read the platform's own "
                   "dashboard. Not 'at a glance' and costs attention every time.",
            tags=["software", "human"],
            estimates=RouteEstimates(expected_upside=scale * max(manual_cov, 0.02), success_probability=0.9,
                                     time_cost_hours=0.1, reversibility=1.0, optionality=0.7, risk=0.1,
                                     authority_cost=0.9, information_gain=0.1),
            estimate_rationale={"expected_upside": f"half the coverage {best['title']} supports ({manual_cov:.2f}): "
                                                   "the same numbers, read by hand, not at a glance"},
            operations=[OperationSpec(key="sw.human.manual", goal="Read the platform dashboard", tool="human",
                                      action="perform", inputs={"required_action": "Sign in and read the usage "
                                                                                   "numbers on the dashboard",
                                                                "estimated_time_seconds": 120, "kind": "physical"},
                                      verification=VerificationSpec(method="human_confirmed"))]))
    return routes


def _verify_passed() -> VerificationSpec:
    return VerificationSpec(method="predicate", predicate={"path": "passed", "op": "eq", "value": True})


def _app_reuse_routes(cap: dict[str, Any], rel: str, need: dict[str, Any], scale: float) -> list[RouteProposal]:
    if rel == "can_do":
        return [RouteProposal(
            key=f"software-use-{cap['slug']}", archetype="reuse", title=f"Do it with '{cap['title']}' (already running)",
            thesis=("An application Regent built and verified can do this through its API; Regent makes the calls "
                    "and reads back that they took effect. Nothing new is built."),
            tags=["software", "reuse"],
            estimates=RouteEstimates(expected_upside=scale * 0.95, success_probability=0.9, time_cost_hours=0.02,
                                     reversibility=0.9, optionality=0.9, risk=0.05, authority_cost=0.0,
                                     information_gain=0.1),
            operations=[OperationSpec(key="sw.use", goal=f"Do the request through {cap['title']}", tool="software",
                                      action="use_app", inputs={"capability_id": cap["id"]}, timeout_s=300,
                                      verification=_verify_passed())])]
    gaps = "; ".join(cap.get("gaps") or []) or "the new requirements"
    return [RouteProposal(
        key=f"software-extend-{cap['slug']}", archetype="extend", title=f"Extend '{cap['title']}' (new version, same data)",
        thesis=(f"The application the principal already uses holds the data this needs but lacks: {gaps}. A coding agent "
                "builds the next version; Regent tests it on a copy of the real data, promotes it with a backup and "
                "rolls back if any record is lost. The principal keeps one place for everything."),
        tags=["software", "extend", "coding_agent"],
        estimates=RouteEstimates(expected_upside=scale * 0.9, success_probability=0.6, time_cost_hours=1.0,
                                 money_cost=0.0, reversibility=0.85, optionality=0.8, risk=0.25, authority_cost=0.35,
                                 information_gain=0.3),
        estimate_rationale={"reversibility": "the previous version and a backup of its data are kept for rollback"},
        operations=[OperationSpec(key="sw.design", goal="Design the next version (interface kept, data migrated)",
                                  tool="software", action="design_app", inputs={"capability_id": cap["id"]},
                                  timeout_s=600, verification=VerificationSpec(method="schema",
                                                                                required_keys=["designed"])),
                    OperationSpec(key="sw.extend", goal="Build, test on real data, promote or roll back",
                                  tool="software", action="extend_app", retry=RetryPolicy(max_attempts=1), depends_on=["sw.design"],
                                  inputs={"capability_id": cap["id"]}, timeout_s=7200,
                                  verification=_verify_passed())]),
        RouteProposal(
        key=f"software-separate-{cap['slug']}", archetype="build", title="Build a separate application for this",
        thesis=("Leave the existing application alone and build another one: no migration risk, but the data is in "
                "two places and the principal has two things to open."),
        tags=["software", "coding_agent"],
        estimates=RouteEstimates(expected_upside=scale * 0.5, success_probability=0.6, time_cost_hours=1.0,
                                 reversibility=1.0, optionality=0.6, risk=0.2, authority_cost=0.35,
                                 information_gain=0.2),
        operations=[OperationSpec(key="sw.design", goal="Design a separate application", tool="software",
                                  action="design_app", timeout_s=600,
                                  verification=VerificationSpec(method="schema", required_keys=["designed"])),
                    OperationSpec(key="sw.build", goal="Build it", tool="software", action="build_app", retry=RetryPolicy(max_attempts=1),
                                  depends_on=["sw.design"], timeout_s=7200, verification=_verify_passed())])]


def tool_strategies(mission: dict[str, Any], need: dict[str, Any], inv: dict[str, Any], scale: float
                    ) -> list[RouteProposal]:
    """An ability the principal will keep using: build it, use an existing product, or do without."""
    core = [r["id"] for r in need.get("requirements", []) if r.get("priority", "core") == "core"] or \
        [r["id"] for r in need.get("requirements", [])]
    rm = inv.get("remind") or {}
    native = []
    if rm.get("regent_can_do_it") and rm.get("message") and (rm.get("once_at_local") or rm.get("daily_at_local")):
        when = rm.get("daily_at_local") and f"every day at {rm['daily_at_local']}" or rm.get("once_at_local")
        native = [RouteProposal(
            key="regent-message", archetype="native", title=f"Regent tells you itself ({when})",
            thesis=("A message from Regent at the right time does exactly this: no account anywhere, nothing to "
                    "build, none of your time." + (f" Assumed: {rm['assumed']}." if rm.get("assumed") else "")),
            tags=["native"],
            estimates=RouteEstimates(expected_upside=scale * 1.0, success_probability=0.97, time_cost_hours=0.0,
                                     reversibility=1.0, optionality=0.9, risk=0.02, authority_cost=0.0,
                                     information_gain=0.0),
            operations=[OperationSpec(key="regent.message", goal=f"Schedule the message ({when})", tool="software",
                                      action="remind", inputs={"plan": rm}, verification=_verify_passed())])]
    buildable = (inv.get("alternatives_meta") or {}).get("app_can_do_it", True)
    routes = native + [RouteProposal(
        key="software-build-app", archetype="build", title="Have the application built, then verify and run it",
        thesis=("Nothing that exists does all of this; a coding agent builds it to Regent's interface contract and "
                f"Regent's own acceptance scenarios ({len(core)} core requirements), integrating "
                f"{len(inv.get('app_sources', []))} live endpoints. Regent inspects, builds, tests, runs it in a real "
                "browser, repairs it with the agent until it passes, and keeps it running."),
        tags=["software", "build", "coding_agent"],
        estimates=RouteEstimates(expected_upside=scale * 1.0, success_probability=0.75, time_cost_hours=0.2,
                                 reversibility=1.0, optionality=0.9, risk=0.2, authority_cost=0.2,
                                 information_gain=0.4),
        estimate_rationale={"success_probability": "agent-built software, accepted only after Regent's scenarios pass "
                                                   "(bounded repair rounds); every recorded build was accepted",
                            "time_cost_hours": "about 10 minutes of machine time; the principal's part is one approval",
                            "authority_cost": "one approval (~10 s): an autonomous coding agent writes code Regent "
                                              "then runs (COMMIT)"},
        operations=[OperationSpec(key="sw.design", goal="Design the application Regent will have built",
                                  tool="software", action="design_app", timeout_s=600,
                                  verification=VerificationSpec(method="schema", required_keys=["designed"])),
                    OperationSpec(key="sw.build", goal="Delegate, inspect, build, test, run, accept, repair, promote",
                                  tool="software", action="build_app", retry=RetryPolicy(max_attempts=1), depends_on=["sw.design"], timeout_s=7200,
                                  verification=_verify_passed())])]
    if not buildable:      # an app of its own cannot book, buy or act in the principal's name elsewhere
        routes = native
    for a in inv.get("alternatives", []):
        if not a.get("reachable"):
            continue
        met = [r for r in a.get("meets", []) if r in core]
        fit = len(met) / max(len(core), 1)
        routes.append(RouteProposal(
            key=f"software-existing-{re.sub(r'[^a-z0-9]+', '-', a['name'].lower())[:30]}", archetype="use_existing",
            title=f"Use {a['name']}", thesis=(f"An existing product; meets {len(met)}/{len(core)} core requirements as "
                                              f"it is. Account needed: {a['account_needed']}. Privacy: {a['privacy']}. "
                                              "From then on the principal does the work there themselves: Regent "
                                              "cannot act in it for them."
                                              + (f" Not enough because: {a['why_not']}" if a.get("why_not") else "")),
            tags=["software", "use_existing"] + (["third_party_data"] if a.get("account_needed") else []),
            estimates=RouteEstimates(expected_upside=scale * fit * 0.6, success_probability=0.9, time_cost_hours=0.2,
                                     reversibility=0.7, optionality=0.6, risk=0.2,
                                     authority_cost=0.6 if a.get("account_needed") else 0.2, information_gain=0.1),
            operations=[OperationSpec(key="sw.human.signup", goal=f"Create an account at {a['name']}", tool="human",
                                      action="perform", inputs={"required_action": f"Create an account at {a['url']}",
                                                                 "estimated_time_seconds": 300, "kind": "identity"},
                                      verification=VerificationSpec(method="human_confirmed"))]))
    routes.append(RouteProposal(
        key="software-manual", archetype="human", title="Keep it by hand (a notes app)",
        thesis="No software: the principal writes everything down themselves each time. Nothing is integrated.",
        tags=["software", "human"],
        estimates=RouteEstimates(expected_upside=scale * 0.3, success_probability=0.95, time_cost_hours=0.0,
                                 reversibility=1.0, optionality=0.8, risk=0.05, authority_cost=0.9,
                                 information_gain=0.0),
        operations=[OperationSpec(key="sw.human.manual", goal="Keep notes by hand", tool="human", action="perform",
                                  inputs={"required_action": "Keep the list in a notes app", "kind": "physical",
                                          "estimated_time_seconds": 60},
                                  verification=VerificationSpec(method="human_confirmed"))]))
    return routes
