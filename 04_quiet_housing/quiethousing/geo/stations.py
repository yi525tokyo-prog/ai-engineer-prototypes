"""Station scale from MLIT 国土数値情報 S12 (駅別乗降客数, daily riders per station).

OSM tells us where stations are but not how busy they are. S12 is the
official national dataset: one LineString per (station, line) with yearly
ridership. Records sharing a group code (S12_001g) form one transfer
complex (e.g. all of Shinjuku). Ridership double-counted under another
operator is marked with a duplicate code (2) and zero riders, so summing
the non-duplicate records of a group gives the complex total.

The 6 MB zip is downloaded once and reduced to a compact JSON cache.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, MultiLineString
from shapely.strtree import STRtree

from ..http import Fetcher

log = logging.getLogger(__name__)
S12_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/S12/S12-24/S12-24_GML.zip"
N_YEARS = 13  # S12-24: fields S12_006..S12_057 are 13 yearly groups of 4


def latest_passengers(props: dict[str, Any]) -> tuple[int, int | None]:
    """Latest non-duplicate ridership in a S12 record -> (riders, year_index)."""
    for k in range(N_YEARS - 1, -1, -1):
        dup = props.get(f"S12_{6 + 4 * k:03d}")
        has = props.get(f"S12_{7 + 4 * k:03d}")
        val = props.get(f"S12_{9 + 4 * k:03d}")
        if dup == 1 and has == 1 and isinstance(val, (int, float)) and val > 0:
            return int(val), k
    return 0, None


def build_complexes(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"names": set(), "lines": set(), "passengers": 0, "segments": []})
    for f in features:
        p = f["properties"]
        g = groups[p.get("S12_001g") or p.get("S12_001c")]
        g["names"].add(p.get("S12_001"))
        g["lines"].add(f"{p.get('S12_002')} {p.get('S12_003')}")
        g["passengers"] += latest_passengers(p)[0]
        geom = f.get("geometry") or {}
        if geom.get("type") == "LineString":
            g["segments"].append(geom["coordinates"])
        elif geom.get("type") == "MultiLineString":
            g["segments"].extend(geom["coordinates"])
    out = []
    for code, g in groups.items():
        if not g["segments"]:
            continue
        out.append({"code": code, "name": "/".join(sorted(n for n in g["names"] if n)), "lines": sorted(g["lines"]),
                    "passengers": g["passengers"], "segments": g["segments"]})
    return out


@dataclass
class StationComplex:
    code: str
    name: str
    lines: list[str]
    passengers: int
    geom: MultiLineString


class StationIndex:
    def __init__(self, complexes: list[dict[str, Any]]):
        self.items: list[StationComplex] = []
        for c in complexes:
            segs = [s for s in c["segments"] if len(s) >= 2]
            if not segs:
                continue
            self.items.append(StationComplex(c["code"], c["name"], c["lines"], int(c["passengers"]), MultiLineString([LineString(s) for s in segs])))
        self.tree = STRtree([s.geom for s in self.items]) if self.items else None

    @classmethod
    def load(cls, cache_dir: str | Path, fetcher: Fetcher | None) -> "StationIndex":
        cache = Path(cache_dir) / "s12_station_complexes.json"
        if not cache.exists():
            if fetcher is None:
                log.warning("S12 station data not cached and no fetcher; station scale unavailable")
                return cls([])
            log.info("downloading MLIT S12 station ridership (%s)", S12_URL)
            resp = fetcher.request("GET", S12_URL, interval_s=0, timeout=300)
            resp.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            name = next(n for n in zf.namelist() if n.startswith("UTF-8/") and n.endswith(".geojson"))
            features = json.loads(zf.read(name).decode("utf-8"))["features"]
            complexes = build_complexes(features)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(complexes, ensure_ascii=False), encoding="utf-8")
        return cls(json.loads(cache.read_text(encoding="utf-8")))

    def candidates(self, lon0: float, lat0: float, dlon: float, dlat: float) -> list[StationComplex]:
        if self.tree is None:
            return []
        from shapely.geometry import box

        idx = self.tree.query(box(lon0 - dlon, lat0 - dlat, lon0 + dlon, lat0 + dlat))
        return [self.items[i] for i in idx]
