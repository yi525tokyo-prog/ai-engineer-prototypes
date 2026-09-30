"""GenericPack: housing acquisition for any country Regent has no dedicated pack for.

Sources come from the registry: global aggregators, the country's seed entry points when
a seed list exists, and whatever discovery verifies. Listings are read by the
site-agnostic :class:`GenericListingExtractor`; buildings are located with public
geocoders where one exists for the country (UK postcodes.io, US Census) and every unit
gets a distance to the city centre as a mobility proxy.
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import quote

from sqlalchemy import select

from regent.acquisition.discovery import Candidate, SourceDiscovery
from regent.acquisition.housing import locale as L
from regent.acquisition.housing.generic_extract import GenericListingExtractor
from regent.acquisition.housing.packs.base import SourcePack
from regent.acquisition.housing.packs.seeds import AUTHORITY, COUNTRY_NAMES, COUNTRY_SEEDS, GLOBAL_SOURCES, \
    housing_vocabulary
from regent.acquisition.navigate import next_page
from regent.acquisition.tables import AcqEntity, AcqMention, AcqSourceRecipe
from regent.acquisition.types import EnrichmentJobSpec, SourceSpec

MIN_LOCAL_SOURCES = 2


def haversine_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


class GenericPack(SourcePack):
    name = "generic"

    def __init__(self, adapter, country: str):
        super().__init__(adapter)
        self.country = country.upper()
        self.name = f"generic:{self.country}"
        cur, langs, per = L.COUNTRIES.get(self.country, ("USD", ("en",), "month"))
        self.currency, self.languages, self.default_period = cur, langs, per
        self._x: dict[str, GenericListingExtractor] = {}

    def extractor(self, region: dict[str, Any] | None = None):
        city = (region or {}).get("name")
        key = city or ""
        if key not in self._x:
            self._x[key] = GenericListingExtractor(self.country, locality=city)
        return self._x[key]

    def seed_sources(self) -> list[SourceSpec]:
        return COUNTRY_SEEDS.get(self.country, [])

    def authority_urls(self) -> list[str]:
        return AUTHORITY.get(self.country, [])

    def _vocab(self, region):
        x = self.extractor(region)

        def verify(doc, country, city):
            if doc is None:
                return 0
            return sum(1 for m in x.extract(doc, "listing") if m.entity_type == "unit")

        def city_share(doc, city):
            us = [m for m in x.extract(doc, "listing") if m.entity_type == "unit"]
            if not us:
                return 0.0
            c = city.lower()
            hit = sum(1 for m in us if c in " ".join(str(v) for v in (
                m.raw_text, m.value("title"), (m.parent.value("address") if m.parent else ""),
                (m.parent.value("locality") if m.parent else ""), (m.links or {}).get("detail_url", ""))).lower())
            return hit / len(us)
        return housing_vocabulary(verify, city_share)

    # ------------------------------------------------------------- discovery

    def discover(self, engine, region: dict[str, Any]) -> dict[str, Any]:
        city = region["name"]
        langs = list(self.languages)
        disc = SourceDiscovery(engine, self.adapter.name, self._vocab(region))
        disc.registry.seed(self.country, self.seed_sources(), origin=f"pack:{self.country}")
        disc.registry.seed("*", GLOBAL_SOURCES, origin="global")
        engine.checkpoint()
        local = disc.registry.usable(self.country)
        report: dict[str, Any] = {"sources": [], "discovery": []}
        if len(local) < MIN_LOCAL_SOURCES and engine.budget_left():
            # bootstrap: this country has too few known local sources -- find some on the web
            cands = disc.candidates(self.country, city, langs, self.authority_urls())
            engine.log("discovery", f"[{self.country}] bootstrapping sources for {city}: {len(cands)} candidates "
                                    f"({', '.join(sorted({c.channel for c in cands})) or 'none'})")
            verified = 0
            for c in cands[:12]:
                if not engine.budget_left() or verified >= MIN_LOCAL_SOURCES:
                    break
                r = disc.verify(c, self.country, city, langs)
                report["discovery"].append(r)
                engine.log("discovery", f"[{self.country}] candidate {c.host} via {c.channel}: {r['status']}"
                                        + (f" ({r.get('why')})" if r.get("why") else f", {r.get('records')} records"))
                verified += r["status"] == "verified"
            engine.checkpoint()
            local = disc.registry.usable(self.country)
        glob = disc.registry.usable("*")
        for rec in local + glob:
            if not engine.budget_left():
                break
            report["sources"].append(self._use(engine, disc, rec, region, langs))
            engine.refresh_dirty()
            engine.checkpoint()
        return report

    def _use(self, engine, disc: SourceDiscovery, rec: AcqSourceRecipe, region: dict, langs: list[str]) -> dict:
        city = region["name"]
        trail_known = ((rec.evidence or {}).get("trails") or {}).get(city)
        kind = "public_data" if rec.kind == "public_data" else ("operator" if rec.kind == "operator" else "portal")
        doc = None
        if trail_known:                                     # learned earlier: go straight to the listing page
            doc = engine.fetch(trail_known[-1], purpose="listing", kind=kind, render=rec.render or "auto")
            trail = trail_known
            if doc is not None and not doc.ok:
                doc = None
        if doc is None:
            words = [city] if rec.kind != "hostel" else [COUNTRY_NAMES.get(self.country, self.country), city]
            doc, trail = disc.navigate_to_listings(rec.entry_url, words[-1] if len(words) == 1 else city, langs,
                                                   render=rec.render or "auto", kind=kind, admin=region.get("admin"))
            if doc is None and rec.kind == "hostel":
                doc, trail = disc.navigate_to_listings(rec.entry_url, words[0], langs, render="static", kind=kind)
        if doc is None:
            last = engine.fetcher.db.get(__import__("regent.acquisition.tables", fromlist=["AcqSource"]).AcqSource,
                                         rec.host)
            blocked = last is not None and (last.last_status or "").startswith("blocked")
            disc.registry.record(rec.id, ok=False, status="blocked" if blocked else None,
                                 note=f"{city}: no listings reached ({last.last_status if last else 'unreachable'})")
            engine.log("discovery", f"{rec.host} {city}: no listing page"
                                    + (f" ({last.last_status})" if last else ""))
            return {"host": rec.host, "records": 0, "status": "blocked" if blocked else "no_listings"}
        x = self.extractor(region)
        pages, n = 0, 0
        while doc is not None and doc.ok and pages < 2:
            ms = x.extract(doc, "listing")
            engine.ingest(doc, ms, source_kind="portal" if rec.kind in ("portal", "aggregator", "hostel") else rec.kind,
                          region=region)
            n += sum(1 for m in ms if m.entity_type == "unit")
            pages += 1
            engine.log("discovery", f"{rec.host} {city} p{pages}: {len(ms)} records", url=doc.final_url)
            nxt = next_page(doc.html, doc.final_url) if pages < 2 else None
            doc = engine.fetch(nxt, purpose="listing", kind="portal", render=rec.render or "auto") if nxt else None
        disc.registry.record(rec.id, ok=n > 0, records=n, city=city, trail=trail)
        return {"host": rec.host, "records": n, "pages": pages, "origin": rec.origin}

    # ---------------------------------------------------------- price signal

    def price_signal(self, engine, cand: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
        """One live listing page for a candidate city, comparable with other regions: the country's
        own portals first (apartments), global aggregators after (often rooms, labelled so).
        A successful navigation is remembered in the source registry for the deep acquisition."""
        import statistics as st

        region = {"name": cand["name"]}
        x = self.extractor(region)
        disc = SourceDiscovery(engine, self.adapter.name, self._vocab(region))
        disc.registry.seed(self.country, self.seed_sources(), origin=f"pack:{self.country}")
        docs = []
        admin = cand.get("admin")
        for rec in disc.registry.usable(self.country)[:3]:
            if not engine.budget_left():
                break
            doc, trail = disc.navigate_to_listings(rec.entry_url, cand["name"], list(self.languages), kind="portal",
                                                   render=rec.render or "auto", admin=admin)
            if doc is None:
                disc.registry.record(rec.id, ok=False, note=f"price signal: no {cand['name']} listings reached")
                continue
            disc.registry.record(rec.id, ok=True, records=0, city=cand["name"], trail=trail)
            docs.append(doc)
            homes = [m for m in x.extract(doc, "listing") if m.entity_type == "unit"
                     and m.value("unit_kind") not in ("room", "hostel_bed")]
            if len(homes) >= 3:
                break                       # a rooms-only portal is not enough: try the next local source
        def has_homes() -> bool:
            return any(sum(1 for m in x.extract(d, "listing") if m.entity_type == "unit"
                           and m.value("unit_kind") not in ("room", "hostel_bed")) >= 3 for d in docs)

        for _origin, url in (cand.get("urls") or {}).items():
            if has_homes() or not engine.budget_left():
                break
            doc = engine.fetch(url, purpose="market", kind="portal", render="static")
            if doc is not None and doc.ok:
                if sum(1 for m in x.extract(doc, "listing") if m.entity_type == "unit") < 3:
                    doc, _t = disc.navigate_to_listings(doc.final_url, cand["name"], list(self.languages),
                                                        kind="portal", render="static", max_steps=1)
                if doc is not None:
                    docs.append(doc)
        homes: dict[str, list[float]] = {}
        small: dict[str, list[float]] = {}
        rooms: dict[str, list[float]] = {}
        where = None
        for doc in docs:
            for m in x.extract(doc, "listing"):
                if m.entity_type != "unit" or not m.value("rent"):
                    continue
                kind, cur, v = m.value("unit_kind"), m.value("currency"), float(m.value("rent"))
                if kind == "hostel_bed":
                    continue
                target = rooms if kind == "room" else homes
                target.setdefault(cur, []).append(v)
                if kind != "room" and (m.value("bedrooms") is None or m.value("bedrooms") <= 1):
                    small.setdefault(cur, []).append(v)
            where = doc.final_url
        for pool, label in ((small, "studios/1-bed"), (homes, "homes"), (rooms, "rooms only")):
            if pool:
                cur, vs = max(pool.items(), key=lambda kv: len(kv[1]))
                if len(vs) >= 3:
                    return st.median(vs), cur, f"median of {len(vs)} live {label} listings on {where}"
        return None, None, None

    # ------------------------------------------------------------ enrichment

    def enrichment_jobs(self, engine, entity: AcqEntity) -> list[EnrichmentJobSpec]:
        jobs: list[EnrichmentJobSpec] = []
        urls: dict[str, str] = {}
        for m in engine.db.scalars(select(AcqMention).where(AcqMention.entity_id == entity.id)):
            u = (m.links or {}).get("detail_url")
            if u and m.host not in urls and m.url != u:
                urls[m.host] = u
        for host, u in list(urls.items())[:1]:
            jobs.append(self.job("detail", entity.id, {"url": u, "kind": "portal", "verify": False},
                                 f"deep research: listing page on {host}", 1.0))
        if entity.parent_id:
            jobs.append(self.job("geocode", entity.parent_id, {}, f"locate the building ({self.country} public geocoder)",
                                 0.9))
            jobs.append(self.job("centre", entity.parent_id, {"region_id": entity.region_id},
                                 "mobility proxy: distance to the city centre", 0.8))
        jobs.append(self.job("move_in", entity.id, {}, "is the move-in date actually feasible?", 0.5))
        return jobs

    def job_geocode(self, engine, job) -> dict[str, Any]:
        b = engine.db.get(AcqEntity, job.entity_id)
        bb = (b.beliefs or {}) if b else {}
        if (bb.get("coords") or {}).get("value"):
            return {"_status": "skipped", "reason": "listing already carries coordinates"}
        pc = (bb.get("postcode") or {}).get("value")
        addr = (bb.get("address") or {}).get("value")
        if self.country == "GB" and pc:
            url = f"https://api.postcodes.io/postcodes/{quote(str(pc))}"
            data = engine.fetcher.get_json(url)
            r = (data or {}).get("result") or {}
            if r.get("latitude"):
                self._add(engine, b.id, "coords", [r["latitude"], r["longitude"]], 0.8, f"postcodes.io {pc}",
                          host="api.postcodes.io", kind="public_data", url=url)
                return {"coords": [r["latitude"], r["longitude"]]}
            return {"_status": "failed", "reason": "postcode not found"}
        if self.country == "US" and addr:
            url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address="
                   f"{quote(addr)}&benchmark=Public_AR_Current&format=json")
            data = engine.fetcher.get_json(url)
            ms = ((data or {}).get("result") or {}).get("addressMatches") or []
            if ms:
                c = ms[0]["coordinates"]
                self._add(engine, b.id, "coords", [c["y"], c["x"]], 0.8, f"US Census: {ms[0].get('matchedAddress')}",
                          host="geocoding.geo.census.gov", kind="public_data", url=url)
                return {"coords": [c["y"], c["x"]]}
            return {"_status": "failed", "reason": "address not matched"}
        return {"_status": "skipped", "reason": f"no street-level public geocoder known for {self.country}"}

    def job_centre(self, engine, job) -> dict[str, Any]:
        engine.refresh_dirty()
        b = engine.db.get(AcqEntity, job.entity_id)
        c = (((b.beliefs if b else {}) or {}).get("coords") or {}).get("value")
        reg = engine.db.get(AcqEntity, job.params.get("region_id") or "") if job.params.get("region_id") else None
        rc = (((reg.beliefs if reg else {}) or {}).get("region_coords") or {}).get("value")
        if not c or not rc:
            return {"_status": "skipped", "reason": "no building or centre coordinates"}
        km = round(haversine_km(c, rc), 2)
        self._add(engine, b.id, "centre_km", km, 0.8, f"straight-line distance to the geocoded centre of {reg.label}",
                  host="regent", kind="derived")
        return {"centre_km": km}


def candidate_from(host: str, channel: str) -> Candidate:
    return Candidate(host, f"https://{host}/", channel)
