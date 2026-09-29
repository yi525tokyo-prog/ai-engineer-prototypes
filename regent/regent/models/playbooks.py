"""Strategy playbooks used by the local (deterministic) strategist.

Each playbook inspects the world model and, when it applies, emits competing
``RouteProposal``s with estimates, sensitivities (how estimates respond to
facts that are not yet known), blockers, required capabilities and concrete
operations. Playbooks read *entity attributes*, not scenario-specific code, so
they generalize to any world containing contracts, marketplaces, projects,
commitments and places.

Remote LLM providers produce the same schema from the same inputs; the local
strategist guarantees the loop runs with zero credentials.
"""

from __future__ import annotations

from typing import Any, Callable

from regent.schemas import (
    Blocker,
    CostEstimate,
    Effect,
    OperationSpec,
    RetryPolicy,
    RouteEstimates,
    RouteProposal,
    Sensitivity,
    Uncertainty,
    VerificationSpec,
    FallbackSpec,
)

World = dict[str, Any]
Mission = dict[str, Any]


def _ents(world: World, kind: str) -> list[dict[str, Any]]:
    return [e for e in world["entities"] if e["kind"] == kind]


def _ent(world: World, eid: str | None) -> dict[str, Any] | None:
    if not eid:
        return None
    for e in world["entities"]:
        if e["id"] == eid:
            return e
    return None


def _money(world: World) -> dict[str, Any] | None:
    for r in world.get("resources", []):
        if r["kind"] == "money":
            return r
    return None


def _home(world: World) -> dict[str, Any] | None:
    for p in _ents(world, "place"):
        if p["attrs"].get("is_home"):
            return p
    return None


def _sens(fact: str, op: str, value: Any, rationale: str, **effects: dict[str, float]) -> Sensitivity:
    return Sensitivity(fact=fact, op=op, value=value, rationale=rationale,
                       effects={k: Effect(**v) for k, v in effects.items()})


def _reply_ops(msg: dict[str, Any] | None, key: str, intent: str, *, send: bool, depends: list[str] | None = None,
               points: list[str] | None = None) -> list[OperationSpec]:
    """Draft (AUTO) and optionally send (COMMIT) a reply to an unanswered message."""
    if not msg or msg["attrs"].get("answered"):
        return []
    sender = msg["attrs"].get("from")
    ops = [OperationSpec(
        key=f"{key}.draft", goal=f"Draft reply to '{msg['name']}' ({intent})", tool="email", action="draft",
        inputs={"reply_to_message": msg["id"], "to": [sender], "intent": intent, "points": points or [],
                "compose": True},
        verification=VerificationSpec(method="schema", required_keys=["draft_id", "body"]),
        cost_estimate=CostEstimate(minutes=1),
        depends_on=depends or [],
    )]
    if send:
        ops.append(OperationSpec(
            key=f"{key}.send", goal=f"Send reply to '{msg['name']}'", tool="email", action="send",
            inputs={"draft_id": f"{{{{ops.{key}.draft.outputs.draft_id}}}}", "to": [sender],
                    "in_reply_to": msg["id"]},
            depends_on=[f"{key}.draft"] + (depends or []),
            verification=VerificationSpec(method="state_check", check={
                "tool": "email", "action": "outbox", "inputs": {},
                "expect": {"contains_id": "{{self.outputs.message_id}}"}}),
            emits={"sent": f"message.{msg['id']}.answered"},
            cost_estimate=CostEstimate(minutes=0.2),
        ))
    return ops


# ---------------------------------------------------------------- playbooks


