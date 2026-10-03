"""Build the OSM tile cache from a local .osm.pbf extract instead of Overpass.

Public Overpass instances are rate limited and often overloaded; for hundreds
or thousands of listings a regional extract (e.g. Kanto, ~600 MB) is both
faster and more reliable. This importer selects exactly the features the
Overpass layers select (shared constants in overpass.py) and writes them as
Overpass-shaped elements into the same per-layer tile files, so everything
downstream is identical regardless of the source.

Pass 1 reads only relations: multipolygon landuse relations -> member way ids.
Pass 2 reads nodes + ways with a node-location index: tagged nodes (POIs,
stations, signals), roads/rails (full geometry + node ids), POI ways
(centroid), landuse ways and landuse-relation member ways.
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .overpass import (
    LANDUSE,
    LAYERS,
    POI_AMENITY,
    POI_LEISURE,
    RAIL_KINDS,
    ROAD_HIGHWAYS,
    SHOP_EXCLUDE,
    OverpassTileCache,
    layer_version,
    tile_of,
)

log = logging.getLogger(__name__)

_ROADS, _RAILS, _AMEN, _LEIS, _LANDUSE = set(ROAD_HIGHWAYS), set(RAIL_KINDS), set(POI_AMENITY), set(POI_LEISURE), set(LANDUSE)


def is_poi(tags: dict[str, str]) -> bool:
    shop = tags.get("shop")
    return tags.get("amenity") in _AMEN or tags.get("leisure") in _LEIS or (shop is not None and shop not in SHOP_EXCLUDE)


def classify(tags: dict[str, str], kind: str) -> list[str]:
    """Which layers an OSM object with these tags belongs to (mirrors the Overpass queries)."""
    layers = []
    if kind == "way" and (tags.get("highway") in _ROADS or tags.get("railway") in _RAILS):
        layers.append("transport")
    if kind == "node" and (tags.get("railway") in ("station", "halt") or tags.get("highway") == "traffic_signals"):
        layers.append("transport")
    if is_poi(tags) or (kind == "way" and tags.get("landuse") in _LANDUSE):
        layers.append("activity")
    return layers


def coverage_tiles(pbf_path: str) -> set[tuple[int, int]] | None:
    """Tiles fully inside the extract's header bbox (None if the file declares no bbox).
    Tiles on the edge would be silently incomplete, so they are left to Overpass."""
    import osmium

    box = osmium.io.Reader(pbf_path, osmium.osm.NOTHING).header().box()
    if not box.valid():
        return None
    bl, tr = box.bottom_left, box.top_right
    y0, x0 = tile_of(bl.lat, bl.lon)
    y1, x1 = tile_of(tr.lat, tr.lon)
    return {(y, x) for y in range(y0 + 1, y1) for x in range(x0 + 1, x1)}


def _tiles_of_bbox(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> list[tuple[int, int]]:
    y0, x0 = tile_of(min_lat, min_lon)
    y1, x1 = tile_of(max_lat, max_lon)
    return [(y, x) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


class TileSink:
    """Collects elements per (layer, tile); only wanted tiles are kept (None = all)."""

    def __init__(self, wanted: set[tuple[int, int]] | None):
        self.wanted = wanted
        self.data: dict[tuple[str, tuple[int, int]], list[dict[str, Any]]] = defaultdict(list)

    def add(self, layer: str, el: dict[str, Any], tiles: Iterable[tuple[int, int]]) -> None:
        for t in tiles:
            if self.wanted is None or t in self.wanted:
                self.data[(layer, t)].append(el)

    def write(self, cache: OverpassTileCache, source: str, all_tiles: Iterable[tuple[int, int]]) -> int:
        n = 0
        now = time.time()
        for t in all_tiles:
            for layer in LAYERS:
                p = cache.path(layer, t)
                p.parent.mkdir(parents=True, exist_ok=True)
                payload = {"tile": list(t), "layer": layer, "version": layer_version(layer), "osm_base": source,
                           "fetched_at": now, "source": "pbf", "elements": self.data.get((layer, t), [])}
                tmp = p.with_suffix(".tmp")
                with gzip.open(tmp, "wt", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
                tmp.replace(p)
                n += 1
        return n


def parse_poly(text: str):
    """Osmosis .poly boundary file -> shapely (Multi)Polygon. Sections starting with '!' are holes."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    lines = [ln.strip() for ln in text.splitlines()]
    outers, holes, i = [], [], 1
    while i < len(lines):
        name = lines[i]
        if name == "END" or not name:
            i += 1
            continue
        pts, i = [], i + 1
        while i < len(lines) and lines[i] != "END":
            x, y = lines[i].split()[:2]
            pts.append((float(x), float(y)))
            i += 1
        i += 1
        if len(pts) >= 3:
            (holes if name.startswith("!") else outers).append(Polygon(pts).buffer(0))
    shape = unary_union(outers)
    return shape.difference(unary_union(holes)) if holes else shape


def tiles_inside(tiles: Iterable[tuple[int, int]], coverage) -> set[tuple[int, int]]:
    from shapely.geometry import box

    from .overpass import tile_bbox

    out = set()
    for t in tiles:
        s, w, n, e = tile_bbox(t)
        if coverage.contains(box(w, s, e, n)):
            out.add(t)
    return out


