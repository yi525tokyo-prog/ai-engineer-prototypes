"""HousingAdapter: housing as the first World Acquisition domain.

Nothing here lists candidate properties. The adapter knows *kinds* of sources
(portals, operators, public datasets) and their entry pages -- the way a
person knows that rental portals exist -- and plans how to reach live content
by navigation. Candidates, prices and availability all come from what the
fetcher actually retrieves at run time.
"""

from __future__ import annotations

import os
import re
import statistics
from typing import Any

from sqlalchemy import select

from regent.acquisition.domain import DomainAdapter
from regent.acquisition.housing.enricher import HousingEnricher
from regent.acquisition.housing.extractor import HousingExtractor
from regent.acquisition.housing.resolver import HousingEntityResolver, building_features
from regent.acquisition.tables import AcqEntity, AcqJob, AcqMention, AcqRequest
from regent.acquisition.types import AttrSpec, EnrichmentJobSpec, Mention, SourceSpec
from regent.ids import utcnow
from regent.schemas import (CostEstimate, OperationSpec, RouteEstimates, RouteProposal, Sensitivity,
                            Effect, Uncertainty, VerificationSpec)

H, D, Y = 3600.0, 86400.0, 365 * 86400.0

ATTRS: dict[str, AttrSpec] = {a.name: a for a in [
    # unit / listing level -- time-sensitive
    AttrSpec("availability", "unit", "bool", ttl_s=6 * H),
    AttrSpec("rent", "unit", "number", "JPY", ttl_s=24 * H, rel_tol=0.005),
    AttrSpec("management_fee", "unit", "number", "JPY", ttl_s=24 * H, abs_tol=100),
    AttrSpec("deposit", "unit", "number", "JPY", ttl_s=24 * H, rel_tol=0.01, abs_tol=100),
    AttrSpec("key_money", "unit", "number", "JPY", ttl_s=24 * H, rel_tol=0.01, abs_tol=100),
    AttrSpec("move_in", "unit", "text", ttl_s=24 * H),
    AttrSpec("info_updated_at", "unit", "date", ttl_s=24 * H),
    AttrSpec("next_update_at", "unit", "date", ttl_s=24 * H),
    AttrSpec("earliest_move_in_est", "unit", "date", ttl_s=24 * H),
    AttrSpec("move_in_feasible", "unit", "bool", ttl_s=24 * H),
    AttrSpec("conditions", "unit", "text", ttl_s=7 * D), AttrSpec("lease_term", "unit", "text", ttl_s=30 * D),
    AttrSpec("guarantor", "unit", "text", ttl_s=30 * D), AttrSpec("other_initial_cost", "unit", "text", ttl_s=7 * D),
    AttrSpec("renewal_fee", "unit", "text", ttl_s=30 * D), AttrSpec("transaction_type", "unit", "text", ttl_s=7 * D),
    AttrSpec("internet", "unit", "text", ttl_s=90 * D), AttrSpec("nearby_pois", "unit", "json", ttl_s=90 * D),
    AttrSpec("housing_type", "unit", "text", ttl_s=30 * D),
    # unit -- stable
    AttrSpec("layout", "unit", "text", ttl_s=180 * D), AttrSpec("area_m2", "unit", "number", "m2", ttl_s=Y, abs_tol=0.15),
    AttrSpec("floor", "unit", "number", ttl_s=Y), AttrSpec("room_number", "unit", "text", ttl_s=Y),
    # building
    AttrSpec("name", "building", "text", ttl_s=180 * D), AttrSpec("address", "building", "text", ttl_s=Y),
    AttrSpec("stations", "building", "json", ttl_s=180 * D), AttrSpec("built_year", "building", "number", ttl_s=Y),
    AttrSpec("floors_total", "building", "number", ttl_s=Y), AttrSpec("structure", "building", "text", ttl_s=Y),
    AttrSpec("building_type", "building", "text", ttl_s=30 * D), AttrSpec("coords", "building", "json", ttl_s=Y),
    AttrSpec("nearest_stations_public", "building", "json", ttl_s=180 * D),
    AttrSpec("walk_check", "building", "json", ttl_s=180 * D),
    AttrSpec("rail_distance_m", "building", "number", ttl_s=Y, abs_tol=5),
    AttrSpec("libraries_nearby", "building", "json", ttl_s=90 * D),
    AttrSpec("universities_nearby", "building", "json", ttl_s=90 * D),
    AttrSpec("hub_minutes_est", "building", "json", ttl_s=180 * D),
    AttrSpec("review", "building", "text", ttl_s=30 * D),
    AttrSpec("vacancies", "building", "number", ttl_s=24 * H),
]}
MARKET = AttrSpec("market_rent", "area", "number", "JPY", ttl_s=30 * D, rel_tol=0.01)

