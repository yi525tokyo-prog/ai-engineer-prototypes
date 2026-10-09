"""The explicit loop: replanning after evidence, hysteresis, route switching, and
the full seeded case study end-to-end against a live server + real browser."""

import httpx
from sqlalchemy import select

from regent.core.constitution.model import ConstitutionModel
from regent.core.loop import RegentLoop
from regent.core.observe.events import EventStore
from regent.core.routes.generator import RouteGenerator
from regent.core.world.state import WorldView
from regent.db import ConstitutionItem, Decision, HumanInterrupt, Mission, Operation, Route, Skill
from regent.sim import scenario
from tests.helpers import mission, prop, route_from, world_with_cash

WRITE = lambda key, path: {"key": key, "goal": f"write {path}", "tool": "fs", "action": "write",  # noqa: E731
                           "inputs": {"path": path, "content": "x"},
                           "verification": {"method": "schema", "required_keys": ["bytes"]}}


def _pin_generation(db, m):
    """Freeze route generation so the test controls the route set (capability sync first,
    since capability changes legitimately trigger regeneration)."""
    from regent.core.capabilities.manager import CapabilityManager
    from regent.runtime import get_services

    CapabilityManager(db, get_services()).sync_from_registry()
    m.attrs = {**(m.attrs or {}), "generation_signature": RouteGenerator._world_signature(WorldView.load(db))}


def _two_routes(db):
    world_with_cash(db)
    m = mission(db)
    a = route_from(db, m, prop("alpha", expected_upside=100, success_probability=0.7, operations=[WRITE("a1", "a.txt")],
                              sensitivities=[{"fact": "alpha.ok", "op": "eq", "value": False, "rationale": "alpha broken",
                                              "effects": {"success_probability": {"mul": 0.1}}}]))
    b = route_from(db, m, prop("beta", expected_upside=100, success_probability=0.5, operations=[WRITE("b1", "b.txt")]))
    _pin_generation(db, m)
    db.commit()
    return m, a, b


def test_loop_selects_executes_and_verifies(db, services):
    m, a, b = _two_routes(db)
    reps = RegentLoop(db, services).run(m.id)
    db.refresh(m)
    assert m.selected_route_id == a.id
    op = db.scalar(select(Operation).where(Operation.route_id == a.id))
    assert op.status == "succeeded" and op.evidence_ids
    phases = [p["phase"] for p in reps[0].phases]
    assert phases[:7] == ["observe", "model", "generate", "evaluate", "select", "decompose", "execute"]
    assert "verify" in phases and "update_world" in phases
    assert m.status == "monitoring"
    first = db.scalar(select(Decision).where(Decision.mission_id == m.id, Decision.kind == "route_selected"))
    assert first.snapshot_id and len(first.routes_considered) == 2


def test_replanning_switches_route_on_new_evidence(db, services):
    m, a, b = _two_routes(db)
    loop = RegentLoop(db, services)
    loop.run(m.id)
    EventStore(db).append("fact_observed", {"key": "alpha.ok", "value": False}, source="world")
    db.commit()
    loop.run(m.id)
    db.refresh(m)
    assert m.selected_route_id == b.id
    d = db.scalar(select(Decision).where(Decision.mission_id == m.id, Decision.kind == "plan_changed"))
    assert d.previous_route_id == a.id and d.selected_route_id == b.id
    alpha = next(r for r in d.rationale["routes"] if r["key"] == "alpha")
    assert alpha["new_evidence_effects"][0]["fact"] == "alpha.ok"
    assert alpha["estimate_changes"]["success_probability"]["to"] < alpha["estimate_changes"]["success_probability"]["from"]
    assert db.scalar(select(Operation).where(Operation.route_id == b.id)).status == "succeeded"


