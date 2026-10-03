"""Scoring / rejection logic: hard rules, curves, decisions and explicit reasons."""
import copy

from quiethousing.config import DEFAULTS
from quiethousing.scoring import decay, evaluate, evaluate_rules, get_metric

EV = DEFAULTS["evaluation"]


def quiet_profile():
    return {
        "road": {"motorway_m": None, "trunk_m": 1100, "primary_m": 620, "secondary_m": 300, "tertiary_m": 150, "residential_m": 10,
                 "nearest_major_m": 620, "nearest_major_type": "primary", "major_len_250": 0},
        "rail": {"surface_m": 1200, "tracks_at_nearest": 2, "nearest_elevated": False, "underground_m": None},
        "station": {"nearest_m": 700, "nearest_name": "X", "nearest_passengers": 20000, "max_passengers_300": 0, "max_passengers_800": 20000,
                    "biggest_800_name": "X", "biggest_800_m": 700},
        "intersection": {"major_m": 600},
        "poi": {"nightlife_250": 0, "nightlife_500": 2, "commercial_250": 6, "commercial_500": 30, "bar_250": 0, "karaoke_250": 0},
        "landuse": {"commercial_retail_250": 0.0, "residential_250": 0.8},
        "data": {"roads_within_search": 400, "poi_total_1000": 120},
    }


def test_decay_curve():
    assert decay(None, 30, 300) == 0.0
    assert decay(10, 30, 300) == 1.0
    assert decay(300, 30, 300) == 0.0
    assert abs(decay(165, 30, 300) - 0.5) < 1e-9


def test_get_metric_paths():
    p = quiet_profile()
    assert get_metric(p, "road.primary_m") == 620
    assert get_metric(p, "road.nope") is None
    assert get_metric(p, "nope.x") is None


def test_quiet_place_is_kept_with_reasons():
    ev = evaluate(quiet_profile(), EV)
    assert ev["decision"] == "KEEP"
    assert ev["quality"] >= 80
    text = " ".join(ev["reasons"])
    assert "nearest primary road 620m" in text
    assert "surface railway 1.2km" in text
    assert "low commercial activity" in text
    assert "predominantly residential" in text


def test_hard_rules_reject_with_measured_values():
    p = quiet_profile()
    p["road"]["primary_m"] = 38
    p["rail"]["surface_m"] = 55
    p["poi"]["nightlife_250"] = 20
    ev = evaluate(p, EV)
    assert ev["decision"] == "REJECT"
    assert "primary road 38m away" in ev["reasons"]
    assert "surface railway 55m away" in ev["reasons"]
    assert "20 nightlife POIs within 250m" in ev["reasons"]
    assert {h["metric"] for h in ev["rule_hits"]} == {"road.primary_m", "rail.surface_m", "poi.nightlife_250"}


def test_none_distance_never_triggers_rule():
    hits = evaluate_rules({"road": {"motorway_m": None}}, [{"metric": "road.motorway_m", "op": "<", "value": 100}])
    assert hits == []


def test_soft_threshold_rejects_when_quality_low():
    p = quiet_profile()
    # no single rule fires, but everything is moderately busy
    p["road"]["primary_m"] = 60
    p["road"]["major_len_250"] = 900
    p["rail"]["surface_m"] = 90
    p["rail"]["tracks_at_nearest"] = 4
    p["poi"].update({"nightlife_250": 10, "nightlife_500": 40, "commercial_250": 70, "commercial_500": 250})
    ev = evaluate(p, EV)
    assert not ev["rule_hits"]
    assert ev["decision"] == "REJECT"
    assert ev["reasons"][0].startswith("environment quality")


def test_thresholds_are_configurable_without_code_change():
    p = quiet_profile()
    ev2 = copy.deepcopy(EV)
    ev2["hard_rules"].append({"metric": "road.primary_m", "op": "<", "value": 700, "label": "primary road {v}m (strict)"})
    assert evaluate(p, EV)["decision"] == "KEEP"
    out = evaluate(p, ev2)
    assert out["decision"] == "REJECT" and out["reasons"] == ["primary road 620m (strict)"]
    ev3 = copy.deepcopy(EV)
    ev3["hard_rules"] = [dict(r, enabled=False) for r in ev3["hard_rules"]]
    p["road"]["primary_m"] = 10
    assert not evaluate(p, ev3)["rule_hits"]


def test_scores_monotonic_in_distance():
    q = []
    for d in (20, 80, 150, 400):
        p = quiet_profile()
        p["road"]["primary_m"] = d
        q.append(evaluate(p, EV)["scores"]["road_noise_score"])
    assert q == sorted(q, reverse=True)


def test_missing_profile_is_unknown():
    ev = evaluate(None, EV)
    assert ev["decision"] == "UNKNOWN"


def test_data_warnings():
    p = quiet_profile()
    p["data"]["poi_total_1000"] = 0
    ev = evaluate(p, EV, {"coord_source": "gsi_town", "coord_check": None})
    assert any("under-mapped" in w for w in ev["warnings"])
    assert any("approximate coordinates" in w for w in ev["warnings"])
