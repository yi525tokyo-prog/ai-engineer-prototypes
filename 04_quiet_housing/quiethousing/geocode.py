"""Coordinate resolution.

Sources, in order of trust:
  1. goodroom map page: `mapInitialize(lat, lon, zoom)` - set by the listing agent.
  2. GSI (国土地理院) address search - authoritative address-point geocoder,
     typically house-number (番/号) precision for Japanese addresses.

Both are used: goodroom coordinates are cross-checked against GSI. When they
disagree by more than `max_disagreement_m` and GSI matched at block/house
level, the GSI point wins (agents occasionally drop the pin at the station or
ward office). Everything is cached in SQLite.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from .http import Fetcher
from .store import Store

GSI_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_KANJI_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def normalize_address(addr: str) -> str:
    """NFKC + unify dashes so '南馬込６ー２９ー８' -> '南馬込6-29-8'."""
    a = unicodedata.normalize("NFKC", addr or "").strip()
    a = re.sub(r"(?<=\d)\s*[ー－−‐‑–—―ｰ]\s*(?=\d)", "-", a)
    a = re.sub(r"\s+", "", a)
    a = re.sub(r"(?<=[^\d\-])-(?=\d)", "", a)  # agent typos like '馬絹-6-13-27'
    return a


def gsi_precision(title: str) -> str:
    t = unicodedata.normalize("NFKC", title or "")
    if re.search(r"\d+号$", t) or re.search(r"\d+番(地)?(\d+)?$", t):
        return "house"
    if re.search(r"(丁目|番地?)$", t) or re.search(r"[一二三四五六七八九十]+丁目", t):
        return "chome"
    return "town"


def gsi_geocode(fetcher: Fetcher, store: Store, address: str) -> dict[str, Any] | None:
    q = normalize_address(address)
    if not q:
        return None
    hit, cached = store.get_geocode("gsi:" + q)
    if hit:
        return cached
    resp = fetcher.request("GET", GSI_URL, params={"q": q}, interval_s=1.0)
    result = None
    if resp.status_code == 200:
        try:
            feats = resp.json()
        except ValueError:
            feats = []
        if feats:
            f = feats[0]
            lon, lat = f["geometry"]["coordinates"]
            title = f.get("properties", {}).get("title", "")
            result = {"lat": lat, "lon": lon, "title": title, "precision": gsi_precision(title), "n_candidates": len(feats)}
        store.put_geocode("gsi:" + q, "gsi", result)
    return result


def choose_coordinates(
    goodroom: dict[str, Any] | None, gsi: dict[str, Any] | None, max_disagreement_m: float = 150.0
) -> tuple[float | None, float | None, str | None, dict[str, Any]]:
    """Pure decision function. Returns (lat, lon, source, check-info)."""
    check: dict[str, Any] = {"goodroom": goodroom, "gsi": gsi}
    if goodroom and gsi:
        d = haversine_m(goodroom["lat"], goodroom["lon"], gsi["lat"], gsi["lon"])
        check["disagreement_m"] = round(d, 1)
        if d > max_disagreement_m and gsi.get("precision") == "house":
            check["note"] = f"goodroom pin {d:.0f}m from GSI house-level geocode; using GSI"
            return gsi["lat"], gsi["lon"], "gsi_house", check
        return goodroom["lat"], goodroom["lon"], "goodroom_map", check
    if goodroom:
        return goodroom["lat"], goodroom["lon"], "goodroom_map", check
    if gsi:
        src = "gsi_" + gsi.get("precision", "town")
        return gsi["lat"], gsi["lon"], src, check
    return None, None, None, check
