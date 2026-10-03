"""Transparent environmental evaluation: measurements -> sub-scores -> decision.

No learned or LLM judgement here. Every number in the output can be traced to
a profile measurement and a threshold in config["evaluation"]:

  * hard rules   - "reject if road.primary_m < 40" style; each hit becomes a
                   human-readable reason quoting the measured value.
  * risk curves  - piecewise-linear distance decay per source (0 = far, weight = adjacent).
  * densities    - counts normalised by a saturation count.
  * quality      - 100 - blend(max sub-risk, weighted mean sub-risk).

Sub-scores are *risk* (0 = quiet, 100 = very noisy/busy). `quality` is the
inverse summary (higher = better for focused work), used for ranking.
"""
from __future__ import annotations

import operator
from typing import Any

OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}


def get_metric(profile: dict[str, Any], path: str) -> Any:
    cur: Any = profile
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def decay(d: float | None, near: float, far: float) -> float:
    """1.0 at/below `near`, 0.0 at/above `far`, linear between. None (nothing found) -> 0."""
    if d is None:
        return 0.0
    if d <= near:
        return 1.0
    if d >= far:
        return 0.0
    return (far - d) / (far - near)


def _sat(v: float | None, full: float) -> float:
    if not v or not full:
        return 0.0
    return min(1.0, float(v) / float(full))


def fmt_m(v: float | None) -> str:
    if v is None:
        return "none within 1.2km"
    return f"{v / 1000:.1f}km" if v >= 1000 else f"{int(v)}m"


