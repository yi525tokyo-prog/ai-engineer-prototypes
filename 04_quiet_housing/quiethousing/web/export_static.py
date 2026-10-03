"""Export results as a self-contained static site (no server needed).

  data/site/index.html          the viewer (copied from static/report.html)
  data/site/data.json           every evaluated listing: property, measurements, evaluation
  data/site/features/<g>.json   nearby roads/rails/POIs/junctions per location, grouped by tile

The static viewer filters, sorts and inspects; crawling and threshold
changes still need `python -m quiethousing run` (or the local server UI).
"""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..geo.features import FeatureSet, parse_elements
from ..geo.metrics import major_junctions
from ..geo.overpass import LAYERS, tile_of, tiles_for
from ..http import FetchError
from ..pipeline import Context, flat_row, results
from ..store import loc_key

STATIC = Path(__file__).parent / "static"
RADIUS = 450


def _rc(coords) -> list[list[float]]:
    return [[round(x, 5), round(y, 5)] for x, y in coords]


def location_features(fs: FeatureSet, lat: float, lon: float) -> dict[str, Any]:
    dlat, dlon = RADIUS / 110574, RADIUS / (111320 * 0.81)
    from shapely.geometry import box

    win = box(lon - dlon, lat - dlat, lon + dlon, lat + dlat)
    roads = [{"c": r.cls, "n": r.name or r.ref, "s": int(r.surface), "e": int(r.elevated), "g": _rc(r.geom.intersection(win).coords)}
             for r in fs.roads if r.cls in ("motorway", "trunk", "primary", "secondary", "tertiary") and r.geom.intersects(win)
             and r.geom.intersection(win).geom_type == "LineString"]
    rails = [{"n": r.name, "s": int(r.surface), "l": int(r.light), "g": _rc(r.geom.intersection(win).coords)}
             for r in fs.rails if not r.minor and r.geom.intersects(win) and r.geom.intersection(win).geom_type == "LineString"]
    pois = [[round(p.lon, 5), round(p.lat, 5), "n" if "nightlife" in p.categories else "f" if "food" in p.categories else "r", p.name or p.kind]
            for p in fs.pois if abs(p.lat - lat) < dlat and abs(p.lon - lon) < dlon]
    return {"roads": roads, "rails": rails, "pois": pois}


def export_site(ctx: Context, out_dir: Path | None = None) -> Path:
    out = Path(out_dir or ctx.data_dir / "site")
    if out.exists():
        shutil.rmtree(out)
    (out / "features").mkdir(parents=True)
    rows = results(ctx)
    data = []
    groups: dict[tuple[int, int], dict[str, tuple[float, float]]] = defaultdict(dict)
    for r in rows:
        f = flat_row(r)
        p = r["property"]
        f.update({
            "id": p["estate_id"], "title": p.get("title"), "total_rent": p.get("total_rent"), "stations": p.get("stations"),
            "floor_source": p.get("floor_source"), "structure": p.get("structure"), "city": p.get("city"),
            "reasons_list": r["evaluation"].get("reasons") or [], "warnings_list": r["evaluation"].get("warnings") or [],
            "score_notes": r["evaluation"].get("score_notes") or {}, "env": r["environment"],
        })
        if f.get("env"):
            f["env"] = {k: v for k, v in f["env"].items() if k not in ("tiles",)}
        if p.get("lat") is not None:
            t = tile_of(p["lat"], p["lon"])
            f["fg"] = f"{t[0]}_{t[1]}"
            f["fk"] = loc_key(p["lat"], p["lon"])
            groups[t][f["fk"]] = (p["lat"], p["lon"])
        data.append(f)
    for t, locs in groups.items():
        fs, seen = FeatureSet(), set()
        tiles = {tt for lat, lon in locs.values() for tt in tiles_for(lat, lon, RADIUS + 50)}
        for layer in LAYERS:
            for tt in tiles:
                try:
                    parse_elements(ctx.tiles.get(layer, tt, allow_fetch=False)["elements"], fs, seen)
                except FetchError:
                    pass
        junc = major_junctions(fs.roads)
        payload = {}
        for k, (lat, lon) in locs.items():
            lf = location_features(fs, lat, lon)
            dlat, dlon = RADIUS / 110574, RADIUS / (111320 * 0.81)
            lf["junctions"] = [[round(j["lon"], 5), round(j["lat"], 5), " × ".join(j["roads"])] for j in junc
                               if abs(j["lat"] - lat) < dlat and abs(j["lon"] - lon) < dlon]
            payload[k] = lf
        (out / "features" / f"{t[0]}_{t[1]}.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    run = ctx.store.last_run()
    (out / "data.json").write_text(json.dumps({"rows": data, "last_run": run, "constraints": ctx.cfg["constraints"],
                                               "rules": ctx.cfg["evaluation"]["hard_rules"], "min_quality": ctx.cfg["evaluation"]["min_quality"]},
                                              ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    shutil.copy(STATIC / "report.html", out / "index.html")
    return out