def test_hysteresis_keeps_incumbent_on_small_changes(db, services):
    world_with_cash(db)
    m = mission(db)
    a = route_from(db, m, prop("a", expected_upside=100, success_probability=0.6, sensitivities=[
        {"fact": "tiny", "op": "eq", "value": True, "effects": {"success_probability": {"add": -0.01}}}]))
    route_from(db, m, prop("b", expected_upside=100, success_probability=0.595))
    _pin_generation(db, m)
    db.commit()
    loop = RegentLoop(db, services)
    loop.run(m.id)
    db.refresh(m)
    assert m.selected_route_id == a.id
    EventStore(db).append("fact_observed", {"key": "tiny", "value": True})
    db.commit()
    loop.run(m.id)
    db.refresh(m)
    assert m.selected_route_id == a.id  # b now leads, but by less than the switch margin
    kept = db.scalar(select(Decision).where(Decision.mission_id == m.id, Decision.kind == "plan_kept"))
    assert kept is not None and "keeping incumbent" in kept.summary


def test_constitution_learns_from_override(db, services):
    m, a, b = _two_routes(db)
    RegentLoop(db, services).run(m.id)
    a.effective = {"optionality": 0.2, "risk": 0.5, "money_cost": 0}
    b.effective = {"optionality": 0.9, "risk": 0.2, "money_cost": 0}
    b.tags = ["keeps_options_open"]
    cm = ConstitutionModel(db)
    ups = cm.observe_choice(b, [a], kind="override")
    ids = {u["id"] for u in ups}
    assert "inferred:optionality" in ids and "inferred:risk" in ids
    opt = db.get(ConstitutionItem, "inferred:optionality")
    c1 = opt.confidence
    cm.observe_choice(b, [a], kind="override")
    assert db.get(ConstitutionItem, "inferred:optionality").confidence > c1  # repeated decisions strengthen it
    assert db.get(ConstitutionItem, "inferred:tag:keeps_options_open").confidence > 0.5
    grouped = cm.grouped()
    assert set(grouped) == {"hard_constraints", "strong_preferences", "weak_preferences", "priorities", "conflicts"}


def test_constitution_conflict_detection(db):
    cm = ConstitutionModel(db)
    cm.upsert(id="x1", type="strong_preference", statement="more optionality", dimension="optionality",
              direction=1, confidence=0.8)
    cm.upsert(id="x2", type="strong_preference", statement="commit hard", dimension="optionality",
              direction=-1, confidence=0.7)
    assert cm.grouped()["conflicts"]


# --------------------------------------------------------------- case study

