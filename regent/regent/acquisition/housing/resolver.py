"""HousingEntityResolver: Building -> Unit identity across listing sources.

Buildings are blocked by municipality + town and scored on name similarity,
chome/banchi agreement, construction year, total floors, station/walk overlap
and (when geocoded) distance. Units are only compared within the same
building and scored on layout, floor area, floor, room number and rent.
Rent is weak evidence: different sites legitimately disagree on it -- that
disagreement is what the claim layer is for, not a reason to split a unit.
Two different rows of the *same* page are never the same unit.
"""

from __future__ import annotations

import math
from difflib import SequenceMatcher
from typing import Any

from regent.acquisition.housing import text as T


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class HousingEntityResolver:
    def block_keys(self, entity_type: str, f: dict[str, Any]) -> tuple[str, list[str]]:
        if entity_type == "building":
            city, town = f.get("city") or "", (f.get("town") or "").rstrip("0123456789-")
            if not city:
                return "?", ["?"]
            store = f"{city}|{town}"
            return store, [store] if town else [f"{city}|"]
        if entity_type == "area":
            return f.get("area", ""), [f.get("area", "")]
        return "", [""]   # units: candidates are the parent's children

    def score(self, entity_type: str, m: dict[str, Any], e: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        if entity_type == "building":
            return self._building(m, e)
        if entity_type == "unit":
            return self._unit(m, e)
        if entity_type == "area":
            same = m.get("area") == e.get("area")
            return (0.99 if same else 0.0), {"area_equal": same}
        return 0.0, {}

    def _building(self, m: dict, e: dict) -> tuple[float, dict]:
        logit, d = -2.0, {}
        n1, n2 = m.get("name_key") or "", e.get("name_key") or ""
        if n1 and n2:
            sim = SequenceMatcher(None, n1, n2).ratio()
            d["name_sim"] = round(sim, 3)
            logit += 4.5 if sim >= 0.9 else (2.5 if sim >= 0.75 else (0 if sim >= 0.5 else -3.5))
        c1, c2 = m.get("chome"), e.get("chome")
        if c1 is not None and c2 is not None:
            d["chome_equal"] = c1 == c2
            logit += 1.5 if c1 == c2 else -5
        a1, a2 = (m.get("address") or ""), (e.get("address") or "")
        if a1 and a2 and "-" in a1 and "-" in a2:
            d["address_equal"] = a1 == a2
            logit += 2.5 if a1 == a2 else -2.0
        b1, b2 = m.get("built_year"), e.get("built_year")
        if b1 and b2:
            diff = abs(int(b1) - int(b2))
            d["built_year_diff"] = diff
            logit += 2.0 if diff == 0 else (0.5 if diff == 1 else -3.0)
        f1, f2 = m.get("floors_total"), e.get("floors_total")
        if f1 and f2:
            d["floors_equal"] = f1 == f2
            logit += 1.0 if f1 == f2 else -2.5
        s1 = {s["station"]: s["walk_min"] for s in (m.get("stations") or [])}
        s2 = {s["station"]: s["walk_min"] for s in (e.get("stations") or [])}
        if s1 and s2:
            common = set(s1) & set(s2)
            close = [k for k in common if abs(s1[k] - s2[k]) <= 2]
            d["stations_common"] = sorted(common)
            # the same building's walk time differs by rounding (<= 2 min) across sites, not by more
            logit += 1.5 if close else (-2.0 if common else -1.5)
        g1, g2 = m.get("coords"), e.get("coords")
        if g1 and g2:
            dist = _haversine_m(g1, g2)
            d["distance_m"] = round(dist)
            logit += 2.0 if dist <= 60 else (-4.0 if dist > 300 else 0)
        if not (n1 and n2) and not d.get("address_equal") and not (g1 and g2):
            # without a name, a street number or coordinates, block-level agreement (chome, year,
            # stations) fits many buildings: at most "ambiguous" -- a matching unit inside can
            # still lift it to a merge through joint evidence
            logit = min(logit, 1.0)
            d["identity_capped"] = "no name/street number on one side"
        p = _sig(logit)
        d["logit"] = round(logit, 2)
        return p, d

    def _unit(self, m: dict, e: dict) -> tuple[float, dict]:
        logit, d = -1.0, {}
        docs = set(e.get("_docs") or [])
        if m.get("_doc") in docs:
            identical = all(m.get(k) == e.get(k) for k in ("layout", "area_m2", "floor", "room_number", "rent"))
            d["same_document"] = True
            if not identical:
                return 0.01, d
        l1, l2 = m.get("layout"), e.get("layout")
        if l1 and l2:
            d["layout_equal"] = l1 == l2
            logit += 1.5 if l1 == l2 else -6
        a1, a2 = m.get("area_m2"), e.get("area_m2")
        if a1 and a2:
            diff = abs(float(a1) - float(a2))
            d["area_diff"] = round(diff, 2)
            logit += 3.5 if diff <= 0.1 else (0 if diff <= 0.3 else (-1 if diff <= 0.6 else (-3 if diff <= 1.5 else -6)))
        f1, f2 = m.get("floor"), e.get("floor")
        if f1 is not None and f2 is not None:
            d["floor_equal"] = f1 == f2
            logit += 1.5 if f1 == f2 else -5
        r1, r2 = m.get("room_number"), e.get("room_number")
        if r1 and r2:
            d["room_equal"] = r1 == r2
            logit += 4.0 if r1 == r2 else -6
        p1 = m.get("rent")
        same_host = (e.get("_terms_by_host") or {}).get(m.get("_host") or "", [])
        if p1 and same_host:
            # One site shows one set of terms per listing at a time: different rent / fees / deposit /
            # key money on the *same* site means a different room or a different agent's listing
            # (kept apart as separate listings), never a conflict to average away.
            same = any(_same_terms(_terms(m), t) for t in same_host)
            d["same_host_terms_equal"] = same
            logit += 1.0 if same else -6
        elif p1 and e.get("rent"):
            # across sites a price difference is a *claim conflict*, not evidence of another room --
            # unless it is large
            p2 = float(e["rent"])
            rel = abs(float(p1) - p2) / max(float(p1), p2)
            d["rent_rel_diff"] = round(rel, 3)
            logit += 1.5 if rel <= 0.01 else (0.5 if rel <= 0.03 else (-0.5 if rel <= 0.06 else -2))
        d["logit"] = round(logit, 2)
        return _sig(logit), d

    def merge(self, entity_type: str, e: dict[str, Any], m: dict[str, Any]) -> dict[str, Any]:
        out = dict(e)
        for k, v in m.items():
            if k.startswith("_"):
                continue
            if k == "stations":
                have = {s["station"] for s in out.get("stations") or []}
                out["stations"] = list(out.get("stations") or []) + [s for s in v or [] if s["station"] not in have]
            elif out.get(k) in (None, "", []) and v not in (None, "", []):
                out[k] = v
        if m.get("_doc"):
            out["_docs"] = sorted(set(out.get("_docs") or []) | {m["_doc"]})
        if m.get("_host") and m.get("rent"):
            tb = dict(out.get("_terms_by_host") or {})
            seen = list(tb.get(m["_host"], []))
            if _terms(m) not in seen:
                seen.append(_terms(m))
            tb[m["_host"]] = seen[-20:]
            out["_terms_by_host"] = tb
        return out

    def label(self, entity_type: str, f: dict[str, Any]) -> str:
        if entity_type == "building":
            return f.get("name") or f.get("address") or "unnamed building"
        if entity_type == "unit":
            parts = [f.get("layout"), f"{f['area_m2']}m²" if f.get("area_m2") else None,
                     f"{f['floor']}F" if f.get("floor") is not None else None,
                     f"#{f['room_number']}" if f.get("room_number") else None]
            return " ".join(p for p in parts if p) or "unit"
        return f.get("area") or entity_type


TERMS = ("rent", "management_fee", "deposit", "key_money")


def _terms(f: dict[str, Any]) -> list[float | None]:
    return [None if f.get(k) is None else float(f[k]) for k in TERMS]


def _same_terms(a: list[float | None], b: list[float | None]) -> bool:
    """Rent within 0.5 %, other amounts within 100 yen; a term missing on either side is no evidence."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x is None or y is None:
            continue
        if i == 0 and abs(x - y) / max(x, y, 1.0) > 0.005:
            return False
        if i > 0 and abs(x - y) > 100:
            return False
    return True


def _haversine_m(a: list[float], b: list[float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def building_features(mention_keys: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    f = dict(mention_keys)
    f["name"] = claims.get("name")
    f["stations"] = claims.get("stations") or []
    if claims.get("address") and not f.get("address"):
        f["address"] = claims["address"]
        f.update({k: v for k, v in T.parse_address(claims["address"]).items() if k in ("city", "town", "chome")})
    return f
