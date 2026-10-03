"""Housing constraints and the merged property record, including missing data."""
from quiethousing.constraints import build_property, check_constraints

C = {
    "max_rent": None, "max_total_rent": 150000, "min_floor_area_m2": 25, "prefectures": ["東京都"], "cities": [],
    "exclude_cities": [], "max_walk_minutes": 12, "min_floor": 2, "unknown_floor_passes": True,
    "max_building_age_years": None, "layouts": [], "exclude_fixed_term_lease": False, "required_facilities": [],
}


def listing(list_=None, detail=None):
    return {"estate_id": "1", "url": "u", "region": "tokyo", "list": list_ or {}, "detail": detail, "lat": None, "lon": None, "coord_source": None}


STUB = {"rent": 120000, "management_fee": 8000, "prefecture": "東京都", "city": "杉並区", "floor_area_m2": 30.5,
        "layout": "1LDK", "room": "304号室", "walk_minutes": 8, "nearest_station": "荻窪駅"}


def test_passes_and_derives_fields():
    p = build_property(listing(STUB))
    assert p["total_rent"] == 128000 and p["floor"] == 3 and p["floor_source"] == "room_number"
    assert check_constraints(p, C) == {"passed": True, "failures": [], "unknown": []}


def test_failures_are_explained():
    p = build_property(listing(dict(STUB, rent=150000, floor_area_m2=20.0, room="101号室", prefecture="神奈川県")))
    r = check_constraints(p, C)
    assert not r["passed"]
    assert any("rent+fee" in f for f in r["failures"])
    assert any("area 20.0" in f for f in r["failures"])
    assert "floor 1 < 2" in r["failures"]
    assert any("prefecture" in f for f in r["failures"])


def test_unknown_fields_do_not_fail_at_list_stage():
    p = build_property(listing({"rent": 90000}))
    r = check_constraints(p, C)
    assert r["passed"]
    assert {"floor_area_m2", "walk_minutes", "prefecture", "floor"} <= set(r["unknown"])


def test_unknown_floor_policy_after_detail():
    p = build_property(listing(dict(STUB, room="ベーシック号室"), {"room": "ベーシック号室", "rent": 120000}))
    assert check_constraints(p, C)["passed"]
    assert not check_constraints(p, dict(C, unknown_floor_passes=False))["passed"]


def test_detail_overrides_list_and_floor_sanity():
    det = {"rent": 125000, "management_fee": 10000, "room": "1101号室", "total_floors": 5, "address": "東京都杉並区荻窪1-2-3",
           "stations": [{"line": "JR", "station": "荻窪駅", "walk_minutes": 9, "bus": False}, {"station": "南阿佐ケ谷駅", "walk_minutes": 7, "bus": False}]}
    p = build_property(listing(STUB, det))
    assert p["rent"] == 125000 and p["total_rent"] == 135000
    assert p["address"] == "東京都杉並区荻窪1-2-3"
    assert p["walk_minutes"] == 7 and p["nearest_station"] == "南阿佐ケ谷駅"
    assert p["floor"] is None and p["floor_source"] == "inconsistent"  # 11F in a 5-storey building


def test_city_layout_and_facility_filters():
    p = build_property(listing(STUB, {"facilities_on": ["バストイレ別"], "other_facilities": []}))
    assert not check_constraints(p, dict(C, cities=["世田谷区"]))["passed"]
    assert check_constraints(p, dict(C, cities=["杉並区", "世田谷区"]))["passed"]
    assert not check_constraints(p, dict(C, exclude_cities=["杉並"]))["passed"]
    assert not check_constraints(p, dict(C, layouts=["1K", "1DK"]))["passed"]
    assert check_constraints(p, dict(C, required_facilities=["バストイレ別"]))["passed"]
    assert not check_constraints(p, dict(C, required_facilities=["オートロック"]))["passed"]
    q = build_property(listing(dict(STUB, layout="ワンルーム")))
    assert check_constraints(q, dict(C, layouts=["1R"]))["passed"]