REGION = os.environ.get("REGENT_HOME_REGION", "東京都")
SINGLE_LAYOUTS = {"1R", "1K", "1DK", "1LDK"}


def sources(region: str) -> dict[str, list[SourceSpec]]:
    """Entry points only. What lies behind them is discovered at run time."""
    return {
        "market": [
            SourceSpec("suumo.jp", "portal", "https://suumo.jp/chintai/", nav=[region], purpose="rent market by area"),
            SourceSpec("www.oakhouse.jp", "operator", "https://www.oakhouse.jp/", nav=[f"{region}のシェアハウス"],
                       render="browser", purpose="share-house market by area"),
        ],
        "discovery": [
            SourceSpec("suumo.jp", "portal", "https://suumo.jp/chintai/", nav=[region, "{area}"], max_pages=2),
            SourceSpec("www.homes.co.jp", "portal", "https://www.homes.co.jp/chintai/", nav=[region, "{area}"], max_pages=2),
            SourceSpec("www.chintai.net", "portal", "https://www.chintai.net/", nav=[region, "{area}"], max_pages=1),
            SourceSpec("www.athome.co.jp", "portal", "https://www.athome.co.jp/chintai/", nav=[region, "{area}"], max_pages=1),
            SourceSpec("www.ur-net.go.jp", "operator", "https://www.ur-net.go.jp/chintai/", nav=[region, "{area}"], max_pages=1),
            SourceSpec("www.oakhouse.jp", "operator", "https://www.oakhouse.jp/", nav=[f"{region}のシェアハウス", "{area}"],
                       render="browser", max_pages=1),
            SourceSpec("www.monthly-mansion.com", "portal", "https://www.monthly-mansion.com/", nav=[region, "{area}"],
                       max_pages=1),
        ],
    }


NAV_HINTS = {
    "suumo.jp": {"market": ([["soba"]], []), "discovery": ([["/chintai/"], ["/sc_"]], ["soba", "ensen", "kensaku", "/jj/", "mansion/", "ekimae"])},
    "www.homes.co.jp": {"discovery": ([["/chintai/"], ["list"]], ["mansion", "kodate"])},
}


