"""Authority classification/decisions and structured human interrupts."""

from regent.core.authority.manager import AuthorityManager
from regent.core.human.interrupts import HumanInterruptManager
from regent.core.observe.events import EventStore
from regent.core.world.state import WorldView
from regent.db import Operation, Resource
from regent.ids import new_id
from regent.schemas import Blocked
from tests.helpers import mission


def _op(db, m, tool, action, inputs=None, level=None, **kw):
    am = AuthorityManager(db, __import__("regent.runtime", fromlist=["get_services"]).get_services().tools)
    op = Operation(id=new_id("op"), mission_id=m.id, route_id=kw.get("route_id"), key=kw.get("key", f"{tool}.{action}"),
                   goal=kw.get("goal", "test"), executor="connector", tool=tool, action=action,
                   required_authority=level or am.classify(tool, action, inputs or {}), inputs=inputs or {},
                   outputs={}, retry_policy={"max_attempts": 1, "fallback": [], "fallback_index": -1},
                   verification=kw.get("verification", {"method": "schema", "required_keys": []}),
                   cost_estimate={}, depends_on=[], resolves=[], emits=kw.get("emits", {}), attempt_log=[],
                   evidence_ids=[], status="pending")
    db.add(op)
    db.flush()
    return op


def test_authority_classification(db, services):
    am = AuthorityManager(db, services.tools)
    assert am.classify("email", "draft") == "AUTO"
    assert am.classify("email", "send") == "COMMIT"
    assert am.classify("commerce", "purchase") == "COMMIT"
    assert am.classify("human", "perform") == "IDENTITY"
    assert am.classify("fs", "write") == "AUTO"
    assert am.classify("browser", "run", {"steps": [{"do": "navigate", "url": "x"}]}) == "AUTO"
    assert am.classify("browser", "run", {"steps": [{"do": "click", "selector": "#buy"}]}) == "COMMIT"
    assert am.classify("nonexistent", "thing") == "COMMIT"  # unknown => treated as mutation
    assert am.classify("fs", "write", declared="COMMIT") == "COMMIT"  # specs may raise, never lower


def test_standing_grants_and_constraints(db, services):
    m = mission(db)
    es = EventStore(db)
    es.append("entity_upserted", {"id": "friend", "kind": "person", "name": "F", "attrs": {"known_contact": True}})
    es.append("entity_upserted", {"id": "stranger", "kind": "person", "name": "S", "attrs": {}})
    am = AuthorityManager(db, services.tools)
    am.grant("email.send", "COMMIT", {"recipients": "known_contacts"}, grant_id="g1")
    am.grant("commerce.purchase", "COMMIT", {"max_amount": 3000}, grant_id="g2")
    w = WorldView.load(db)
    assert am.decide(_op(db, m, "email", "send", {"to": ["friend"]}), w).allowed
    d = am.decide(_op(db, m, "email", "send", {"to": ["stranger"]}), w)
    assert not d.allowed and d.needs == "approval"
    assert am.decide(_op(db, m, "commerce", "purchase", {"amount": 2000}), w).allowed
    assert not am.decide(_op(db, m, "commerce", "purchase", {"amount": 11000}), w).allowed
    assert am.decide(_op(db, m, "fs", "write", {"path": "a"}), w).allowed
    ident = am.decide(_op(db, m, "human", "perform"), w)
    assert not ident.allowed and ident.needs == "identity"
    am.revoke("g1")
    assert not am.decide(_op(db, m, "email", "send", {"to": ["friend"]}), WorldView.load(db)).allowed


def test_blocker_becomes_bounded_interrupt_and_resume(db, services):
    m = mission(db)
    op = _op(db, m, "browser", "run", {"steps": [{"do": "navigate", "url": "http://portal"}]}, goal="confirm slot")
    hm = HumanInterruptManager(db, services)
    hi = hm.from_blocker(op, Blocked(type="captcha", detail="CAPTCHA detected", url="http://portal"))
    assert op.status == "waiting_human"
    assert hi.kind == "identity"
    assert "http://portal" in hi.required_action
    assert hi.estimated_time_seconds <= 60
    assert hi.blocking_operation.startswith("browser.run")
    assert hi.resume_condition == {"type": "page_state", "url": "http://portal", "blocker_absent": "captcha"}
    # raising again for the same op does not duplicate
    assert hm.from_blocker(op, Blocked(type="captcha", url="http://portal")).id == hi.id
    hm.resolve(hi.id, {"done": True})
    assert op.status == "pending"  # the executor re-runs the browser op
    assert op.inputs["_after_human"] == hi.id


def test_authorization_interrupt_approve_and_deny(db, services):
    m = mission(db)
    EventStore(db).append("resource_changed", {"id": "att", "kind": "attention", "name": "A", "unit": "min",
                                               "balance": 30, "limit": 30})
    hm = HumanInterruptManager(db, services)
    op = _op(db, m, "commerce", "purchase", {"vendor": "v", "item": "i", "amount": 11000}, goal="buy passes")
    hi = hm.for_authorization(op, "no grant")
    assert hi.kind == "authorization" and "11,000" in hi.required_action
    assert set(hi.response_schema) == {"approve", "grant_standing"}
    hm.resolve(hi.id, {"approve": True})
    assert op.status == "pending" and op.authority_decision["approved_once"]
    assert AuthorityManager(db, services.tools).decide(op, WorldView.load(db)).allowed
    assert db.get(Resource, "att").balance < 30  # principal attention is a budgeted resource

    op2 = _op(db, m, "commerce", "purchase", {"amount": 50000}, key="p2")
    hi2 = hm.for_authorization(op2, "no grant")
    hm.resolve(hi2.id, {"approve": False})
    assert op2.status == "cancelled" and op2.error == "denied by principal"


def test_fact_resume_condition_auto_resolves(db, services):
    m = mission(db)
    op = _op(db, m, "human", "perform", {"required_action": "inspect the car"})
    hm = HumanInterruptManager(db, services)
    hi = hm.raise_interrupt(op, mission_id=m.id, kind="physical", reason="needs eyes",
                            required_action="Inspect the car", estimated_time_seconds=300,
                            resume_condition={"type": "fact", "fact": "car.inspected", "op": "eq", "value": True})
    w = WorldView.load(db)
    facts, present = w.fact_map()
    assert hm.check_resume_conditions(facts, present) == []
    EventStore(db).append("fact_observed", {"key": "car.inspected", "value": True})
    facts, present = WorldView.load(db).fact_map()
    resolved = hm.check_resume_conditions(facts, present)
    assert [h.id for h in resolved] == [hi.id]
    assert hi.resolution == "condition_observed"
