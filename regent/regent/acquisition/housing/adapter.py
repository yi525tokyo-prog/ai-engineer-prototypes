"""HousingAdapter: housing as a World Acquisition domain, for any geography.

The adapter is orchestration only. It does not know any country:

* the :class:`GeographyResolver` decides *where* to look (regions compete; the
  principal's current country is evidence, not a default);
* each region is acquired by a :mod:`source pack <regent.acquisition.housing.packs>`
  -- the Japan pack, or the generic pack that works anywhere from global sources plus
  sources it discovers and verifies on the web;
* one funnel per region ranks candidates against that region's own live market;
* strategies compete *across* regions (lease here, lease abroad, furnished/room,
  hostel, defer), with money in one reference currency and the right to stay as an
  explicit uncertainty.

Nothing lists candidate properties; everything comes from pages fetched at run time.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from sqlalchemy import func, select

from regent.acquisition.domain import DomainAdapter
from regent.acquisition.housing import ontology as O
from regent.acquisition.housing.geography import GeographyResolver, language_of
from regent.acquisition.housing.packs.base import SourcePack
from regent.acquisition.housing.packs.generic import GenericPack
from regent.acquisition.housing.packs.japan import JP_HOSTS, JapanPack
from regent.acquisition.housing.resolver import HousingResolver, building_features
from regent.acquisition.tables import AcqEntity, AcqJob, AcqMention, AcqRequest, ENTITY_ORDER
from regent.acquisition.types import EnrichmentJobSpec, FreshnessPolicy, Mention
from regent.ids import utcnow
from regent.schemas import (CostEstimate, Effect, OperationSpec, RouteEstimates, RouteProposal, Sensitivity,
                            Uncertainty, VerificationSpec)

ATTRS = O.ATTRS
SHORTLIST_PER_REGION = 3
SMALL_KINDS = ("studio", "room", "apartment")


class HousingAdapter(DomainAdapter):
    name = "housing"
    tags = ("housing",)
    keywords = ("住居", "住まい", "住む", "部屋探し", "賃貸", "引っ越", "引越", "物件", "アパート", "マンション",
                "housing", "apartment", "place to live", "rent a flat", "somewhere to live", "wohnung", "vivienda")
    attributes = ATTRS
    time_sensitive = ("availability", "rent")
    network_jobs = SourcePack.network_jobs

    def __init__(self):
        self._resolver = HousingResolver()
        self._packs: dict[str, SourcePack] = {}
        self.geography = GeographyResolver(self)
        self.active_pack: SourcePack | None = None
        self.active_region: dict[str, Any] | None = None

    # -------------------------------------------------------------- packs

    def pack_for(self, country: str) -> SourcePack:
        cc = (country or "").upper()
        if cc not in self._packs:
            self._packs[cc] = JapanPack(self) if cc == "JP" else GenericPack(self, cc)
        return self._packs[cc]

    def _pack_for_doc(self, doc) -> SourcePack:
        if doc.host in JP_HOSTS:
            return self.pack_for("JP")
        if self.active_pack is not None:
            return self.active_pack
        return self.pack_for("US")

    def resolver(self):
        return self._resolver

    def extract(self, doc, purpose: str) -> list[Mention]:
        pack = self._pack_for_doc(doc)
        return pack.extractor(self.active_region).extract(doc, purpose)

    def mention_features(self, m: Mention) -> dict[str, Any]:
        claims = {c.attribute: c.value for c in m.claims}
        kf = dict(m.key_fields)
        if "listing_url" in kf or kf.get("country"):          # generic-pack records
            if m.entity_type == "unit":
                for k in ("management_fee", "deposit"):
                    if claims.get(k) is not None:
                        kf[k] = claims[k]
                if kf.get("bedrooms") is None and claims.get("bedrooms") is not None:
                    kf["bedrooms"] = claims["bedrooms"]
            return kf
        if m.entity_type == "building":
            return building_features(m.key_fields, claims)
        if m.entity_type == "unit":
            kf["housing_type"] = claims.get("housing_type") or "rent"
            for k in ("management_fee", "deposit", "key_money"):   # listing terms: same-site identity evidence
                if claims.get(k) is not None:
                    kf[k] = claims[k]
            return kf
        return kf

    def job_spec(self, kind: str, entity_id: str, params: dict, reason: str, priority: float) -> EnrichmentJobSpec:
        return EnrichmentJobSpec(kind=kind, entity_id=entity_id, params=params, priority=priority, reason=reason)

    def spec(self, attr: str):
        return O.spec(attr)

    def policy(self) -> FreshnessPolicy:
        class _Specs(dict):
            def get(self_inner, k, default=None):
                return O.spec(k) or default

        return FreshnessPolicy(_Specs(ATTRS))

    # ------------------------------------------------------- what's missing?

    def information_needs(self, mission, world, state: dict[str, Any]) -> list[dict[str, Any]]:
        params = self.params_from_world(mission, world)
        needs = []
        if not state.get("discovery_done") and not state.get("discovery_running"):
            needs.append({"action": "discover", "params": params, "priority": 3.0, "blocking": True,
                          "reason": "no live housing options are known anywhere: cannot compare strategies"})
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
        text = f"{getattr(mission, 'title', '')} {getattr(mission, 'objective', '')}"

        def fact(*keys):
            return next((facts[k] for k in keys if facts.get(k) not in (None, "")), None) or \
                next((attrs[k.split(".")[-1]] for k in keys if attrs.get(k.split(".")[-1]) not in (None, "")), None)

        p = {
            "mission_text": text.strip(),
            "country": fact("principal.country", "principal.current_country"),
            "citizenship": fact("principal.citizenship"),
            "stay_in_country": fact("principal.stay_in_country"),
            "regions": fact("principal.preferred_regions", "principal.regions"),
            "languages": fact("principal.languages") or ([language_of(text)] if language_of(text) else []),
            "household": int(fact("principal.household_size", "principal.household") or 1),
            "max_rent": fact("principal.max_rent"),
            "move_in_by": fact("principal.move_in_by"),
            "work_location": fact("principal.work_location"),
        }
        p["assumptions"] = [
            {"key": k, "value": v, "why": why} for k, v, why in (
                ("geography", None, "no place stated: regions compete, current country is only evidence"
                 if not p["regions"] and not p["stay_in_country"] else None),
                ("citizenship", None, "citizenship unknown: the right to live abroad is an uncertainty"
                 if not p["citizenship"] else None),
                ("household", p["household"], "household size unknown: assuming one person"
                 if not facts.get("principal.household_size") else None),
                ("max_rent", None, "budget unknown: bounded by each region's live market" if not p["max_rent"] else None),
                ("work_location", None, "work location unknown" if not p["work_location"] else None),
            ) if why]
        return p

    # -------------------------------------------------------------- discovery

    def run_discovery(self, engine, request: AcqRequest) -> None:
        params = request.params or {}
        engine.request.plan = {"assumptions": params.get("assumptions", []),
                               "stages": ["geography", "sources", "discovery", "funnel", "deep_research"]}
        packs = {cc: self.pack_for(cc) for cc in ([params["country"]] if params.get("country") else
                                                   [c for c in ("JP",) if "ja" in (params.get("languages") or [])])}
        regions = self.geography.resolve(engine, params, packs, self.pack_for)
        if not regions:
            engine.log("geography", "no region could be evaluated with live data")
            return
        done = []
        for i, region in enumerate(regions):
            left = len(regions) - i
            share = max(8, (engine.max_pages - engine.stats["pages"]) // max(left, 1))
            stop_at = engine.stats["pages"] + share
            pack = self.pack_for(region["country"])
            self.active_pack, self.active_region = pack, region
            region.setdefault("max_areas", 1 if len(regions) > 2 else (2 if len(regions) > 1 else
                                                                        int(params.get("max_areas", 4))))
            region.setdefault("compact", len(regions) > 2)
            engine.log("region", f"acquiring {region['name']} ({region['country']}) with {pack.name}, "
                                 f"~{share} pages")
            saved = engine.max_pages
            engine.max_pages = min(saved, stop_at)
            try:
                summary = pack.discover(engine, region)
            except Exception as e:     # one region failing never sinks the others
                summary = {"error": f"{type(e).__name__}: {e}"[:300]}
                engine.log("region", f"{region['name']}: {summary['error']}")
            finally:
                engine.max_pages = saved
            n = engine.db.scalar(select(func.count()).select_from(AcqEntity).where(
                AcqEntity.region_id == region["id"], AcqEntity.entity_type == "unit")) or 0
            done.append({**{k: region.get(k) for k in ("id", "name", "country", "utility", "price_ref", "stay_p")},
                         "units": n, "summary": _brief_summary(summary)})
            engine.request.plan = {**engine.request.plan, "regions_acquired": list(done)}   # new object: JSON change
            engine.refresh_dirty()
            engine.checkpoint()
        self.active_pack = self.active_region = None

    # ----------------------------------------------------------------- funnel

    def region_info(self, engine, region_id: str | None) -> dict[str, Any]:
        reg = engine.db.get(AcqEntity, region_id) if region_id else None
        if reg is None:
            return {"id": None, "name": None, "country": "JP", "fx_per_ref": None, "coords": None}
        b = reg.beliefs or {}

        def v(k):
            return (b.get(k) or {}).get("value")

        return {"id": reg.id, "name": (reg.features or {}).get("name"), "country": v("country"),
                "fx_per_ref": v("fx_per_ref"), "coords": v("region_coords"), "stay": v("stay_rules") or {},
                "currency": v("region_currency"), "label": reg.label}

    def unit_view(self, engine, e: AcqEntity, regions: dict | None = None) -> dict[str, Any]:
        b = e.beliefs or {}
        bl = engine.db.get(AcqEntity, e.parent_id) if e.parent_id else None
        bb = (bl.beliefs if bl else {}) or {}

        def v(d, k):
            return (d.get(k) or {}).get("value")

        reg = (regions or {}).get(e.region_id) or self.region_info(engine, e.region_id)
        pack = self.pack_for(reg["country"] or "JP")
        extras = pack.view_extras(b, bb)
        rent, fee = v(b, "rent"), v(b, "management_fee") or 0
        monthly = (rent or 0) + (fee or 0) if rent else None
        cur = v(b, "currency") or reg.get("currency") or pack.currency
        fx = reg.get("fx_per_ref") if cur == reg.get("currency") else None
        kind = v(b, "unit_kind") or ("room" if v(b, "housing_type") == "share_house" else
                                     "furnished" if v(b, "housing_type") == "monthly" else "apartment")
        bedrooms = v(b, "bedrooms") if v(b, "bedrooms") is not None else extras.get("bedrooms")
        addr = v(bb, "address") or v(bb, "locality") or ""
        return {
            "id": e.id, "label": e.label, "building_id": e.parent_id,
            "building": v(bb, "name") or addr or v(b, "title") or "", "address": addr,
            "region_id": e.region_id, "region": reg.get("name"), "country": reg.get("country"),
            "housing_type": v(b, "housing_type") or ("share_house" if kind == "room" else "rent"), "kind": kind,
            "rent": rent, "fee": fee, "monthly": monthly, "currency": cur,
            "monthly_ref": round(monthly / fx, 2) if monthly and fx else None,
            "deposit": v(b, "deposit"), "key_money": v(b, "key_money"), "layout": v(b, "layout"),
            "bedrooms": bedrooms, "area_m2": v(b, "area_m2"), "floor": v(b, "floor"),
            "walk_min": extras.get("walk_min"), "station": extras.get("station"), "single_ok": extras.get("single_ok"),
            "centre_km": v(bb, "centre_km"), "built_year": v(bb, "built_year"),
            "availability": v(b, "availability"), "availability_conf": (b.get("availability") or {}).get("confidence", 0),
            "rent_conf": (b.get("rent") or {}).get("confidence", 0),
            "fresh": all((b.get(a) or {}).get("fresh", True) for a in self.time_sensitive if a in b),
            "conflicts": [a for a, x in b.items() if x.get("conflict")] + [f"building.{a}" for a, x in bb.items()
                                                                           if x.get("conflict")],
            "n_sources": len(e.source_hosts or []), "sources": e.source_hosts or [],
            "move_in": v(b, "move_in"), "earliest_move_in": v(b, "earliest_move_in_est"),
            "coords": v(bb, "coords"), "rail_distance_m": v(bb, "rail_distance_m"),
            "hub_minutes": v(bb, "hub_minutes_est"), "libraries": v(bb, "libraries_nearby"),
            "universities": v(bb, "universities_nearby"), "internet": v(b, "internet"),
            "info_updated_at": v(b, "info_updated_at"), "title": v(b, "title"),
        }

    def funnel(self, engine, request: AcqRequest) -> dict[str, Any]:
        params = request.params or {}
        units = list(engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name,
                                                               AcqEntity.entity_type == "unit",
                                                               AcqEntity.status == "active").order_by(*ENTITY_ORDER)))
        mentions = engine.db.scalar(select(func.count(AcqMention.id)).where(AcqMention.entity_type == "unit")) or 0
        regions = {rid: self.region_info(engine, rid) for rid in {e.region_id for e in units}}
        views = {e.id: self.unit_view(engine, e, regions) for e in units}
        market: dict[Any, float] = {}
        for rid in regions:
            vals = [u["monthly"] for u in views.values() if u["region_id"] == rid and u["monthly"]
                    and u["kind"] not in ("hostel_bed",)]
            if vals:
                market[rid] = statistics.median(vals)
        household = int(params.get("household", 1))
        passed_by_region: dict[Any, list[AcqEntity]] = {}
        for e in units:
            u = views[e.id]
            reasons = []
            if not e.region_id:
                reasons.append("not part of any acquired region")
            if u["rent"] is None:
                reasons.append("no rent claim")
            if u["availability"] is False:
                reasons.append("not available")
            elif u["availability_conf"] < 0.4:
                reasons.append(f"availability unconfirmed ({u['availability_conf']:.2f})")
            if u["kind"] == "hostel_bed":
                reasons.append("hostel bed: evidence for the no-commitment strategy, not a home")
            if household <= 1:
                if u["single_ok"] is False:
                    reasons.append(f"layout {u['layout']} larger than needed for household=1")
                elif u["single_ok"] is None and u["bedrooms"] is not None and u["bedrooms"] > 1:
                    reasons.append(f"{u['bedrooms']} bedrooms: larger than needed for household=1")
            if u["area_m2"] and u["area_m2"] < 13 and u["kind"] not in ("room",):
                reasons.append(f"{u['area_m2']} m2 below 13 m2")
            budget = params.get("max_rent")
            cap = float(budget) if budget else (market.get(u["region_id"]) * 1.15 if market.get(u["region_id"]) else None)
            if cap and u["monthly"] and u["monthly"] > cap * (1.0 if budget else 1.1):
                reasons.append(f"monthly {int(u['monthly']):,} above {'budget' if budget else 'regional market cap'} "
                               f"{int(cap):,}")
            if u["walk_min"] is not None and u["walk_min"] > 20:
                reasons.append(f"{u['walk_min']} min walk to station")
            score, parts = self.score(u, cap)
            e.score = score
            e.score_detail = {"parts": parts, "rejections": reasons, "cap": cap, "view": {k: u[k] for k in (
                "rent", "monthly", "currency", "monthly_ref", "kind", "bedrooms", "layout", "area_m2", "walk_min",
                "centre_km", "availability_conf", "n_sources", "region", "country")}}
            e.stage = "rejected" if reasons else "discovered"
            if not reasons:
                passed_by_region.setdefault(e.region_id, []).append(e)
        filtered_n, shortlisted, per_region_stats = 0, [], {}
        for rid, passed in passed_by_region.items():
            passed.sort(key=lambda e: (-e.score, e.created_at))      # ties: creation order, never row order
            k_filter = 25 if len(regions) <= 1 else 10
            k_short = 8 if len(regions) <= 1 else SHORTLIST_PER_REGION
            flt = passed[:k_filter]
            for e in flt:
                e.stage = "filtered"
            per_building: dict[Any, int] = {}
            sl = []
            for e in flt:
                if e.parent_id and per_building.get(e.parent_id, 0) >= 2:
                    continue
                per_building[e.parent_id] = per_building.get(e.parent_id, 0) + 1
                e.stage = "shortlisted"
                sl.append(e)
                if len(sl) >= k_short:
                    break
            filtered_n += len(flt)
            shortlisted += sl
            name = regions.get(rid, {}).get("label") or "unassigned"
            per_region_stats[name] = {"units": sum(1 for u in views.values() if u["region_id"] == rid),
                                      "passed": len(passed), "filtered": len(flt), "shortlisted": len(sl),
                                      "market_monthly": market.get(rid),
                                      "currency": regions.get(rid, {}).get("currency")}
        for rid, reg in regions.items():
            name = reg.get("label") or "unassigned"
            per_region_stats.setdefault(name, {"units": sum(1 for u in views.values() if u["region_id"] == rid),
                                               "passed": 0, "filtered": 0, "shortlisted": 0,
                                               "market_monthly": market.get(rid), "currency": reg.get("currency")})
        buildings = {e.parent_id for e in units if e.parent_id}
        stats = {"mentions": mentions, "units": len(units), "buildings": len(buildings),
                 "passed_filters": sum(len(v) for v in passed_by_region.values()), "filtered": filtered_n,
                 "shortlisted": len(shortlisted), "rejection_reasons": _top_reasons(units),
                 "market_areas": len(market), "regions": per_region_stats}
        engine.request.stats = {**(engine.request.stats or {}), "funnel": stats}
        engine.log("funnel", f"{mentions} mentions -> {len(units)} units -> {stats['passed_filters']} pass -> "
                             f"{filtered_n} filtered -> {len(shortlisted)} shortlisted across {len(per_region_stats)} regions")
        return stats

    @staticmethod
    def score(u: dict[str, Any], cap: float | None) -> tuple[float, dict[str, float]]:
        parts = {}
        if u["monthly"] and cap:
            parts["cost"] = 0.35 * max(0.0, min(1.0, 1 - u["monthly"] / (cap * 1.1)))
        if u["walk_min"] is not None or u["centre_km"] is None:
            parts["access"] = 0.15 * (max(0.0, 1 - (u["walk_min"] or 12) / 20))
        else:
            parts["access"] = 0.15 * max(0.0, 1 - u["centre_km"] / 15)
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

    def _pack_of_entity(self, engine, entity: AcqEntity) -> SourcePack:
        reg = self.region_info(engine, entity.region_id)
        return self.pack_for(reg["country"] or "JP")

    def enrichment_jobs(self, engine, entity: AcqEntity) -> list[EnrichmentJobSpec]:
        return self._pack_of_entity(engine, entity).enrichment_jobs(engine, entity)

    def run_job(self, engine, job: AcqJob) -> dict[str, Any]:
        name = (job.params or {}).get("pack") or "japan"
        pack = self.pack_for("JP") if name == "japan" else self.pack_for(name.split(":")[-1])
        ent = engine.db.get(AcqEntity, job.entity_id) if job.entity_id else None
        self.active_pack = pack
        self.active_region = self.region_info(engine, ent.region_id) if ent is not None and ent.region_id else None
        engine.region_ctx = {"id": ent.region_id} if ent is not None and ent.region_id else None
        try:
            return pack.run(engine, job)
        finally:
            self.active_pack = self.active_region = None
            engine.region_ctx = None

    def recheck_jobs(self, engine, entity: AcqEntity) -> list[EnrichmentJobSpec]:
        pack = self._pack_of_entity(engine, entity)
        urls = {}
        for m in engine.db.scalars(select(AcqMention).where(AcqMention.entity_id == entity.id)
                                   .order_by(AcqMention.observed_at.desc(), AcqMention.url)):
            if m.host not in urls:
                urls[m.host] = ((m.links or {}).get("detail_url"), m.url)
        out = []
        for host, (detail, page) in list(urls.items())[:2]:
            out.append(pack.job("recheck", entity.id, {"url": detail or page, "detail": bool(detail), "force": True},
                                f"TTL expired: re-observe on {host}", 2.0))
        return out

    # ---------------------------------------------------------------- project

    def project(self, engine, request: AcqRequest) -> list[dict[str, Any]]:
        evs: list[dict[str, Any]] = []
        stats = request.stats or {}
        # region-level decisions live in the discovery request, not in later enrich/recheck requests
        disc = engine.db.scalar(select(AcqRequest).where(AcqRequest.domain == self.name, AcqRequest.goal == "discover",
                                                         AcqRequest.plan.isnot(None))
                                .order_by(AcqRequest.created_at.desc()).limit(1)) if request.goal != "discover" else request
        plan = (disc.plan if disc is not None else None) or request.plan or {}
        ref = plan.get("ref_currency")
        units = list(engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name,
                                                               AcqEntity.entity_type == "unit",
                                                               AcqEntity.stage.in_(("filtered", "shortlisted", "deep"))).order_by(*ENTITY_ORDER)))
        regions = {r.id: r for r in engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name,
                                                                              AcqEntity.entity_type == "region").order_by(*ENTITY_ORDER))}
        rinfo = {rid: self.region_info(engine, rid) for rid in regions}
        for rid, r in regions.items():
            info = rinfo[rid]
            planned = next((x for x in plan.get("regions", []) if x.get("id") == rid), {})
            evs.append({"type": "entity_upserted", "id": rid, "kind": "region", "name": r.label, "attrs": {
                "country": info["country"], "currency": info["currency"], "fx_per_ref": info["fx_per_ref"],
                "ref_currency": ref, "coords": info["coords"], "stay_p": planned.get("stay_p",
                                                                                 (info.get("stay") or {}).get("p_allowed")),
                "stay_basis": (info.get("stay") or {}).get("basis"), "home": planned.get("home", False),
                "distance_km": planned.get("distance_km"), "utility": planned.get("utility"),
                "official_guidance": (info.get("stay") or {}).get("official_guidance"), "acq_entity": rid}})
        seen_b = set()
        views = []
        for e in units:
            u = self.unit_view(engine, e, rinfo)
            views.append((e, u))
            evs.append({"type": "entity_upserted", "id": e.id, "kind": "unit",
                        "name": f"{u['building']} {e.label}".strip() if u["building"] else f"{e.label} ({u['region']})",
                        "merge": False, "attrs": {
                            "housing_type": u["housing_type"], "kind": u["kind"], "stage": e.stage, "score": e.score,
                            "region_id": e.region_id, "region": u["region"], "country": u["country"],
                            "rent": u["rent"], "monthly": u["monthly"], "currency": u["currency"],
                            "monthly_ref": u["monthly_ref"], "ref_currency": ref, "deposit": u["deposit"],
                            "key_money": u["key_money"], "layout": u["layout"], "bedrooms": u["bedrooms"],
                            "area_m2": u["area_m2"], "walk_min": u["walk_min"], "station": u["station"],
                            "centre_km": u["centre_km"], "address": u["address"],
                            "availability": u["availability"], "availability_confidence": u["availability_conf"],
                            "rent_confidence": u["rent_conf"], "fresh": u["fresh"], "conflicts": u["conflicts"],
                            "sources": u["sources"], "move_in": u["move_in"], "earliest_move_in": u["earliest_move_in"],
                            "rail_distance_m": u["rail_distance_m"], "hub_minutes": u["hub_minutes"],
                            "libraries": u["libraries"], "universities": u["universities"], "internet": u["internet"],
                            "acq_entity": e.id},
                        "relations": ([{"rel": "located_at", "dst": e.parent_id}] if e.parent_id else [])
                        + ([{"rel": "located_at", "dst": e.region_id}] if e.region_id else [])})
            if e.parent_id and e.parent_id not in seen_b:
                seen_b.add(e.parent_id)
                evs.append({"type": "entity_upserted", "id": e.parent_id, "kind": "building", "name": u["building"],
                            "attrs": {"address": u["address"], "coords": u["coords"], "built_year": u["built_year"]}})
        # region-level market evidence, in each region's own currency and in the reference currency
        all_units = list(engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.name,
                                                                   AcqEntity.entity_type == "unit").order_by(*ENTITY_ORDER)))
        per_region: dict[str, dict[str, list[float]]] = {}
        for e in all_units:
            u = self.unit_view(engine, e, rinfo)
            if not u["monthly_ref"] or not e.region_id:
                continue
            d = per_region.setdefault(e.region_id, {"room": [], "furnished": [], "hostel_bed": [], "apartment": []})
            bucket = "hostel_bed" if u["kind"] == "hostel_bed" else "room" if u["kind"] == "room" else \
                "furnished" if u["housing_type"] == "monthly" or "housinganywhere" in " ".join(u["sources"]) or \
                "spotahome" in " ".join(u["sources"]) else "apartment"
            d[bucket].append(u["monthly_ref"])
        market = {rinfo[rid]["label"]: {k: (round(statistics.median(v)) if v else None) for k, v in d.items()}
                  for rid, d in per_region.items() if rid in rinfo}
        facts = [
            {"key": "housing.ref_currency", "value": ref},
            {"key": "housing.ref_per_usd", "value": (plan.get("fx") or {}).get("ref_per_usd")},
            {"key": "housing.regions", "value": [x.get("name") + f" ({x.get('country')})" for x in plan.get("regions", [])]},
            {"key": "housing.geography_rationale", "value": plan.get("geography_rationale")},
            {"key": "housing.assumptions", "value": (request.params or {}).get("assumptions", [])},
            {"key": "housing.funnel", "value": stats.get("funnel", {})},
            {"key": "housing.market_by_region_ref", "value": market},
            {"key": "housing.sources", "value": {h: {k: v for k, v in s.items() if k in ("pages", "ok", "blocked",
                                                                                           "mentions", "last")}
                                                 for h, s in (stats.get("by_host") or {}).items()}},
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


def _brief_summary(s: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(s, dict):
        return {}
    out = {k: v for k, v in s.items() if k in ("areas", "area_rationale", "error")}
    if s.get("sources"):
        out["sources"] = [{k: x.get(k) for k in ("host", "records", "status", "origin")} for x in s["sources"]]
    if s.get("discovery"):
        out["discovery"] = [{k: x.get(k) for k in ("host", "channel", "status", "why", "records")}
                            for x in s["discovery"]]
    return out


def _top_reasons(units: list[AcqEntity]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in units:
        for r in (e.score_detail or {}).get("rejections", []):
            k = re.sub(r"[\d,.]+", "N", r)
            out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1])[:10])


# ------------------------------------------------------------------ strategies

RELOCATION_USD = (150.0, 0.08)     # labelled prior: fixed cost + USD per km (travel, first nights, shipping minimum)


def housing_strategies(mission: dict[str, Any], world: dict[str, Any]) -> list[RouteProposal]:
    """Competing strategies across regions, not a property ranking.

    For each acquired region: lease there (with the shortlist as backups). Across regions:
    a furnished/room option, hostel days and not committing yet. Money is in the reference
    currency; moving to a region the principal may not be allowed to live in carries that
    as success probability and as an uncertainty (``principal.right_to_reside.<CC>``) whose
    answer can flip the plan."""
    facts = world.get("facts", {})
    ents = world.get("entities", [])
    regions = {e["id"]: e for e in ents if e["kind"] == "region"}
    units = [e for e in ents if e["kind"] == "unit" and e["attrs"].get("stage") in ("shortlisted", "deep")]
    ref = facts.get("housing.ref_currency") or next((u["attrs"].get("ref_currency") for u in units), None) or "USD"
    fx_ref_usd = _fx_ref_per_usd(facts)
    routes: list[RouteProposal] = []
    base_unc = [Uncertainty(question="Where will the principal work?", fact_key="principal.work_location",
                            affects=["success_probability", "optionality"]),
                Uncertainty(question="What monthly budget is acceptable?", fact_key="principal.max_rent",
                            affects=["success_probability"])]
    by_region: dict[str, list[dict]] = {}
    for u in units:
        if u["attrs"].get("region_id") and u["attrs"].get("monthly_ref"):
            by_region.setdefault(u["attrs"]["region_id"], []).append(u)
    region_monthlies = []
    for rid, us in sorted(by_region.items(), key=lambda kv: (regions.get(kv[0]) or {}).get("name") or ""):
        reg = regions.get(rid, {"attrs": {}, "name": "home", "id": rid})
        ra = reg["attrs"]
        cc = ra.get("country") or "JP"
        rent_units = sorted([u for u in us if u["attrs"].get("kind") not in ("room", "hostel_bed")
                             and u["attrs"].get("housing_type", "rent") == "rent"],
                            key=lambda u: (-(u["attrs"].get("score") or 0), u.get("name") or "",
                                           u["attrs"].get("monthly") or 0, u["attrs"].get("area_m2") or 0))
        if not rent_units:
            continue          # only rooms here: that evidence feeds the room strategy, not a lease
        top = rent_units[:3]

        def m_ref(u):
            return u["attrs"].get("monthly_ref") or u["attrs"].get("monthly")

        monthly = statistics.median(m_ref(u) for u in top)
        region_monthlies.append(monthly)

        def upfront(u):
            a = u["attrs"]
            scale = (a.get("monthly_ref") / a["monthly"]) if a.get("monthly_ref") and a.get("monthly") else 1.0
            dep = a.get("deposit")
            dep_ref = dep * scale if dep is not None else (a.get("rent") or a["monthly"]) * scale  # prior: 1 month
            return dep_ref + (a.get("key_money") or 0) * scale + m_ref(u)

        initial = statistics.median(upfront(u) for u in top)
        home = bool(ra.get("home"))
        dist = ra.get("distance_km") or 0
        relocation = 0.0 if home or not fx_ref_usd else (RELOCATION_USD[0] + RELOCATION_USD[1] * dist) * fx_ref_usd
        p_avail = 1.0
        for u in top:
            p_avail *= 1 - (u["attrs"].get("availability_confidence") or 0.5) * 0.75
        p_avail = 1 - p_avail
        stay_p = float(ra.get("stay_p") if ra.get("stay_p") is not None else (0.9 if home else 0.3))
        p = round(max(0.01, min(0.99, p_avail * stay_p)), 3)
        stale = any(not u["attrs"].get("fresh", True) for u in top)
        conflicts = sum(len(u["attrs"].get("conflicts") or []) for u in top)
        best = top[0]
        work_known = facts.get("principal.work_location") is not None
        hubs = [u["attrs"].get("hub_minutes") for u in top if isinstance(u["attrs"].get("hub_minutes"), dict)]
        centre = [u["attrs"].get("centre_km") for u in top if u["attrs"].get("centre_km") is not None]
        if work_known:
            p_far, far_why = 0.0, "work location known"
        elif hubs:
            p_far = statistics.mean(sum(1 for v in h.values() if v > 45) / max(len(h), 1) for h in hubs)
            far_why = f"work location unknown: {p_far:.0%} of major hubs > 45 min away (estimated)"
        elif centre:
            p_far = statistics.mean(min(1.0, c / 20) for c in centre)
            far_why = f"work location unknown: candidates {statistics.mean(centre):.1f} km from the centre on average"
        else:
            p_far, far_why = 0.5, "work location unknown and no mobility evidence yet (prior 50 %)"
        upside = round(24 * (1 - 0.6 * p_far), 2)
        city = (reg.get("name") or "").split(",")[0] or "here"
        slug = re.sub(r"[^a-z0-9]+", "-", f"{cc}-{city}".lower()).strip("-")
        right_fact = f"principal.right_to_reside.{cc}"
        sens = [Sensitivity(fact="housing.shortlist.available_count", op="lt", value=1,
                            effects={"success_probability": Effect(mul=0.3)}, rationale="no fresh available candidate"),
                Sensitivity(fact="principal.max_rent", op="lt", value=monthly,
                            effects={"success_probability": Effect(mul=0.4)}, rationale="candidates exceed stated budget")]
        unc = list(base_unc)
        if not home or stay_p < 0.9:
            sens += [Sensitivity(fact=right_fact, op="eq", value=False, effects={"success_probability": Effect(set=0.01)},
                                 rationale=f"no right to live in {cc}"),
                     Sensitivity(fact=right_fact, op="eq", value=True,
                                 effects={"success_probability": Effect(set=round(max(0.01, min(0.99, p_avail)), 3)),
                                          "risk": Effect(add=-0.2)},
                                 rationale=f"right to live in {cc} confirmed")]
            unc.append(Uncertainty(question=f"May the principal live (and work) in {cc}?", fact_key=right_fact,
                                   affects=["success_probability", "risk"], resolvable_by="human"))
        title = (f"Lease now in {city}: {best['name']}" if home else f"Move to {city} ({cc}) and lease: {best['name']}")
        routes.append(RouteProposal(
            key=f"housing-lease-{slug}", archetype="housing_lease", tags=["long_term_stability", "commitment"]
            + ([] if home else ["relocation"]),
            title=title + (f" (+{len(top) - 1} backups)" if len(top) > 1 else ""),
            thesis=(f"Sign a standard lease in {city}. Best evidenced candidate: {best['name']} at "
                    f"{int(m_ref(best)):,} {ref}/month. Median of top {len(top)}: {int(monthly):,} {ref}/month, "
                    f"~{int(initial + relocation):,} {ref} upfront" + ("" if home else
                    f" incl. ~{int(relocation):,} {ref} relocation (prior, {int(dist):,} km)") + "."
                    + ("" if home else f" Right to live in {cc}: p={stay_p:.2f} ({ra.get('stay_basis') or 'unknown'}).")),
            estimates=RouteEstimates(expected_upside=upside, success_probability=p,
                                     time_cost_hours=20 if home else 45, money_cost=round(initial + relocation, 2),
                                     information_gain=0.25, reversibility=0.25 if home else 0.15,
                                     optionality=0.3 if home else 0.25,
                                     risk=min(0.95, 0.25 + 0.2 * p_far + (0.1 if stale else 0) + 0.05 * conflicts
                                              + (0 if home else 0.35 * (1 - stay_p))),
                                     authority_cost=0.5 if home else 0.7),
            estimate_rationale={
                "expected_upside": f"24 months of stable housing, discounted by wrong-place risk -- {far_why}",
                "success_probability": f"availability over top candidates ({p_avail:.2f}) x right to stay ({stay_p:.2f})",
                "money_cost": f"median of deposit (1 month if not stated) + key money + first month, in {ref}"
                              + ("" if home else "; relocation is a labelled prior (150 USD + 0.08 USD/km)")},
            sensitivities=sens, uncertainty=unc,
            operations=[
                OperationSpec(key="lease.recheck", tool="acquire", action="recheck", kind="probe",
                              goal=f"Re-verify availability and rent of the top {city} candidates at their sources",
                              inputs={"entity_ids": [u["attrs"].get("acq_entity") for u in top], "force": True},
                              resolves=["housing.shortlist.available_count"],
                              verification=VerificationSpec(method="schema", required_keys=["request_id"]),
                              timeout_s=600, cost_estimate=CostEstimate(minutes=3)),
                OperationSpec(key="lease.brief", tool="fs", action="write", depends_on=["lease.recheck"],
                              goal="Write a viewing brief with every claim's source and freshness",
                              inputs={"path": f"housing/{mission['id']}/{slug}-viewing-brief.md",
                                      "content": "{{ops.lease.recheck.outputs.brief_markdown}}"},
                              verification=VerificationSpec(method="schema", required_keys=["bytes"])),
                OperationSpec(key="lease.contact", tool="human", action="perform", depends_on=["lease.brief"],
                              goal=f"Request a viewing of {best['name']}",
                              inputs={"required_action": f"Request a viewing for {best['name']} "
                                                         f"({best['attrs'].get('address') or city}) using the listing's "
                                                         "inquiry form (needs your name and contact details)",
                                      "reason": "inquiry forms require the principal's identity and contact details",
                                      "estimated_time_seconds": 180, "kind": "identity",
                                      "context": {"entity": best["id"], "sources": best["attrs"].get("sources")},
                                      "response_schema": {"done": {"type": "boolean"}}},
                              verification=VerificationSpec(method="human_confirmed")),
            ]))
    # furnished / room options across regions (live evidence where acquired)
    market = facts.get("housing.market_by_region_ref") or {}
    cheapest_room = _cheapest(market, "room")
    cheapest_furn = _cheapest(market, "furnished")
    cheapest_hostel = _cheapest(market, "hostel_bed")
    typical = statistics.median(region_monthlies) if region_monthlies else None
    if cheapest_furn or not region_monthlies:
        city, val = cheapest_furn or (None, None)
        routes.append(RouteProposal(
            key="housing-monthly", archetype="housing_monthly", tags=["flexible", "short_term"],
            title="Furnished mid-term rental while deciding" + (f" (cheapest evidence: {city})" if city else ""),
            thesis=("Furnished monthly/mid-term rental: fast, reversible, more per month. "
                    + (f"Live offers in {city}: median {int(val):,} {ref}/month." if val else
                       "No live furnished offer acquired; estimate is a prior.")),
            estimates=RouteEstimates(expected_upside=3, success_probability=0.85 if val else 0.6, time_cost_hours=6,
                                     money_cost=(val or (typical or 1000) * 1.6) * 1.2, information_gain=0.3,
                                     reversibility=0.85, optionality=0.8, risk=0.3 if val else 0.5, authority_cost=0.2),
            estimate_rationale={"money_cost": "one month + fees" + ("" if val else " (prior: no live evidence)")},
            uncertainty=base_unc))
    if cheapest_room or not region_monthlies:
        city, val = cheapest_room or (None, None)
        routes.append(RouteProposal(
            key="housing-share", archetype="housing_share", tags=["low_upfront", "flexible"],
            title="Room in a shared house" + (f" (cheapest evidence: {city})" if city else ""),
            thesis=("Private room in a shared/managed house: low upfront cost, month-to-month. "
                    + (f"Live offers in {city}: median {int(val):,} {ref}/month." if val else "No live room offers.")),
            estimates=RouteEstimates(expected_upside=9, success_probability=0.8 if val else 0.55, time_cost_hours=8,
                                     money_cost=(val or (typical or 1000) * 0.6) * 1.5, information_gain=0.2,
                                     reversibility=0.75, optionality=0.7, risk=0.3, authority_cost=0.3),
            estimate_rationale={"expected_upside": "months of reasonably stable housing (shared living discounted)",
                                "money_cost": "first month + contract fee"},
            uncertainty=base_unc))
    hcity, hval = cheapest_hostel or (None, None)
    routes.append(RouteProposal(
        key="housing-hostel", archetype="housing_hostel", tags=["no_commitment"],
        title="Stay in hostels/hotels day by day" + (f" (live prices: {hcity})" if hcity else ""),
        thesis=("Keep lodging day by day. " + (f"Live hostel beds in {hcity}: ~{int(hval):,} {ref}/month equivalent."
                                               if hval else "No live hostel price acquired; the estimate is a prior.")),
        estimates=RouteEstimates(expected_upside=0.5, success_probability=0.95, time_cost_hours=4,
                                 money_cost=hval or (typical or 1000) * 1.4, information_gain=0.05, reversibility=1.0,
                                 optionality=0.95, risk=0.55, authority_cost=0.1),
        estimate_rationale={"money_cost": "30 nights at live per-bed prices" if hval else
                            "prior: 1.4 x typical monthly rent (no live hostel evidence)"}))
    home = next((e for e in ents if e["kind"] == "place" and e["attrs"].get("is_home")), None)
    if home is not None:
        routes.append(RouteProposal(
            key="housing-existing", archetype="housing_existing", tags=["no_commitment"],
            title=f"Stay at {home['name']}", thesis="Use the existing base while other uncertainties resolve.",
            estimates=RouteEstimates(expected_upside=2, success_probability=0.9, time_cost_hours=1, money_cost=0,
                                     reversibility=1.0, optionality=0.9, risk=0.3, information_gain=0.1)))
    routes.append(RouteProposal(
        key="housing-defer", archetype="housing_defer", tags=["defensive"],
        title="Don't commit until work location and right to stay are known",
        thesis="Where to live depends on where the principal can work and legally stay; keep options open and "
               "resolve those first.",
        estimates=RouteEstimates(expected_upside=0.5, success_probability=1.0, time_cost_hours=0.5, money_cost=0,
                                 information_gain=0.4, reversibility=1.0, optionality=1.0, risk=0.45, authority_cost=0.0),
        sensitivities=[Sensitivity(fact="principal.work_location", op="exists",
                                   effects={"information_gain": Effect(set=0.05), "expected_upside": Effect(set=0.2)},
                                   rationale="work location now known: waiting no longer buys information")],
        uncertainty=base_unc))
    return routes


def _cheapest(market: dict[str, dict], bucket: str) -> tuple[str, float] | None:
    vals = [(city, d.get(bucket)) for city, d in (market or {}).items() if d.get(bucket)]
    return min(vals, key=lambda x: x[1]) if vals else None


def _fx_ref_per_usd(facts: dict[str, Any]) -> float | None:
    """Units of the reference currency per USD (ECB cross rate recorded during geography)."""
    v = facts.get("housing.ref_per_usd")
    return float(v) if v else None
