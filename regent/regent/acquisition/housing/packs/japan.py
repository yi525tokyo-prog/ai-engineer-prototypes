"""Japan pack: Japanese portals and operators, GSI geocoding, MLIT National Land Numerical
Information (railways, libraries, universities), Tokyo-area hub estimates, prefecture codes
and the Japanese listing extractor. Nothing here is used outside Japan.
"""

from __future__ import annotations

import json
import re
import math
import statistics
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lxml import etree
from sqlalchemy import select

from regent.acquisition.housing.extractor import HousingExtractor
from regent.acquisition.housing.packs.base import SourcePack
from regent.acquisition.navigate import next_page
from regent.acquisition.tables import AcqEntity, AcqMention, ENTITY_ORDER
from regent.acquisition.types import EnrichmentJobSpec, SourceSpec
from regent.config import settings

N02_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-23/N02-23_GML.zip"
KSJ_POINT_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/{d}/{d}-13/{d}-13_{pref}.zip"
GSI_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch?q={q}"
PREF_CODES = {"東京都": "13", "神奈川県": "14", "埼玉県": "11", "千葉県": "12", "大阪府": "27", "京都府": "26",
              "愛知県": "23", "福岡県": "40", "北海道": "01", "兵庫県": "28"}
HUBS = ("東京", "新宿", "渋谷", "池袋", "品川")
WALK_M_PER_MIN = 80.0
DETOUR = 1.3


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _seg_dist_m(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    # equirectangular projection around p (fine at city scale)
    kx = 111320 * math.cos(math.radians(p[0]))
    ky = 110540
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


class PublicData:
    """Lazy loader for public datasets (downloaded under fetch policy, cached on disk)."""

    _stations: list[dict] | None = None
    _sections: list[list[tuple[float, float]]] | None = None
    _points: dict[str, list[dict]] = {}

    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.dir = settings.workspace / "public_data"

    def _n02(self) -> Path | None:
        return self.fetcher.download(N02_URL, self.dir / "N02-23_GML.zip")

    def stations(self) -> list[dict]:
        if PublicData._stations is None:
            z = self._n02()
            if z is None:
                return []
            d = json.loads(zipfile.ZipFile(z).read("UTF-8/N02-23_Station.geojson"))
            merged: dict[str, dict] = {}
            for f in d["features"]:
                p = f["properties"]
                coords = f["geometry"]["coordinates"]
                lat = sum(c[1] for c in coords) / len(coords)
                lon = sum(c[0] for c in coords) / len(coords)
                name = p["N02_005"]
                key = f"{name}|{round(lat, 2)}|{round(lon, 2)}"
                s = merged.setdefault(key, {"name": name, "lat": lat, "lon": lon, "lines": []})
                s["lines"].append(f"{p['N02_004']} {p['N02_003']}")
            PublicData._stations = list(merged.values())
        return PublicData._stations

    def sections(self) -> list[list[tuple[float, float]]]:
        if PublicData._sections is None:
            z = self._n02()
            if z is None:
                return []
            d = json.loads(zipfile.ZipFile(z).read("UTF-8/N02-23_RailroadSection.geojson"))
            out = []
            for f in d["features"]:
                g = f["geometry"]
                lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
                for ln in lines:
                    out.append([(c[1], c[0]) for c in ln])
            PublicData._sections = out
        return PublicData._sections

    def points(self, dataset: str, pref_code: str) -> list[dict]:
        key = f"{dataset}-{pref_code}"
        if key not in PublicData._points:
            url = KSJ_POINT_URL.format(d=dataset, pref=pref_code)
            z = self.fetcher.download(url, self.dir / f"{dataset}-13_{pref_code}.zip")
            if z is None:
                PublicData._points[key] = []
                return []
            zf = zipfile.ZipFile(z)
            xml_name = next(n for n in zf.namelist() if n.endswith(f"{dataset}-13_{pref_code}.xml"))
            root = etree.fromstring(zf.read(xml_name))
            ns = {"gml": "http://www.opengis.net/gml/3.2", "ksj": "http://nlftp.mlit.go.jp/ksj/schemas/ksj-app",
                  "xlink": "http://www.w3.org/1999/xlink"}
            pos = {}
            for pt in root.iter("{http://www.opengis.net/gml/3.2}Point"):
                gid = pt.get("{http://www.opengis.net/gml/3.2}id")
                p = pt.find("gml:pos", ns)
                if gid and p is not None and p.text:
                    lat, lon = map(float, p.text.split())
                    pos[gid] = (lat, lon)
            out = []
            for el in root:
                name = el.find("ksj:name", ns)
                ref = el.find("ksj:position", ns)
                if name is None or ref is None:
                    continue
                gid = (ref.get("{http://www.w3.org/1999/xlink}href") or "").lstrip("#")
                if gid in pos:
                    out.append({"name": name.text or "", "lat": pos[gid][0], "lon": pos[gid][1]})
            PublicData._points[key] = out
        return PublicData._points[key]


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
JP_HOSTS = {s.host for v in sources("東京都").values() for s in v}
SINGLE_LAYOUTS = {"1R", "1K", "1DK", "1LDK"}
PREFECTURE = re.compile(r"^(東京都|北海道|京都府|大阪府|[^\s]{2,3}県)$")


def layout_bedrooms(layout: str | None) -> int | None:
    """Japanese floor-plan code -> comparable bedroom count (1R/1K = studio)."""


    if not layout:
        return None
    m = re.match(r"^(\d)(S?)(L?)(D?)(K|R)", layout)
    if not m:
        return None
    n = int(m.group(1))
    if m.group(5) == "R" or (n == 1 and not m.group(3) and not m.group(4)):
        return 0
    return n


class JapanPack(SourcePack):
    country = "JP"
    name = "japan"
    languages = ("ja",)
    currency = "JPY"
    default_period = "month"

    def __init__(self, adapter):
        super().__init__(adapter)
        self._xs: dict[str, HousingExtractor] = {}

    def x(self, pref: str | None) -> HousingExtractor:
        """The extractor for one prefecture: addresses and market areas that omit the prefecture
        belong to the region being acquired -- there is no national default."""
        key = pref if pref and PREFECTURE.match(pref) else ""
        if key not in self._xs:
            self._xs[key] = HousingExtractor(default_pref=key)
        return self._xs[key]

    def extractor(self, region=None):
        return self.x((region or {}).get("nav_name") or (region or {}).get("name"))

    def seed_sources(self) -> list[SourceSpec]:
        return sources("{region}")["discovery"]

    def authority_urls(self) -> list[str]:
        return ["https://www.isa.go.jp/en/applications/procedures/index.html"]

    def city_candidates(self, engine) -> list[dict[str, Any]]:
        """Prefectures offered by a national portal's entry page (acquired, not listed here)."""
        from regent.acquisition.navigate import anchors

        doc = engine.fetch("https://suumo.jp/chintai/", purpose="navigate", kind="portal", render="static")
        if doc is None or not doc.ok:
            return []
        names = []
        for text, _u in anchors(doc.html, doc.final_url):
            if PREFECTURE.match(text) and text not in names:
                names.append(text)
        return [{"name": n, "country": "JP", "pack": self.name, "evidence": {"listed_on": doc.final_url}}
                for n in names]

    # ------------------------------------------------------------- discovery

    def discover(self, engine, region: dict[str, Any]) -> dict[str, Any]:
        reg = region.get("nav_name") or region["name"]
        srcs = sources(reg)
        xr = self.x(reg)
        compact = bool(region.get("compact"))
        params = engine.request.params or {}
        engine.log("market", f"[JP] scanning rent markets for {reg}")
        for s in srcs["market"]:
            hints, avoid = NAV_HINTS.get(s.host, {}).get("market", (None, []))
            doc, trail = engine.navigate(s.entry_url, s.nav, kind=s.kind, render=s.render, hints=hints, avoid=avoid)
            if doc is not None and doc.ok:
                ents = engine.ingest(doc, xr.extract(doc, "market"), source_kind=s.kind)
                engine.log("market", f"{s.host}: {len(ents)} market records", trail=trail)
            else:
                engine.log("market", f"{s.host}: unreachable ({_why(doc)})", trail=trail)
        engine.refresh_dirty()
        engine.checkpoint()
        areas, rationale = self.choose_areas(engine, params, reg, int(region.get("max_areas") or params.get("max_areas", 4)))
        engine.log("choose_areas", f"[JP] areas in {reg}: {', '.join(areas)}", rationale=rationale)
        engine.checkpoint()
        for area in areas:
            for s in srcs["discovery"]:
                if not engine.budget_left():
                    break
                terms = [t.replace("{area}", area) for t in s.nav]
                hints, avoid = NAV_HINTS.get(s.host, {}).get("discovery", (None, []))
                doc, trail = engine.navigate(s.entry_url, terms, kind=s.kind, render=s.render, hints=hints, avoid=avoid)
                pages = 0
                max_pages = 1 if compact else s.max_pages
                while doc is not None and doc.ok and pages < max_pages:
                    ms = xr.extract(doc, "discovery")
                    engine.ingest(doc, ms, source_kind=s.kind, region=region)
                    pages += 1
                    engine.log("discovery", f"{s.host} {area} p{pages}: {len(ms)} records", url=doc.final_url)
                    nxt = next_page(doc.html, doc.final_url) if pages < max_pages else None
                    doc = engine.fetch(nxt, purpose="listing", kind=s.kind, render=s.render) if nxt else None
                if pages == 0:
                    engine.log("discovery", f"{s.host} {area}: no listing page ({_why(doc)})", trail=trail)
                engine.refresh_dirty()
                engine.checkpoint()
        return {"areas": areas, "area_rationale": rationale}

    def choose_areas(self, engine, params, region: str, k: int) -> tuple[list[str], str]:
        if params.get("areas"):
            return list(params["areas"]), "principal-specified areas"
        rows = []
        for e in engine.db.scalars(select(AcqEntity).where(AcqEntity.domain == self.adapter.name,
                                                           AcqEntity.entity_type == "area").order_by(*ENTITY_ORDER)):
            b = e.beliefs or {}
            rent = (b.get("market_rent_1k_1dk") or b.get("market_rent_1r") or {}).get("value")
            name = (e.features or {}).get("area", "")
            if rent and name.startswith(region):
                rows.append((float(rent), name[len(region):]))
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
        chosen: list[str] = []
        for r in cheap + around:
            if r[1] not in chosen:
                chosen.append(r[1])
            if len(chosen) >= k:
                break
        why = (f"{policy}; 1K/1DK market rents from live data; two most affordable "
               f"({', '.join(f'{a} {int(r):,}' for r, a in cheap)}) plus areas nearest the median ({int(med):,})"
               + (f"; within budget {int(budget):,}" if budget else "; budget unknown"))
        return chosen, why

    def price_signal(self, engine, cand: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
        """The national portal's rent-market table for the prefecture (1K/1DK median across areas)."""
        reg = cand.get("nav_name") or cand["name"]
        s = sources(reg)["market"][0]
        xr = self.x(reg)
        hints, avoid = NAV_HINTS.get(s.host, {}).get("market", (None, []))
        doc, trail = engine.navigate(s.entry_url, s.nav, kind=s.kind, render=s.render, hints=hints, avoid=avoid)
        if doc is None or not doc.ok:
            return None, None, None
        ms = xr.extract(doc, "market")
        engine.ingest(doc, ms, source_kind=s.kind)
        vals = [c.value for m in ms for c in m.claims if c.attribute == "market_rent_1k_1dk" and c.value]
        if not vals:
            return None, None, None
        return statistics.median(vals), "JPY", f"median 1K/1DK market rent over {len(vals)} areas on {doc.final_url}"

    # ------------------------------------------------------------ enrichment

    def enrichment_jobs(self, engine, entity: AcqEntity) -> list[EnrichmentJobSpec]:
        jobs: list[EnrichmentJobSpec] = []
        urls: dict[str, str] = {}
        for m in engine.db.scalars(select(AcqMention).where(AcqMention.entity_id == entity.id).order_by(AcqMention.observed_at, AcqMention.url)):
            u = (m.links or {}).get("detail_url")
            if u and m.host not in urls and m.url != u:
                urls[m.host] = u
        for host, u in list(urls.items())[:2]:
            jobs.append(self.job("detail", entity.id, {"url": u, "kind": "portal"},
                                 f"deep research: detail page on {host}", 1.0))
        if entity.parent_id:
            b = entity.parent_id
            jobs += [self.job("geocode", b, {}, "locate the building (GSI)", 0.95),
                     self.job("stations", b, {}, "nearest stations from public railway data", 0.9),
                     self.job("rail_noise", b, {}, "train-noise proxy: distance to rail line", 0.85),
                     self.job("facilities", b, {}, "libraries and universities nearby (public data)", 0.8),
                     self.job("hubs", b, {}, "commute estimate to major hubs (work location unknown)", 0.75)]
        jobs.append(self.job("move_in", entity.id, {}, "is the move-in date actually feasible?", 0.5))
        return jobs

    def view_extras(self, b: dict, bb: dict) -> dict[str, Any]:
        def v(d, k):
            return (d.get(k) or {}).get("value")

        stations = v(bb, "stations") or []
        return {"walk_min": min((s["walk_min"] for s in stations), default=None),
                "station": min(stations, key=lambda s: s["walk_min"])["station"] if stations else None,
                "bedrooms": layout_bedrooms(v(b, "layout")), "single_ok": (v(b, "layout") in SINGLE_LAYOUTS
                                                                          if v(b, "layout") else None)}

    def job_geocode(self, engine, job) -> dict[str, Any]:
        from regent.acquisition.tables import AcqEntity

        b = engine.db.get(AcqEntity, job.entity_id)
        addr = ((b.beliefs or {}).get("address") or {}).get("value") if b else None
        if not addr:
            return {"_status": "skipped", "reason": "no address belief"}
        data = engine.fetcher.get_json(GSI_URL.format(q=quote(addr)))
        if not data:
            return {"_status": "failed", "reason": "geocoder returned nothing"}
        lon, lat = data[0]["geometry"]["coordinates"]
        title = data[0]["properties"].get("title", "")
        # chome-level precision: confidence reflects how much of the address matched
        precision = "chome" if re.search(r"丁目|\d", addr) else "town"
        self._add(engine, b.id, "coords", [round(lat, 6), round(lon, 6)], 0.85 if precision == "chome" else 0.6,
                  f"GSI: {title}", host="msearch.gsi.go.jp", kind="public_data",
                  url=GSI_URL.format(q=quote(addr)))
        b.features = {**(b.features or {}), "coords": [lat, lon]}
        return {"coords": [lat, lon], "matched": title, "precision": precision}

    def job_stations(self, engine, job) -> dict[str, Any]:
        engine.refresh_dirty()
        b, c = self._coords(engine, job.entity_id)
        if c is None:
            return {"_status": "skipped", "reason": "not geocoded"}
        st = PublicData(engine.fetcher).stations()
        if not st:
            return {"_status": "failed", "reason": "station dataset unavailable"}
        near = sorted(((haversine_m(c, (s["lat"], s["lon"])), s) for s in st
                       if abs(s["lat"] - c[0]) < 0.03 and abs(s["lon"] - c[1]) < 0.04), key=lambda x: x[0])[:3]
        out = [{"station": s["name"], "distance_m": round(d), "walk_min_est": math.ceil(d * DETOUR / WALK_M_PER_MIN),
                "lines": sorted(set(s["lines"]))[:4]} for d, s in near]
        self._add(engine, b.id, "nearest_stations_public", out, 0.8,
                  "MLIT N02 railway data, straight-line distance x1.3 detour at 80 m/min",
                  host="nlftp.mlit.go.jp", kind="public_data", url=N02_URL)
        # cross-check the advertised walk time for the same station (a discrepancy is a claim, not an edit)
        adv = {s["station"]: s["walk_min"] for s in (((b.beliefs or {}).get("stations") or {}).get("value") or [])}
        checks = []
        for s in out:
            if s["station"] in adv:
                checks.append({"station": s["station"], "advertised": adv[s["station"]], "estimated": s["walk_min_est"]})
        if checks:
            self._add(engine, b.id, "walk_check", checks, 0.7, "advertised vs public-data estimate",
                      host="regent", kind="derived")
        return {"nearest": out, "walk_checks": checks}

    def job_rail_noise(self, engine, job) -> dict[str, Any]:
        engine.refresh_dirty()
        b, c = self._coords(engine, job.entity_id)
        if c is None:
            return {"_status": "skipped", "reason": "not geocoded"}
        best = float("inf")
        for sec in PublicData(engine.fetcher).sections():
            if not any(abs(p[0] - c[0]) < 0.01 and abs(p[1] - c[1]) < 0.012 for p in sec[:: max(1, len(sec) // 4)] + [sec[-1]]):
                continue
            for a, z in zip(sec, sec[1:]):
                best = min(best, _seg_dist_m(c, a, z))
        if best == float("inf"):
            best = 1500.0
        self._add(engine, b.id, "rail_distance_m", round(best), 0.7,
                  "distance to nearest railway line (MLIT N02); <100 m suggests train noise",
                  host="nlftp.mlit.go.jp", kind="public_data", url=N02_URL)
        return {"rail_distance_m": round(best)}

    def job_facilities(self, engine, job) -> dict[str, Any]:
        engine.refresh_dirty()
        b, c = self._coords(engine, job.entity_id)
        if c is None:
            return {"_status": "skipped", "reason": "not geocoded"}
        addr = ((b.beliefs or {}).get("address") or {}).get("value") or ""
        pref = next((p for p in PREF_CODES if addr.startswith(p)), None)
        if pref is None:
            return {"_status": "skipped", "reason": "prefecture dataset not mapped"}
        pd = PublicData(engine.fetcher)
        res = {}
        for dataset, word, attr in (("P27", "図書館", "libraries_nearby"), ("P29", "大学", "universities_nearby")):
            pts = pd.points(dataset, PREF_CODES[pref])
            near = sorted((haversine_m(c, (p["lat"], p["lon"])), p["name"]) for p in pts if word in p["name"])
            near = [{"name": n, "distance_m": round(d)} for d, n in near if d <= 1500][:5]
            self._add(engine, b.id, attr, near, 0.8, f"MLIT {dataset} ({word}) within 1.5 km",
                      host="nlftp.mlit.go.jp", kind="public_data", url=KSJ_POINT_URL.format(d=dataset, pref=PREF_CODES[pref]))
            res[attr] = len(near)
        return res

    def job_hubs(self, engine, job) -> dict[str, Any]:
        engine.refresh_dirty()
        b, c = self._coords(engine, job.entity_id)
        if c is None:
            return {"_status": "skipped", "reason": "not geocoded"}
        st = PublicData(engine.fetcher).stations()
        hubs = {}
        for h in HUBS:
            cands = [s for s in st if s["name"] == h]
            if not cands:
                continue
            s = min(cands, key=lambda s: haversine_m(c, (s["lat"], s["lon"])))
            d = haversine_m(c, (s["lat"], s["lon"]))
            # door-to-door estimate: walk to a station + rail at ~28 km/h effective + 1 transfer if far
            minutes = round(10 + d * 1.25 / (28000 / 60) + (6 if d > 6000 else 0))
            hubs[h] = minutes
        self._add(engine, b.id, "hub_minutes_est", hubs, 0.45,
                  "estimate: straight line x1.25 at 28 km/h effective rail + 10 min access (+6 transfer if >6 km)",
                  host="regent", kind="derived")
        return {"hub_minutes_est": hubs}


def _why(doc) -> str:
    if doc is None:
        return "budget"
    if doc.blocked:
        return f"blocked: {doc.blocked.get('type')} {doc.blocked.get('detail', '')}".strip()
    return f"HTTP {doc.status}" + (f" {doc.error}" if doc.error else "")
