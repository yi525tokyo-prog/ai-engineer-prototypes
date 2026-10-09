"""Route generation schema, scoring, ranking, uncertainties, constitution effects."""

import pytest

from regent.core.constitution.model import ConstitutionModel
from regent.core.evaluate.evaluator import Evaluator, utility
from regent.core.goals.missions import MissionGraph
from regent.core.observe.events import EventStore
from regent.core.routes.generator import RouteGenerator, combine_estimates
from regent.core.world.state import WorldView
from regent.models.local import LocalStrategist
from regent.schemas import RouteProposal
from regent.sim import scenario
from tests.helpers import mission, prop, route_from, world_with_cash


def test_local_provider_routes_validate_against_schema(db, services):
    scenario.seed(db, "http://127.0.0.1:9")
    m = MissionGraph(db).get(scenario.ROOT_MISSION)
    w = WorldView.load(db)
    from regent.core.routes.generator import mission_dict

    routes = LocalStrategist().generate_routes(mission_dict(m), w.summary(), {})
    assert len(routes) >= 3
    for r in routes:
        RouteProposal.model_validate(r.model_dump())  # round-trips through the schema
        for op in r.operations:
            assert services.tools.has_action(op.tool, op.action) or op.tool in ("invoice", "payments")
        assert 0 <= r.estimates.success_probability <= 1
    archetypes = {r.archetype for r in routes}
    assert {"pursue_contract", "bridge_income", "productize_asset"} <= archetypes


def test_generator_persists_competing_routes_with_sources(db, services):
    scenario.seed(db, "http://127.0.0.1:9")
    m = MissionGraph(db).get(scenario.ROOT_MISSION)
    res = RouteGenerator(db, services).generate(m, WorldView.load(db), reason="test")
    assert len(res.created) >= 3
    for r in res.created:
        assert "local" in r.estimate_sources
        assert set(r.estimates) >= {"expected_upside", "success_probability", "optionality"}
        assert r.operation_specs is not None
    # idempotent: a second generation merges rather than duplicates
    res2 = RouteGenerator(db, services).generate(m, WorldView.load(db), reason="again")
    assert res2.created == []


def test_generic_mission_still_gets_competing_routes(db, services):
    m = mission(db, title="Learn Rust", objective="Become productive in Rust within a month")
    res = RouteGenerator(db, services).generate(m, WorldView.load(db), reason="test")
    assert len(res.created) >= 3
    assert {r.archetype for r in res.created} >= {"direct", "information_first", "defer"}


def test_combine_estimates_records_disagreement():
    combined, dis = combine_estimates({
        "anthropic": {"estimates": {"success_probability": 0.8}},
        "local": {"estimates": {"success_probability": 0.4}},
    })
    assert 0.4 < combined["success_probability"] < 0.8
    assert dis and dis[0]["field"] == "success_probability"


def test_utility_is_concave_past_scale():
    assert utility(50, 100) == 0.5
    assert utility(100, 100) == 1.0
    assert utility(300, 100) == pytest.approx(1.5)


def test_scoring_components_and_sensitivities(db):
    world_with_cash(db)
    m = mission(db)
    r = route_from(db, m, prop("a", expected_upside=100, success_probability=0.6, optionality=0.5, sensitivities=[
        {"fact": "x.status", "op": "eq", "value": "bad", "rationale": "bad news",
         "effects": {"success_probability": {"mul": 0.5}}}]))
    ev = Evaluator(db)
    evals, ctx = ev.evaluate(m, [r], WorldView.load(db))
    e = evals[0]
    assert e.score == pytest.approx(sum(e.components.values()), abs=1e-3)
    assert e.effective["success_probability"] == 0.6
    EventStore(db).append("fact_observed", {"key": "x.status", "value": "bad"})
    evals2, _ = ev.evaluate(m, [r], WorldView.load(db))
    assert evals2[0].effective["success_probability"] == pytest.approx(0.3)
    assert evals2[0].applied[0]["fact"] == "x.status"
    assert evals2[0].score < e.score


def test_ranking_orders_by_score_and_excludes_blocked(db):
    world_with_cash(db)
    m = mission(db)
    good = route_from(db, m, prop("good", expected_upside=100, success_probability=0.8))
    ok = route_from(db, m, prop("ok", expected_upside=100, success_probability=0.5))
    blocked = route_from(db, m, prop("blocked", expected_upside=500, success_probability=0.9,
                                     blockers=[{"kind": "capability", "capability": "teleport", "reason": "no"}],
                                     required_capabilities=["teleport"]))
    evals, _ = Evaluator(db).evaluate(m, [ok, blocked, good], WorldView.load(db))
    assert [e.route.key for e in evals if e.selectable] == ["good", "ok"]
    b = next(e for e in evals if e.route.key == "blocked")
    assert not b.selectable and "teleport" in b.blocked[0]
    assert b.missing_capabilities == ["teleport"]


def test_hard_constraint_invalidates_route(db):
    world_with_cash(db)
    m = mission(db)
    ConstitutionModel(db).upsert(id="hc", type="hard_constraint", statement="No debt", rule={"forbid_tag": "debt"})
    loan = route_from(db, m, prop("loan", expected_upside=1000, success_probability=0.99, tags=["debt"]))
    evals, _ = Evaluator(db).evaluate(m, [loan], WorldView.load(db))
    assert evals[0].invalid and not evals[0].selectable


def test_constitution_and_scarcity_shape_weights(db):
    m = mission(db, tags=["income"])
    w0, _ = Evaluator(db).weights(m)
    ConstitutionModel(db).upsert(id="opt", type="strong_preference", statement="prefer optionality",
                                 dimension="optionality", confidence=0.8)
    w1, why = Evaluator(db).weights(m)
    assert w1["optionality"] > w0["optionality"]
    world_with_cash(db, balance=100000, burn=200000)  # 0.5 months runway
    w2, why2 = Evaluator(db).weights(m)
    assert w2["money_cost"] > w1["money_cost"]
    assert any(x["source"] == "treasury" for x in why2)


def test_scoped_constitution_item_only_affects_matching_missions(db):
    a = mission(db, tags=["income"])
    b = mission(db, tags=["workplace"])
    ConstitutionModel(db).upsert(id="p", type="priority", statement="cash first", dimension="expected_upside",
                                 confidence=0.8, rule={"scope_tags": ["income"]})
    wa, _ = Evaluator(db).weights(a)
    wb, _ = Evaluator(db).weights(b)
    assert wa["expected_value"] > wb["expected_value"]


def test_decision_relevant_uncertainty_detects_flip(db):
    world_with_cash(db)
    m = mission(db)
    fragile = route_from(db, m, prop("fragile", expected_upside=100, success_probability=0.7, sensitivities=[
        {"fact": "client.budget", "op": "eq", "value": "frozen", "effects": {"success_probability": {"mul": 0.1}}}]))
    steady = route_from(db, m, prop("steady", expected_upside=100, success_probability=0.5))
    unc = Evaluator(db).decision_relevant_uncertainties(m, [fragile, steady], WorldView.load(db), fragile.id)
    top = unc[0]
    assert top["fact"] == "client.budget" and top["flips_selection"] and top["voi"] > 0
