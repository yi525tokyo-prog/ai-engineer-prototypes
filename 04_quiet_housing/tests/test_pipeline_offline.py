"""End-to-end over stored data, no network: stub+detail -> coords -> tiles (pre-seeded) -> profile -> decision."""
import gzip
import json
import time
from pathlib import Path

from quiethousing.goodroom.parse import parse_detail_page, parse_list_page, parse_map_page
from quiethousing.geo.overpass import LAYERS, tiles_for
from quiethousing.geo.metrics import SEARCH_M
from quiethousing.pipeline import Context, enrich, environment_version, resolve_coords, results

FX = Path(__file__).parent / "fixtures"
KX = 111_320.0 * 0.8124


def test_offline_pipeline(tmp_path):
    ctx = Context.open(tmp_path, offline=True)
    ctx.cfg["constraints"].update({"prefectures": ["東京都"], "min_floor": None, "max_total_rent": 200000, "min_floor_area_m2": 10, "max_walk_minutes": 15})
    stubs = parse_list_page((FX / "list_tokyo.html").read_text(encoding="utf-8"))["listings"]
    now = time.time()
    for s in stubs:
        ctx.store.upsert_list_stub(s, now)
    ctx.store.commit()
    # one listing gets its real detail + map page
    det = parse_detail_page((FX / "detail_1001717995.html").read_text(encoding="utf-8"), today_year=2026)
    det["goodroom_coords"] = {"lat": 35.5823, "lon": 139.7074}
    ctx.store.set_detail("1001717995", det)
    assert resolve_coords(ctx)["resolved"] == 1  # offline: goodroom pin only, no GSI call

    lat, lon = 35.5823, 139.7074
    # synthetic OSM: a primary road 30 m north of the building -> must be rejected by the hard rule
    road = {"type": "way", "id": 1, "tags": {"highway": "primary", "name": "第二京浜"}, "nodes": [1, 2],
            "geometry": [{"lat": lat + 30 / 110574, "lon": lon - 0.01}, {"lat": lat + 30 / 110574, "lon": lon + 0.01}]}
    for layer in LAYERS:
        for t in tiles_for(lat, lon, SEARCH_M + 50):
            p = ctx.tiles.path(layer, t)
            p.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(p, "wt") as f:
                json.dump({"elements": [road] if layer == "transport" else []}, f)

    st = enrich(ctx, allow_fetch=False)
    assert st["computed"] == 1 and st.get("overpass_queries", 0) == 0
    assert enrich(ctx, allow_fetch=False)["cached"] == 1  # second run: nothing recomputed

    rows = results(ctx)
    by_id = {r["property"]["estate_id"]: r for r in rows}
    r = by_id["1001717995"]
    assert r["evaluation"]["decision"] == "REJECT"
    assert "primary road 30m away" in r["evaluation"]["reasons"]
    assert r["environment"]["road"]["primary_name"] == "第二京浜"
    # listings without coordinates are reported, not silently dropped
    assert any(x["evaluation"]["decision"] == "UNKNOWN" for x in rows)
    assert ctx.store.get_environment(lat, lon, environment_version()) is not None
