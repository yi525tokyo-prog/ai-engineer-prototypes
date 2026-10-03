"""Turn raw Overpass elements into typed, classified geographic features.

All functions here are pure. Geometries are shapely objects in lon/lat; the
metric computations project them locally (see metrics.py).

Classification decisions that matter for noise:
  * Road class comes from `highway`; `*_link` ramps inherit the parent class.
  * Tunnels (tunnel=yes/building_passage/culvert, covered=yes, or layer<0
    without a bridge) are NOT surface noise sources: Tokyo has many
    underground expressway sections (Yamate Tunnel) and most subways.
  * Bridges / layer>0 are flagged as elevated (elevated expressways and
    viaduct railways radiate noise further).
  * Rail `service=yard|siding|spur|crossover` is kept but flagged as minor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import polygonize, unary_union

ROAD_CLASS_ORDER = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service"]
MAJOR_ROAD_CLASSES = {"motorway", "trunk", "primary"}
INTERSECTION_ROAD_CLASSES = {"trunk", "primary", "secondary"}

NIGHTLIFE_AMENITY = {"bar", "pub", "nightclub", "karaoke_box", "stripclub", "love_hotel", "gambling", "casino", "hookah_lounge"}
NIGHTLIFE_LEISURE = {"adult_gaming_centre"}  # pachinko / pachislot halls in Japan
FOOD_AMENITY = {"restaurant", "cafe", "fast_food", "food_court", "ice_cream", "biergarten"}
ENTERTAINMENT_AMENITY = {"cinema", "theatre", "events_venue"}
ENTERTAINMENT_LEISURE = {"amusement_arcade"}
LANDUSE_KINDS = {"commercial", "retail", "industrial", "residential"}
NIGHTLIFE_CUISINE = {"izakaya", "yakitori", "kushikatsu", "kushiage"}


def road_class(highway: str | None) -> str | None:
    if not highway:
        return None
    base = highway[:-5] if highway.endswith("_link") else highway
    return base if base in ROAD_CLASS_ORDER else None


def _layer(tags: dict[str, str]) -> float:
    try:
        return float(str(tags.get("layer", "0")).split(";")[0])
    except ValueError:
        return 0.0


def is_underground(tags: dict[str, str]) -> bool:
    if tags.get("tunnel") in ("yes", "building_passage", "culvert", "covered"):
        return True
    if tags.get("covered") == "yes":
        return True
    if tags.get("location") in ("underground",):
        return True
    if _layer(tags) < 0 and tags.get("bridge") in (None, "no"):
        return True
    return False


def is_elevated(tags: dict[str, str]) -> bool:
    if tags.get("bridge") not in (None, "no"):
        return True
    return _layer(tags) > 0 and not is_underground(tags)


@dataclass
class Road:
    osm_id: int
    cls: str
    highway: str
    name: str | None
    ref: str | None
    surface: bool
    elevated: bool
    geom: LineString
    nodes: list[int]
    lanes: int | None = None


LIGHT_RAIL_KINDS = {"tram", "light_rail", "monorail", "funicular"}


@dataclass
class Rail:
    osm_id: int
    kind: str
    name: str | None
    service: str | None
    surface: bool
    elevated: bool
    geom: LineString
    highspeed: bool = False

    @property
    def minor(self) -> bool:
        return self.service in ("yard", "siding", "spur", "crossover")

    @property
    def light(self) -> bool:
        """Trams, light rail (世田谷線), AGT/monorails: slow or rubber-tyred, much quieter than heavy rail."""
        return self.kind in LIGHT_RAIL_KINDS


@dataclass
class Poi:
    osm_id: str
    lat: float
    lon: float
    categories: set[str]
    kind: str
    name: str | None


@dataclass
class LandUse:
    osm_id: str
    kind: str
    geom: Polygon | MultiPolygon


@dataclass
class FeatureSet:
    roads: list[Road] = field(default_factory=list)
    rails: list[Rail] = field(default_factory=list)
    osm_stations: list[tuple[str | None, float, float]] = field(default_factory=list)
    signals: list[tuple[int, float, float]] = field(default_factory=list)
    pois: list[Poi] = field(default_factory=list)
    landuse: list[LandUse] = field(default_factory=list)


def _line(el: dict[str, Any]) -> LineString | None:
    g = el.get("geometry")
    if not g or len(g) < 2:
        return None
    coords = [(p["lon"], p["lat"]) for p in g if p]
    if len(coords) < 2:
        return None
    return LineString(coords)


def _lanes(tags: dict[str, str]) -> int | None:
    try:
        return int(str(tags.get("lanes", "")).split(";")[0])
    except ValueError:
        return None


def poi_categories(tags: dict[str, str]) -> tuple[set[str], str] | None:
    """Map OSM tags to activity categories. Returns (categories, kind) or None."""
    amenity, leisure, shop = tags.get("amenity"), tags.get("leisure"), tags.get("shop")
    cats: set[str] = set()
    kind = amenity or leisure or (f"shop={shop}" if shop else None)
    if amenity in NIGHTLIFE_AMENITY or leisure in NIGHTLIFE_LEISURE:
        cats.add("nightlife")
    if amenity in FOOD_AMENITY:
        cats.add("food")
        cuisine = set((tags.get("cuisine") or "").split(";"))
        if cuisine & NIGHTLIFE_CUISINE:
            cats.add("nightlife")
    if amenity in ENTERTAINMENT_AMENITY or leisure in ENTERTAINMENT_LEISURE:
        cats.add("entertainment")
    if shop and shop not in ("vacant", "no"):
        cats.add("convenience" if shop == "convenience" else "retail")
    if amenity in ("bar", "pub"):
        cats.add("bar")
    if amenity == "restaurant":
        cats.add("restaurant")
    if amenity == "karaoke_box":
        cats.add("karaoke")
    if amenity == "nightclub":
        cats.add("nightclub")
    if not cats:
        return None
    cats.add("commercial")
    return cats, kind or "?"


def _relation_polygon(el: dict[str, Any]) -> Polygon | MultiPolygon | None:
    outers, inners = [], []
    for m in el.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        coords = [(p["lon"], p["lat"]) for p in m["geometry"] if p]
        if len(coords) < 2:
            continue
        (inners if m.get("role") == "inner" else outers).append(LineString(coords))
    if not outers:
        return None
    polys = list(polygonize(unary_union(outers)))
    if not polys:
        return None
    shape = unary_union(polys)
    if inners:
        holes = list(polygonize(unary_union(inners)))
        if holes:
            shape = shape.difference(unary_union(holes))
    return shape if not shape.is_empty else None


def parse_elements(elements: Iterable[dict[str, Any]], fs: FeatureSet | None = None, seen: set | None = None) -> FeatureSet:
    """Classify Overpass elements into a FeatureSet. Dedupes by (type, id) across tiles."""
    fs = fs or FeatureSet()
    seen = seen if seen is not None else set()
    for el in elements:
        key = (el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)
        tags = el.get("tags") or {}
        t = el.get("type")
        if t == "way" and "highway" in tags:
            cls = road_class(tags["highway"])
            line = _line(el)
            if cls and line is not None:
                fs.roads.append(Road(el["id"], cls, tags["highway"], tags.get("name"), tags.get("ref"),
                                     not is_underground(tags), is_elevated(tags), line, el.get("nodes") or [], _lanes(tags)))
            continue
        if t == "way" and "railway" in tags:
            line = _line(el)
            if line is not None:
                hs = tags.get("highspeed") == "yes" or "新幹線" in (tags.get("name") or "")
                fs.rails.append(Rail(el["id"], tags["railway"], tags.get("name"), tags.get("service"),
                                     not is_underground(tags), is_elevated(tags), line, hs))
            continue
        if t == "node" and tags.get("railway") in ("station", "halt"):
            fs.osm_stations.append((tags.get("name"), el["lat"], el["lon"]))
            continue
        if t == "node" and tags.get("highway") == "traffic_signals":
            fs.signals.append((el["id"], el["lat"], el["lon"]))
            continue
        if tags.get("landuse") in LANDUSE_KINDS and (("geometry" in el and t == "way") or (t == "relation" and "members" in el)):
            geom = None
            if t == "way":
                g = el.get("geometry") or []
                coords = [(p["lon"], p["lat"]) for p in g if p]
                if len(coords) >= 4 and coords[0] == coords[-1]:
                    poly = Polygon(coords)
                    geom = poly if poly.is_valid else poly.buffer(0)
            else:
                geom = _relation_polygon(el)
            if geom is not None and not geom.is_empty:
                fs.landuse.append(LandUse(f"{t}/{el['id']}", tags["landuse"], geom))
            continue
        pc = poi_categories(tags)
        if pc:
            if t == "node":
                lat, lon = el.get("lat"), el.get("lon")
            else:
                c = el.get("center") or {}
                lat, lon = c.get("lat"), c.get("lon")
            if lat is None or lon is None:
                continue
            fs.pois.append(Poi(f"{t}/{el['id']}", lat, lon, pc[0], pc[1], tags.get("name")))
    return fs
