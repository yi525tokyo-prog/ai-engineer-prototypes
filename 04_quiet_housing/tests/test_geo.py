"""Road classification, railway distance / track counting, POI density, intersections - on synthetic OSM data
with geometry placed at exactly known metric offsets."""
import math

import pytest

from quiethousing.geo.features import is_elevated, is_underground, parse_elements, poi_categories, road_class
from quiethousing.geo.metrics import FeatureIndex
from quiethousing.geo.overpass import tile_bbox, tile_of, tiles_for
from quiethousing.geo.stations import StationIndex, build_complexes, latest_passengers

LAT0, LON0 = 35.6, 139.7
KX = 111_320.0 * math.cos(math.radians(LAT0))
KY = 110_574.0


def ll(x_m, y_m):
    """metres east/north of the origin -> {'lat','lon'}"""
    return {"lat": LAT0 + y_m / KY, "lon": LON0 + x_m / KX}


_ids = iter(range(1, 10**6))


def way(tags, pts, nodes=None):
    geom = [ll(x, y) for x, y in pts]
    return {"type": "way", "id": next(_ids), "tags": tags, "geometry": geom, "nodes": nodes or [next(_ids) for _ in pts]}


def node(tags, x, y):
    p = ll(x, y)
    return {"type": "node", "id": next(_ids), "tags": tags, "lat": p["lat"], "lon": p["lon"]}


def hline(y, tags, x0=-2000, x1=2000, nodes=None):
    return way(tags, [(x0, y), (x1, y)], nodes)


def vline(x, tags, y0=-2000, y1=2000, nodes=None):
    return way(tags, [(x, y0), (x, y1)], nodes)


# --- classification ------------------------------------------------------
def test_road_class_links_inherit_parent():
    assert road_class("motorway_link") == "motorway"
    assert road_class("primary_link") == "primary"
    assert road_class("residential") == "residential"
    assert road_class("footway") is None
    assert road_class(None) is None


def test_underground_and_elevated_flags():
    assert is_underground({"tunnel": "yes"})
    assert is_underground({"layer": "-2"})
    assert not is_underground({"layer": "-1", "bridge": "yes"})
    assert is_underground({"covered": "yes"})
    assert is_elevated({"bridge": "viaduct"})
    assert is_elevated({"layer": "2"})
    assert not is_elevated({"layer": "1", "tunnel": "yes"})


def test_poi_categories():
    assert "nightlife" in poi_categories({"amenity": "bar"})[0]
    assert "nightlife" in poi_categories({"leisure": "adult_gaming_centre"})[0]  # pachinko
    izakaya = poi_categories({"amenity": "restaurant", "cuisine": "izakaya"})[0]
    assert {"food", "nightlife", "restaurant", "commercial"} <= izakaya
    assert "convenience" in poi_categories({"shop": "convenience"})[0]
    assert poi_categories({"shop": "vacant"}) is None
    assert poi_categories({"amenity": "bench"}) is None


def test_parse_dedupes_across_tiles():
    w = hline(100, {"highway": "primary"})
    fs = parse_elements([w, dict(w)])
    assert len(fs.roads) == 1


# --- road distances -------------------------------------------------------
def build(elements):
    return FeatureIndex(parse_elements(elements))


def test_road_distance_per_class_and_tunnel_excluded():
    fi = build([
        hline(100, {"highway": "primary", "name": "P"}),
        hline(-40, {"highway": "secondary"}),
        vline(300, {"highway": "motorway", "bridge": "yes"}),
        vline(-30, {"highway": "motorway", "tunnel": "yes"}),  # underground expressway: not a surface source
        hline(15, {"highway": "residential"}),
        hline(60, {"highway": "trunk_link"}),
    ])
    p = fi.measure(LAT0, LON0)["road"]
    assert p["primary_m"] == pytest.approx(100, abs=1.5)
    assert p["secondary_m"] == pytest.approx(40, abs=1.5)
    assert p["motorway_m"] == pytest.approx(300, abs=2)
    assert p["motorway_elevated"] is True
    assert p["underground_major_m"] == pytest.approx(30, abs=1.5)
    assert p["trunk_m"] == pytest.approx(60, abs=1.5)
    assert p["residential_m"] == pytest.approx(15, abs=1)
    assert p["nearest_major_type"] == "trunk" and p["nearest_major_m"] == pytest.approx(60, abs=1.5)
    assert p["tertiary_m"] is None
    # primary at 100m crosses the 250m disc: chord length 2*sqrt(250^2-100^2) = 458m (+ trunk_link 2*sqrt(250^2-60^2)=485)
    assert p["major_len_250"] == pytest.approx(458 + 485, rel=0.02)