def pursue_contracts(mission: Mission, world: World) -> list[RouteProposal]:
    out = []
    for c in _ents(world, "contract"):
        a = c["attrs"]
        if a.get("status") not in ("prospective", "offered") or a.get("category") == "gig":
            continue
        cid = c["id"]
        upside = float(a.get("value") or float(a.get("monthly_value", 0)) * float(a.get("duration_months", 1)))
        p = float(a.get("base_probability", 0.5))
        exclusive = bool(a.get("exclusive"))
        prep_h = float(a.get("prep_hours", 4))
        tags = ["pauses_project"] if a.get("pauses_project") else ["keeps_project_alive"]
        if exclusive:
            tags.append("exclusive_commitment")
        counterparty = _ent(world, a.get("counterparty"))
        cp_name = counterparty["name"] if counterparty else c["name"]
        msg = _ent(world, a.get("message"))
        appt = _ent(world, a.get("appointment"))
        project = _ent(world, a.get("requires_demo_of"))
        ops: list[OperationSpec] = []
        sens: list[Sensitivity] = []
        unc: list[Uncertainty] = []

        if a.get("portal_url"):
            ops.append(OperationSpec(
                key=f"{cid}.portal", kind="probe", tool="browser", action="run",
                goal=f"Check {cp_name}'s client portal: confirm the meeting slot and read status notes",
                inputs={"steps": [
                    {"do": "navigate", "url": a["portal_url"]},
                    {"do": "extract", "selector": "#slot-status", "as": "slot_status"},
                    {"do": "extract", "selector": "#budget-status", "as": "budget_status"},
                    {"do": "extract", "selector": "#meeting-format", "as": "meeting_format"},
                    {"do": "extract", "selector": "#revised-value", "as": "revised_value"},
                    {"do": "extract", "selector": "#client-note", "as": "client_note"},
                ]},
                resolves=[f"contract.{cid}.budget_status"] + ([f"event.{appt['id']}.format"] if appt else []),
                emits={"budget_status": f"contract.{cid}.budget_status",
                       "revised_value": f"contract.{cid}.revised_value",
                       **({"meeting_format": f"event.{appt['id']}.format"} if appt else {}),
                       "slot_status": f"contract.{cid}.slot_status"},
                verification=VerificationSpec(method="predicate",
                                              predicate={"path": "slot_status", "op": "eq", "value": "confirmed"}),
                cost_estimate=CostEstimate(minutes=1, attention_s=30),
                timeout_s=60,
            ))
            unc.append(Uncertainty(question=f"Is {cp_name}'s budget actually approved?",
                                   fact_key=f"contract.{cid}.budget_status",
                                   affects=["success_probability", "expected_upside"], resolvable_by=f"{cid}.portal"))
        if a.get("status") == "prospective":
            sens += [
                _sens(f"contract.{cid}.budget_status", "eq", "frozen", f"{cp_name} budget frozen: deal unlikely this quarter",
                      success_probability={"mul": 0.25}, information_gain={"set": 0.05}),
                _sens(f"contract.{cid}.budget_status", "eq", "pilot_only", f"{cp_name} can only fund a small pilot",
                      success_probability={"mul": 0.8}),
                _sens(f"contract.{cid}.budget_status", "eq", "approved", f"{cp_name} budget approved",
                      success_probability={"add": 0.2}, information_gain={"set": 0.05}),
                _sens(f"contract.{cid}.revised_value", "gt", 0, "Counterparty revised the deal value",
                      expected_upside={"from_fact": True}),
            ]

        if project is not None:
            pid = project["id"]
            ops.append(OperationSpec(
                key=f"{cid}.tests", tool="code", action="run_tests",
                goal=f"Run {project['name']} test suite to check demo readiness",
                inputs={"path": project["attrs"].get("path", pid)},
                emits={"failed": f"project.{pid}.tests_failed", "passed": f"project.{pid}.tests_passed"},
                resolves=[f"project.{pid}.tests_failed"],
                verification=VerificationSpec(method="schema", required_keys=["passed", "failed", "summary"]),
                cost_estimate=CostEstimate(minutes=1), timeout_s=120,
            ))
            ops.append(OperationSpec(
                key=f"{cid}.triage", tool="llm", action="complete", depends_on=[f"{cid}.tests"],
                goal=f"Triage failing {project['name']} tests and estimate fix effort",
                inputs={"task": "analyze_test_failures",
                        "context": {"output": f"{{{{ops.{cid}.tests.outputs.output}}}}",
                                    "failed": f"{{{{ops.{cid}.tests.outputs.failed}}}}",
                                    "failing": f"{{{{ops.{cid}.tests.outputs.failing}}}}"}},
                emits={"fix_hours": f"project.{pid}.fix_hours"},
                verification=VerificationSpec(method="schema", required_keys=["text"]),
                cost_estimate=CostEstimate(api_usd=0.02, minutes=0.5),
            ))
            sens.append(_sens(f"project.{pid}.tests_failed", "gt", 0,
                              f"{project['name']} demo has failing tests: extra prep, some demo risk",
                              time_cost_hours={"add": 4}, success_probability={"mul": 0.92}))
            sens.append(_sens(f"project.{pid}.tests_failed", "eq", 0, f"{project['name']} demo is green",
                              success_probability={"mul": 1.05}))

        travel_h = 0.0
        if appt is not None:
            home = _home(world)
            loc = appt["attrs"].get("location_id")
            if home and loc:
                ops.append(OperationSpec(
                    key=f"{cid}.travel", tool="maps", action="route", kind="probe",
                    goal=f"Estimate travel to '{appt['name']}'",
                    inputs={"origin": home["id"], "destination": loc, "mode": "transit"},
                    emits={"minutes": f"event.{appt['id']}.travel_minutes", "fare": f"event.{appt['id']}.fare"},
                    resolves=[f"event.{appt['id']}.travel_minutes"],
                    verification=VerificationSpec(method="schema", required_keys=["minutes"]),
                ))
                travel_h = 2.0
            sens.append(_sens(f"event.{appt['id']}.format", "eq", "remote", "Meeting is remote: no travel",
                              time_cost_hours={"add": -travel_h}))
            ops.append(OperationSpec(
                key=f"{cid}.prep", tool="calendar", action="hold",
                goal=f"Block preparation time before '{appt['name']}'",
                inputs={"title": f"Prep: {appt['name']}", "before_event": appt["id"], "hours": prep_h},
                verification=VerificationSpec(method="schema", required_keys=["event_id"]),
            ))
        ops += _reply_ops(msg, f"{cid}.reply", "confirm attendance and demo" if appt else "accept next step",
                          send=True, depends=[f"{cid}.portal"] if a.get("portal_url") else [],
                          points=a.get("reply_points", []))
        paused = _ent(world, a["pauses_project"]) if isinstance(a.get("pauses_project"), str) else None
        if paused is not None:
            ops.append(OperationSpec(
                key=f"{cid}.handoff", tool="fs", action="write",
                goal=f"Write a pause/handoff note for {paused['name']} so it can be resumed later",
                inputs={"path": f"notes/{paused['id']}-pause.md", "content":
                        f"# Pausing {paused['name']}\n\nPaused to take {c['name']}.\n"
                        f"Resume checklist: run tests, review open issues, re-read this note.\n"},
                verification=VerificationSpec(method="state_check", check={
                    "tool": "fs", "action": "read", "inputs": {"path": f"notes/{paused['id']}-pause.md"},
                    "expect": {"nonempty": "content"}}),
            ))
        if exclusive:
            # An exclusive commitment means telling other live prospects, not ghosting them.
            months = a.get("duration_months")
            for other in _ents(world, "contract"):
                if other["id"] == cid or other["attrs"].get("status") not in ("prospective", "offered"):
                    continue
                ops += _reply_ops(_ent(world, other["attrs"].get("message")), f"{cid}.defer.{other['id']}",
                                  "keep the door open", send=True, depends=[f"{cid}.reply.send"] if msg else [],
                                  points=[f"I'm moving ahead with a {months}-month engagement, so I can't commit "
                                          "to new work right now." if months else
                                          "I'm moving ahead with another engagement, so I can't commit right now.",
                                          "I'd be glad to revisit afterwards - a pilot in the new year could work well."])
        if a.get("deadline"):
            unc.append(Uncertainty(question=f"Will {cp_name} still hold the offer until {a['deadline']}?",
                                   fact_key=f"contract.{cid}.offer_open", affects=["success_probability"]))
            sens.append(_sens(f"contract.{cid}.offer_open", "eq", False, "Offer withdrawn",
                              success_probability={"set": 0.0}))
        n_commit = sum(1 for o in ops if o.action in ("send", "invite", "purchase"))
        out.append(RouteProposal(
            key=f"pursue-{cid}", archetype="pursue_contract", tags=tags,
            title=f"Win {c['name']}",
            thesis=(f"Secure {int(upside):,} {a.get('currency', '')} from {cp_name}. "
                    + ("Exclusive commitment: pauses other work. " if exclusive else "Keeps other work alive. ")
                    + (a.get("thesis_note") or "")).strip(),
            estimates=RouteEstimates(
                expected_upside=upside, success_probability=p, time_cost_hours=prep_h + travel_h,
                money_cost=float(a.get("prep_cost", 0)),
                information_gain=0.35 if a.get("status") == "prospective" else 0.1,
                reversibility=float(a.get("reversibility", 0.25 if exclusive else 0.6)),
                optionality=float(a.get("optionality", 0.15 if exclusive else 0.6)),
                risk=float(a.get("risk", 0.35)), authority_cost=min(1.0, 0.1 * n_commit + 0.1),
            ),
            estimate_rationale={
                "expected_upside": f"contract value from world model ({c['id']})",
                "success_probability": "base_probability attribute; adjusted by evidence sensitivities",
            },
            sensitivities=sens, uncertainty=unc, operations=ops,
            dependencies=[x for x in [a.get("appointment"), a.get("requires_demo_of")] if x],
            blockers=[Blocker(kind="fact", fact=f"contract.{cid}.offer_open", op="eq", value=False,
                              reason="offer withdrawn")],
        ))
    return out


