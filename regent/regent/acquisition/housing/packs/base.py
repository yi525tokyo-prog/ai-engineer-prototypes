"""Source packs: what Regent knows about acquiring housing in one geography.

A pack contributes *local knowledge* -- currency and languages, the extractor that reads
the local sources, seed sources and official entry points, public-data enrichment, and how
to split a region into areas. Everything else (claims, beliefs, identity, freshness,
funnel, strategies) is shared. :class:`JapanPack` is one pack; :class:`GenericPack` works
for any country from global sources plus sources it discovers on the web.

The jobs below are common to all packs: detail pages, original-listing verification,
operator pages, move-in feasibility and TTL rechecks.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

from regent.acquisition.tables import AcqEntity
from regent.acquisition.types import ClaimIn, EnrichmentJobSpec, SourceSpec
from regent.config import settings


class SourcePack:
    country: str = "*"
    name: str = "pack"
    languages: tuple[str, ...] = ("en",)
    currency: str = "USD"
    default_period: str = "month"
    network_jobs = ("detail", "verify_source", "geocode", "recheck", "operator_page")

    def __init__(self, adapter):
        self.adapter = adapter

    # --------------------------------------------------------- knowledge

    def extractor(self, region: dict[str, Any] | None = None):
        raise NotImplementedError

    def seed_sources(self) -> list[SourceSpec]:
        return []

    def authority_urls(self) -> list[str]:
        """Official guidance pages (housing, residence) -- starting points for discovery and stay rules."""
        return []

    def city_candidates(self, engine) -> list[dict[str, Any]]:
        """Regions inside this pack's country worth considering, from acquired pages."""
        return []

    def discover(self, engine, region: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def enrichment_jobs(self, engine, unit: AcqEntity) -> list[EnrichmentJobSpec]:
        return []

    def view_extras(self, unit_beliefs: dict, building_beliefs: dict) -> dict[str, Any]:
        """Pack-specific fields for the unit view (e.g. Japanese walk minutes)."""
        return {}

    def job(self, kind: str, entity_id: str, params: dict, reason: str, priority: float) -> EnrichmentJobSpec:
        return EnrichmentJobSpec(kind=kind, entity_id=entity_id, params={**params, "pack": self.name},
                                 priority=priority, reason=reason)

    # --------------------------------------------------------------- run

    def run(self, engine, job) -> dict[str, Any]:
        fn = getattr(self, f"job_{job.kind}", None)
        if fn is None:
            return {"_status": "skipped", "reason": f"no handler for {job.kind} in {self.name}"}
        return fn(engine, job)

    def _building(self, engine, unit):
        from regent.acquisition.tables import AcqEntity

        return engine.db.get(AcqEntity, unit.parent_id) if unit.parent_id else None


    def _add(self, engine, entity_id: str, attr: str, value: Any, conf: float, evidence: str, *, host: str,
             kind: str, url: str = "") -> None:
        engine.claims.add(entity_id, ClaimIn(attr, value, conf, evidence, engine.claims.now(), "housing-enricher"),
                          source_host=host, source_kind=kind, url=url)
        engine.dirty.add(entity_id)

    # --------------------------------------------------------------- jobs


    def job_detail(self, engine, job) -> dict[str, Any]:
        url = job.params["url"]
        doc, ents = engine.fetch_and_ingest(url, purpose="detail", kind=job.params.get("kind", "portal"),
                                            pin=job.entity_id)
        if doc is None:
            return {"_status": "skipped", "reason": "budget"}
        if not doc.ok:
            return {"_status": "blocked" if doc.blocked else "failed", "blocked": doc.blocked, "status": doc.status}
        # a detail page resolves to the same unit through the resolver; also pin it explicitly
        from regent.acquisition.tables import AcqMention

        mentions = [m for m in engine.db.query(AcqMention).filter(AcqMention.document_id == doc.id)]
        source_url = next((m.links.get("source_url") for m in mentions if (m.links or {}).get("source_url")), None)
        out = {"entities": [e.id for e in ents], "source_url": source_url}
        if source_url and job.params.get("verify", True):
            engine.schedule([self.job("verify_source", job.entity_id, {"url": source_url},
                                                   "follow the original listing to the managing company", 0.9)])
        elif job.params.get("verify", True):
            engine.refresh_dirty()
            u = engine.db.get(AcqEntity, job.entity_id)
            dom = ((u.beliefs or {}).get("agent_domain") or {}).get("value") if u else None
            if dom:
                engine.schedule([self.job("operator_page", job.entity_id, {"domain": dom},
                                                       f"find this room on the listing agent's own site ({dom})",
                                                       0.85)])
                out["agent_domain"] = dom
        return out


    def job_operator_page(self, engine, job) -> dict[str, Any]:
        """The portal names the agent but does not link its page: find the room on the agent's own
        site through web search, then verify it there. Without a search API this is recorded as an
        explicit gap -- crawler-permitted HTML search engines are not available."""
        dom = job.params["domain"]
        if not settings.search_api_key:
            return {"_status": "skipped", "reason": "needs REGENT_SEARCH_API_KEY to locate the room on "
                                                    f"{dom} (the portal gives the agent, not its page)"}
        from regent.connectors.services import BraveSearch

        u = engine.db.get(AcqEntity, job.entity_id)
        b = engine.db.get(AcqEntity, u.parent_id) if u is not None and u.parent_id else None
        name = (((b.beliefs if b else {}) or {}).get("name") or {}).get("value") or ""
        layout = (((u.beliefs if u else {}) or {}).get("layout") or {}).get("value") or ""
        if not name:
            return {"_status": "skipped", "reason": "no building name to search for"}
        try:
            hits = BraveSearch().search(f"site:{dom} {name} {layout}".strip(), 5)
        except Exception as e:
            return {"_status": "failed", "reason": f"search: {type(e).__name__}: {e}"[:200]}
        hit = next((h for h in hits if urlparse(h["url"]).netloc.endswith(dom)), None)
        if hit is None:
            self._add(engine, job.entity_id, "operator_verified", False, 0.5,
                      f"no page for '{name}' found on {dom}", host=dom, kind="search")
            return {"found": False, "domain": dom}
        doc, ents = engine.fetch_and_ingest(hit["url"], purpose="verify", kind="operator", pin=job.entity_id,
                                            pin_min_p=0.5)
        same = doc is not None and doc.ok and job.entity_id in [e.id for e in ents]
        if doc is not None and doc.ok:
            self._add(engine, job.entity_id, "operator_verified", same, 0.8,
                      f"{'same room' if same else 'no matching room'} on {doc.final_url}", host=doc.host,
                      kind="operator", url=doc.final_url)
        return {"found": True, "url": hit["url"], "verified_same_unit": same}


    def job_verify_source(self, engine, job) -> dict[str, Any]:
        doc, ents = engine.fetch_and_ingest(job.params["url"], purpose="verify", kind="operator",
                                            pin=job.entity_id, pin_min_p=0.5)
        if doc is None:
            return {"_status": "skipped", "reason": "budget"}
        if not doc.ok:
            return {"_status": "blocked" if doc.blocked else "failed", "blocked": doc.blocked, "status": doc.status}
        return {"host": doc.host, "final_url": doc.final_url, "entities": [e.id for e in ents],
                "verified_same_unit": job.entity_id in [e.id for e in ents]}


    def _coords(self, engine, entity_id):
        from regent.acquisition.tables import AcqEntity

        b = engine.db.get(AcqEntity, entity_id)
        c = ((b.beliefs or {}).get("coords") or {}).get("value") if b else None
        return b, (tuple(c) if c else None)


    def job_move_in(self, engine, job) -> dict[str, Any]:
        from regent.acquisition.tables import AcqEntity

        u = engine.db.get(AcqEntity, job.entity_id)
        engine.refresh_dirty()
        mi = ((u.beliefs or {}).get("move_in") or {}).get("value")
        target = (engine.request.params or {}).get("move_in_by")
        if not mi:
            return {"_status": "skipped", "reason": "no move-in claim"}
        today = engine.claims.now().date()
        if mi == "immediate":
            earliest = today + timedelta(days=14)   # screening + contract typically ~2 weeks
        elif re.match(r"\d{4}-\d{2}-\d{2}", str(mi)):
            earliest = max(date.fromisoformat(mi), today + timedelta(days=14))
        else:
            return {"_status": "skipped", "reason": f"move-in '{mi}' not a date"}
        ok = None if not target else earliest <= date.fromisoformat(target)
        self._add(engine, u.id, "earliest_move_in_est", earliest.isoformat(), 0.6,
                  f"move_in={mi} + ~14 days screening/contract", host="regent", kind="derived")
        if ok is not None:
            self._add(engine, u.id, "move_in_feasible", ok, 0.6, f"earliest {earliest} vs needed by {target}",
                      host="regent", kind="derived")
        return {"earliest": earliest.isoformat(), "feasible": ok}


    def job_recheck(self, engine, job) -> dict[str, Any]:
        """Re-observe time-sensitive claims at their source (detail page, else index page)."""
        doc, ents = engine.fetch_and_ingest(job.params["url"], purpose="recheck", kind=job.params.get("kind", "portal"),
                                            pin=job.entity_id if job.params.get("detail") else None)
        if doc is None:
            return {"_status": "skipped", "reason": "budget"}
        if not doc.ok:
            if doc.status in (404, 410):
                self._add(engine, job.entity_id, "availability", False, 0.75, f"listing URL now HTTP {doc.status}",
                          host=doc.host, kind=job.params.get("kind", "portal"), url=job.params["url"])
                return {"availability": False, "status": doc.status}
            return {"_status": "blocked" if doc.blocked else "failed", "status": doc.status}
        return {"re_observed": [e.id for e in ents]}
