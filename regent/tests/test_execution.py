"""Execution result logging, reroutes, verification, capability acquisition, skills, global brain."""

import pytest
from sqlalchemy import select

from regent.core.capabilities.manager import CapabilityManager
from regent.core.executor.executor import Executor, resolve_templates
from regent.core.memory.skills import SkillExtractor
from regent.core.observe.events import EventStore
from regent.core.replan.replanner import Replanner
from regent.core.verify.verifier import Verifier
from regent.core.world.state import WorldView
from regent.db import Capability, Event, Evidence, Fact, LedgerEntry, Skill
from regent.global_brain.brain import LocalGlobalBrain, PrivacyViolation
from tests.helpers import mission
from tests.test_authority_and_interrupts import _op


def test_execution_result_logging(db, services, workspace):
    m = mission(db)
    op = _op(db, m, "fs", "write", {"path": "notes/a.md", "content": "hello"},
             verification={"method": "schema", "required_keys": ["bytes"]}, emits={"bytes": "notes.a.bytes"})
    rep = Executor(db, services).run(m, WorldView.load(db))
    assert rep.started == [op.id]
    assert op.status == "unverified"
    assert op.attempts == 1 and op.attempt_log[0]["status"] == "ok"
    assert op.outputs["bytes"] == 5
    assert (workspace / "notes/a.md").read_text() == "hello"
    types = [e.type for e in db.scalars(select(Event).where(Event.mission_id == m.id))]
    assert "operation_started" in types and "tool_succeeded" in types
    vr, ev = Verifier(db, services).verify_and_record(op)
    assert vr.verdict == "pass" and op.status == "succeeded"
    assert db.get(Evidence, ev.id).operation_id == op.id
    assert db.get(Fact, "notes.a.bytes").evidence_id == ev.id


def test_failed_tool_reroutes_via_fallback_and_learns_skill(db, services):
    m = mission(db)
    EventStore(db).append("resource_changed", {"id": "api", "kind": "api_spend", "name": "API", "unit": "USD",
                                               "balance": 5, "limit": 5})
    from regent.connectors.services import LocalSearchIndex

    db.commit()  # connectors write through their own transactions
    LocalSearchIndex().index("https://x/1", "python contract", "remote python contract work")
    op = _op(db, m, "search", "web", {"query": "python contract"},
             verification={"method": "schema", "required_keys": ["results"]})
    op.retry_policy = {"max_attempts": 1, "fallback": [{"tool": "search", "action": "local"}], "fallback_index": -1}
    ex = Executor(db, services)
    ex.run(m, WorldView.load(db))
    assert op.status == "pending" and op.tool == "search" and op.action == "local"
    assert "missing credential REGENT_SEARCH_API_KEY" in op.attempt_log[0]["error"]
    assert any(e.type == "operation_rerouted" for e in db.scalars(select(Event)))
    ex.run(m, WorldView.load(db))
    Verifier(db, services).verify_and_record(op)
    assert op.status == "succeeded" and op.outputs["count"] == 1
    skills = SkillExtractor(db).extract(op)
    assert skills and skills[0].failure_modes == ["missing_credential:REGENT_SEARCH_API_KEY"]
    assert {s.domain for s in db.scalars(select(Skill))} == {"private", "global"}
    # the learned reroute is applied up-front next time
    op2 = _op(db, m, "search", "web", {"query": "python"}, key="again",
              verification={"method": "schema", "required_keys": ["results"]})
    ex.run(m, WorldView.load(db))
    assert op2.attempt_log[0]["status"] == "skipped" and "skill" in op2.attempt_log[0]["via"]
    assert op2.status == "unverified" and op2.action == "local"


def test_verification_methods(db, services):
    m = mission(db)
    v = Verifier(db, services)
    op = _op(db, m, "maps", "route", verification={"method": "predicate",
                                                   "predicate": {"path": "slot", "op": "eq", "value": "confirmed"}})
    op.outputs = {"slot": "pending"}
    assert v.verify(op).verdict == "fail"
    op.outputs = {"slot": "confirmed"}
    assert v.verify(op).verdict == "pass"
    op.verification = {"method": "schema", "required_keys": ["a", "b"]}
    op.outputs = {"a": 1}
    assert v.verify(op).verdict == "fail"
    # state_check: independent read-back through a read-only tool
    send = _op(db, m, "email", "send", {"to": ["x"], "subject": "s", "body": "b"}, key="send",
               verification={"method": "state_check", "check": {
                   "tool": "email", "action": "outbox", "inputs": {},
                   "expect": {"contains_id": "{{self.outputs.message_id}}"}}})
    send.outputs = {"message_id": "does-not-exist"}
    assert v.verify(send).verdict == "fail"
    from regent.connectors.services import LocalMailbox

    db.commit()  # connectors write through their own transactions
    real = LocalMailbox().send(to=["x"], subject="s", body="b")
    send.outputs = {"message_id": real["id"]}
    assert v.verify(send).verdict == "pass"


