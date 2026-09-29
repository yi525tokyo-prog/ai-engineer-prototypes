"""Route Generator.

Every available provider independently proposes routes; proposals are merged
by key. Estimates from each provider are kept separately (``estimate_sources``)
and combined with provider weights -- disagreement becomes explicit
uncertainty rather than being voted away. Operations naming unknown tools are
dropped with a critique. Providers then criticize the merged set; critiques
are stored as low-weight estimate sources, never applied as truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.capabilities.manager import CapabilityManager
from regent.core.observe.events import EventStore
from regent.core.world.state import WorldView
from regent.db import Mission, Operation, Route
from regent.global_brain.brain import LocalGlobalBrain
from regent.ids import new_id, utcnow
from regent.schemas import ESTIMATE_FIELDS, RouteProposal

PROVIDER_WEIGHT = {"local": 0.7, "capability_manager": 1.0}
CRITIQUE_WEIGHT = 0.3


@dataclass
class GenerationResult:
    created: list[Route] = field(default_factory=list)
    updated: list[Route] = field(default_factory=list)
    provider_outputs: list[dict[str, Any]] = field(default_factory=list)


def mission_dict(m: Mission) -> dict[str, Any]:
    return {"id": m.id, "title": m.title, "objective": m.objective, "tags": m.tags or [],
            "success_criteria": m.success_criteria or [], "value_scale": m.value_scale,
            "horizon_days": m.horizon_days, "attrs": m.attrs or {}}


def route_dict(r: Route) -> dict[str, Any]:
    return {k: getattr(r, k) for k in ("id", "key", "title", "thesis", "archetype", "estimates", "effective",
                                       "sensitivities", "required_capabilities", "operation_specs", "uncertainty",
                                       "evidence_ids", "tags", "status")}


def combine_estimates(sources: dict[str, dict[str, Any]]) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Weighted combination across providers; returns (estimates, disagreements)."""
    combined: dict[str, float] = {}
    disagreements = []
    for f in ESTIMATE_FIELDS:
        vals = []
        for name, src in sources.items():
            v = (src.get("estimates") or {}).get(f)
            if v is None:
                continue
            w = CRITIQUE_WEIGHT if name.startswith("critique:") else PROVIDER_WEIGHT.get(name, 1.0)
            vals.append((float(v), w, name))
        if not vals:
            continue
        tw = sum(w for _, w, _ in vals)
        combined[f] = round(sum(v * w for v, w, _ in vals) / tw, 6)
        lo, hi = min(v for v, _, _ in vals), max(v for v, _, _ in vals)
        scale = max(abs(hi), abs(lo), 1e-9)
        if len(vals) > 1 and (hi - lo) / scale > 0.25:
            disagreements.append({"field": f, "min": lo, "max": hi,
                                  "by": {n: v for v, _, n in vals}})
    return combined, disagreements