class HousingAdapter(DomainAdapter):
    name = "housing"
    tags = ("housing",)
    attributes = ATTRS
    time_sensitive = ("availability", "rent")
    network_jobs = HousingEnricher.network_jobs

    def __init__(self):
        self.extractor = HousingExtractor()
        self._resolver = HousingEntityResolver()
        self.enricher = HousingEnricher(self)

    def resolver(self):
        return self._resolver

    def extract(self, doc, purpose: str) -> list[Mention]:
        return self.extractor.extract(doc, purpose)

    def mention_features(self, m: Mention) -> dict[str, Any]:
        claims = {c.attribute: c.value for c in m.claims}
        if m.entity_type == "building":
            return building_features(m.key_fields, claims)
        if m.entity_type == "unit":
            f = dict(m.key_fields)
            f["housing_type"] = claims.get("housing_type") or "rent"
            return f
        return dict(m.key_fields)

    def job_spec(self, kind: str, entity_id: str, params: dict, reason: str, priority: float) -> EnrichmentJobSpec:
        return EnrichmentJobSpec(kind=kind, entity_id=entity_id, params=params, priority=priority, reason=reason)

    # ------------------------------------------------------------ attributes

    def spec(self, attr: str) -> AttrSpec | None:
        if attr.startswith("market_rent"):
            return MARKET
        return ATTRS.get(attr)

    def policy(self):  # market attributes are dynamic (one per layout column)
        from regent.acquisition.types import FreshnessPolicy

        class _Specs(dict):
            def get(self_inner, k, default=None):
                return self.spec(k) or default

        return FreshnessPolicy(_Specs(ATTRS))

    # ------------------------------------------------------- what's missing?

    def information_needs(self, mission, world, state: dict[str, Any]) -> list[dict[str, Any]]:
        params = self.params_from_world(mission, world)
        needs = []
        if not state.get("discovery_done") and not state.get("discovery_running"):
            needs.append({"action": "discover", "params": params, "priority": 3.0,
                          "reason": "no live housing options are known: cannot compare strategies without them"})
            return needs
        if state.get("shortlist_pending_enrichment"):
            needs.append({"action": "enrich", "params": {**params, "entity_ids": state["shortlist_pending_enrichment"]},
                          "priority": 2.0, "reason": "shortlisted candidates lack verification, location and "
                                                     "move-in facts needed to evaluate them"})
        if state.get("stale_shortlisted"):
            needs.append({"action": "recheck", "params": {**params, "entity_ids": state["stale_shortlisted"]},
                          "priority": 2.5, "reason": "time-sensitive claims (availability/rent) are past their TTL"})
        return needs

    def params_from_world(self, mission, world) -> dict[str, Any]:
        facts = {k: f.value for k, f in world.facts.items()} if hasattr(world, "facts") else {}
        attrs = (mission.attrs or {}) if hasattr(mission, "attrs") else {}
        p = {
            "region": facts.get("principal.region") or attrs.get("region") or REGION,
            "household": int(facts.get("principal.household_size") or attrs.get("household") or 1),
            "max_rent": facts.get("principal.max_rent") or attrs.get("max_rent"),
            "move_in_by": facts.get("principal.move_in_by") or attrs.get("move_in_by"),
            "areas": facts.get("principal.preferred_areas") or attrs.get("areas"),
            "work_location": facts.get("principal.work_location"),
        }
        p["assumptions"] = [
            {"key": k, "value": v, "why": why}
            for k, v, why in (
                ("region", p["region"], "principal.region unknown: using locale default" if not facts.get("principal.region") else None),
                ("household", p["household"], "household size unknown: assuming one person" if not facts.get("principal.household_size") else None),
                ("max_rent", p["max_rent"], "budget unknown: bounded by live market rents per area" if not p["max_rent"] else None),
                ("work_location", p["work_location"], "work location unknown: commute measured to major hubs" if not p["work_location"] else None),
            ) if why
        ]
        return p

    # -------------------------------------------------------------- discovery

    def run_discovery(self, engine, request: AcqRequest) -> None:
        params = request.params or {}
        region = params.get("region") or REGION
        srcs = sources(region)
        engine.request.plan = {"region": region, "assumptions": params.get("assumptions", []),
                               "stages": ["market", "choose_areas", "discovery", "funnel", "deep_research"]}
        # 1. market scan
        engine.log("market", f"scanning rent markets for {region}")
        for s in srcs["market"]:
            hints, avoid = NAV_HINTS.get(s.host, {}).get("market", (None, []))
            doc, trail = engine.navigate(s.entry_url, s.nav, kind=s.kind, render=s.render, hints=hints, avoid=avoid)
            if doc is not None and doc.ok:
                ents = engine.ingest(doc, self.extract(doc, "market"), source_kind=s.kind)
                engine.log("market", f"{s.host}: {len(ents)} market records", trail=trail)
            else:
                engine.log("market", f"{s.host}: unreachable ({_why(doc)})", trail=trail)
        engine.refresh_dirty()
        engine.checkpoint()
        # 2. choose areas from live market data
        areas, rationale = self.choose_areas(engine, params)
        engine.request.plan = {**engine.request.plan, "areas": areas, "area_rationale": rationale}
        engine.log("choose_areas", f"areas: {', '.join(areas)}", rationale=rationale)
        engine.checkpoint()
        # 3. broad discovery across sources x areas
        for area in areas:
            for s in srcs["discovery"]:
                if not engine.budget_left():
                    break
                terms = [t.replace("{area}", area) for t in s.nav]
                hints, avoid = NAV_HINTS.get(s.host, {}).get("discovery", (None, []))
                doc, trail = engine.navigate(s.entry_url, terms, kind=s.kind, render=s.render, hints=hints, avoid=avoid)
                pages = 0
                while doc is not None and doc.ok and pages < s.max_pages:
                    ms = self.extract(doc, "discovery")
                    ents = engine.ingest(doc, ms, source_kind=s.kind)
                    pages += 1
                    engine.log("discovery", f"{s.host} {area} p{pages}: {len(ms)} records", url=doc.final_url)
                    from regent.acquisition.navigate import next_page

                    nxt = next_page(doc.html, doc.final_url) if pages < s.max_pages else None
                    doc = engine.fetch(nxt, purpose="listing", kind=s.kind, render=s.render) if nxt else None
                if pages == 0:
                    engine.log("discovery", f"{s.host} {area}: no listing page ({_why(doc)})", trail=trail)
                engine.refresh_dirty()
                engine.checkpoint()

    def choose_areas(self, engine, params) -> tuple[list[str], str]:
        region = params.get("region") or REGION
        if params.get("areas"):
            return list(params["areas"]), "principal-specified areas"
        rows = []
        for e in engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name, AcqEntity.entity_type == "area")):
            b = e.beliefs or {}
            rent = (b.get("market_rent_1k_1dk") or b.get("market_rent_1r") or {}).get("value")
            name = (e.features or {}).get("area", "")
            if rent and name.startswith(region):
                rows.append((float(rent), name[len(region):] if name.startswith(region) else name))
        if not rows:
            return [], "no market data reachable: cannot choose areas"
        wards = [r for r in rows if r[1].endswith("区")]
        pool = wards if len(wards) >= 4 else rows
        policy = "wards (dense rail network) preferred" if pool is wards else "all municipalities"
        budget = params.get("max_rent")
        if budget:
            pool = [r for r in pool if r[0] <= float(budget)] or pool
        pool.sort()
        med = statistics.median(r[0] for r in pool)
        cheap = pool[:2]
        around = sorted(pool, key=lambda r: abs(r[0] - med))
        chosen = []
        for r in cheap + around:
            if r[1] not in chosen:
                chosen.append(r[1])
            if len(chosen) >= int(params.get("max_areas", 4)):
                break
        why = (f"{policy}; 1K/1DK market rents from live data; two most affordable "
               f"({', '.join(f'{a} {int(r):,}' for r, a in cheap)}) plus areas nearest the median ({int(med):,})"
               + (f"; within budget {int(budget):,}" if budget else "; budget unknown"))
        return chosen, why

    # ----------------------------------------------------------------- funnel

    def unit_view(self, engine, e: AcqEntity) -> dict[str, Any]:
        b = e.beliefs or {}
        bl = engine.db.get(AcqEntity, e.parent_id) if e.parent_id else None
        bb = (bl.beliefs if bl else {}) or {}

        def v(d, k):
            return (d.get(k) or {}).get("value")

        stations = v(bb, "stations") or []
        walk = min((s["walk_min"] for s in stations), default=None)
        rent, fee = v(b, "rent"), v(b, "management_fee") or 0
        area_name = None
        addr = v(bb, "address") or ""
        m = re.match(r"^(東京都|北海道|京都府|大阪府|[^\s]{2,3}県)?(.+?[区市])", addr)
        if m:
            area_name = m.group(2)
        return {
            "id": e.id, "label": e.label, "building_id": e.parent_id, "building": v(bb, "name") or addr or "?",
            "address": addr, "area_name": area_name, "housing_type": v(b, "housing_type") or "rent",
            "rent": rent, "fee": fee, "monthly": (rent or 0) + (fee or 0) if rent else None,
            "deposit": v(b, "deposit"), "key_money": v(b, "key_money"), "layout": v(b, "layout"),
            "area_m2": v(b, "area_m2"), "floor": v(b, "floor"), "walk_min": walk,
            "station": min(stations, key=lambda s: s["walk_min"])["station"] if stations else None,
            "built_year": v(bb, "built_year"),
            "availability": v(b, "availability"), "availability_conf": (b.get("availability") or {}).get("confidence", 0),
            "rent_conf": (b.get("rent") or {}).get("confidence", 0),
            "fresh": all((b.get(a) or {}).get("fresh", True) for a in self.time_sensitive if a in b),
            "conflicts": [a for a, x in b.items() if x.get("conflict")] + [f"building.{a}" for a, x in bb.items() if x.get("conflict")],
            "n_sources": len(e.source_hosts or []), "sources": e.source_hosts or [],
            "move_in": v(b, "move_in"), "earliest_move_in": v(b, "earliest_move_in_est"),
            "coords": v(bb, "coords"), "rail_distance_m": v(bb, "rail_distance_m"),
            "hub_minutes": v(bb, "hub_minutes_est"), "libraries": v(bb, "libraries_nearby"),
            "universities": v(bb, "universities_nearby"), "nearest_public": v(bb, "nearest_stations_public"),
            "internet": v(b, "internet"), "info_updated_at": v(b, "info_updated_at"),
        }

    def funnel(self, engine, request: AcqRequest) -> dict[str, Any]:
        params = request.params or {}
        units = list(engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name,
                                                               AcqEntity.entity_type == "unit",
                                                               AcqEntity.status == "active")))
        mentions = engine.db.scalar(select(__import__("sqlalchemy").func.count(AcqMention.id))
                                    .where(AcqMention.entity_type == "unit")) or 0
        market = {}
        for a in engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name, AcqEntity.entity_type == "area")):
            b = a.beliefs or {}
            name = (a.features or {}).get("area", "")
            r = (b.get("market_rent_1k_1dk") or b.get("market_rent_1r") or {}).get("value")
            if r:
                market[name] = float(r)
        allowed = SINGLE_LAYOUTS if int(params.get("household", 1)) <= 1 else None
        region = params.get("region") or REGION
        views, passed = [], []
        for e in units:
            u = self.unit_view(engine, e)
            reasons = []
            if u["rent"] is None:
                reasons.append("no rent claim")
            if u["availability"] is False:
                reasons.append("not available")
            elif u["availability_conf"] < 0.4:
                reasons.append(f"availability unconfirmed ({u['availability_conf']:.2f})")
            if u["housing_type"] == "rent":
                if allowed and u["layout"] and u["layout"] not in allowed:
                    reasons.append(f"layout {u['layout']} larger than needed for household={params.get('household', 1)}")
                if u["area_m2"] and u["area_m2"] < 13:
                    reasons.append(f"{u['area_m2']} m2 below 13 m2")
            budget = params.get("max_rent")
            ref = market.get(f"{region}{u['area_name']}") if u["area_name"] else None
            cap = float(budget) if budget else ((ref or statistics.median(market.values())) * 1.15 if market else None)
            if cap and u["monthly"] and u["monthly"] > cap * (1.0 if budget else 1.1):
                reasons.append(f"monthly {int(u['monthly']):,} above {'budget' if budget else 'market-based cap'} {int(cap):,}")
            if u["walk_min"] is not None and u["walk_min"] > 20:
                reasons.append(f"{u['walk_min']} min walk to station")
            score, parts = self.score(u, cap)
            e.score = score
            e.score_detail = {"parts": parts, "rejections": reasons, "cap": cap, "view": {k: u[k] for k in (
                "rent", "monthly", "layout", "area_m2", "walk_min", "station", "availability_conf", "n_sources")}}
            e.stage = "rejected" if reasons else "discovered"
            views.append(u)
            if not reasons:
                passed.append(e)
        passed.sort(key=lambda e: -e.score)
        filtered, shortlisted, per_building = passed[:25], [], {}
        for e in filtered:
            e.stage = "filtered"
        for e in filtered:
            if per_building.get(e.parent_id, 0) >= 2:
                continue
            per_building[e.parent_id] = per_building.get(e.parent_id, 0) + 1
            e.stage = "shortlisted"
            shortlisted.append(e)
            if len(shortlisted) >= 8:
                break
        buildings = {e.parent_id for e in units if e.parent_id}
        stats = {"mentions": mentions, "units": len(units), "buildings": len(buildings),
                 "passed_filters": len(passed), "filtered": len(filtered), "shortlisted": len(shortlisted),
                 "rejection_reasons": _top_reasons(units), "market_areas": len(market)}
        engine.request.stats = {**(engine.request.stats or {}), "funnel": stats}
        engine.log("funnel", f"{mentions} mentions -> {len(units)} units -> {len(passed)} pass -> "
                             f"{len(filtered)} filtered -> {len(shortlisted)} shortlisted")
        return stats

    @staticmethod
    def score(u: dict[str, Any], cap: float | None) -> tuple[float, dict[str, float]]:
        parts = {}
        if u["monthly"] and cap:
            parts["cost"] = 0.35 * max(0.0, min(1.0, 1 - u["monthly"] / (cap * 1.1)))
        parts["walk"] = 0.15 * (max(0.0, 1 - (u["walk_min"] or 12) / 20))
        parts["size"] = 0.12 * min((u["area_m2"] or 15) / 30, 1.0)
        if u["built_year"]:
            parts["age"] = 0.05 * max(0.0, 1 - (utcnow().year - int(u["built_year"])) / 40)
        parts["evidence"] = 0.15 * (u["availability_conf"] + u["rent_conf"]) / 2
        parts["corroboration"] = 0.10 * min(u["n_sources"], 3) / 3
        if u["rent"] and (u["deposit"] is not None or u["key_money"] is not None):
            upfront = (u["deposit"] or 0) + (u["key_money"] or 0)
            parts["upfront"] = -0.08 * min(upfront / (u["rent"] * 4), 1.0)
        if u["conflicts"]:
            parts["conflicts"] = -0.03 * len(u["conflicts"])
        return round(sum(parts.values()), 4), {k: round(v, 4) for k, v in parts.items()}

    # ------------------------------------------------------------ enrichment

    def enrichment_jobs(self, engine, entity: AcqEntity) -> list[EnrichmentJobSpec]:
        jobs: list[EnrichmentJobSpec] = []
        urls: dict[str, str] = {}
        for m in engine.db.scalars(select(AcqMention).where(AcqMention.entity_id == entity.id)):
            u = (m.links or {}).get("detail_url")
            if u and m.host not in urls and m.url != u:
                urls[m.host] = u
        for host, u in list(urls.items())[:2]:
            jobs.append(self.job_spec("detail", entity.id, {"url": u, "kind": "portal"},
                                      f"deep research: detail page on {host}", 1.0))
        if entity.parent_id:
            b = entity.parent_id
            jobs += [self.job_spec("geocode", b, {}, "locate the building (GSI)", 0.95),
                     self.job_spec("stations", b, {}, "nearest stations from public railway data", 0.9),
                     self.job_spec("rail_noise", b, {}, "train-noise proxy: distance to rail line", 0.85),
                     self.job_spec("facilities", b, {}, "libraries and universities nearby (public data)", 0.8),
                     self.job_spec("hubs", b, {}, "commute estimate to major hubs (work location unknown)", 0.75)]
        jobs.append(self.job_spec("move_in", entity.id, {}, "is the move-in date actually feasible?", 0.5))
        return jobs

    def run_job(self, engine, job: AcqJob) -> dict[str, Any]:
        return self.enricher.run(engine, job)

    def recheck_jobs(self, engine, entity: AcqEntity) -> list[EnrichmentJobSpec]:
        urls = {}
        for m in engine.db.scalars(select(AcqMention).where(AcqMention.entity_id == entity.id)
                                   .order_by(AcqMention.observed_at.desc())):
            if m.host not in urls:
                urls[m.host] = ((m.links or {}).get("detail_url"), m.url)
        out = []
        for host, (detail, page) in list(urls.items())[:2]:
            out.append(self.job_spec("recheck", entity.id, {"url": detail or page, "detail": bool(detail), "force": True},
                                     f"TTL expired: re-observe on {host}", 2.0))
        return out

    # ---------------------------------------------------------------- project

    def project(self, engine, request: AcqRequest) -> list[dict[str, Any]]:
        evs: list[dict[str, Any]] = []
        stats = request.stats or {}
        units = list(engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name,
                                                               AcqEntity.entity_type == "unit",
                                                               AcqEntity.stage.in_(("filtered", "shortlisted", "deep")))))
        seen_b = set()
        views = []
        for e in units:
            u = self.unit_view(engine, e)
            views.append((e, u))
            evs.append({"type": "entity_upserted", "id": e.id, "kind": "unit", "name": f"{u['building']} {e.label}",
                        "merge": False, "attrs": {
                            "housing_type": u["housing_type"], "stage": e.stage, "score": e.score,
                            "rent": u["rent"], "monthly": u["monthly"], "deposit": u["deposit"],
                            "key_money": u["key_money"], "layout": u["layout"], "area_m2": u["area_m2"],
                            "walk_min": u["walk_min"], "station": u["station"], "address": u["address"],
                            "availability": u["availability"], "availability_confidence": u["availability_conf"],
                            "rent_confidence": u["rent_conf"], "fresh": u["fresh"], "conflicts": u["conflicts"],
                            "sources": u["sources"], "move_in": u["move_in"], "earliest_move_in": u["earliest_move_in"],
                            "rail_distance_m": u["rail_distance_m"], "hub_minutes": u["hub_minutes"],
                            "libraries": u["libraries"], "universities": u["universities"], "internet": u["internet"],
                            "acq_entity": e.id},
                        "relations": [{"rel": "located_at", "dst": e.parent_id}] if e.parent_id else []})
            if e.parent_id and e.parent_id not in seen_b:
                seen_b.add(e.parent_id)
                evs.append({"type": "entity_upserted", "id": e.parent_id, "kind": "building", "name": u["building"],
                            "attrs": {"address": u["address"], "coords": u["coords"], "built_year": u["built_year"]}})
        # market facts
        share, market = [], {}
        for a in engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name, AcqEntity.entity_type == "area")):
            b = a.beliefs or {}
            name = (a.features or {}).get("area", "")
            if (b.get("market_rent_share_house") or {}).get("value"):
                share.append(float(b["market_rent_share_house"]["value"]))
            r = (b.get("market_rent_1k_1dk") or {}).get("value")
            if r:
                market[name] = r
        offers = {"share_house": [], "monthly": []}
        for e, u in views:
            if u["housing_type"] in offers and u["monthly"]:
                offers[u["housing_type"]].append(u["monthly"])
        all_units = list(engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name,
                                                                   AcqEntity.entity_type == "unit")))
        for t in ("share_house", "monthly"):
            offers[t] += [self.unit_view(engine, e)["monthly"] for e in all_units
                          if (e.beliefs or {}).get("housing_type", {}).get("value") == t and e.stage == "rejected"
                          and self.unit_view(engine, e)["monthly"]]
        facts = [
            {"key": "housing.region", "value": (request.params or {}).get("region")},
            {"key": "housing.assumptions", "value": (request.params or {}).get("assumptions", [])},
            {"key": "housing.areas_searched", "value": (request.plan or {}).get("areas", [])},
            {"key": "housing.funnel", "value": stats.get("funnel", {})},
            {"key": "housing.sources", "value": {h: {k: v for k, v in s.items() if k in ("pages", "ok", "blocked", "mentions", "last")}
                                                 for h, s in (stats.get("by_host") or {}).items()}},
            {"key": "housing.market.rent_1k", "value": market},
            {"key": "housing.share_house.median_rent", "value": statistics.median(share) if share else None},
            {"key": "housing.share_house.offers", "value": len(offers["share_house"])},
            {"key": "housing.monthly.offers", "value": len(offers["monthly"])},
            {"key": "housing.monthly.median", "value": statistics.median(offers["monthly"]) if offers["monthly"] else None},
            {"key": "housing.shortlist.count", "value": sum(1 for e, _ in views if e.stage in ("shortlisted", "deep"))},
            {"key": "housing.shortlist.available_count",
             "value": sum(1 for e, u in views if e.stage in ("shortlisted", "deep") and u["availability"]
                          and u["availability_conf"] >= 0.5 and u["fresh"])},
            {"key": "housing.conflicts_open", "value": sum(len(u["conflicts"]) for _, u in views)},
            {"key": "housing.acquisition_request", "value": request.id},
        ]
        evs.append({"type": "fact_observed", "facts": [{**f, "confidence": 0.9} for f in facts]})
        return evs

    # -------------------------------------------------------------- strategies

    def strategies(self, mission: dict[str, Any], world: dict[str, Any]) -> list[RouteProposal]:
        return housing_strategies(mission, world)