def test_distance_is_to_segment_not_vertices():
    # a long road whose vertices are both 1km away but passes 50m from the point
    fi = build([way({"highway": "primary"}, [(-1000, 50), (1000, 50)])])
    assert fi.measure(LAT0, LON0)["road"]["primary_m"] == pytest.approx(50, abs=1)


# --- railways -------------------------------------------------------------
def test_railway_distance_and_parallel_tracks():
    tracks = [vline(200 + 4 * i, {"railway": "rail", "name": "Main Line"}) for i in range(4)]
    fi = build(tracks + [
        vline(50, {"railway": "subway", "tunnel": "yes", "name": "Sub"}),
        vline(-120, {"railway": "rail", "service": "siding"}),
    ])
    r = fi.measure(LAT0, LON0)["rail"]
    assert r["surface_m"] == pytest.approx(200, abs=1.5)
    assert r["tracks_at_nearest"] == 4
    assert r["underground_m"] == pytest.approx(50, abs=1.5)
    assert r["minor_track_m"] == pytest.approx(120, abs=1.5)
    assert r["nearest_name"] == "Main Line"
    assert r["track_len_250"] == pytest.approx(4 * 2 * math.sqrt(250**2 - 206**2), rel=0.1)
    assert r["lines_500"] == 1


def test_split_track_ways_not_double_counted():
    # one track split into two ways meeting exactly at the probe crossing
    nid = 999_999
    a = way({"railway": "rail"}, [(80, -500), (80, 0)], [1, nid])
    b = way({"railway": "rail"}, [(80, 0), (80, 500)], [nid, 2])
    r = build([a, b]).measure(LAT0, LON0)["rail"]
    assert r["surface_m"] == pytest.approx(80, abs=1)
    assert r["tracks_at_nearest"] == 1


def test_no_railway():
    r = build([hline(10, {"highway": "residential"})]).measure(LAT0, LON0)["rail"]
    assert r["surface_m"] is None and r["track_len_500"] == 0


# --- POIs -----------------------------------------------------------------
def test_poi_density_multiple_radii():
    els = [node({"amenity": "bar"}, 50, 0) for _ in range(3)]
    els += [node({"amenity": "restaurant"}, 0, 200) for _ in range(5)]
    els += [node({"shop": "convenience"}, 400, 0)]
    els += [node({"amenity": "karaoke_box"}, 0, -900)]
    els += [{"type": "way", "id": next(_ids), "tags": {"amenity": "nightclub"}, "center": ll(-240, 0)}]
    p = build(els).measure(LAT0, LON0)["poi"]
    assert p["nightlife_100"] == 3
    assert p["nightlife_250"] == 4
    assert p["nightlife_1000"] == 5
    assert p["food_250"] == 5 and p["food_100"] == 0
    assert p["convenience_250"] == 0 and p["convenience_500"] == 1
    assert p["commercial_100"] == 3 and p["commercial_250"] == 9 and p["commercial_500"] == 10 and p["commercial_1000"] == 11
    assert p["nearest_nightlife_m"] == pytest.approx(50, abs=1)


