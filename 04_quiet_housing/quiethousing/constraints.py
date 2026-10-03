"""Housing constraints (rent, area, location, floor, ...) and the flat property record.

`build_property` merges the search-list stub with the detail page (detail wins)
into one flat dict. `check_constraints` is pure and reports *why* a listing
fails. Fields that are unknown at the current stage (e.g. floor before the
detail page is fetched) never cause a failure; they are reported as unknown
so the pipeline can decide whether to fetch more.
"""
from __future__ import annotations

from typing import Any

from .goodroom.parse import floor_from_room


def build_property(listing: dict[str, Any]) -> dict[str, Any]:
    stub = listing.get("list") or {}
    det = listing.get("detail") or {}
    p: dict[str, Any] = {
        "estate_id": listing["estate_id"],
        "url": listing["url"],
        "region": listing.get("region"),
        "has_detail": bool(det),
    }
    for k in ("title", "building_name", "rent", "management_fee", "layout", "floor_area_m2", "room", "prefecture", "city"):
        v = det.get(k)
        p[k] = v if v not in (None, "") else stub.get(k)
    p["address"] = det.get("address") or stub.get("address_city")
    stations = det.get("stations") or []
    if not stations and stub.get("nearest_station"):
        stations = [{"line": stub.get("nearest_line"), "station": stub.get("nearest_station"), "walk_minutes": stub.get("walk_minutes"), "bus": False}]
    p["stations"] = stations
    walks = [s["walk_minutes"] for s in stations if s.get("walk_minutes") is not None and not s.get("bus")]
    p["walk_minutes"] = min(walks) if walks else stub.get("walk_minutes")
    p["nearest_station"] = next((s["station"] for s in stations if s.get("walk_minutes") == p["walk_minutes"]), stub.get("nearest_station"))
    floor, src = floor_from_room(p.get("room"))
    p["floor"], p["floor_source"] = floor, src
    for k in ("built_year", "built_month", "building_age_years", "structure", "total_floors", "direction", "fixed_term_lease",
              "move_in", "deposit", "key_money", "brokerage_fee", "facilities_on", "other_facilities", "info_updated"):
        p[k] = det.get(k)
    if p["floor"] is not None and p.get("total_floors") and p["floor"] > p["total_floors"]:
        # room-number heuristic contradicted by building height (e.g. room "1101" in a 5F building)
        p["floor"], p["floor_source"] = None, "inconsistent"
    rent, fee = p.get("rent"), p.get("management_fee")
    p["total_rent"] = (rent + (fee or 0)) if rent is not None else None
    p["list_flags"] = stub.get("list_flags") or []
    p["lat"], p["lon"], p["coord_source"] = listing.get("lat"), listing.get("lon"), listing.get("coord_source")
    p["coord_check"] = listing.get("coord_check")
    return p


def _fail(fails: list[str], msg: str) -> None:
    fails.append(msg)


def check_constraints(p: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
    """Return {'passed': bool, 'failures': [...], 'unknown': [...]}."""
    fails: list[str] = []
    unknown: list[str] = []

    def num_max(field: str, limit: Any, label: str, unit: str = "") -> None:
        if limit is None:
            return
        v = p.get(field)
        if v is None:
            unknown.append(field)
        elif v > limit:
            _fail(fails, f"{label} {v:,}{unit} > {limit:,}{unit}")

    def num_min(field: str, limit: Any, label: str, unit: str = "") -> None:
        if limit is None:
            return
        v = p.get(field)
        if v is None:
            unknown.append(field)
        elif v < limit:
            _fail(fails, f"{label} {v}{unit} < {limit}{unit}")

    num_max("rent", c.get("max_rent"), "rent", "円")
    num_max("total_rent", c.get("max_total_rent"), "rent+fee", "円")
    num_min("floor_area_m2", c.get("min_floor_area_m2"), "area", "㎡")
    num_max("walk_minutes", c.get("max_walk_minutes"), "walk", "min")
    num_max("building_age_years", c.get("max_building_age_years"), "building age", "y")

    if c.get("min_floor") is not None:
        if p.get("floor") is None:
            unknown.append("floor")
            if not c.get("unknown_floor_passes", True) and p.get("has_detail"):
                _fail(fails, "floor unknown")
        elif p["floor"] < c["min_floor"]:
            _fail(fails, f"floor {p['floor']} < {c['min_floor']}")

    prefs = c.get("prefectures") or []
    if prefs:
        if not p.get("prefecture"):
            unknown.append("prefecture")
        elif p["prefecture"] not in prefs:
            _fail(fails, f"prefecture {p['prefecture']} not in allowed list")
    city = p.get("city") or ""
    cities = c.get("cities") or []
    if cities:
        if not city:
            unknown.append("city")
        elif not any(city.startswith(x) or x in city for x in cities):
            _fail(fails, f"city {city} not in allowed list")
    for x in c.get("exclude_cities") or []:
        if x and x in city:
            _fail(fails, f"city {city} excluded")

    layouts = c.get("layouts") or []
    if layouts:
        lay = p.get("layout")
        if not lay:
            unknown.append("layout")
        else:
            norm = "1R" if lay in ("ワンルーム", "1R") else lay
            if norm not in layouts and lay not in layouts:
                _fail(fails, f"layout {lay} not in {layouts}")

    if c.get("exclude_fixed_term_lease") and p.get("fixed_term_lease"):
        _fail(fails, "fixed-term lease (定期借家)")
    req = c.get("required_facilities") or []
    if req and p.get("has_detail"):
        have = set(p.get("facilities_on") or []) | set(p.get("other_facilities") or [])
        for f in req:
            if f not in have:
                _fail(fails, f"missing facility {f}")
    return {"passed": not fails, "failures": fails, "unknown": sorted(set(unknown))}
