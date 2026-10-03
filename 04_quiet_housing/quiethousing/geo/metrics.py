"""Quantitative environmental measurements around a point.

`FeatureIndex` wraps a FeatureSet (all OSM features of a group of tiles) with
STR-trees and precomputed topology (major-road junctions). `measure()` then
computes a nested, JSON-serialisable profile for one location:

  road.*          nearest surface distance per road class, major-road length in radius
  rail.*          nearest surface railway, parallel-track count, track length, line count
  station.*       nearest station + ridership (MLIT S12), biggest hub within radii
  intersection.*  nearest major junction, junction / signal counts
  poi.*           counts per activity category within 100/250/500/1000 m
  landuse.*       share of buffer covered by commercial/retail/industrial/residential
  data.*          data-coverage indicators (so sparse OSM areas are visible)

Distances are computed in a local equirectangular projection centred on the
point (error < 0.3% within 2 km), on exact line geometry (not node vertices).
`None` for a distance means "nothing within the search radius".
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np
import shapely
import shapely.ops
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .features import INTERSECTION_ROAD_CLASSES, MAJOR_ROAD_CLASSES, ROAD_CLASS_ORDER, FeatureSet
from .stations import StationIndex

METRICS_VERSION = "m6"
RADII = (100, 250, 500, 1000)
SEARCH_M = 1200.0
POI_CATEGORIES = ("commercial", "food", "restaurant", "bar", "nightlife", "karaoke", "nightclub", "convenience", "retail", "entertainment")
M_PER_DEG_LAT = 110_574.0


def _kx(lat0: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat0))


def project(geoms, lon0: float, lat0: float):
    """Project lon/lat shapely geometries (array) to local metres around (lon0, lat0)."""
    kx = _kx(lat0)
    return shapely.transform(geoms, lambda c: np.column_stack(((c[:, 0] - lon0) * kx, (c[:, 1] - lat0) * M_PER_DEG_LAT)))


def _r(x: float | None, nd: int = 0) -> float | None:
    if x is None:
        return None
    return round(float(x), nd) if nd else int(round(float(x)))


def major_junctions(roads) -> list[dict[str, Any]]:
    """Junction nodes of the major surface network.

    A node is a junction if its degree in the trunk/primary/secondary surface
    graph is >= 3 (a way passing through counts 2, a way ending counts 1) and at
    least one incident way is trunk/primary, and the incident ways carry at
    least two distinct road identities (name/ref) - so a dual carriageway
    splitting or a road merely changing its tags is not counted.
    """
    deg: dict[int, int] = defaultdict(int)
    coords: dict[int, tuple[float, float]] = {}
    cls_at: dict[int, set[str]] = defaultdict(set)
    ident_at: dict[int, set[str]] = defaultdict(set)
    for r in roads:
        if r.cls not in INTERSECTION_ROAD_CLASSES or not r.surface or r.highway.endswith("_link"):
            continue
        n = len(r.nodes)
        cs = list(r.geom.coords)
        if n != len(cs):
            continue
        ident = r.ref or r.name or f"way{r.osm_id}"
        for i, nid in enumerate(r.nodes):
            deg[nid] += 1 if i in (0, n - 1) else 2
            coords[nid] = cs[i]
            cls_at[nid].add(r.cls)
            ident_at[nid].add(ident)
    out = []
    for nid, d in deg.items():
        if d >= 3 and cls_at[nid] & {"trunk", "primary"} and len(ident_at[nid]) >= 2:
            lon, lat = coords[nid]
            out.append({"node": nid, "lon": lon, "lat": lat, "classes": sorted(cls_at[nid]), "roads": sorted(ident_at[nid])})
    return out


class FeatureIndex:
    def __init__(self, fs: FeatureSet, stations: StationIndex | None = None, zoning=None):
        self.fs = fs
        self.stations = stations
        self.zoning = zoning
        self.road_geoms = np.array([r.geom for r in fs.roads], dtype=object)
        self.road_tree = STRtree(self.road_geoms) if len(self.road_geoms) else None
        self.rail_geoms = np.array([r.geom for r in fs.rails], dtype=object)
        self.rail_tree = STRtree(self.rail_geoms) if len(self.rail_geoms) else None
        self.lu_geoms = np.array([l.geom for l in fs.landuse], dtype=object)
        self.lu_tree = STRtree(self.lu_geoms) if len(self.lu_geoms) else None
        self.poi_lat = np.array([p.lat for p in fs.pois], dtype=float)
        self.poi_lon = np.array([p.lon for p in fs.pois], dtype=float)
        self.poi_mask = {c: np.array([c in p.categories for p in fs.pois], dtype=bool) for c in POI_CATEGORIES}
        self.junctions = major_junctions(fs.roads)
        self.j_lat = np.array([j["lat"] for j in self.junctions], dtype=float)
        self.j_lon = np.array([j["lon"] for j in self.junctions], dtype=float)
        self.sig_lat = np.array([s[1] for s in fs.signals], dtype=float)
        self.sig_lon = np.array([s[2] for s in fs.signals], dtype=float)
        self.st_lat = np.array([s[1] for s in fs.osm_stations], dtype=float)
        self.st_lon = np.array([s[2] for s in fs.osm_stations], dtype=float)

    # ------------------------------------------------------------------
    def _query(self, tree, lat: float, lon: float, radius: float):
        if tree is None:
            return np.array([], dtype=int)
        dlat = radius / M_PER_DEG_LAT
        dlon = radius / _kx(lat)
        return tree.query(box(lon - dlon, lat - dlat, lon + dlon, lat + dlat))

    @staticmethod
    def _point_dists(lats, lons, lat0, lon0):
        if len(lats) == 0:
            return np.array([], dtype=float)
        return np.hypot((lons - lon0) * _kx(lat0), (lats - lat0) * M_PER_DEG_LAT)

    # ------------------------------------------------------------------
    def measure(self, lat: float, lon: float) -> dict[str, Any]:
        origin = Point(0.0, 0.0)
        disc = {r: origin.buffer(r, quad_segs=16) for r in RADII}
        prof: dict[str, Any] = {"version": METRICS_VERSION, "search_radius_m": SEARCH_M}
        prof["road"] = self._roads(lat, lon, origin, disc)
        prof["rail"] = self._rails(lat, lon, origin, disc)
        prof["station"] = self._stations(lat, lon, origin)
        prof["intersection"] = self._intersections(lat, lon)
        prof["poi"] = self._pois(lat, lon)
        prof["landuse"] = self._landuse(lat, lon, disc)
        prof["zoning"] = self.zoning.measure(lat, lon, project, disc) if self.zoning is not None else {}
        prof["data"] = {
            "roads_within_search": int(len(self._query(self.road_tree, lat, lon, SEARCH_M))),
            "poi_total_1000": prof["poi"].get("commercial_1000", 0),
            "landuse_mapped_500": prof["landuse"].get("mapped_500"),
        }
        return prof

    def _roads(self, lat, lon, origin, disc) -> dict[str, Any]:
        out: dict[str, Any] = {f"{c}_m": None for c in ROAD_CLASS_ORDER}
        idx = self._query(self.road_tree, lat, lon, SEARCH_M)
        if len(idx) == 0:
            out.update({"nearest_major_m": None, "nearest_major_type": None})
            return out
        geoms = project(self.road_geoms[idx], lon, lat)
        d = shapely.distance(geoms, origin)
        best: dict[str, tuple[float, int]] = {}
        for k, i in enumerate(idx):
            r = self.fs.roads[i]
            if not r.surface or d[k] > SEARCH_M:
                continue
            if r.cls not in best or d[k] < best[r.cls][0]:
                best[r.cls] = (d[k], i)
        for c, (dist, i) in best.items():
            out[f"{c}_m"] = _r(dist)
            if c in ("motorway", "trunk", "primary", "secondary"):
                rr = self.fs.roads[i]
                name = rr.name or rr.ref
                if not name:  # unnamed ramp/segment: borrow the nearest named way of the same class
                    named = [(d[k], j) for k, j in enumerate(idx) if self.fs.roads[j].cls == c and self.fs.roads[j].surface
                             and (self.fs.roads[j].name or self.fs.roads[j].ref) and d[k] <= dist + 60]
                    if named:
                        rj = self.fs.roads[min(named)[1]]
                        name = rj.name or rj.ref
                out[f"{c}_name"] = name
                if c == "motorway":
                    out["motorway_elevated"] = rr.elevated
        # wide (>= 4 lanes) roads below primary carry primary-like traffic; OSM class alone undersells them
        wide = [(d[k], i) for k, i in enumerate(idx) if self.fs.roads[i].surface and (self.fs.roads[i].lanes or 0) >= 4
                and self.fs.roads[i].cls in ("secondary", "tertiary", "unclassified", "residential") and d[k] <= SEARCH_M]
        if wide:
            dw, iw = min(wide)
            out["wide_road_m"], out["wide_road_name"] = _r(dw), self.fs.roads[iw].name or self.fs.roads[iw].ref
        else:
            out["wide_road_m"] = None
        major = [(best[c][0], c) for c in ("motorway", "trunk", "primary") if c in best]
        if major:
            dm, cm = min(major)
            out["nearest_major_m"], out["nearest_major_type"] = _r(dm), cm
        else:
            out["nearest_major_m"], out["nearest_major_type"] = None, None
        # underground major roads (informational: tunnels are excluded from noise)
        under = [d[k] for k, i in enumerate(idx) if not self.fs.roads[i].surface and self.fs.roads[i].cls in MAJOR_ROAD_CLASSES]
        out["underground_major_m"] = _r(min(under)) if under else None
        # major-road length (m) inside 100 / 250 m
        for r in (100, 250):
            sel = [k for k, i in enumerate(idx) if self.fs.roads[i].surface and self.fs.roads[i].cls in MAJOR_ROAD_CLASSES and d[k] <= r]
            out[f"major_len_{r}"] = _r(sum(g.length for g in shapely.intersection(geoms[sel], disc[r]))) if sel else 0
            sel2 = [k for k, i in enumerate(idx) if self.fs.roads[i].surface and self.fs.roads[i].cls == "secondary" and d[k] <= r]
            out[f"secondary_len_{r}"] = _r(sum(g.length for g in shapely.intersection(geoms[sel2], disc[r]))) if sel2 else 0
        return out

    def _rails(self, lat, lon, origin, disc) -> dict[str, Any]:
        out: dict[str, Any] = {"surface_m": None, "nearest_name": None, "nearest_kind": None, "nearest_elevated": None,
                               "tracks_at_nearest": 0, "underground_m": None, "minor_track_m": None,
                               "heavy_surface_m": None, "light_surface_m": None, "highspeed_m": None, "heavy_elevated": None}
        idx = self._query(self.rail_tree, lat, lon, SEARCH_M)
        for r in (250, 500):
            out[f"track_len_{r}"] = 0
        out["lines_500"] = 0
        if len(idx) == 0:
            return out
        geoms = project(self.rail_geoms[idx], lon, lat)
        d = shapely.distance(geoms, origin)
        main = [k for k, i in enumerate(idx) if self.fs.rails[i].surface and not self.fs.rails[i].minor and d[k] <= SEARCH_M]
        under = [k for k, i in enumerate(idx) if not self.fs.rails[i].surface]
        minor = [k for k, i in enumerate(idx) if self.fs.rails[i].surface and self.fs.rails[i].minor]
        if under:
            out["underground_m"] = _r(min(d[k] for k in under))
        if minor:
            out["minor_track_m"] = _r(min(d[k] for k in minor))
        if not main:
            return out
        heavy = [k for k in main if not self.fs.rails[idx[k]].light]
        light = [k for k in main if self.fs.rails[idx[k]].light]
        if heavy:
            kh = min(heavy, key=lambda k: d[k])
            out["heavy_surface_m"] = _r(d[kh])
            out["heavy_elevated"] = self.fs.rails[idx[kh]].elevated
            out["heavy_name"] = self.fs.rails[idx[kh]].name
            out["heavy_tracks"] = count_parallel_tracks(geoms[heavy], geoms[kh], origin)
        if light:
            kl = min(light, key=lambda k: d[k])
            out["light_surface_m"] = _r(d[kl])
            out["light_name"] = self.fs.rails[idx[kl]].name
        hs = [k for k in main if self.fs.rails[idx[k]].highspeed]
        if hs:
            out["highspeed_m"] = _r(min(d[k] for k in hs))
        kbest = min(main, key=lambda k: d[k])
        rb = self.fs.rails[idx[kbest]]
        out.update({"surface_m": _r(d[kbest]), "nearest_name": rb.name, "nearest_kind": rb.kind, "nearest_elevated": rb.elevated})
        out["tracks_at_nearest"] = count_parallel_tracks(geoms[main], geoms[kbest], origin)
        for r in (250, 500):
            sel = [k for k in main if d[k] <= r]
            out[f"track_len_{r}"] = _r(sum(g.length for g in shapely.intersection(geoms[sel], disc[r]))) if sel else 0
        names = {self.fs.rails[idx[k]].name for k in main if d[k] <= 500 and self.fs.rails[idx[k]].name}
        out["lines_500"] = len(names)
        return out

    def _stations(self, lat, lon, origin) -> dict[str, Any]:
        out: dict[str, Any] = {"nearest_m": None, "nearest_name": None, "nearest_passengers": None, "source": None}
        for r in (300, 500, 800):
            out[f"max_passengers_{r}"] = 0
        out["count_800"] = 0
        if self.stations is not None:
            dlat, dlon = 1000 / M_PER_DEG_LAT, 1000 / _kx(lat)
            cands = self.stations.candidates(lon, lat, dlon, dlat)
            if cands:
                geoms = project(np.array([c.geom for c in cands], dtype=object), lon, lat)
                d = shapely.distance(geoms, origin)
                order = np.argsort(d)
                k0 = order[0]
                if d[k0] <= 1000:
                    out.update({"nearest_m": _r(d[k0]), "nearest_name": cands[k0].name, "nearest_passengers": cands[k0].passengers,
                                "nearest_lines": cands[k0].lines, "source": "mlit_s12"})
                for r in (300, 500, 800):
                    near = [cands[k].passengers for k in range(len(cands)) if d[k] <= r]
                    out[f"max_passengers_{r}"] = max(near) if near else 0
                big = max(((cands[k].passengers, k) for k in range(len(cands)) if d[k] <= 800), default=None)
                if big:
                    out["biggest_800_name"] = cands[big[1]].name
                    out["biggest_800_m"] = _r(d[big[1]])
                out["count_800"] = int(sum(1 for k in range(len(cands)) if d[k] <= 800))
        if out["nearest_m"] is None and len(self.st_lat):
            dd = self._point_dists(self.st_lat, self.st_lon, lat, lon)
            k = int(np.argmin(dd))
            if dd[k] <= 1000:
                out.update({"nearest_m": _r(dd[k]), "nearest_name": self.fs.osm_stations[k][0], "source": "osm"})
        return out

    def _intersections(self, lat, lon) -> dict[str, Any]:
        out: dict[str, Any] = {"major_m": None, "major_count_250": 0, "signals_100": 0, "signals_250": 0}
        if len(self.j_lat):
            dd = self._point_dists(self.j_lat, self.j_lon, lat, lon)
            k = int(np.argmin(dd))
            if dd[k] <= SEARCH_M:
                out["major_m"] = _r(dd[k])
                out["major_roads"] = self.junctions[k]["roads"]
            out["major_count_250"] = int((dd <= 250).sum())
        if len(self.sig_lat):
            ds = self._point_dists(self.sig_lat, self.sig_lon, lat, lon)
            out["signals_100"] = int((ds <= 100).sum())
            out["signals_250"] = int((ds <= 250).sum())
        return out

    def _pois(self, lat, lon) -> dict[str, Any]:
        out: dict[str, Any] = {}
        dd = self._point_dists(self.poi_lat, self.poi_lon, lat, lon)
        for c in POI_CATEGORIES:
            m = self.poi_mask[c] if len(dd) else np.array([], dtype=bool)
            for r in RADII:
                out[f"{c}_{r}"] = int(((dd <= r) & m).sum()) if len(dd) else 0
        if len(dd):
            nl = np.where(self.poi_mask["nightlife"], dd, np.inf)
            out["nearest_nightlife_m"] = _r(nl.min()) if np.isfinite(nl.min()) else None
        else:
            out["nearest_nightlife_m"] = None
        return out

    def _landuse(self, lat, lon, disc) -> dict[str, Any]:
        out: dict[str, Any] = {}
        idx = self._query(self.lu_tree, lat, lon, 500)
        kinds = ("commercial", "retail", "industrial", "residential")
        if len(idx) == 0:
            for r in (250, 500):
                for k in kinds:
                    out[f"{k}_{r}"] = 0.0
                out[f"commercial_retail_{r}"] = 0.0
                out[f"mapped_{r}"] = 0.0
            return out
        geoms = project(self.lu_geoms[idx], lon, lat)
        geoms = shapely.make_valid(geoms)
        by_kind: dict[str, list] = defaultdict(list)
        for k, i in enumerate(idx):
            by_kind[self.fs.landuse[i].kind].append(geoms[k])
        for r in (250, 500):
            area = disc[r].area
            unions = {}
            for k in kinds:
                gs = by_kind.get(k)
                unions[k] = unary_union(gs).intersection(disc[r]) if gs else None
                out[f"{k}_{r}"] = round(unions[k].area / area, 3) if unions[k] is not None else 0.0
            cr = [g for g in (unions["commercial"], unions["retail"]) if g is not None]
            out[f"commercial_retail_{r}"] = round(unary_union(cr).area / area, 3) if cr else 0.0
            allg = [g for g in unions.values() if g is not None]
            out[f"mapped_{r}"] = round(unary_union(allg).area / area, 3) if allg else 0.0
        return out


def count_parallel_tracks(track_geoms, nearest_geom, origin: Point, probe_len: float = 60.0) -> int:
    """Count parallel tracks next to the nearest one.

    Cast a probe perpendicular to the nearest track (through the nearest
    point, extending `probe_len` m beyond it, 5 m before) and count distinct
    crossings with track geometries. OSM in Japan maps each track as its own
    way, so a 4-track main line yields 4. Crossings closer than 2 m are merged
    (split ways meeting at a node).
    """
    p_near = shapely.ops.nearest_points(nearest_geom, origin)[0]
    dx, dy = p_near.x - origin.x, p_near.y - origin.y
    n = math.hypot(dx, dy)
    if n < 0.5:
        # we are on the track: use the track's normal instead
        proj = nearest_geom.project(p_near)
        a = nearest_geom.interpolate(max(0.0, proj - 5))
        b = nearest_geom.interpolate(proj + 5)
        tx, ty = b.x - a.x, b.y - a.y
        tn = math.hypot(tx, ty) or 1.0
        ux, uy = -ty / tn, tx / tn
        probe = LineString([(p_near.x - ux * probe_len, p_near.y - uy * probe_len), (p_near.x + ux * probe_len, p_near.y + uy * probe_len)])
    else:
        ux, uy = dx / n, dy / n
        probe = LineString([(p_near.x - ux * 5, p_near.y - uy * 5), (p_near.x + ux * probe_len, p_near.y + uy * probe_len)])
    pts: list[tuple[float, float]] = []
    for g in shapely.intersection(track_geoms, probe):
        if g.is_empty:
            continue
        for part in getattr(g, "geoms", [g]):
            if part.geom_type == "Point":
                pts.append((part.x, part.y))
            elif part.geom_type == "LineString":  # collinear overlap: count once
                c = part.interpolate(0.5, normalized=True)
                pts.append((c.x, c.y))
    pts.sort()
    distinct: list[tuple[float, float]] = []
    for p in pts:
        if all(math.hypot(p[0] - q[0], p[1] - q[1]) > 2.0 for q in distinct):
            distinct.append(p)
    return max(1, len(distinct))
