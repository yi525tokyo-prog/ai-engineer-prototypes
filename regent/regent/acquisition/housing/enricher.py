"""HousingEnricher: deep research on shortlisted candidates.

Jobs (each produces claims with provenance; none fabricate values):

* ``detail``        fetch each listing's detail page (move-in, update dates,
                    structure, conditions, internet, surroundings, "listing ended")
* ``verify_source`` follow the detail page's original-listing link (情報掲載元) to
                    the managing company's own page and extract it as an
                    *operator* source -- the strongest check on rent/availability
* ``geocode``       GSI address search (Geospatial Information Authority of Japan)
* ``stations``      nearest stations from MLIT National Land Numerical Information
                    (N02 railway data): straight-line distance + walk estimate,
                    which is compared against the listing's advertised walk time
* ``rail_noise``    distance to the nearest railway line (N02 sections)
* ``facilities``    libraries (P27) and universities (P29) within 1.5 km
* ``hubs``          estimated rail minutes to major hubs (derived, low confidence)
* ``move_in``       whether the stated move-in is compatible with the principal's date

Public datasets are downloaded once through the same robots/rate-limit policy.
"""

from __future__ import annotations

import json
import math
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lxml import etree

from regent.acquisition.types import ClaimIn
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


class HousingEnricher:
    network_jobs = ("detail", "verify_source", "geocode", "recheck")

    def __init__(self, adapter):
        self.adapter = adapter

    # ----------------------------------------------------------------- run

    def run(self, engine, job) -> dict[str, Any]:
        fn = getattr(self, f"job_{job.kind}", None)
        if fn is None:
            return {"_status": "skipped", "reason": f"no handler for {job.kind}"}
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
            engine.schedule([self.adapter.job_spec("verify_source", job.entity_id, {"url": source_url},
                                                   "follow the original listing to the managing company", 0.9)])
        return out

    def job_verify_source(self, engine, job) -> dict[str, Any]:
        doc, ents = engine.fetch_and_ingest(job.params["url"], purpose="verify", kind="operator",
                                            pin=job.entity_id, pin_min_p=0.5)
        if doc is None:
            return {"_status": "skipped", "reason": "budget"}
        if not doc.ok:
            return {"_status": "blocked" if doc.blocked else "failed", "blocked": doc.blocked, "status": doc.status}
        return {"host": doc.host, "final_url": doc.final_url, "entities": [e.id for e in ents],
                "verified_same_unit": job.entity_id in [e.id for e in ents]}

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

    def _coords(self, engine, entity_id):
        from regent.acquisition.tables import AcqEntity

        b = engine.db.get(AcqEntity, entity_id)
        c = ((b.beliefs or {}).get("coords") or {}).get("value") if b else None
        return b, (tuple(c) if c else None)

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