def test_seeded_case_study_end_to_end(db, services, live_server):
    """The full first case study: ingest -> compete -> choose -> execute -> CAPTCHA
    interrupt -> human clears it -> automatic resume -> evidence -> re-rank -> switch."""
    scenario.seed(db, live_server)
    loop = RegentLoop(db, services)
    loop.run_all()
    root = db.get(Mission, scenario.ROOT_MISSION)
    db.refresh(root)
    routes = list(db.scalars(select(Route).where(Route.mission_id == root.id)))
    assert len(routes) >= 3
    selected = db.get(Route, root.selected_route_id)
    assert selected.key == "pursue-contract_kinoshita"

    # machine work was performed for real
    tests_op = db.scalar(select(Operation).where(Operation.key == "contract_kinoshita.tests"))
    assert tests_op.status == "succeeded" and tests_op.outputs["failed"] == 2 and tests_op.outputs["passed"] == 8
    # exactly one bounded human action for the root mission: the portal CAPTCHA
    open_hi = [h for h in db.scalars(select(HumanInterrupt).where(HumanInterrupt.status == "open",
                                                                  HumanInterrupt.mission_id == root.id))]
    assert len(open_hi) == 1
    hi = open_hi[0]
    assert hi.kind == "identity" and "/sim/portal/kinoshita" in hi.required_action and hi.context["url"].endswith("/sim/portal/kinoshita")
    assert hi.resume_condition["type"] == "page_state"
    assert root.status == "waiting_human"
    # the portal probe was prioritised because it can flip the selection
    unc = root.attrs["uncertainties"][0]
    assert unc["fact"] == "contract.contract_kinoshita.budget_status" and unc["flips_selection"]
    # the send that depends on the portal has not happened
    send = db.scalar(select(Operation).where(Operation.key == "contract_kinoshita.reply.send"))
    assert send.status == "pending"

    # the workplace sub-mission acted within standing authority (no questions asked)
    work = db.get(Mission, scenario.WORK_MISSION)
    db.refresh(work)
    assert db.get(Route, work.selected_route_id).key == "work-place_friend_office"
    aoi = db.scalar(select(Operation).where(Operation.key == "place_friend_office.accept.send"))
    assert aoi.status == "succeeded" and aoi.authority_decision["grant_id"] == "grant_email_known"

    # the principal clears the CAPTCHA on their own device
    r = httpx.post(f"{live_server}/sim/portal/kinoshita/verify", data={"code": scenario.PORTAL_CODE})
    assert r.status_code in (200, 303)
    loop.run_all()
    db.expire_all()
    root = db.get(Mission, scenario.ROOT_MISSION)
    db.refresh(hi)
    assert hi.status == "resolved" and hi.resolution == "condition_observed"  # automatic resume
    portal = db.scalar(select(Operation).where(Operation.key == "contract_kinoshita.portal"))
    assert portal.status == "succeeded" and portal.outputs["budget_status"] == "frozen"
    w = WorldView.load(db)
    assert w.fact("contract.contract_kinoshita.budget_status") == "frozen"
    assert w.facts["contract.contract_kinoshita.budget_status"].evidence_id in portal.evidence_ids

    # strategy changed because of that evidence
    assert db.get(Route, root.selected_route_id).key == "pursue-contract_northbridge"
    change = db.scalar(select(Decision).where(Decision.mission_id == root.id, Decision.kind == "plan_changed"))
    kin = next(x for x in change.rationale["routes"] if x["key"] == "pursue-contract_kinoshita")
    assert any(e["fact"] == "contract.contract_kinoshita.budget_status" for e in kin["new_evidence_effects"])
    assert db.scalar(select(Operation).where(Operation.key == "contract_kinoshita.reply.send")).status == "cancelled"
    # the new route executed within authority; relationships were kept
    assert w.fact("message.msg_northbridge.answered") is True
    assert w.fact("message.msg_kinoshita.answered") is True
    # a reusable skill was extracted from attempt -> blocked -> human -> success
    sk = [s for s in db.scalars(select(Skill)) if "captcha" in " ".join(s.failure_modes or [])]
    assert sk and any(step["step"] == "human.interrupt" for step in sk[0].procedure)
    # a capability was acquired by building a tool
    assert WorldView.load(db).capability_available("invoice.generate")
    # the change is visible in the cockpit
    c = httpx.get(f"{live_server}/api/missions/{root.id}/cockpit").json()
    assert c["best_route"]["key"] == "pursue-contract_northbridge"
    assert c["changes"][0]["kind"] == "plan_changed"
    assert c["blocked_by_you"] == []


def test_second_replan_uses_built_capability(db, services, live_server):
    """World keeps changing: the new incumbent's offer is withdrawn -> invalidated -> the
    runner-up (which needs the tool Regent built earlier) takes over and uses it."""
    scenario.seed(db, live_server)
    loop = RegentLoop(db, services)
    loop.run_all()
    httpx.post(f"{live_server}/sim/portal/kinoshita/verify", data={"code": scenario.PORTAL_CODE})
    loop.run_all()
    scenario.apply_script(db, "northbridge_withdraws")
    loop.run_all()
    db.expire_all()
    root = db.get(Mission, scenario.ROOT_MISSION)
    nb = db.scalar(select(Route).where(Route.key == "pursue-contract_northbridge"))
    assert nb.status == "invalidated" and "offer withdrawn" in nb.invalidated_reason
    assert db.get(Route, root.selected_route_id).key == "bridge-service_codemarket"
    inv = db.scalar(select(Operation).where(Operation.key == "service_codemarket.invoice"))
    assert inv.status == "succeeded" and inv.outputs["total"] > 0
    kinds = [d.kind for d in db.scalars(select(Decision).where(Decision.mission_id == root.id)
                                        .order_by(Decision.created_at))]
    assert kinds.count("plan_changed") == 2
    scenario.apply_script(db, "aoi_confirms_desk")
    loop.run_all()
    db.expire_all()
    assert db.get(Mission, scenario.WORK_MISSION).status == "completed"