def bridge_income(mission: Mission, world: World) -> list[RouteProposal]:
    out = []
    months = max(1.0, float(mission.get("horizon_days", 90)) / 30.0)
    for s in _ents(world, "service"):
        a = s["attrs"]
        if a.get("category") != "marketplace":
            continue
        sid = s["id"]
        monthly = float(a.get("typical_monthly", 100000))
        ops = [
            OperationSpec(
                key=f"{sid}.scan", kind="probe", tool="search", action="web",
                goal=f"Scan {s['name']} for open short contracts matching my skills",
                inputs={"query": a.get("search_query", f"{s['name']} freelance contract"), "limit": 8},
                emits={"count": f"market.{sid}.open_gigs"}, resolves=[f"market.{sid}.open_gigs"],
                verification=VerificationSpec(method="schema", required_keys=["results", "count"]),
                retry=RetryPolicy(max_attempts=1, fallback=[FallbackSpec(
                    tool="search", action="local", reason="web search API unavailable: use local index")]),
                cost_estimate=CostEstimate(api_usd=0.005),
            ),
            OperationSpec(
                key=f"{sid}.proposal", tool="llm", action="complete", depends_on=[f"{sid}.scan"],
                goal="Draft a reusable proposal for the best-matching gigs",
                inputs={"task": "draft_proposal", "context": {"results": f"{{{{ops.{sid}.scan.outputs.results}}}}",
                                                               "skills": a.get("skills", [])}},
                verification=VerificationSpec(method="schema", required_keys=["text"]),
                cost_estimate=CostEstimate(api_usd=0.02),
            ),
            OperationSpec(
                key=f"{sid}.save", tool="fs", action="write", depends_on=[f"{sid}.proposal"],
                goal="Save proposal to workspace",
                inputs={"path": f"proposals/{sid}.md", "content": f"{{{{ops.{sid}.proposal.outputs.text}}}}"},
                verification=VerificationSpec(method="schema", required_keys=["bytes"]),
            ),
            OperationSpec(
                key=f"{sid}.invoice", tool="invoice", action="generate", depends_on=[f"{sid}.save"],
                goal="Prepare invoice template for first gig",
                inputs={"client": "First bridge client", "amount": monthly, "currency": a.get("currency", "JPY"),
                        "items": [{"description": "Development work", "amount": monthly}]},
                verification=VerificationSpec(method="schema", required_keys=["path", "total"]),
            ),
        ]
        out.append(RouteProposal(
            key=f"bridge-{sid}", archetype="bridge_income", tags=["keeps_project_alive", "incremental"],
            title=f"Bridge with short contracts via {s['name']}",
            thesis=(f"Earn ~{int(monthly):,}/month from short non-exclusive work while keeping the project alive. "
                    "Lower ceiling, high optionality."),
            estimates=RouteEstimates(
                expected_upside=monthly * months, success_probability=float(a.get("base_probability", 0.5)),
                time_cost_hours=float(a.get("hours", 60)), money_cost=float(a.get("fees", 0)),
                information_gain=0.4, reversibility=0.85, optionality=0.8, risk=0.3, authority_cost=0.1,
            ),
            sensitivities=[
                _sens(f"market.{sid}.open_gigs", "gt", 5, "Healthy supply of matching gigs",
                      success_probability={"mul": 1.2}),
                _sens(f"market.{sid}.open_gigs", "lt", 2, "Few matching gigs", success_probability={"mul": 0.5}),
                _sens("capability.invoice.generate", "ne", "available", "Cannot invoice clients yet",
                      success_probability={"mul": 0.85}),
            ],
            required_capabilities=["invoice.generate"],
            uncertainty=[Uncertainty(question=f"Are there enough matching gigs on {s['name']}?",
                                     fact_key=f"market.{sid}.open_gigs", affects=["success_probability"],
                                     resolvable_by=f"{sid}.scan")],
            operations=ops,
        ))
    return out


