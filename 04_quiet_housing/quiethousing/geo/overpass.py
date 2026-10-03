"""OpenStreetMap data via Overpass, fetched as cached fixed-grid tiles.

Why tiles: listings cluster heavily (hundreds of rooms within a few km²), so
a fixed grid turns N per-listing queries into a small set of reusable tile
queries. A tile is ~2.2 x 2.25 km; a listing needs the tiles intersecting a
~1.2 km disc around it (1-4 tiles). Tiles are cached forever on disk
(gzip JSON) under a per-layer version, so re-runs make zero Overpass calls.

Layers are independent queries with their own version. Adding a future layer
(parks, libraries, buildings, ...) adds a query without invalidating the
existing caches.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Iterable

from ..http import FetchError, Fetcher

log = logging.getLogger(__name__)

TILE_DLAT = 0.02
TILE_DLON = 0.025

ROAD_CLASSES = (
    "motorway|trunk|primary|secondary|tertiary|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
    "|unclassified|residential|living_street|service"
)
RAIL_KINDS = "rail|light_rail|subway|tram|monorail|narrow_gauge|funicular"

LAYERS: dict[str, str] = {
    # roads/rails need node ids (intersection topology) + full geometry
    "transport": f"""
(
  way[highway~"^({ROAD_CLASSES})$"];
  way[railway~"^({RAIL_KINDS})$"];
);
out body geom qt;
(
  node[railway~"^(station|halt)$"];
  node[highway=traffic_signals];
);
out body qt;
""",
    # activity: POIs as points (ways/relations -> center), landuse polygons with geometry
    "activity": """
