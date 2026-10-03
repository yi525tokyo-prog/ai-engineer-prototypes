"""Configuration: housing constraints, acquisition scope and evaluation rules.

Everything a user may want to tune lives in one JSON file (default
`config.json` next to the data dir). Missing keys fall back to DEFAULTS, so an
old config keeps working when new knobs are added.

Three independent sections:
  acquisition  - what to crawl and how politely (never affects evaluation)
  constraints  - housing filters (rent, area, floor, ...) applied to listings
  evaluation   - environmental hard-reject rules + scoring curves + weights

Changing `constraints` or `evaluation` never requires re-crawling or
re-fetching geodata: measurements are stored, decisions are recomputed.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "acquisition": {
        # goodroom large areas: tokyo (Kanto), osaka, nagoya, fukuoka, hokkaido, hiroshima
        "regions": ["tokyo"],
        # optional goodroom small_area_cd prefilter (server side); [] = whole region
        "small_area_codes": [],
        "max_list_pages": 200,
        "list_page_size": 100,
        "request_interval_s": 1.5,
        "list_ttl_hours": 12,
        "detail_ttl_hours": 168,
        # only fetch detail/map pages for listings that pass list-level constraints
        "detail_only_for_constraint_pass": True,
        "max_details_per_run": 2000,
        "overpass_endpoints": [
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
        ],
        "overpass_interval_s": 2.0,
        "validate_coords_with_gsi": True,
    },
    "constraints": {
        "max_rent": None,  # yen, rent only
        "max_total_rent": 160000,  # yen, rent + management fee
        "min_floor_area_m2": 20,
        "prefectures": ["東京都"],
        "cities": [],  # e.g. ["世田谷区", "杉並区"]; [] = any
        "exclude_cities": [],
        "max_walk_minutes": 15,
        "min_floor": 2,  # None = no limit
        "unknown_floor_passes": True,
        "max_building_age_years": None,
        "layouts": [],  # e.g. ["1K", "1DK", "1LDK"]; [] = any
        "exclude_fixed_term_lease": False,
        "required_facilities": [],  # e.g. ["バストイレ別"]
    },
    "evaluation": {
        # quality = 100 - risk; KEEP needs quality >= min_quality and no hard rule hit
        "min_quality": 50,
        # hard rules: reject when metric <op> value. Metric names = profile keys.
        "hard_rules": [
            {"metric": "road.motorway_m", "op": "<", "value": 120, "label": "expressway (surface/elevated) {v}m away"},
            {"metric": "road.trunk_m", "op": "<", "value": 50, "label": "trunk road {v}m away"},
            {"metric": "road.primary_m", "op": "<", "value": 40, "label": "primary road {v}m away"},
            {"metric": "rail.surface_m", "op": "<", "value": 60, "label": "surface railway {v}m away"},
            {"metric": "poi.nightlife_250", "op": ">=", "value": 12, "label": "{v} nightlife POIs within 250m"},
            {"metric": "poi.commercial_250", "op": ">=", "value": 80, "label": "{v} commercial POIs within 250m"},
            {"metric": "intersection.major_m", "op": "<", "value": 50, "label": "major intersection {v}m away"},
            {"metric": "station.max_passengers_300", "op": ">=", "value": 200000, "label": "hub station ({v} riders/day) within 300m"},
        ],
        # risk curves: risk = weight * clamp((far - d) / (far - near)), near->full, far->0
        "road_curves": {
            "motorway": {"near": 60, "far": 500, "weight": 100},
            "trunk": {"near": 30, "far": 300, "weight": 85},
            "primary": {"near": 25, "far": 250, "weight": 75},
            "secondary": {"near": 15, "far": 150, "weight": 50},
            "tertiary": {"near": 8, "far": 70, "weight": 25},
        },
        "rail_curve": {"near": 30, "far": 400, "weight": 90},
        "rail_elevated_factor": 1.15,
        "rail_track_bonus_per_track": 0.08,
        "station_curve": {"passengers_full": 300000, "near": 150, "far": 800, "weight": 60},
        "intersection_curve": {"near": 30, "far": 200, "weight": 40},
        # density saturation: count at which the sub-score reaches 100
        "nightlife_saturation": {"nightlife_250": 15, "nightlife_500": 45},
        "commercial_saturation": {"commercial_250": 90, "commercial_500": 280, "landuse_commercial_250": 0.5},
        "weights": {"road": 0.35, "rail": 0.25, "nightlife": 0.2, "commercial": 0.2},
        # final risk = max_blend * max(sub) + (1 - max_blend) * weighted mean
        "max_blend": 0.5,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path | None) -> dict[str, Any]:
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as f:
            return _merge(DEFAULTS, json.load(f))
    return copy.deepcopy(DEFAULTS)


def save_config(path: str | Path, cfg: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def section_hash(cfg: dict[str, Any], section: str) -> str:
    return hashlib.sha1(json.dumps(cfg.get(section), sort_keys=True).encode()).hexdigest()[:12]