def productize_assets(mission: Mission, world: World) -> list[RouteProposal]:
    out = []
    for p in _ents(world, "project"):
        a = p["attrs"]
        if not a.get("monetizable"):
            continue
        pid = p["id"]
        ops = [
            OperationSpec(
                key=f"{pid}.market", kind="probe", tool="search", action="web",
                goal=f"Research competing products and pricing for {p['name']}",
                inputs={"query": a.get("market_query", f"{p['name']} alternatives pricing"), "limit": 8},
                emits={"count": f"market.{pid}.competitors"}, resolves=[f"market.{pid}.competitors"],
                verification=VerificationSpec(method="schema", required_keys=["results"]),
                retry=RetryPolicy(max_attempts=1, fallback=[FallbackSpec(tool="search", action="local")]),
                cost_estimate=CostEstimate(api_usd=0.005),
            ),
            OperationSpec(
                key=f"{pid}.tests", tool="code", action="run_tests", goal=f"Check {p['name']} build health",
                inputs={"path": a.get("path", pid)},
                emits={"failed": f"project.{pid}.tests_failed"},
                verification=VerificationSpec(method="schema", required_keys=["passed", "failed"]),
            ),
            OperationSpec(
                key=f"{pid}.landing", tool="fs", action="write", depends_on=[f"{pid}.market"],
                goal="Draft a landing page for a paid beta",
                inputs={"path": f"{a.get('path', pid)}/landing.md",
                        "content": f"# {p['name']} beta\n\n{a.get('pitch', '')}\n"},
                verification=VerificationSpec(method="schema", required_keys=["bytes"]),
            ),
            OperationSpec(
                key=f"{pid}.checkout", tool="payments", action="create_checkout", depends_on=[f"{pid}.landing"],
                goal="Create a checkout link for beta subscriptions",
                inputs={"product": p["name"], "price": a.get("beta_price", 1500)},
                verification=VerificationSpec(method="schema", required_keys=["url"]),
            ),
        ]
        out.append(RouteProposal(
            key=f"productize-{pid}", archetype="productize_asset",
            tags=["keeps_project_alive", "unconventional", "high_variance"],
            title=f"Launch {p['name']} as a paid beta",
            thesis="Turn the project into revenue directly. High variance, highest information gain, slow cash.",
            estimates=RouteEstimates(
                expected_upside=float(a.get("potential_value", 500000)),
                success_probability=float(a.get("base_probability", 0.15)),
                time_cost_hours=float(a.get("launch_hours", 80)), money_cost=float(a.get("launch_cost", 5000)),
                information_gain=0.8, reversibility=0.7, optionality=0.75, risk=0.6, authority_cost=0.2,
            ),
            sensitivities=[
                _sens(f"market.{pid}.competitors", "gt", 3, "Crowded market", success_probability={"mul": 0.7}),
                _sens(f"market.{pid}.competitors", "lt", 2, "Open market", success_probability={"mul": 1.4}),
                _sens(f"project.{pid}.tests_failed", "gt", 0, "Product not release-ready",
                      time_cost_hours={"add": 6}),
            ],
            required_capabilities=["payments.checkout"],
            blockers=[Blocker(kind="capability", capability="payments.checkout",
                              reason="cannot take payments yet")],
            operations=ops,
        ))
    return out


