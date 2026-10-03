"""Property extraction from real goodroom HTML (saved fixtures) and malformed input."""
from pathlib import Path

from quiethousing.goodroom.parse import (
    floor_from_room,
    parse_area_codes,
    parse_detail_page,
    parse_list_page,
    parse_map_page,
    parse_yen,
    split_prefecture,
)

FX = Path(__file__).parent / "fixtures"


def read(name):
    return (FX / name).read_text(encoding="utf-8")


def test_list_page_extracts_all_cards():
    r = parse_list_page(read("list_tokyo.html"))
    assert r["total"] == 9383
    assert r["has_next"] is True
    assert len(r["listings"]) == 30
    first = r["listings"][0]
    assert first["estate_id"] == "1001717995"
    assert first["url"] == "https://www.goodrooms.jp/tokyo/detail/001/1001717995/"
    assert first["building_name"] == "ミリアレジデンス南馬込"
    assert first["rent"] == 163000 and first["management_fee"] == 20000
    assert first["prefecture"] == "東京都" and first["city"] == "大田区"
    assert first["layout"] == "1LDK" and first["floor_area_m2"] == 38.91
    assert first["nearest_station"] == "西馬込駅" and first["walk_minutes"] == 7
    assert first["nearest_line"] == "東京都浅草線"
    assert first["room"] == "304号室"
    # every card must have the essentials
    for x in r["listings"]:
        assert x["rent"] and x["url"].startswith("https://www.goodrooms.jp/")
        assert x["floor_area_m2"] and x["prefecture"]


def test_detail_page_regular():
    d = parse_detail_page(read("detail_1001717995.html"), today_year=2026)
    assert d["building_name"] == "ミリアレジデンス南馬込"
    assert d["rent"] == 163000 and d["management_fee"] == 20000
    assert d["address"] == "東京都大田区南馬込６ー２９ー８"
    assert d["prefecture"] == "東京都" and d["city"] == "大田区"
    assert [s["station"] for s in d["stations"]] == ["西馬込駅", "池上駅", "馬込駅"]
    assert d["stations"][0]["walk_minutes"] == 7 and d["stations"][0]["line"] == "東京都浅草線"
    assert d["built_year"] == 2022 and d["built_month"] == 11 and d["building_age_years"] == 4
    assert d["total_floors"] == 4 and d["structure"] == "鉄筋コン" and d["direction"] == "東"
    assert d["fixed_term_lease"] is True
    assert "オートロック" in d["facilities_on"]


def test_detail_page_shared_residence_non_numeric_room():
    d = parse_detail_page(read("detail_shared_residence.html"), today_year=2026)
    assert d["room"] == "ベーシック号室"
    assert d["building_name"] == "goodroom residence 学芸大学"
    assert d["layout"] == "ワンルーム" and d["floor_area_m2"] == 11.0
    assert d["key_money"] == "1ヶ月"
    assert d["built_year"] == 1980
    assert floor_from_room(d["room"]) == (None, None)


def test_map_page_coordinates():
    m = parse_map_page(read("map_1001635182.html"))
    assert m == {"lat": 35.6210759, "lon": 139.6815929, "zoom": 16}


def test_map_page_rejects_missing_or_placeholder():
    assert parse_map_page("") is None
    assert parse_map_page("<html>no map</html>") is None
    assert parse_map_page("mapInitialize(0, 0, 16)") is None  # outside Japan -> junk pin


def test_malformed_and_empty_pages():
    assert parse_list_page("") == {"listings": [], "total": None, "has_next": False}
    d = parse_detail_page("<html><body>maintenance</body></html>")
    assert d["rent"] is None and d["address"] is None and d["stations"] == [] and d["building_name"] is None
    # truncated card: missing price / spec must not crash and yield None fields
    card = "<article class='searchEstateList'><a class='searchEstateList__wrap' href='/tokyo/detail/001/123/'></a></article>"
    r = parse_list_page(card)
    assert r["listings"][0]["estate_id"] == "123"
    assert r["listings"][0]["rent"] is None and r["listings"][0]["floor_area_m2"] is None


def test_small_helpers():
    assert parse_yen("163,000円") == 163000
    assert parse_yen("6.6万円") == 66000
    assert parse_yen("なし") == 0
    assert parse_yen("要確認") is None
    assert split_prefecture("神奈川県横浜市中区") == ("神奈川県", "横浜市中区")
    assert split_prefecture("大阪府吹田市") == ("大阪府", "吹田市")
    assert floor_from_room("0204号室") == (2, "room_number")
    assert floor_from_room("1201号室") == (12, "room_number")
    assert floor_from_room("3階") == (3, "explicit")
    assert floor_from_room("地下1階") == (-1, "explicit")
    assert floor_from_room("12号室") == (None, None)  # 2-digit room numbers say nothing about floor
    assert floor_from_room(None) == (None, None)


def test_area_codes():
    codes = parse_area_codes(read("list_tokyo.html"))
    assert codes["1000"] == "港区"
    assert codes["1013"] == "足立区"
