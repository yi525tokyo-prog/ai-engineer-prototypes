"""Caching (pages, geocodes, Overpass tiles, environment profiles) and coordinate resolution."""
import time

import pytest

from quiethousing.geo.overpass import OverpassTileCache
from quiethousing.geocode import choose_coordinates, gsi_geocode, gsi_precision, haversine_m, normalize_address
from quiethousing.http import FetchError, Fetcher
from quiethousing.store import Store


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.encoding = "utf-8"

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeFetcher(Fetcher):
    def __init__(self, store, responses):
        super().__init__(store, min_interval_s=0)
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.sqlite")


def test_page_cache_ttl(store):
    f = FakeFetcher(store, [FakeResp(200, text="v1"), FakeResp(200, text="v2")])
    assert f.get_text("https://x/a", max_age_s=3600) == (200, "v1")
    assert f.get_text("https://x/a", max_age_s=3600) == (200, "v1")  # cached
    assert len(f.calls) == 1
    store.conn.execute("UPDATE page_cache SET fetched_at=?", (time.time() - 7200,))
    assert f.get_text("https://x/a", max_age_s=3600) == (200, "v2")  # expired -> refetch
    assert len(f.calls) == 2


def test_server_errors_not_cached(store):
    f = FakeFetcher(store, [FakeResp(500, text="oops"), FakeResp(200, text="ok")])
    assert f.get_text("https://x/b")[0] == 500
    assert f.get_text("https://x/b") == (200, "ok")


def test_gsi_geocode_cached_including_misses(store):
    feat = [{"geometry": {"coordinates": [139.707397, 35.582329]}, "properties": {"title": "東京都大田区南馬込六丁目２９番８号"}}]
    f = FakeFetcher(store, [FakeResp(200, feat), FakeResp(200, [])])
    r = gsi_geocode(f, store, "東京都大田区南馬込６ー２９ー８")
    assert r["lat"] == 35.582329 and r["precision"] == "house"
    assert gsi_geocode(f, store, "東京都大田区南馬込6-29-8") == r  # normalised query hits the cache
    assert gsi_geocode(f, store, "存在しない住所") is None
    assert gsi_geocode(f, store, "存在しない住所") is None  # negative result cached too
    assert len(f.calls) == 2


def test_address_normalisation_and_precision():
    assert normalize_address("東京都大田区南馬込６ー２９ー８") == "東京都大田区南馬込6-29-8"
    assert normalize_address(" 東京都 目黒区碑文谷4丁目24－21") == "東京都目黒区碑文谷4丁目24-21"
    assert normalize_address("神奈川県川崎市宮前区馬絹-6-13-27") == "神奈川県川崎市宮前区馬絹6-13-27"
    assert gsi_precision("東京都大田区南馬込六丁目２９番８号") == "house"
    assert gsi_precision("東京都目黒区碑文谷四丁目") == "chome"
    assert gsi_precision("東京都目黒区") == "town"


def test_choose_coordinates():
    gr = {"lat": 35.6210759, "lon": 139.6815929}
    gsi_close = {"lat": 35.621128, "lon": 139.681564, "precision": "house"}
    lat, lon, src, chk = choose_coordinates(gr, gsi_close)
    assert src == "goodroom_map" and chk["disagreement_m"] < 10
    gsi_far = {"lat": 35.63, "lon": 139.69, "precision": "house"}
    assert choose_coordinates(gr, gsi_far)[2] == "gsi_house"
    gsi_far_town = dict(gsi_far, precision="town")
    assert choose_coordinates(gr, gsi_far_town)[2] == "goodroom_map"  # coarse geocode can't overrule the pin
    assert choose_coordinates(None, gsi_far_town)[2] == "gsi_town"
    assert choose_coordinates(None, None)[:3] == (None, None, None)
    assert 1100 < haversine_m(35.68, 139.76, 35.69, 139.76) < 1120


def test_overpass_tile_cache_persists_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    payload = {"elements": [{"type": "node", "id": 1, "lat": 35.6, "lon": 139.7, "tags": {"amenity": "bar"}}], "osm3s": {"timestamp_osm_base": "x"}}
    f = FakeFetcher(None, [FakeResp(503), FakeResp(200, payload)])
    c = OverpassTileCache(tmp_path, f, ["https://ep1", "https://ep2"], 0)
    f.retries = 1
    d = c.get("activity", (1780, 5588))
    assert d["elements"][0]["id"] == 1
    assert [call[1] for call in f.calls] == ["https://ep1", "https://ep2"]  # first endpoint failed -> second
    # a fresh cache object (new process) reads from disk, no network
    c2 = OverpassTileCache(tmp_path, None, [], 0)
    assert c2.get("activity", (1780, 5588))["elements"][0]["id"] == 1
    with pytest.raises(FetchError):
        c2.get("activity", (1, 1))


def test_overpass_runtime_error_remark_is_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    bad = {"elements": [], "remark": "runtime error: Query timed out in \"query\""}
    f = FakeFetcher(None, [FakeResp(200, bad)])
    c = OverpassTileCache(tmp_path, f, ["https://ep1"], 0)
    with pytest.raises(FetchError):
        c.get("transport", (1, 2))
    assert not c.has("transport", (1, 2))  # partial result never cached


def test_overpass_heavy_tile_is_split_into_quadrants(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    class SplitFetcher(FakeFetcher):
        def request(self, method, url, **kw):
            q = kw["data"]["data"]
            self.calls.append(q)
            bbox = [float(x) for x in q.split("[bbox:")[1].split("]")[0].split(",")]
            if bbox[2] - bbox[0] > 0.015:  # full tile: server gives up
                return FakeResp(504)
            shared = {"type": "way", "id": 7, "tags": {"highway": "primary"}}  # crosses quadrants
            own = {"type": "node", "id": int(bbox[0] * 1e6 + bbox[1] * 1e3), "tags": {"shop": "bakery"}}
            return FakeResp(200, {"elements": [shared, own]})

    f = SplitFetcher(None, [])
    c = OverpassTileCache(tmp_path, f, ["https://ep1"], 0)
    d = c.get("activity", (1780, 5588))
    ids = sorted(e["id"] for e in d["elements"])
    assert len(ids) == 5 and ids.count(7) == 1  # 4 quadrant-own nodes + the shared way once


def test_environment_cache_keyed_by_location_and_version(store):
    store.put_environment(35.6000001, 139.7, "v1", {"a": 1})
    assert store.get_environment(35.6000004, 139.7, "v1") == {"a": 1}  # same ~1m cell
    assert store.get_environment(35.6, 139.7, "v2") is None  # extractor version bump invalidates
    assert store.environments(["35.60000,139.70000"], "v1") == {"35.60000,139.70000": {"a": 1}}