def reduce_burn(mission: Mission, world: World) -> list[RouteProposal]:
    money = _money(world)
    commitments = [c for c in _ents(world, "commitment") if c["attrs"].get("negotiable")]
    if not money or not commitments:
        return []
    burn = float((money.get("attrs") or {}).get("monthly_burn", 0))
    ops: list[OperationSpec] = [OperationSpec(
        key="burn.runway", tool="analysis", action="runway", goal="Compute runway and burn breakdown",
        inputs={}, emits={"runway_months": "treasury.runway_months"},
        verification=VerificationSpec(method="schema", required_keys=["runway_months"]),
    )]
    saving = 0.0
    for c in commitments:
        a = c["attrs"]
        saving += float(a.get("deferrable_amount", 0))
        ops += _reply_ops(_ent(world, a.get("message")), f"{c['id']}.ask", "request payment deferral",
                          send=True, points=a.get("ask_points", []))
    return [RouteProposal(
        key="reduce-burn", archetype="buy_time", tags=["keeps_project_alive", "defensive"],
        title="Buy time: defer commitments and cut burn",
        thesis=f"Negotiate deferrals (~{int(saving):,}) to extend runway without new income. Cheap, reversible, small.",
        estimates=RouteEstimates(expected_upside=saving, success_probability=0.5, time_cost_hours=2,
                                 information_gain=0.2, reversibility=0.9, optionality=0.7, risk=0.25,
                                 authority_cost=0.2),
        sensitivities=[_sens("treasury.runway_months", "lt", 1, "Runway under a month: deferral alone is insufficient",
                             expected_upside={"mul": 0.8})],
        operations=ops,
        dependencies=[c["id"] for c in commitments],
    )] if burn else []