def test_landuse_share():
    sq = lambda x0, y0, x1, y1: [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    els = [
        way({"landuse": "commercial"}, sq(-1000, 0, 1000, 1000)),  # northern half
        way({"landuse": "residential"}, sq(-1000, -1000, 1000, 0)),
    ]
    lu = build(els).measure(LAT0, LON0)["landuse"]
    assert lu["commercial_250"] == pytest.approx(0.5, abs=0.02)
    assert lu["residential_250"] == pytest.approx(0.5, abs=0.02)
    assert lu["mapped_500"] == pytest.approx(1.0, abs=0.02)


# --- intersections --------------------------------------------------------
def test_major_intersection_detection():
    j = 424242
    a = way({"highway": "primary", "name": "A"}, [(-1000, 120), (0, 120), (1000, 120)], [1001, j, 1002])
    b = way({"highway": "secondary", "name": "B"}, [(0, -1000), (0, 120), (0, 1000)], [1003, j, 1004])
    # a road changing tags mid-way (same name, end-to-end) is not an intersection
    c1 = way({"highway": "primary", "name": "C"}, [(-1000, -300), (300, -300)], [2001, 2002])
    c2 = way({"highway": "primary", "name": "C"}, [(300, -300), (1000, -300)], [2002, 2003])
    it = build([a, b, c1, c2]).measure(LAT0, LON0)["intersection"]
    assert it["major_m"] == pytest.approx(120, abs=1.5)
    assert it["major_count_250"] == 1


def test_minor_crossing_is_not_major_intersection():
    j = 515151
    a = way({"highway": "secondary", "name": "A"}, [(-500, 0), (0, 0), (500, 0)], [1, j, 2])
    b = way({"highway": "secondary", "name": "B"}, [(0, -500), (0, 0), (0, 500)], [3, j, 4])
    assert build([a, b]).measure(LAT0, LON0)["intersection"]["major_m"] is None


# --- stations (MLIT S12) --------------------------------------------------
def s12_feature(name, group, riders, coords, dup=1):
    props = {"S12_001": name, "S12_001c": group + name, "S12_001g": group, "S12_002": "op", "S12_003": "line"}
    for k in range(13):
        props[f"S12_{6 + 4 * k:03d}"] = dup
        props[f"S12_{7 + 4 * k:03d}"] = 1
        props[f"S12_{9 + 4 * k:03d}"] = riders if dup == 1 else 0
    return {"properties": props, "geometry": {"type": "LineString", "coordinates": coords}}


def test_station_complex_and_scale():
    p1 = ll(0, 290)
    p2 = ll(50, 290)
    feats = [
        s12_feature("Big", "G1", 500000, [[p1["lon"], p1["lat"]], [p2["lon"], p2["lat"]]]),
        s12_feature("Big", "G1", 300000, [[p1["lon"], p1["lat"]], [p2["lon"], p2["lat"]]]),
        s12_feature("Big", "G1", 0, [[p1["lon"], p1["lat"]], [p2["lon"], p2["lat"]]], dup=2),
    ]
    q1, q2 = ll(-100, -150), ll(-100, -120)
    feats.append(s12_feature("Small", "G2", 20000, [[q1["lon"], q1["lat"]], [q2["lon"], q2["lat"]]]))
    assert latest_passengers(feats[0]["properties"])[0] == 500000
    cx = build_complexes(feats)
    assert {c["name"]: c["passengers"] for c in cx} == {"Big": 800000, "Small": 20000}
    fi = FeatureIndex(parse_elements([]), StationIndex(cx))
    st = fi.measure(LAT0, LON0)["station"]
    assert st["nearest_name"] == "Small" and st["nearest_m"] == pytest.approx(math.hypot(100, 120), abs=2)
    assert st["max_passengers_300"] == 800000  # Big is ~290m north
    assert st["biggest_800_name"] == "Big"


# --- tiles ----------------------------------------------------------------
def test_tiles_cover_search_disc():
    ts = tiles_for(35.6999, 139.7249, 1250)
    s, w, n, e = zip(*[tile_bbox(t) for t in ts])
    assert min(s) <= 35.6999 - 1250 / 110574 and max(n) >= 35.6999 + 1250 / 110574
    assert tile_of(35.6999, 139.7249) in ts
    assert len(ts) == len(set(ts))
