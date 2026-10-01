"""Software capabilities: from one sentence to a verified capability Regent keeps using.

The end-to-end tests replay a live benchmark run: the pages Regent fetched (tests/fixtures/
web_software) and the reasoning worker's recorded answers (tests/fixtures/reasoning_software).
A reasoning question that was not recorded fails loudly, so any change in what Regent asks the
worker -- or in what it observed -- shows up here instead of being papered over.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from regent.acquisition import service
from regent.acquisition.replay import ReplayTransport
from regent.software import capability as K
from regent.software import compose as P
from regent.software import connectors as C
from regent.software import expr as X
from regent.software import verify as V
from regent.software.need import check, question_id
from regent.software.reasoner import Reasoner, set_reasoner

FIX = Path(__file__).parent / "fixtures" / "web_software"
ANSWERS = Path(__file__).parent / "fixtures" / "reasoning_software"
LINDY = "I want to know, at a glance, how many real people are actually using LindyBooks."
LINDY_AGAIN = "How many people actually read LindyBooks these days?"
LAUNDRY = "Every morning, tell me whether it's a good day to dry laundry outside in Osaka."
NOW = datetime.now(timezone.utc)


def _ctx(series: dict[str, list]) -> X.EvalContext:
    return X.EvalContext(series=lambda r: series.get(r, []), now=NOW)


# ------------------------------------------------------------------ the expression language

def test_metric_expressions_are_safe_and_honest():
    for bad in ('__import__("os")', 'latest("a:b").real', "lambda: 1", "x + 1", 'open("f")'):
        with pytest.raises(X.ExprError):
            X.parse(bad)
    h = lambda m: NOW - timedelta(minutes=m)  # noqa: E731
    daily = [(h(300), 5), (h(200), 9), (h(100), 2), (h(10), 4)]     # a counter that reset at midnight
    assert X.evaluate('increase("s:c", "24h")', _ctx({"s:c": daily})) == 4 + 2 + 2
    events = [(h(5), [{"t": (NOW - timedelta(days=2)).timestamp() * 1000}, {"t": (NOW - timedelta(days=40)).timestamp()}])]
    assert X.evaluate('count_items("s:e", "t", "7d")', _ctx({"s:e": events})) == 1
    assert X.evaluate('count_items("s:e", "t", "all")', _ctx({"s:e": events})) == 2
    # nothing observed is unknown, never zero
    assert X.evaluate('count_items("s:none", "t", "7d")', _ctx({})) is None
    assert X.evaluate('distinct_items("s:none")', _ctx({})) is None
    # decisions: values with units compare as numbers; an unknown input leaves the verdict unknown
    s = {"p:rain": [(h(1), "0%")], "p:t": [(h(1), "29℃")], "p:idx": [(h(1), "大変よく乾く")]}
    assert X.evaluate('latest("p:rain") <= 30 and latest("p:t") >= 15', _ctx(s)) is True
    assert X.evaluate('latest("p:idx") == "大変よく乾く" or latest("p:rain") > 50', _ctx(s)) is True
    assert X.evaluate('latest("p:missing") < 30 and latest("p:t") >= 15', _ctx(s)) is None


def test_need_checks_and_question_references():
    need = {"subjects": [{"name": "LindyBooks", "kind": "product", "as_written": "LindyBooks"},
                         {"name": "Acme Analytics", "kind": "product", "as_written": "Acme Analytics"}],
            "questions": [{"id": "active", "question": "How many distinct real people used LindyBooks recently?",
                           "quantity": "people", "population": "humans"}]}
    checks = check(LINDY, need)
    assert [c["passed"] for c in checks][:2] == [True, False]      # an invented subject is caught
    assert question_id(need, "How many distinct real people used LindyBooks recently?") == "active"
    assert question_id(need, "active") == "active" and question_id(need, "What is the weather?") is None


# ------------------------------------------------------------------ composition

def _inventory(**extra) -> dict:
    fund = "https://api.example.org/api/fund"
    fields = [
        {"endpoint": fund, "path": "paid.count", "meaning": "payments recorded", "counts": "actions",
         "relation_to_need": "lower_bound", "question": "active", "time_semantics": "cumulative",
         "confidence": 0.8, "path_exists": True, "evidence_found": True, "origin": "product"},
        {"endpoint": "connector:cloudflare_analytics", "path": "uniques_last_full_day", "counts": "people",
         "meaning": "distinct IPs per day incl. bots", "relation_to_need": "upper_bound", "question": "active",
         "time_semantics": "snapshot", "confidence": 0.6, "path_exists": True, "evidence_found": True},
        {"endpoint": "connector:stripe_payments", "path": "paying_people", "counts": "people",
         "meaning": "distinct payers", "relation_to_need": "lower_bound", "question": "active",
         "time_semantics": "cumulative", "confidence": 0.8, "path_exists": True, "evidence_found": True},
        {"endpoint": "connector:client_side_analytics", "path": "visitors", "counts": "people",
         "meaning": "visitors", "relation_to_need": "direct", "question": "active",
         "time_semantics": "snapshot", "confidence": 0.8, "path_exists": True, "evidence_found": True},
    ]
    plat = [{**C.CLOUDFLARE.describe(), "params": {"host": "example.org"}, "host": "example.org", "forbidden_by": []},
            {**C.STRIPE.describe(), "params": {}, "host": "example.org", "forbidden_by": []},
            {**C.CLIENT_ANALYTICS.describe(), "params": {}, "host": "example.org",
             "forbidden_by": ["the product promises no tracking"]}]
    return {"public_fields": fields, "platform_sources": plat, "constraints": [], **extra}


NEED = {"sentence": LINDY, "subjects": [{"name": "LindyBooks", "kind": "product"}],
        "questions": [{"id": "active", "question": "How many real people use it?", "answer_type": "count",
                       "unit": "people", "priority": "core", "windows": ["7d"]}],
        "deliverable": {"form": "glance_view", "refresh": "continuous"}}


def test_composer_states_only_what_the_data_supports():
    spec = P.compose(NEED, _inventory())
    by_form = {m["form"]: m for m in spec["metrics"]}
    # a count of payments proves only that *someone* paid: at least one person, not N
    low = by_form["lower_bound"]
    assert low["expr"].startswith("min(latest(") and low["expr"].endswith(", 1)")
    # the public answer is a bound; the single action that improves it is the one that makes a range
    # (another lower bound from Stripe adds nothing; adding tracking is forbidden by the promise)
    [u] = [u for u in spec["unanswered"] if u["question"] == "active"]
    assert u["unlock"]["connector"] == "cloudflare_analytics" and u["unlock"]["gives"] == "range"
    with_cf = P.compose(NEED, _inventory(), include=["cloudflare_analytics"])
    rng = next(m for m in with_cf["metrics"] if m["form"] == "range")
    assert rng["headline"] == 1 and "expr_hi" in rng
    assert P.coverage_estimate(NEED, _inventory(), ["cloudflare_analytics"]) > P.coverage_estimate(NEED, _inventory(), [])


# ------------------------------------------------------------------ runtime + verification

def _cloudflare_contract(request: httpx.Request) -> httpx.Response:
    """Shape of Cloudflare's documented API (zones + GraphQL Analytics). A contract test of the
    client -- not a stand-in for live data anywhere outside this test."""
    if "/zones" in request.url.path:
        return httpx.Response(200, json={"result": [{"id": "z1", "name": "example.org"}]})
    days = [(NOW.date() - timedelta(days=i)).isoformat() for i in range(3, -1, -1)]
    groups = [{"dimensions": {"date": d}, "sum": {"requests": 900, "pageViews": 300}, "uniq": {"uniques": 120 + i}}
              for i, d in enumerate(days)]
    return httpx.Response(200, json={"data": {"viewer": {"zones": [{"httpRequests1dGroups": groups}]}}})


def test_capability_never_fakes_a_blocked_source_and_upgrades_when_unlocked(db, services, monkeypatch, workspace):
    fund = {"paid": {"count": 2}}
    public = httpx.MockTransport(lambda r: httpx.Response(200, json=fund))
    monkeypatch.setattr(C, "TRANSPORT", public)
    monkeypatch.setattr(V, "TRANSPORT", public)
    monkeypatch.setattr(V, "BROWSER", False)
    spec = P.compose(NEED, _inventory(), include=["cloudflare_analytics"])
    cap = K.save_version(db, mission_id=None, need=NEED, signature=["active@lindybooks"], spec=spec,
                         implementation="composed", provenance={}, reason="test")
    res = V.run(db, cap, services=services)
    assert res["passed"], res["summary"]
    metrics = {m["id"]: m for m in K.compute(db, cap)}
    cf = [m for m in metrics.values() if "cloudflare" in m["id"] or m["form"] == "range"]
    assert cf and all(m["value"] is None and m["status"] == "blocked" and m["display"] == "—" for m in cf)
    assert any(m["display"] == "≥ 1" for m in metrics.values())
    # the principal provides the credential; the same capability now reads the platform
    from regent.software import secrets

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setattr(C, "TRANSPORT", httpx.MockTransport(
        lambda r: _cloudflare_contract(r) if "cloudflare" in r.url.host else httpx.Response(200, json=fund)))
    assert secrets.present("CLOUDFLARE_API_TOKEN")
    res2 = V.run(db, cap, services=services)
    assert res2["passed"] and res2["coverage"] > res["coverage"]
    rng = next(m for m in K.compute(db, cap) if m["form"] == "range")
    assert rng["display"] == "1 – 122"          # last *complete* day: today's partial row is ignored


def test_verification_rejects_a_capability_that_shows_numbers_it_cannot_have(db, services, monkeypatch, workspace):
    monkeypatch.setattr(V, "BROWSER", False)
    monkeypatch.setattr(C, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, json={"paid": {"count": 2}})))
    monkeypatch.setattr(V, "TRANSPORT", C.TRANSPORT)
    spec = P.compose(NEED, _inventory(), include=["cloudflare_analytics"])
    # a worker "fixes" the blocked metric by defaulting it to zero
    for m in spec["metrics"]:
        if "cloudflare" in m["id"]:
            m["expr"] = f'coalesce({m["expr"]}, 0)'
    cap = K.save_version(db, mission_id=None, need=NEED, signature=[], spec=spec, implementation="delegated",
                         provenance={"agent": "claimed done"}, reason="test")
    res = V.run(db, cap, services=services)
    assert not res["passed"]
    assert any(c["check"] == "no number is shown for a blocked source" and not c["passed"] for c in res["checks"])


# ------------------------------------------------------------------ the loop, end to end (replayed)

@pytest.fixture()
def replayed(monkeypatch):
    t = ReplayTransport(FIX)
    for mod in (service, C, V):
        monkeypatch.setattr(mod, "TRANSPORT", t)
    monkeypatch.setattr(service, "ENGINE_KW", {"max_pages": 200, "min_interval_s": 0, "deadline_s": 600})
    import regent.software.discover as D

    monkeypatch.setattr(D.Fetcher, "__init__", _no_browser(D.Fetcher.__init__))
    r = Reasoner(f"replay:{ANSWERS}")
    set_reasoner(r)
    yield t
    set_reasoner(None)


def _no_browser(init):
    def wrapped(self, *a, **kw):
        kw["allow_browser"] = False
        init(self, *a, **kw)
    return wrapped


def _run(db, sentence: str, ticks: int = 8):
    from regent.core.goals.missions import MissionGraph
    from regent.core.loop import RegentLoop

    m = MissionGraph(db).create(title=sentence, objective=sentence)
    db.commit()
    for i in range(ticks):
        r = RegentLoop(db).tick(m.id, force=(i == 0))
        if r.idle:
            break
    db.expire_all()
    return m


def test_from_one_sentence_to_a_capability_regent_keeps_using(db, services, replayed):
    from regent.db import HumanInterrupt, Operation, Route
    from regent.software.humantime import ledger
    from regent.software.tables import SwCapability

    m = _run(db, LINDY)
    need = m.attrs["need"]
    assert need["handled_as"] == "software_capability" and need["subjects"][0]["name"] == "LindyBooks"
    routes = {r.key: r for r in db.scalars(select(Route).where(Route.mission_id == m.id))}
    # the conventional answer is ruled out by the product's own public promise
    assert routes["software-add-analytics"].status == "invalidated"
    assert "tracking" in routes["software-add-analytics"].invalidated_reason
    assert routes["software-compose-cloudflare_analytics"].status == "selected"
    cap = db.scalar(select(SwCapability).where(SwCapability.mission_id == m.id))
    assert cap.verification["passed"] and cap.status == "degraded" and cap.implementation == "composed"
    r = K.read(db, cap, count_use=False)
    assert all(x["value"] is None for x in r["metrics"] if x["status"] == "blocked")
    assert services.tools.get(cap.tool_name) is not None
    # exactly one bounded human interrupt: a credential only the principal holds
    [hi] = db.scalars(select(HumanInterrupt).where(HumanInterrupt.mission_id == m.id)).all()
    assert hi.kind == "credential" and hi.resume_condition["fact"] == "credential.CLOUDFLARE_API_TOKEN"
    assert hi.estimated_time_seconds <= 300
    assert m.status == "waiting_human"
    t = ledger(db, m.id)
    assert t["active_seconds_spent"] < 30 and t["active_seconds_requested_open"] == hi.estimated_time_seconds
    # a later mission, worded differently, reuses it instead of rebuilding
    m2 = _run(db, LINDY_AGAIN)
    ops = {o.tool + "." + o.action for o in db.scalars(select(Operation).where(Operation.mission_id == m2.id))}
    assert "software.reuse" in ops and "acquire.discover" not in ops
    assert db.scalar(select(SwCapability).where(SwCapability.mission_id == m2.id)) is None


def test_a_different_need_uses_public_data_and_existing_services(db, services, replayed):
    from regent.db import Event, Route
    from regent.software.tables import SwCapability

    m = _run(db, LAUNDRY)
    inv = next(iter(db.scalars(select(Route).where(Route.mission_id == m.id))), None)
    assert inv is not None
    keys = {r.key for r in db.scalars(select(Route).where(Route.mission_id == m.id))}
    assert {"software-use-existing", "software-compose-data", "software-compose-crosscheck"} <= keys
    assert "software-instrument" not in keys        # there is no product of the principal's to change
    cap = db.scalar(select(SwCapability).where(SwCapability.mission_id == m.id))
    assert cap.verification["passed"] and cap.status == "usable" and m.status == "completed"
    r = K.read(db, cap, count_use=False)
    head = min((x for x in r["metrics"] if x.get("headline")), key=lambda x: x["headline"])
    assert head["form"] == "decision" and isinstance(head["value"], bool)
    # the morning delivery happens through maintenance, after the mission is complete
    from regent.core.loop import RegentLoop

    monkey_now = datetime.now(timezone.utc).replace(hour=23, minute=0)   # 08:00 the next day in Tokyo
    K.CLOCK = lambda: monkey_now
    try:
        RegentLoop(db).maintain()
    finally:
        K.CLOCK = None
    db.expire_all()
    sent = list(db.scalars(select(Event).where(Event.type == "principal_notified")))
    assert sent and sent[0].payload["text"].startswith(head["display"])