def select_workplace(mission: Mission, world: World) -> list[RouteProposal]:
    out = []
    days = float(mission.get("attrs", {}).get("days", 5))
    for pl in _ents(world, "place"):
        a = pl["attrs"]
        if not a.get("workplace"):
            continue
        plid = pl["id"]
        cost = float(a.get("cost_per_day", 0)) * days
        ops: list[OperationSpec] = []
        home = _home(world)
        if home and home["id"] != plid:
            ops.append(OperationSpec(
                key=f"{plid}.commute", tool="maps", action="route", kind="probe",
                goal=f"Estimate commute to {pl['name']}",
                inputs={"origin": home["id"], "destination": plid, "mode": "transit"},
                emits={"minutes": f"place.{plid}.commute_minutes"}, resolves=[f"place.{plid}.commute_minutes"],
                verification=VerificationSpec(method="schema", required_keys=["minutes"]),
            ))
        if a.get("offer_message"):
            ops += _reply_ops(_ent(world, a["offer_message"]), f"{plid}.accept", "accept the desk offer",
                              send=True, points=a.get("reply_points", []))
        if cost > 0:
            ops.append(OperationSpec(
                key=f"{plid}.book", tool="commerce", action="purchase",
                goal=f"Buy {int(days)} day passes at {pl['name']}",
                inputs={"vendor": pl["name"], "item": f"{int(days)}-day pass", "amount": cost,
                        "currency": a.get("currency", "JPY")},
                verification=VerificationSpec(method="schema", required_keys=["order_id"]),
                emits={"order_id": f"place.{plid}.booked"},
                cost_estimate=CostEstimate(money=cost),
            ))
        sens = [
            _sens(f"place.{plid}.internet_ok", "eq", False, f"{pl['name']} internet unreliable",
                  success_probability={"mul": 0.3}),
            _sens(f"place.{plid}.commute_minutes", "gt", 45, "Long commute", time_cost_hours={"add": days * 1.5}),
            _sens(f"place.{plid}.available", "eq", True, "Availability confirmed", success_probability={"set": 0.95}),
        ]
        out.append(RouteProposal(
            key=f"work-{plid}", archetype="workplace", tags=["low_cost"] if cost == 0 else [],
            title=f"Work from {pl['name']}",
            thesis=a.get("thesis", f"Base this week's work at {pl['name']}."),
            estimates=RouteEstimates(
                expected_upside=float(a.get("productive_hours", 30)), success_probability=float(a.get("reliability", 0.8)),
                time_cost_hours=float(a.get("commute_hours", 0)) * days, money_cost=cost, information_gain=0.1,
                reversibility=0.9, optionality=0.8, risk=0.2, authority_cost=0.1 if ops else 0.0,
            ),
            sensitivities=sens,
            blockers=[Blocker(kind="fact", fact=f"place.{plid}.available", op="eq", value=False,
                              reason="place unavailable")],
            operations=ops,
        ))
    return out