def test_insufficient_resources_blocks(db, services):
    m = mission(db)
    EventStore(db).append("resource_changed", {"id": "cash", "kind": "money", "name": "Cash", "unit": "JPY",
                                               "balance": 100})
    from regent.core.authority.manager import AuthorityManager

    AuthorityManager(db, services.tools).grant("commerce.purchase", "COMMIT", {})
    op = _op(db, m, "commerce", "purchase", {"vendor": "v", "item": "i", "amount": 5000})
    op.cost_estimate = {"money": 5000}
    Executor(db, services).run(m, WorldView.load(db))
    assert op.status == "blocked" and "insufficient" in op.error


def test_costs_are_ledgered(db, services):
    m = mission(db)
    EventStore(db).append("resource_changed", {"id": "cash", "kind": "money", "name": "Cash", "unit": "JPY",
                                               "balance": 10000})
    from regent.core.authority.manager import AuthorityManager

    AuthorityManager(db, services.tools).grant("commerce.purchase", "COMMIT", {"max_amount": 3000})
    op = _op(db, m, "commerce", "purchase", {"vendor": "v", "item": "pass", "amount": 2200, "currency": "JPY"})
    Executor(db, services).run(m, WorldView.load(db))
    assert op.status == "unverified"
    led = list(db.scalars(select(LedgerEntry)))
    assert led and led[0].amount == -2200 and led[0].operation_id == op.id


def test_templates_resolve_outputs_and_facts(db):
    m = mission(db)
    a = _op(db, m, "fs", "write", key="a")
    a.outputs = {"draft_id": "d1", "n": 3}
    out = resolve_templates({"x": "{{ops.a.outputs.draft_id}}", "n": "{{ops.a.outputs.n}}",
                             "s": "id={{ops.a.outputs.draft_id}} f={{facts.k.v}}"}, {"a": a}, {"k.v": 7})
    assert out == {"x": "d1", "n": 3, "s": "id=d1 f=7"}


def test_capability_acquisition_builds_and_registers_tool(db, services, workspace):
    m = mission(db)
    EventStore(db).append("capability_changed", {"id": "invoice.generate", "name": "invoice.generate",
                                                 "status": "missing"})
    cm = CapabilityManager(db, services)
    opts = cm.acquisition_options("invoice.generate", WorldView.load(db))
    strategies = {o.archetype for o in opts}
    assert {"generate_code", "human_action", "avoid_dependency"} <= strategies
    build = next(o for o in opts if o.archetype == "generate_code")
    assert build.estimates.success_probability >= 0.9  # vetted template exists
    op = _op(db, m, "code", "build_tool", {"capability": "invoice.generate"},
             verification={"method": "predicate", "predicate": {"path": "tests_passed", "op": "eq", "value": True}})
    Executor(db, services).run(m, WorldView.load(db))
    vr, ev = Verifier(db, services).verify_and_record(op)
    assert vr.verdict == "pass", op.outputs.get("test_output")
    Replanner(db, services).apply_consequences(op, ev)
    assert db.get(Capability, "invoice.generate").status == "available"
    assert services.tools.get("invoice") is not None and services.tools.get("invoice").built_by_regent
    use = _op(db, m, "invoice", "generate", {"client": "ACME", "items": [{"description": "w", "amount": 1000}]},
              key="use", verification={"method": "schema", "required_keys": ["path", "total"]})
    Executor(db, services).run(m, WorldView.load(db))
    assert use.status == "unverified" and use.outputs["total"] == 1100


def test_global_brain_refuses_private_data(db):
    EventStore(db).append("entity_upserted", {"id": "p_haruka", "kind": "person", "name": "Haruka Kinoshita"})
    gb = LocalGlobalBrain(db)
    with pytest.raises(PrivacyViolation):
        gb.publish_fact("contact", "Haruka Kinoshita's email is haruka@kinoshita.example")
    with pytest.raises(PrivacyViolation):
        gb.publish_fact("budget", "client budget is JPY 600,000")
    f = gb.publish_fact("pytest", "pytest 9 -q omits the counts line; parse junit xml instead", confidence=0.8)
    assert f.domain == "global"
    assert gb.query_world("pytest counts")[0]["id"] == f.id
    s = gb.publish_skill({"name": "email Haruka Kinoshita", "task_pattern": "reply to Haruka Kinoshita at "
                          "haruka@kinoshita.example", "procedure": [{"step": "email.send", "note": "to Haruka Kinoshita"}]})
    assert "haruka" not in (s.task_pattern + s.name).lower()
    assert "<entity>" in s.name and "<email>" in s.task_pattern