(
  nwr[amenity~"^(restaurant|cafe|fast_food|food_court|ice_cream|biergarten|bar|pub|nightclub|karaoke_box|stripclub|love_hotel|gambling|casino|hookah_lounge|cinema|theatre|events_venue)$"];
  nwr[leisure~"^(adult_gaming_centre|amusement_arcade)$"];
  nwr[shop][shop!~"^(vacant|no)$"];
);
out tags center qt;
(
  way[landuse~"^(commercial|retail|industrial|residential)$"];
  relation[landuse~"^(commercial|retail|industrial|residential)$"][type=multipolygon];
);
out geom qt;
""",
}


def layer_version(layer: str) -> str:
    return hashlib.sha1(LAYERS[layer].encode()).hexdigest()[:10]


def tile_of(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / TILE_DLAT), math.floor(lon / TILE_DLON)


def tile_bbox(tile: tuple[int, int]) -> tuple[float, float, float, float]:
    ty, tx = tile
    return (round(ty * TILE_DLAT, 6), round(tx * TILE_DLON, 6), round((ty + 1) * TILE_DLAT, 6), round((tx + 1) * TILE_DLON, 6))


def deg_radius(lat: float, radius_m: float) -> tuple[float, float]:
    dlat = radius_m / 110_574.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    return dlat, dlon


def tiles_for(lat: float, lon: float, radius_m: float) -> list[tuple[int, int]]:
    """All grid tiles intersecting the bbox of a disc of radius_m around (lat, lon)."""
    dlat, dlon = deg_radius(lat, radius_m)
    y0, x0 = tile_of(lat - dlat, lon - dlon)
    y1, x1 = tile_of(lat + dlat, lon + dlon)
    return [(y, x) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


class OverpassTileCache:
    def __init__(self, cache_dir: str | Path, fetcher: Fetcher | None, endpoints: list[str], interval_s: float = 2.0):
        self.cache_dir = Path(cache_dir)
        self.fetcher = fetcher
        self.endpoints = list(endpoints)
        self.interval_s = interval_s
        self.network_queries = 0
        self._mem: dict[tuple[str, tuple[int, int]], dict[str, Any]] = {}
        self._mem_order: list[tuple[str, tuple[int, int]]] = []
        self.mem_limit = 48
        self._dead: dict[str, int] = {}

    def path(self, layer: str, tile: tuple[int, int]) -> Path:
        return self.cache_dir / f"{layer}-{layer_version(layer)}" / f"{tile[0]}_{tile[1]}.json.gz"

    def has(self, layer: str, tile: tuple[int, int]) -> bool:
        return self.path(layer, tile).exists()

    def get(self, layer: str, tile: tuple[int, int], allow_fetch: bool = True) -> dict[str, Any]:
        key = (layer, tile)
        if key in self._mem:
            return self._mem[key]
        p = self.path(layer, tile)
        if p.exists():
            with gzip.open(p, "rt", encoding="utf-8") as f:
                data = json.load(f)
        else:
            if not allow_fetch or self.fetcher is None:
                raise FetchError(f"tile {layer}{tile} not cached and fetching disabled")
            data = self._fetch(layer, tile)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            tmp.replace(p)
        self._mem[key] = data
        self._mem_order.append(key)
        if len(self._mem_order) > self.mem_limit:
            old = self._mem_order.pop(0)
            self._mem.pop(old, None)
        return data

    def _query_bbox(self, layer: str, bbox: tuple[float, float, float, float]) -> tuple[list[dict[str, Any]], str | None]:
        """One bbox query against the first healthy endpoint. Unreachable endpoints are
        disabled for the session (a blocked mirror otherwise costs minutes per tile)."""
        s, w, n, e = bbox
        q = f"[out:json][timeout:180][maxsize:536870912][bbox:{s},{w},{n},{e}];" + LAYERS[layer]
        errors = []
        for ep in list(self.endpoints) * 2:  # two rounds; the fetcher itself does not retry
            if self._dead.get(ep, 0) >= 1:
                continue
            try:
                self.network_queries += 1
                t0 = time.time()
                resp = self.fetcher.request("POST", ep, data={"data": q}, interval_s=self.interval_s, timeout=240)
                if resp.status_code != 200:
                    raise FetchError(f"HTTP {resp.status_code}")
                data = resp.json()
                remark = data.get("remark") or ""
                if "runtime error" in remark or "Query timed out" in remark or "out of memory" in remark:
                    raise FetchError(f"overpass remark: {remark[:200]}")
                if "elements" not in data:
                    raise FetchError("no elements key")
                self._dead[ep] = 0
                log.info("overpass %s %s: %d elements in %.1fs via %s", layer, bbox, len(data["elements"]), time.time() - t0, ep)
                return data["elements"], data.get("osm3s", {}).get("timestamp_osm_base")
            except (FetchError, ValueError) as e:
                errors.append(f"{ep}: {e}")
                # HTTP-level answers (504, remark) mean the server is alive but the query too heavy
                if "HTTP" not in str(e) and "remark" not in str(e):
                    self._dead[ep] = self._dead.get(ep, 0) + 1
                else:
                    time.sleep(3)
                log.warning("overpass %s %s failed at %s: %s", layer, bbox, ep, e)
        raise FetchError(f"all overpass endpoints failed for {layer}{bbox}: {errors}")

    def _fetch_split(self, layer: str, bbox: tuple[float, float, float, float], depth: int) -> tuple[list[dict[str, Any]], str | None]:
        try:
            return self._query_bbox(layer, bbox)
        except FetchError:
            if depth <= 0:
                raise
        # too heavy for the server: split into quadrants and merge (dedupe by type/id)
        s, w, n, e = bbox
        ms, mw = round((s + n) / 2, 6), round((w + e) / 2, 6)
        seen, out, base = set(), [], None
        for sub in ((s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e)):
            els, base = self._fetch_split(layer, sub, depth - 1)
            for el in els:
                k = (el.get("type"), el.get("id"))
                if k not in seen:
                    seen.add(k)
                    out.append(el)
        return out, base

    def _fetch(self, layer: str, tile: tuple[int, int]) -> dict[str, Any]:
        els, base = self._fetch_split(layer, tile_bbox(tile), depth=2)
        return {"tile": list(tile), "bbox": tile_bbox(tile), "layer": layer, "version": layer_version(layer),
                "osm_base": base, "fetched_at": time.time(), "elements": els}

    def get_many(self, layer: str, tiles: Iterable[tuple[int, int]], allow_fetch: bool = True) -> list[dict[str, Any]]:
        return [self.get(layer, t, allow_fetch) for t in tiles]