def generic_archetypes(mission: Mission, world: World) -> list[RouteProposal]:
    """Always-available competing strategies for any objective."""
    obj = mission.get("objective", mission.get("title", "the objective"))
    scale = float(mission.get("value_scale", 1.0))
    research = OperationSpec(
        key="research", kind="probe", tool="search", action="web", goal=f"Research: {obj}",
        inputs={"query": obj, "limit": 6}, emits={"count": f"mission.{mission['id']}.research_hits"},
        verification=VerificationSpec(method="schema", required_keys=["results"]),
        retry=RetryPolicy(max_attempts=1, fallback=[FallbackSpec(tool="search", action="local")]),
    )
    plan_doc = lambda k: OperationSpec(  # noqa: E731
        key=f"{k}.plan", tool="llm", action="complete", goal=f"Write an execution plan for: {obj}",
        inputs={"task": "plan", "context": {"objective": obj, "approach": k}},
        verification=VerificationSpec(method="schema", required_keys=["text"]),
    )
    save = lambda k: OperationSpec(  # noqa: E731
        key=f"{k}.save", tool="fs", action="write", depends_on=[f"{k}.plan"], goal="Persist plan",
        inputs={"path": f"missions/{mission['id']}/{k}.md", "content": f"{{{{ops.{k}.plan.outputs.text}}}}"},
        verification=VerificationSpec(method="schema", required_keys=["bytes"]),
    )
    return [
        RouteProposal(key="direct", archetype="direct", title="Execute directly",
                      thesis="Commit to the most obvious path and execute it end to end.",
                      estimates=RouteEstimates(expected_upside=scale, success_probability=0.5, time_cost_hours=20,
                                               information_gain=0.2, reversibility=0.5, optionality=0.4, risk=0.4),
                      operations=[plan_doc("direct"), save("direct")]),
        RouteProposal(key="information-first", archetype="information_first", title="Resolve unknowns first",
                      thesis="Spend a small budget on research to sharpen estimates before committing.",
                      estimates=RouteEstimates(expected_upside=scale * 0.8, success_probability=0.55, time_cost_hours=6,
                                               information_gain=0.8, reversibility=0.9, optionality=0.8, risk=0.2),
                      operations=[research, plan_doc("information-first"), save("information-first")]),
        RouteProposal(key="delegate", archetype="delegate", title="Delegate or buy the outcome",
                      thesis="Pay a service or another agent to deliver the outcome; conserve attention.",
                      estimates=RouteEstimates(expected_upside=scale, success_probability=0.45, time_cost_hours=3,
                                               money_cost=scale * 0.3 if scale > 100 else 0, information_gain=0.2,
                                               reversibility=0.4, optionality=0.5, risk=0.45, authority_cost=0.3),
                      operations=[plan_doc("delegate"), save("delegate")]),
        RouteProposal(key="defer", archetype="defer", title="Wait and keep options open",
                      thesis="Do nothing irreversible now; revisit when new information arrives.",
                      estimates=RouteEstimates(expected_upside=scale * 0.2, success_probability=0.8, time_cost_hours=0.5,
                                               information_gain=0.1, reversibility=1.0, optionality=0.95, risk=0.15),
                      operations=[]),
    ]


PLAYBOOKS: dict[str, list[Callable[[Mission, World], list[RouteProposal]]]] = {
    "income": [pursue_contracts, bridge_income, productize_assets, reduce_burn],
    "workplace": [select_workplace],
}


def propose(mission: Mission, world: World) -> list[RouteProposal]:
    tags = set(mission.get("tags") or [])
    routes: list[RouteProposal] = []
    for tag, books in PLAYBOOKS.items():
        if tag in tags:
            for pb in books:
                routes.extend(pb(mission, world))
    if len(routes) < 3:
        routes.extend(generic_archetypes(mission, world))
    return routes
