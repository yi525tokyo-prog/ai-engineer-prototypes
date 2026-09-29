"""Route Evaluator: Regent's own scoring framework (outside the model layer).

Models estimate; Regent scores. For each route:

1. start from the provider-combined estimates;
2. apply *sensitivities* whose facts are now known (evidence-driven updates);
3. check blockers and hard constraints;
4. score with weights derived from base weights x constitution x resource scarcity.

    score =  w_ev   * p * U(upside / value_scale)
           + w_info * information_gain
           + w_opt  * optionality
           + w_rev  * reversibility
           - w_time * min(time_hours / 40, 1.5)
           - w_money* min(money_cost / cash_balance, 1.5)
           - w_risk * risk
           - w_auth * authority_cost
           + constitution tag bonuses

U is concave above the mission's value scale (diminishing returns past "enough").
Every component is returned so the UI can show *why*.

``decision_relevant_uncertainties`` computes, for each unknown fact that some
route is sensitive to, whether resolving it could flip the selection and a
simple value-of-information estimate. The planner uses it to schedule probes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from regent.core.constitution.model import ConstitutionModel
from regent.core.treasury.treasury import Treasury
from regent.core.world.state import WorldView
from regent.db import Mission, Route
from regent.schemas import Blocker, Effect, Sensitivity

BASE_WEIGHTS = {
    "expected_value": 1.0, "information_gain": 0.15, "optionality": 0.25, "reversibility": 0.15,
    "time_cost": 0.3, "money_cost": 0.4, "risk": 0.2, "authority_cost": 0.1,
}
DIM_TO_WEIGHT = {
    "expected_upside": "expected_value", "success_probability": "expected_value", "money_cost": "money_cost",
    "time_cost_hours": "time_cost", "information_gain": "information_gain", "optionality": "optionality",
    "reversibility": "reversibility", "risk": "risk", "authority_cost": "authority_cost",
}
PROB_FIELDS = ("success_probability", "information_gain", "reversibility", "optionality", "risk", "authority_cost")


def free_cash(world: WorldView, horizon_days: float) -> float:
    """Cash not already spoken for by commitments due within the mission horizon (at least one
    month: cash is shared by every mission, so a short mission cannot ignore next month's rent)."""
    from datetime import datetime, timedelta, timezone

    money = world.money()
    if money is None:
        return 1.0
    cash = float(money.balance)
    limit = datetime.now(timezone.utc) + timedelta(days=max(horizon_days, 30))
    for c in world.of_kind("commitment"):
        amt, due = c.attrs.get("amount"), c.attrs.get("due")
        if not amt or not due:
            continue
        try:
            if datetime.fromisoformat(due) <= limit:
                cash -= float(amt)
        except ValueError:
            continue
    return max(cash, 0.1 * float(money.balance))


def utility(upside: float, scale: float) -> float:
    x = max(0.0, upside) / max(scale, 1e-9)
    return min(x, 1.0) + 0.25 * max(0.0, x - 1.0)


@dataclass
class RouteEval:
    route: Route
    effective: dict[str, float]
    applied: list[dict[str, Any]]
    score: float
    components: dict[str, float]
    selectable: bool = True
    blocked: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    rank: int = 0
    missing_capabilities: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {"route_id": self.route.id, "key": self.route.key, "title": self.route.title,
                "score": round(self.score, 4), "rank": self.rank, "selectable": self.selectable,
                "blocked": self.blocked, "invalid": self.invalid}


class Evaluator:
    def __init__(self, db: Session):
        self.db = db
        self.constitution = ConstitutionModel(db)
        self.treasury = Treasury(db)

    # ------------------------------------------------------------ estimates

    @staticmethod
    def effective(route: Route, facts: dict[str, Any], present: set[str],
                  world: WorldView | None = None) -> tuple[dict[str, float], list[dict[str, Any]]]:
        eff = {k: float(v) for k, v in (route.estimates or {}).items() if v is not None}
        applied = []
        for s in route.sensitivities or []:
            sens = Sensitivity.model_validate(s)
            if sens.holds(facts, present) is not True:
                continue
            before = {}
            for f, eff_spec in sens.effects.items():
                before[f] = eff.get(f, 0.0)
                eff[f] = Effect.model_validate(eff_spec).apply(eff.get(f, 0.0), facts.get(sens.fact))
            ev_id = None
            if world is not None and sens.fact in world.facts:
                ev_id = world.facts[sens.fact].evidence_id
            applied.append({"fact": sens.fact, "value": facts.get(sens.fact), "rationale": sens.rationale,
                            "effects": {f: {"from": round(before[f], 4), "to": round(eff[f], 4)} for f in before},
                            "evidence_id": ev_id})
        for f in PROB_FIELDS:
            if f in eff:
                eff[f] = max(0.0, min(1.0, eff[f]))
        for f in ("time_cost_hours", "money_cost", "expected_upside"):
            if f in eff:
                eff[f] = max(0.0, eff[f])
        return eff, applied

    # -------------------------------------------------------------- weights

    def weights(self, mission: Mission) -> tuple[dict[str, float], list[dict[str, Any]]]:
        w = dict(BASE_WEIGHTS)
        why: list[dict[str, Any]] = []
        mult, cwhy = self.constitution.weight_multipliers(mission.tags or [])
        for dim, factor in mult.items():
            key = DIM_TO_WEIGHT.get(dim)
            if key:
                w[key] *= factor
        why += [{"source": "constitution", **x} for x in cwhy]
        sc = self.treasury.scarcity()
        mp = float(sc.get("money", 0))
        if mp:
            w["money_cost"] *= 1 + 1.5 * mp
            w["expected_value"] *= 1 + 0.5 * mp
            why.append({"source": "treasury", "statement": "; ".join(sc.get("notes", [])),
                        "effect": f"money_cost x{1 + 1.5 * mp:.2f}, expected_value x{1 + 0.5 * mp:.2f}"})
        ap = float(sc.get("attention", 0))
        if ap:
            w["authority_cost"] *= 1 + 2 * ap
            why.append({"source": "treasury", "statement": f"principal attention pressure {ap}",
                        "effect": f"authority_cost x{1 + 2 * ap:.2f}"})
        return {k: round(v, 4) for k, v in w.items()}, why

    # ---------------------------------------------------------------- score

    def score(self, route: Route, eff: dict[str, float], w: dict[str, float], mission: Mission,
              world: WorldView, tag_bonus: dict[str, tuple[float, str]]) -> tuple[float, dict[str, float]]:
        balance = max(free_cash(world, mission.horizon_days), 1.0)
        p = eff.get("success_probability", 0.5)
        c = {
            "expected_value": w["expected_value"] * p * utility(eff.get("expected_upside", 0), mission.value_scale),
            "information_gain": w["information_gain"] * eff.get("information_gain", 0),
            "optionality": w["optionality"] * eff.get("optionality", 0.5),
            "reversibility": w["reversibility"] * eff.get("reversibility", 0.5),
            "time_cost": -w["time_cost"] * min(eff.get("time_cost_hours", 0) / 40.0, 1.5),
            "money_cost": -w["money_cost"] * min(eff.get("money_cost", 0) / balance, 1.5),  # vs free cash
            "risk": -w["risk"] * eff.get("risk", 0.3),
            "authority_cost": -w["authority_cost"] * eff.get("authority_cost", 0),
        }
        for tag in route.tags or []:
            if tag in tag_bonus:
                c[f"constitution:{tag}"] = tag_bonus[tag][0]
        c = {k: round(v, 5) for k, v in c.items()}
        return round(sum(c.values()), 5), c

    # ------------------------------------------------------------- evaluate

    def evaluate(self, mission: Mission, routes: list[Route], world: WorldView,
                 facts_override: dict[str, Any] | None = None) -> tuple[list[RouteEval], dict[str, Any]]:
        facts, present = world.fact_map()
        if facts_override:
            facts = {**facts, **facts_override}
            present = present | set(facts_override)
        w, why = self.weights(mission)
        tag_bonus = self.constitution.tag_bonuses(mission.tags or [])
        evals: list[RouteEval] = []
        for r in routes:
            if r.status in ("abandoned", "completed"):
                continue
            eff, applied = self.effective(r, facts, present, world)
            score, comps = self.score(r, eff, w, mission, world, tag_bonus)
            ev = RouteEval(route=r, effective=eff, applied=applied, score=score, components=comps)
            for b in r.blockers or []:
                blk = Blocker.model_validate(b)
                if blk.kind == "fact":
                    cond = blk.condition()
                    if cond is not None and cond.holds(facts, present) is True:
                        ev.invalid.append(blk.reason or f"{blk.fact} {blk.op} {blk.value}")
                elif blk.kind == "capability" and blk.capability and not world.capability_available(blk.capability):
                    ev.blocked.append(f"missing capability {blk.capability}")
            ev.missing_capabilities = [c for c in (r.required_capabilities or []) if not world.capability_available(c)]
            ev.invalid += self.constitution.hard_constraint_violations(r)
            if eff.get("success_probability", 1) <= 0.0:
                ev.invalid.append("success probability is zero")
            ev.selectable = not ev.invalid and not ev.blocked
            evals.append(ev)
        evals.sort(key=lambda e: (not e.selectable, -e.score))
        for i, e in enumerate(evals):
            e.rank = i + 1
        context = {"weights": w, "weight_reasons": why,
                   "tag_bonuses": {k: {"bonus": round(v[0], 4), "item": v[1]} for k, v in tag_bonus.items()},
                   "value_scale": mission.value_scale}
        return evals, context

    # ------------------------------------------------------- uncertainties

    def decision_relevant_uncertainties(self, mission: Mission, routes: list[Route], world: WorldView,
                                        current_choice: str | None) -> list[dict[str, Any]]:
        facts, present = world.fact_map()
        base_evals, _ = self.evaluate(mission, routes, world)
        selectable = [e for e in base_evals if e.selectable]
        if not selectable:
            return []
        choice = current_choice or selectable[0].route.id
        unknown: dict[str, set] = {}
        for r in routes:
            if r.status not in ("alive", "selected"):
                continue
            for s in r.sensitivities or []:
                if s["fact"] in present:
                    continue
                if any((e or {}).get("from_fact") for e in (s.get("effects") or {}).values()):
                    continue  # value comes from the fact itself: no meaningful hypothetical branch
                vals = unknown.setdefault(s["fact"], set())
                v = s.get("value")
                if s["op"] in ("eq", "ne"):
                    vals.add(_hashable(v))
                elif s["op"] in ("gt", "ge"):
                    vals.add(float(v) + 1)
                elif s["op"] in ("lt", "le"):
                    vals.add(float(v) - 1)
                elif s["op"] in ("truthy", "falsy"):
                    vals.update({True, False})
        out = []
        for fact, values in unknown.items():
            branches = []
            flips = False
            voi = 0.0
            for v in values:
                evs, _ = self.evaluate(mission, routes, world, facts_override={fact: _unhash(v)})
                sel = [e for e in evs if e.selectable]
                if not sel:
                    continue
                top = sel[0]
                cur = next((e for e in sel if e.route.id == choice), None)
                cur_score = cur.score if cur else 0.0  # incumbent unviable in this branch
                gain = max(0.0, top.score - cur_score)
                voi = max(voi, gain)
                if top.route.id != choice:
                    flips = True
                branches.append({"value": _unhash(v), "top_route": top.route.key, "top_score": round(top.score, 4),
                                 "choice_score": round(cur_score, 4)})
            resolvers = [f"{r.key}:{o['key']}" for r in routes for o in (r.operation_specs or [])
                         if fact in (o.get("resolves") or [])]
            question = next((u["question"] for r in routes for u in (r.uncertainty or []) if u.get("fact_key") == fact),
                            f"What is {fact}?")
            out.append({"fact": fact, "question": question, "flips_selection": flips, "voi": round(voi, 4),
                        "branches": branches, "resolvable_by": resolvers})
        out.sort(key=lambda x: (-int(x["flips_selection"]), -x["voi"]))
        return out


def _hashable(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return repr(v)
    return v


def _unhash(v: Any) -> Any:
    return v