def _why(doc) -> str:
    if doc is None:
        return "navigation failed"
    if doc.blocked:
        return f"blocked: {doc.blocked.get('type')} {doc.blocked.get('detail', '')}".strip()
    if doc.error:
        return doc.error[:80]
    return f"HTTP {doc.status}"


def _top_reasons(units: list[AcqEntity]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in units:
        for r in (e.score_detail or {}).get("rejections", []):
            k = re.sub(r"[\d,.]+", "N", r)
            out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1])[:8])


# ------------------------------------------------------------------ strategies

def housing_strategies(mission: dict[str, Any], world: dict[str, Any]) -> list[RouteProposal]:
    """Competing *strategies*, not a property ranking: should the principal sign a lease
    at all, and if so which candidates carry the plan? Estimates come from acquired
    evidence (facts + unit entities); where no evidence exists the route says so."""
    facts = world.get("facts", {})
    units = [e for e in world.get("entities", []) if e["kind"] == "unit" and e["attrs"].get("stage") in ("shortlisted", "deep")]
    rent_units = sorted([e for e in units if e["attrs"].get("housing_type", "rent") == "rent"],
                        key=lambda e: -(e["attrs"].get("score") or 0))
    routes: list[RouteProposal] = []
    unc = [Uncertainty(question="Where will the principal work?", fact_key="principal.work_location",
                       affects=["success_probability", "optionality"]),
           Uncertainty(question="What monthly budget is acceptable?", fact_key="principal.max_rent",
                       affects=["success_probability"])]
    # A. lease
    if rent_units:
        top = rent_units[:3]
        monthly = statistics.median(e["attrs"]["monthly"] for e in top)
        initial = statistics.median(
            (e["attrs"].get("deposit") or 0) + (e["attrs"].get("key_money") or 0) + 1.5 * e["attrs"]["rent"] + e["attrs"]["monthly"]
            for e in top)
        p_avail = 1.0
        for e in top:
            p_avail *= 1 - (e["attrs"].get("availability_confidence") or 0.5) * 0.75
        p = round(1 - p_avail, 3)
        stale = any(not e["attrs"].get("fresh", True) for e in top)
        conflicts = sum(len(e["attrs"].get("conflicts") or []) for e in top)
        best = top[0]
        routes.append(RouteProposal(
            key="housing-lease", archetype="housing_lease", tags=["long_term_stability", "commitment"],
            title=f"Lease now: {best['name']}" + (f" (+{len(top) - 1} backups)" if len(top) > 1 else ""),
            thesis=(f"Sign a standard lease. Best evidenced candidate: {best['name']} at {int(best['attrs']['monthly']):,}/month "
                    f"({best['attrs'].get('station') or '?'} {best['attrs'].get('walk_min') or '?'} min). "
                    f"Median of top {len(top)}: {int(monthly):,}/month, ~{int(initial):,} upfront."),
            estimates=RouteEstimates(expected_upside=24, success_probability=p, time_cost_hours=20,
                                     money_cost=initial, information_gain=0.25, reversibility=0.25, optionality=0.3,
                                     risk=min(0.9, 0.25 + (0.1 if stale else 0) + 0.05 * conflicts), authority_cost=0.5),
            estimate_rationale={"expected_upside": "months of stable housing a 2-year lease secures (capped at 24)",
                                "success_probability": "1 - prod(1 - availability confidence x 0.75 screening) over top candidates",
                                "money_cost": "median of deposit + key money + ~1.5 months fees/guarantor + first month"},
            sensitivities=[
                Sensitivity(fact="housing.shortlist.available_count", op="lt", value=1,
                            effects={"success_probability": Effect(mul=0.3)}, rationale="no fresh available candidate"),
                Sensitivity(fact="principal.max_rent", op="lt", value=monthly,
                            effects={"success_probability": Effect(mul=0.4)}, rationale="candidates exceed stated budget"),
            ],
            uncertainty=unc,
            operations=[
                OperationSpec(key="lease.recheck", tool="acquire", action="recheck", kind="probe",
                              goal="Re-verify availability and rent of the top candidates at their sources",
                              inputs={"entity_ids": [e["attrs"].get("acq_entity") for e in top], "force": True},
                              resolves=["housing.shortlist.available_count"],
                              verification=VerificationSpec(method="schema", required_keys=["request_id"]),
                              timeout_s=600, cost_estimate=CostEstimate(minutes=3)),
                OperationSpec(key="lease.brief", tool="fs", action="write", depends_on=["lease.recheck"],
                              goal="Write a viewing brief with every claim's source and freshness",
                              inputs={"path": f"housing/{mission['id']}/viewing-brief.md",
                                      "content": "{{ops.lease.recheck.outputs.brief_markdown}}"},
                              verification=VerificationSpec(method="schema", required_keys=["bytes"])),
                OperationSpec(key="lease.contact", tool="human", action="perform", depends_on=["lease.brief"],
                              goal=f"Request a viewing of {best['name']}",
                              inputs={"required_action": f"Request a viewing for {best['name']} ({best['attrs'].get('address')}) "
                                                         "using the listing's inquiry form (needs your name and phone)",
                                      "reason": "inquiry forms require the principal's identity and contact details",
                                      "estimated_time_seconds": 180, "kind": "identity",
                                      "context": {"entity": best["id"], "sources": best["attrs"].get("sources")},
                                      "response_schema": {"done": {"type": "boolean"}}},
                              verification=VerificationSpec(method="human_confirmed")),
            ]))
    # B. monthly
    mm = facts.get("housing.monthly.median")
    routes.append(RouteProposal(
        key="housing-monthly", archetype="housing_monthly", tags=["flexible", "short_term"],
        title="Monthly apartment while deciding",
        thesis=("Furnished monthly apartment: fast, reversible, expensive per month. "
                + (f"Live offers median {int(mm):,}/month." if mm else "No live monthly offer was acquired yet; estimate is a prior.")),
        estimates=RouteEstimates(expected_upside=3, success_probability=0.85 if mm else 0.6, time_cost_hours=6,
                                 money_cost=(mm or 150000) + 30000, information_gain=0.2 if mm else 0.5,
                                 reversibility=0.85, optionality=0.8, risk=0.3 if mm else 0.5, authority_cost=0.2),
        estimate_rationale={"money_cost": "one month + cleaning fee" + ("" if mm else " (prior: no live evidence)")},
        uncertainty=unc))
    # C. share house
    sh = facts.get("housing.share_house.median_rent")
    routes.append(RouteProposal(
        key="housing-share", archetype="housing_share", tags=["low_upfront", "flexible"],
        title="Share house room",
        thesis=("Private room in a managed share house: low upfront cost, furnished, month-to-month. "
                + (f"Operator's live market median {int(sh):,}/month." if sh else "No live share-house data acquired.")),
        estimates=RouteEstimates(expected_upside=9, success_probability=0.8 if sh else 0.55, time_cost_hours=8,
                                 money_cost=(sh or 60000) + 30000, information_gain=0.2, reversibility=0.75,
                                 optionality=0.7, risk=0.3, authority_cost=0.3),
        estimate_rationale={"expected_upside": "months of reasonably stable housing (shared living discounted)",
                            "money_cost": "first month + typical contract fee"},
        uncertainty=unc))
    # D. hotel / hostel -- no live price source in this run
    routes.append(RouteProposal(
        key="housing-hostel", archetype="housing_hostel", tags=["no_commitment"],
        title="Stay in hotels/hostels",
        thesis="Keep lodging day by day. No live price evidence was acquired; the estimate is a labelled prior.",
        estimates=RouteEstimates(expected_upside=0.5, success_probability=0.95, time_cost_hours=4,
                                 money_cost=(sh or 60000) * 1.4, information_gain=0.05, reversibility=1.0,
                                 optionality=0.95, risk=0.55, authority_cost=0.1),
        estimate_rationale={"money_cost": "prior: 1.4 x share-house median per month (no live hostel evidence)"}))
    # E. existing base -- only when the world knows one
    home = next((e for e in world.get("entities", []) if e["kind"] == "place" and e["attrs"].get("is_home")), None)
    if home is not None:
        routes.append(RouteProposal(
            key="housing-existing", archetype="housing_existing", tags=["no_commitment"],
            title=f"Stay at {home['name']}", thesis="Use the existing base while other uncertainties resolve.",
            estimates=RouteEstimates(expected_upside=2, success_probability=0.9, time_cost_hours=1, money_cost=0,
                                     reversibility=1.0, optionality=0.9, risk=0.3, information_gain=0.1)))
    # F. do not fix yet
    routes.append(RouteProposal(
        key="housing-defer", archetype="housing_defer", tags=["defensive"],
        title="Don't commit until the work location is known",
        thesis="Commute is unknown, so any lease may be in the wrong place. Keep options open; revisit when work is settled.",
        estimates=RouteEstimates(expected_upside=0.5, success_probability=1.0, time_cost_hours=0.5, money_cost=0,
                                 information_gain=0.4, reversibility=1.0, optionality=1.0, risk=0.45, authority_cost=0.0),
        sensitivities=[Sensitivity(fact="principal.work_location", op="exists",
                                   effects={"information_gain": Effect(set=0.05), "expected_upside": Effect(set=0.2)},
                                   rationale="work location now known: waiting no longer buys information")],
        uncertainty=unc))
    return routes