def evaluate_rules(profile: dict[str, Any], rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for rule in rules:
        if rule.get("enabled") is False:
            continue
        v = get_metric(profile, rule["metric"])
        if v is None:  # "nothing within search radius" never trips a < rule, and unknown never trips >=
            continue
        op = OPS[rule["op"]]
        if op(v, rule["value"]):
            label = rule.get("label") or f"{rule['metric']} {rule['op']} {rule['value']}"
            vs = f"{v:,}" if isinstance(v, int) and v >= 10000 else str(v)
            hits.append({"metric": rule["metric"], "value": v, "threshold": rule["value"], "op": rule["op"], "reason": label.replace("{v}", vs)})
    return hits


def road_risk(p: dict[str, Any], ev: dict[str, Any]) -> tuple[float, list[str]]:
    road = p.get("road") or {}
    contrib = []
    for cls, curve in ev["road_curves"].items():
        d = road.get(f"{cls}_m")
        r = curve["weight"] * decay(d, curve["near"], curve["far"])
        if cls == "motorway" and road.get("motorway_elevated") and r > 0:
            r = min(100.0, r * 1.1)
        contrib.append((r, cls, d))
    score = max((c[0] for c in contrib), default=0.0)
    # several major roads close by add up (corner plots, road canyons)
    extra = min(15.0, (road.get("major_len_250") or 0) / 1000 * 10)
    score = min(100.0, score + extra)
    top = sorted([c for c in contrib if c[0] > 0], reverse=True)[:2]
    notes = [f"{cls} road {fmt_m(d)} (risk {r:.0f})" for r, cls, d in top]
    return score, notes


def rail_risk(p: dict[str, Any], ev: dict[str, Any]) -> tuple[float, list[str]]:
    rail = p.get("rail") or {}
    st = p.get("station") or {}
    c = ev["rail_curve"]
    d = rail.get("surface_m")
    r = c["weight"] * decay(d, c["near"], c["far"])
    if r > 0:
        tracks = max(1, rail.get("tracks_at_nearest") or 1)
        r *= 1 + ev.get("rail_track_bonus_per_track", 0.08) * (min(tracks, 8) - 1)
        if rail.get("nearest_elevated"):
            r *= ev.get("rail_elevated_factor", 1.15)
    sc = ev["station_curve"]
    # big stations nearby: crowd/announcement/commercial spill-over
    s_dist = st.get("biggest_800_m")
    s_pass = st.get("max_passengers_800") or 0
    s_r = sc["weight"] * _sat(s_pass, sc["passengers_full"]) * decay(s_dist, sc["near"], sc["far"])
    score = min(100.0, max(r, s_r) + 0.25 * min(r, s_r))
    notes = []
    if d is not None and r > 0:
        notes.append(f"surface railway {fmt_m(d)} ({rail.get('nearest_name') or rail.get('nearest_kind')}, {rail.get('tracks_at_nearest')} tracks{', elevated' if rail.get('nearest_elevated') else ''})")
    if s_r > 5:
        notes.append(f"{st.get('biggest_800_name')} station ({s_pass:,}/day) {fmt_m(s_dist)}")
    return score, notes


def nightlife_risk(p: dict[str, Any], ev: dict[str, Any]) -> tuple[float, list[str]]:
    poi = p.get("poi") or {}
    sat = ev["nightlife_saturation"]
    s = 100 * max(_sat(poi.get("nightlife_250"), sat["nightlife_250"]), 0.8 * _sat(poi.get("nightlife_500"), sat["nightlife_500"]))
    notes = []
    if s > 0:
        notes.append(f"{poi.get('nightlife_250', 0)} nightlife POIs ≤250m, {poi.get('nightlife_500', 0)} ≤500m (bars {poi.get('bar_250', 0)}, karaoke {poi.get('karaoke_250', 0)})")
    return s, notes


def commercial_risk(p: dict[str, Any], ev: dict[str, Any]) -> tuple[float, list[str]]:
    poi = p.get("poi") or {}
    lu = p.get("landuse") or {}
    it = p.get("intersection") or {}
    sat = ev["commercial_saturation"]
    s_poi = max(_sat(poi.get("commercial_250"), sat["commercial_250"]), 0.85 * _sat(poi.get("commercial_500"), sat["commercial_500"]))
    s_lu = _sat(lu.get("commercial_retail_250"), sat["landuse_commercial_250"])
    ic = ev["intersection_curve"]
    s_int = ic["weight"] / 100 * decay(it.get("major_m"), ic["near"], ic["far"])
    s = 100 * min(1.0, max(s_poi, 0.7 * s_lu, s_int) + 0.15 * min(s_poi, s_lu))
    notes = []
    if s_poi > 0:
        notes.append(f"{poi.get('commercial_250', 0)} shops/restaurants ≤250m, {poi.get('commercial_500', 0)} ≤500m")
    if s_lu > 0.1:
        notes.append(f"commercial/retail landuse {lu.get('commercial_retail_250', 0) * 100:.0f}% of 250m disc")
    if s_int > 0.1:
        notes.append(f"major intersection {fmt_m(it.get('major_m'))}")
    return s, notes


def positive_notes(p: dict[str, Any]) -> list[str]:
    road, rail, poi, lu, st = (p.get(k) or {} for k in ("road", "rail", "poi", "landuse", "station"))
    notes = []
    major = [(road.get(f"{c}_m"), c) for c in ("motorway", "trunk", "primary")]
    major = [(d, c) for d, c in major if d is not None]
    if major:
        d, c = min(major)
        notes.append(f"nearest {c} road {fmt_m(d)}")
    else:
        notes.append("no motorway/trunk/primary road within 1.2km")
    if road.get("secondary_m") is not None:
        notes.append(f"nearest secondary road {fmt_m(road.get('secondary_m'))}")
    notes.append(f"surface railway {fmt_m(rail.get('surface_m'))}" + (f" (underground line {fmt_m(rail.get('underground_m'))})" if rail.get("underground_m") is not None and (rail.get("surface_m") is None or rail["underground_m"] < rail["surface_m"]) else ""))
    c250 = poi.get("commercial_250", 0)
    notes.append(f"{'low' if c250 < 25 else 'moderate' if c250 < 60 else 'high'} commercial activity ({c250} POIs ≤250m, {poi.get('nightlife_250', 0)} nightlife)")
    if (lu.get("residential_250") or 0) >= 0.5:
        notes.append(f"predominantly residential landuse ({lu['residential_250'] * 100:.0f}% of 250m disc)")
    if st.get("nearest_m") is not None:
        notes.append(f"nearest station {st.get('nearest_name')} {fmt_m(st['nearest_m'])}" + (f" ({st['nearest_passengers']:,}/day)" if st.get("nearest_passengers") else ""))
    return notes


def data_warnings(p: dict[str, Any], prop: dict[str, Any] | None = None) -> list[str]:
    w = []
    d = p.get("data") or {}
    if (d.get("roads_within_search") or 0) < 20:
        w.append("very few OSM roads around: coordinates or OSM coverage suspect")
    if (d.get("poi_total_1000") or 0) < 5:
        w.append("almost no OSM POIs within 1km: commercial/nightlife may be under-mapped")
    if prop:
        src = prop.get("coord_source")
        if src and src not in ("goodroom_map", "gsi_house"):
            w.append(f"approximate coordinates ({src})")
        chk = prop.get("coord_check") or {}
        if chk.get("note"):
            w.append(chk["note"])
    return w


def evaluate(profile: dict[str, Any] | None, ev: dict[str, Any], prop: dict[str, Any] | None = None) -> dict[str, Any]:
    if not profile:
        return {"decision": "UNKNOWN", "reasons": ["no environmental profile (coordinates or geodata missing)"], "quality": None, "scores": {}}
    subs = {}
    detail = {}
    for name, fn in (("road", road_risk), ("rail", rail_risk), ("nightlife", nightlife_risk), ("commercial", commercial_risk)):
        s, notes = fn(profile, ev)
        subs[name] = round(s, 1)
        detail[name] = notes
    w = ev["weights"]
    wsum = sum(w.values()) or 1.0
    mean = sum(subs[k] * w.get(k, 0) for k in subs) / wsum
    risk = ev.get("max_blend", 0.5) * max(subs.values()) + (1 - ev.get("max_blend", 0.5)) * mean
    quality = round(100 - risk, 1)
    hits = evaluate_rules(profile, ev.get("hard_rules", []))
    reasons: list[str]
    if hits:
        decision = "REJECT"
        reasons = [h["reason"] for h in hits]
    elif quality < ev.get("min_quality", 50):
        decision = "REJECT"
        worst = max(subs, key=subs.get)
        reasons = [f"environment quality {quality} < {ev.get('min_quality')} (worst: {worst} risk {subs[worst]:.0f})"] + detail[worst]
    else:
        decision = "KEEP"
        reasons = positive_notes(profile)
    return {
        "decision": decision,
        "quality": quality,
        "scores": {
            "road_noise_score": subs["road"],
            "railway_noise_score": subs["rail"],
            "nightlife_score": subs["nightlife"],
            "commercial_activity_score": subs["commercial"],
            "total_environment_score": quality,
        },
        "rule_hits": hits,
        "reasons": reasons,
        "score_notes": detail,
        "warnings": data_warnings(profile, prop),
    }