class RouteGenerator:
    def __init__(self, db: Session, services: Any):
        self.db = db
        self.services = services
        self.events = EventStore(db)

    def routes(self, mission_id: str, statuses: tuple[str, ...] | None = None) -> list[Route]:
        q = select(Route).where(Route.mission_id == mission_id)
        if statuses:
            q = q.where(Route.status.in_(statuses))
        return list(self.db.scalars(q.order_by(Route.created_at)))

    def needs_generation(self, mission: Mission, world: WorldView) -> str | None:
        alive = self.routes(mission.id, ("alive", "selected"))
        if not alive:
            return "no live routes"
        sig = self._world_signature(world)
        if (mission.attrs or {}).get("generation_signature") != sig:
            return "world entities changed"
        if (mission.attrs or {}).get("regenerate"):
            return "regeneration requested"
        return None

    @staticmethod
    def _world_signature(world: WorldView) -> str:
        ents = sorted(f"{e.id}:{e.kind}:{e.attrs.get('status', '')}" for e in world.entities.values()
                      if e.kind in ("contract", "service", "project", "place", "commitment", "message", "event"))
        caps = sorted(f"{c.id}:{c.status}" for c in world.capabilities.values())
        return str(hash("|".join(ents + caps)))

    # ----------------------------------------------------------------- run

    def generate(self, mission: Mission, world: WorldView, *, reason: str = "") -> GenerationResult:
        res = GenerationResult()
        mdict = mission_dict(mission)
        wsum = world.summary()
        proposals: list[tuple[str, RouteProposal]] = []
        if "capability_acquisition" in (mission.tags or []):
            cap = (mission.attrs or {}).get("capability")
            for p in CapabilityManager(self.db, self.services).acquisition_options(cap, world):
                proposals.append(("capability_manager", p))
            res.provider_outputs.append({"provider": "capability_manager", "task": "generate_routes",
                                         "routes": [p.key for _, p in proposals]})
        else:
            try:
                skills = LocalGlobalBrain(self.db).query_skill(mission.objective, k=5)
            except Exception:
                skills = []
            context = {"tools": self.services.tools.catalog(), "skills": skills}
            for provider in self.services.providers.all_available():
                try:
                    out = self.services.providers.call(
                        provider, "generate_routes", lambda p=provider: p.generate_routes(mdict, wsum, context),
                        mission_id=mission.id)
                except Exception as e:
                    res.provider_outputs.append({"provider": provider.name, "task": "generate_routes",
                                                 "error": str(e)[:300]})
                    continue
                res.provider_outputs.append({"provider": provider.name, "task": "generate_routes",
                                             "routes": [r.key for r in out]})
                proposals.extend((provider.name, r) for r in out)

        existing = {r.key: r for r in self.routes(mission.id)}
        seen: set[str] = set()
        for provider_name, prop in proposals:
            prop, dropped = self._validate_ops(prop)
            r = existing.get(prop.key)
            if r is None:
                r = Route(id=new_id("rt"), mission_id=mission.id, key=prop.key, title=prop.title, thesis=prop.thesis,
                          archetype=prop.archetype, generated_by=[provider_name], status="alive",
                          estimates={}, effective={}, estimate_sources={}, critiques=[], evidence_ids=[])
                self.db.add(r)
                existing[prop.key] = r
                res.created.append(r)
            else:
                if provider_name not in (r.generated_by or []):
                    r.generated_by = list(r.generated_by or []) + [provider_name]
                if r not in res.created:
                    res.updated.append(r)
            sources = dict(r.estimate_sources or {})
            sources[provider_name] = {"estimates": prop.estimates.model_dump(), "rationale": prop.estimate_rationale,
                                      "at": utcnow().isoformat()}
            r.estimate_sources = sources
            # Structural fields come from the first (or preferred remote) proposer; the route's
            # executed history is never rewritten.
            if provider_name == (r.generated_by or [provider_name])[0] or provider_name not in ("local",):
                started = self.db.scalar(select(Operation.id).where(Operation.route_id == r.id).limit(1))
                r.title, r.thesis, r.archetype, r.tags = prop.title, prop.thesis, prop.archetype, prop.tags
                r.sensitivities = [s.model_dump() for s in prop.sensitivities]
                r.blockers = [b.model_dump() for b in prop.blockers]
                r.required_capabilities = prop.required_capabilities
                r.dependencies = prop.dependencies
                r.uncertainty = [u.model_dump() for u in prop.uncertainty]
                if not started or r.status not in ("selected",):
                    r.operation_specs = [o.model_dump() for o in prop.operations]
            if dropped:
                r.critiques = [c for c in (r.critiques or []) if c.get("provider") != "validator"] + [
                    {"provider": "validator", "issues": [f"dropped operation(s) with unknown tool/action: {dropped}"]}]
            combined, disagreements = combine_estimates(r.estimate_sources)
            r.estimates = combined
            r.uncertainty = [u for u in (r.uncertainty or []) if not u.get("provider_disagreement")] + [
                {"question": f"Providers disagree on {d['field']} ({d['min']:.2f}-{d['max']:.2f})", "fact_key": "",
                 "affects": [d["field"]], "provider_disagreement": d} for d in disagreements]
            if r.status in ("invalidated",) and r.invalidated_reason and r.invalidated_reason.startswith("not proposed"):
                r.status = "alive"
                r.invalidated_reason = None
            r.updated_at = utcnow()
            seen.add(prop.key)
        self.db.flush()
        self._criticize(mission, world, res)
        mission.attrs = {**(mission.attrs or {}), "generation_signature": self._world_signature(world),
                         "regenerate": False, "last_generation_reason": reason}
        self.events.append("routes_generated", {
            "mission_id": mission.id, "reason": reason, "created": [r.key for r in res.created],
            "updated": [r.key for r in res.updated], "providers": res.provider_outputs,
        }, source="regent", mission_id=mission.id)
        return res

    def _validate_ops(self, prop: RouteProposal) -> tuple[RouteProposal, list[str]]:
        keep, dropped = [], []
        for op in prop.operations:
            if self.services.tools.has_action(op.tool, op.action) or op.tool in ("invoice", "payments"):
                keep.append(op)
            else:
                dropped.append(f"{op.tool}.{op.action}")
        prop.operations = keep
        return prop, dropped

    def _criticize(self, mission: Mission, world: WorldView, res: GenerationResult) -> None:
        routes = self.routes(mission.id, ("alive", "selected"))
        if not routes:
            return
        wsum = world.summary()
        rdicts = [route_dict(r) for r in routes]
        by_key = {r.key: r for r in routes}
        for provider in self.services.providers.all_available():
            try:
                crits = self.services.providers.call(
                    provider, "criticize_routes",
                    lambda p=provider: p.criticize_routes(mission_dict(mission), wsum, rdicts), mission_id=mission.id)
            except Exception as e:
                res.provider_outputs.append({"provider": provider.name, "task": "criticize_routes", "error": str(e)[:200]})
                continue
            res.provider_outputs.append({"provider": provider.name, "task": "criticize_routes",
                                         "critiques": len(crits)})
            for c in crits:
                r = by_key.get(c.route_key)
                if r is None:
                    continue
                r.critiques = [x for x in (r.critiques or []) if x.get("provider") != provider.name] + [c.model_dump()]
                if c.adjustments:
                    sources = dict(r.estimate_sources or {})
                    base = dict((sources.get(provider.name) or {}).get("estimates") or r.estimates or {})
                    base.update(c.adjustments)
                    sources[f"critique:{provider.name}"] = {"estimates": base, "rationale": {"issues": c.issues}}
                    r.estimate_sources = sources
                    r.estimates, _ = combine_estimates(sources)
        self.db.flush()