def import_pbf(pbf_path: str | Path, cache: OverpassTileCache, wanted: set[tuple[int, int]] | None, coverage=None) -> dict[str, Any]:
    """Populate tile cache for `wanted` tiles (None = every tile touched by the extract).

    `coverage` (shapely geometry, lon/lat) is the extract boundary: wanted tiles not
    fully inside it are skipped (they would be silently incomplete) and left to Overpass.
    """
    import osmium

    t0 = time.time()
    pbf_path = str(pbf_path)
    if coverage is not None and wanted is not None:
        wanted = tiles_inside(wanted, coverage)
    cov = coverage_tiles(pbf_path)
    if cov is not None:
        wanted = (wanted & cov) if wanted is not None else None
        if wanted is not None and not wanted:
            return {"tile_files": 0, "note": "no wanted tile inside extract coverage"}
    # ---- pass 1: landuse multipolygon relations
    rel_members: dict[int, list[tuple[int, str]]] = {}
    rel_tags: dict[int, dict[str, str]] = {}
    for r in osmium.FileProcessor(pbf_path, osmium.osm.RELATION).with_filter(osmium.filter.KeyFilter("landuse")):
        tags = {t.k: t.v for t in r.tags}
        if tags.get("landuse") in _LANDUSE and tags.get("type") == "multipolygon":
            rel_members[r.id] = [(m.ref, m.role) for m in r.members if m.type == "w"]
            rel_tags[r.id] = tags
    member_ways = {wid for ms in rel_members.values() for wid, _ in ms}
    member_geom: dict[int, list[dict[str, float]]] = {}
    log.info("pbf pass 1: %d landuse relations, %d member ways (%.0fs)", len(rel_tags), len(member_ways), time.time() - t0)

    sink = TileSink(wanted)
    stats = defaultdict(int)
    # ---- pass 2: nodes + ways with locations
    fp = osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY).with_locations()
    for o in fp:
        if o.is_node():
            if not o.tags:
                continue
            tags = {t.k: t.v for t in o.tags}
            layers = classify(tags, "node")
            if not layers or not o.location.valid():
                continue
            lat, lon = o.location.lat, o.location.lon
            el = {"type": "node", "id": o.id, "lat": lat, "lon": lon, "tags": tags}
            for layer in layers:
                sink.add(layer, el, [tile_of(lat, lon)])
                stats[f"node_{layer}"] += 1
            continue
        # ways
        wid = o.id
        is_member = wid in member_ways
        if not o.tags and not is_member:
            continue
        tags = {t.k: t.v for t in o.tags} if o.tags else {}
        layers = classify(tags, "way")
        if not layers and not is_member:
            continue
        nodes, geom = [], []
        for n in o.nodes:
            if n.location.valid():
                nodes.append(n.ref)
                geom.append({"lat": n.location.lat, "lon": n.location.lon})
        if len(geom) < 2:
            continue
        if is_member:
            member_geom[wid] = geom
        if not layers:
            continue
        lats = [g["lat"] for g in geom]
        lons = [g["lon"] for g in geom]
        tiles = _tiles_of_bbox(min(lats), min(lons), max(lats), max(lons))
        if "transport" in layers:
            sink.add("transport", {"type": "way", "id": wid, "tags": tags, "nodes": nodes, "geometry": geom}, tiles)
            stats["way_transport"] += 1
        if "activity" in layers:
            if tags.get("landuse") in _LANDUSE and not is_poi(tags):
                sink.add("activity", {"type": "way", "id": wid, "tags": tags, "geometry": geom}, tiles)
                stats["way_landuse"] += 1
            else:
                c = {"lat": sum(lats) / len(lats), "lon": sum(lons) / len(lons)}
                sink.add("activity", {"type": "way", "id": wid, "tags": tags, "center": c}, [tile_of(c["lat"], c["lon"])])
                stats["way_poi"] += 1
    # ---- landuse relations with member geometry
    for rid, members in rel_members.items():
        mem = [{"type": "way", "ref": wid, "role": role or "outer", "geometry": member_geom[wid]} for wid, role in members if wid in member_geom]
        if not mem:
            continue
        lats = [g["lat"] for m in mem for g in m["geometry"]]
        lons = [g["lon"] for m in mem for g in m["geometry"]]
        sink.add("activity", {"type": "relation", "id": rid, "tags": rel_tags[rid], "members": mem},
                 _tiles_of_bbox(min(lats), min(lons), max(lats), max(lons)))
        stats["relation_landuse"] += 1
    header = osmium.io.Reader(pbf_path, osmium.osm.NOTHING).header()
    source = f"pbf:{Path(pbf_path).name}:{header.get('osmosis_replication_timestamp', '') or ''}"
    tiles_written = wanted if wanted is not None else {t for (_, t) in sink.data}
    stats["tile_files"] = sink.write(cache, source, sorted(tiles_written))
    stats["seconds"] = round(time.time() - t0, 1)
    log.info("pbf import done: %s", dict(stats))
    return dict(stats)
