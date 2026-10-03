"""Local extract import produces the same element shapes the Overpass path yields."""
import pytest

osmium = pytest.importorskip("osmium")

from quiethousing.geo.features import parse_elements
from quiethousing.geo.metrics import FeatureIndex
from quiethousing.geo.overpass import OverpassTileCache, tile_of
from quiethousing.geo.pbf import classify, import_pbf

LAT, LON = 35.605, 139.705  # inside tile (1780, 5588)

XML = f"""<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <bounds minlat="35.50" minlon="139.60" maxlat="35.70" maxlon="139.80"/>
  <node id="1" lat="{LAT + 0.0009}" lon="{LON - 0.01}"/>
  <node id="2" lat="{LAT + 0.0009}" lon="{LON + 0.01}"/>
  <node id="3" lat="{LAT}" lon="{LON + 0.0005}"><tag k="amenity" v="bar"/></node>
  <node id="4" lat="{LAT}" lon="{LON}"><tag k="highway" v="traffic_signals"/></node>
  <node id="5" lat="{LAT - 0.001}" lon="{LON - 0.001}"/>
  <node id="6" lat="{LAT - 0.001}" lon="{LON + 0.001}"/>
  <node id="7" lat="{LAT + 0.001}" lon="{LON + 0.001}"/>
  <node id="8" lat="{LAT + 0.001}" lon="{LON - 0.001}"/>
  <node id="9" lat="{LAT}" lon="{LON}"><tag k="name" v="untagged-ish"/></node>
  <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/><tag k="name" v="R1"/></way>
  <way id="11"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/><tag k="building" v="yes"/><tag k="shop" v="convenience"/></way>
  <way id="12"><nd ref="5"/><nd ref="6"/><nd ref="7"/></way>
  <way id="13"><nd ref="7"/><nd ref="8"/><nd ref="5"/></way>
  <way id="14"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/><tag k="building" v="yes"/></way>
  <relation id="20"><member type="way" ref="12" role="outer"/><member type="way" ref="13" role="outer"/>
    <tag k="type" v="multipolygon"/><tag k="landuse" v="commercial"/></relation>
</osm>
"""


def test_classify_matches_overpass_selection():
    assert classify({"highway": "primary_link"}, "way") == ["transport"]
    assert classify({"highway": "footway"}, "way") == []
    assert classify({"railway": "subway"}, "way") == ["transport"]
    assert classify({"railway": "station"}, "node") == ["transport"]
    assert classify({"shop": "vacant"}, "node") == []
    assert classify({"landuse": "retail"}, "way") == ["activity"]
    assert classify({"building": "yes"}, "way") == []


def test_import_small_extract(tmp_path):
    src = tmp_path / "t.osm"
    src.write_text(XML)
    cache = OverpassTileCache(tmp_path / "tiles", None, [], 0)
    tile = tile_of(LAT, LON)
    st = import_pbf(src, cache, {tile, (0, 0)})
    assert st["tile_files"] == 2  # (0,0) is outside the extract bbox and must not be written as empty
    assert cache.has("transport", tile) and cache.has("activity", tile) and not cache.has("transport", (0, 0))
    fs, seen = None, set()
    for layer in ("transport", "activity"):
        fs = parse_elements(cache.get(layer, tile, allow_fetch=False)["elements"], fs, seen)
    assert [r.name for r in fs.roads] == ["R1"]
    assert fs.roads[0].nodes == [1, 2]
    assert len(fs.signals) == 1
    assert sorted(p.kind for p in fs.pois) == ["bar", "shop=convenience"]
    assert [l.kind for l in fs.landuse] == ["commercial"]
    prof = FeatureIndex(fs).measure(LAT, LON)
    assert prof["road"]["primary_m"] == pytest.approx(100, abs=2)
    assert prof["landuse"]["commercial_250"] > 0
