"""End-to-end pipeline: goodroom -> constraints -> coordinates -> OSM/S12 -> profile -> decision.

Each stage is incremental and idempotent:
  1. acquire_lists     crawl search pages (cached with TTL), upsert listing stubs
  2. acquire_details   detail + map pages only for listings passing list-level constraints
  3. resolve_coords    goodroom map pin, cross-checked with GSI geocoding
  4. enrich            environment profile per unique location (cached by location+version)
  5. results           constraints + evaluation recomputed from stored data (cheap, no network)
"""
from __future__ import annotations

import csv
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from .config import load_config
from .constraints import build_property, check_constraints
from .geo.features import FeatureSet, parse_elements
from .geo.metrics import METRICS_VERSION, SEARCH_M, FeatureIndex
from .geo.overpass import LAYERS, OverpassTileCache, layer_version, tile_of, tiles_for
from .geo.stations import StationIndex
from .geocode import choose_coordinates, gsi_geocode
from .goodroom.parse import BASE_URL, parse_detail_page, parse_list_page, parse_map_page
from .http import FetchError, Fetcher
from .scoring import evaluate
from .store import Store, loc_key

log = logging.getLogger(__name__)


def environment_version() -> str:
    return f"{METRICS_VERSION}|" + "|".join(f"{l}:{layer_version(l)}" for l in sorted(LAYERS)) + "|s12-24"


@dataclass
class Context:
    data_dir: Path
    config_path: Path
    cfg: dict[str, Any] = field(default_factory=dict)
    store: Store | None = None
    fetcher: Fetcher | None = None
    tiles: OverpassTileCache | None = None
    stations: StationIndex | None = None
    progress: Callable[[str], None] = lambda msg: log.info(msg)

    @classmethod
    def open(cls, data_dir: str | Path, config_path: str | Path | None = None, offline: bool = False) -> "Context":
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        cpath = Path(config_path) if config_path else data_dir / "config.json"
        cfg = load_config(cpath)
        store = Store(data_dir / "quiethousing.sqlite")
        acq = cfg["acquisition"]
        fetcher = None if offline else Fetcher(store, min_interval_s=acq["request_interval_s"])
        # Overpass gets its own fetcher without internal retries: endpoint rotation + bbox splitting handle failures
        op_fetcher = None if offline else Fetcher(None, min_interval_s=acq["overpass_interval_s"], timeout_s=240, retries=1)
        tiles = OverpassTileCache(data_dir / "cache" / "overpass", op_fetcher, acq["overpass_endpoints"], acq["overpass_interval_s"])
        return cls(data_dir=data_dir, config_path=cpath, cfg=cfg, store=store, fetcher=fetcher, tiles=tiles)

    def reload_config(self) -> None:
        self.cfg = load_config(self.config_path)

    def station_index(self) -> StationIndex:
        if self.stations is None:
            self.stations = StationIndex.load(self.data_dir / "cache", self.fetcher)
        return self.stations


# ---------------------------------------------------------------------------
def list_url(region: str, page: int, page_size: int, small_area_codes: list[str]) -> str:
    q: list[tuple[str, Any]] = [("rent_count", page_size), ("sort", "new_rent_desc")]
    q += [("small_area_cd[]", c) for c in small_area_codes]
    if page > 1:
        q.append(("p", page))
    return f"{BASE_URL}/{region}/search/estate_list/?{urlencode(q)}"


def acquire_lists(ctx: Context) -> dict[str, Any]:
    acq = ctx.cfg["acquisition"]
    stats = {"pages": 0, "stubs": 0, "inactive_marked": 0}
    if ctx.fetcher is None:
        return stats
    for region in acq["regions"]:
        crawl_start = time.time()
        page, complete, total = 1, False, None
        while page <= acq["max_list_pages"]:
            url = list_url(region, page, acq["list_page_size"], acq.get("small_area_codes") or [])
            status, html = ctx.fetcher.get_text(url, max_age_s=acq["list_ttl_hours"] * 3600)
            if status != 200:
                ctx.progress(f"list page {page} -> HTTP {status}; stopping region {region}")
                break
            parsed = parse_list_page(html)
            total = parsed["total"] or total
            now = time.time()
            for stub in parsed["listings"]:
                ctx.store.upsert_list_stub(stub, now)
            ctx.store.commit()
            stats["pages"] += 1
            stats["stubs"] += len(parsed["listings"])
            if page == 1 or page % 10 == 0:
                ctx.progress(f"[{region}] list page {page}: {len(parsed['listings'])} listings (site total {total})")
            if not parsed["has_next"] or not parsed["listings"]:
                complete = True
                break
            page += 1
        if complete and not acq.get("small_area_codes"):
            stats["inactive_marked"] += ctx.store.mark_inactive_except(region, crawl_start)
    return stats


def acquire_details(ctx: Context) -> dict[str, Any]:
    acq = ctx.cfg["acquisition"]
    cons = ctx.cfg["constraints"]
    stats = {"candidates": 0, "fetched": 0, "failed": 0, "skipped_by_constraints": 0}
    if ctx.fetcher is None:
        return stats
    ttl = acq["detail_ttl_hours"] * 3600
    todo = []
    for lst in ctx.store.listings():
        if acq.get("detail_only_for_constraint_pass", True):
            chk = check_constraints(build_property(lst), cons)
            if not chk["passed"]:
                stats["skipped_by_constraints"] += 1
                continue
        stats["candidates"] += 1
        if lst["detail"] is None or (lst["detail_fetched_at"] or 0) < time.time() - ttl or lst["lat"] is None:
            todo.append(lst)
    todo = todo[: acq["max_details_per_run"]]
    ctx.progress(f"details: {stats['candidates']} candidates pass list-level constraints; fetching {len(todo)}")
    for n, lst in enumerate(todo, 1):
        try:
            status, html = ctx.fetcher.get_text(lst["url"], max_age_s=ttl)
            if status != 200:
                stats["failed"] += 1
                continue
            detail = parse_detail_page(html, today_year=date.today().year)
            mstatus, mhtml = ctx.fetcher.get_text(lst["url"] + "map/", max_age_s=None)
            detail["goodroom_coords"] = parse_map_page(mhtml) if mstatus == 200 else None
            ctx.store.set_detail(lst["estate_id"], detail)
            stats["fetched"] += 1
        except FetchError as e:
            stats["failed"] += 1
            log.warning("detail %s failed: %s", lst["url"], e)
        if n % 25 == 0:
            ctx.progress(f"details: {n}/{len(todo)}")
    return stats


def resolve_coords(ctx: Context) -> dict[str, Any]:
    acq = ctx.cfg["acquisition"]
    stats = {"resolved": 0, "unresolved": 0, "gsi_override": 0}
    for lst in ctx.store.listings():
        det = lst["detail"]
        if not det or lst["lat"] is not None:
            continue
        gr = det.get("goodroom_coords")
        gsi = None
        if det.get("address") and ctx.fetcher is not None and (acq.get("validate_coords_with_gsi") or not gr):
            try:
                gsi = gsi_geocode(ctx.fetcher, ctx.store, det["address"])
            except FetchError as e:
                log.warning("gsi geocode failed for %s: %s", det["address"], e)
        lat, lon, src, check = choose_coordinates(gr, gsi)
        ctx.store.set_coords(lst["estate_id"], lat, lon, src, check)
        if lat is None:
            stats["unresolved"] += 1
        else:
            stats["resolved"] += 1
            stats["gsi_override"] += src == "gsi_house"
    return stats


def enrich(ctx: Context, only_constraint_pass: bool = True, allow_fetch: bool = True) -> dict[str, Any]:
    """Compute environment profiles for every located listing lacking one."""
    version = environment_version()
    cons = ctx.cfg["constraints"]
    pending: dict[str, tuple[float, float]] = {}
    for lst in ctx.store.listings():
        if lst["lat"] is None:
            continue
        if only_constraint_pass and not check_constraints(build_property(lst), cons)["passed"]:
            continue
        pending[loc_key(lst["lat"], lst["lon"])] = (lst["lat"], lst["lon"])
    have = ctx.store.environments(pending.keys(), version)
    todo = {k: v for k, v in pending.items() if k not in have}
    stats = {"locations": len(pending), "cached": len(have), "computed": 0, "failed": 0, "overpass_queries_before": ctx.tiles.network_queries}
    if not todo:
        return stats
    stations = ctx.station_index()
    groups: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for lat, lon in todo.values():
        groups[tile_of(lat, lon)].append((lat, lon))
    all_tiles = {t for pts in groups.values() for lat, lon in pts for t in tiles_for(lat, lon, SEARCH_M + 50)}
    missing = sum(1 for t in all_tiles for l in LAYERS if not ctx.tiles.has(l, t))
    ctx.progress(f"enrich: {len(todo)} new locations in {len(groups)} tile groups; {len(all_tiles)} tiles needed, {missing} tile-layers to download")
    for gi, (anchor, pts) in enumerate(sorted(groups.items()), 1):
        tiles = sorted({t for lat, lon in pts for t in tiles_for(lat, lon, SEARCH_M + 50)})
        try:
            fs, seen = FeatureSet(), set()
            for layer in LAYERS:
                for data in ctx.tiles.get_many(layer, tiles, allow_fetch=allow_fetch):
                    parse_elements(data["elements"], fs, seen)
        except FetchError as e:
            ctx.progress(f"enrich: tile group {anchor} failed: {e}")
            stats["failed"] += len(pts)
            continue
        fi = FeatureIndex(fs, stations)
        for lat, lon in pts:
            prof = fi.measure(lat, lon)
            prof["tiles"] = [list(t) for t in tiles]
            ctx.store.put_environment(lat, lon, version, prof)
            stats["computed"] += 1
        ctx.progress(f"enrich: group {gi}/{len(groups)} {anchor}: {len(pts)} locations, {len(tiles)} tiles (overpass queries so far {ctx.tiles.network_queries})")
    stats["overpass_queries"] = ctx.tiles.network_queries - stats.pop("overpass_queries_before")
    return stats


# ---------------------------------------------------------------------------
def results(ctx: Context, include_constraint_failures: bool = False) -> list[dict[str, Any]]:
    """Recompute constraints + evaluation for all stored listings (no network)."""
    version = environment_version()
    listings = ctx.store.listings()
    keys = [loc_key(l["lat"], l["lon"]) for l in listings if l["lat"] is not None]
    envs = ctx.store.environments(keys, version)
    out = []
    for lst in listings:
        prop = build_property(lst)
        chk = check_constraints(prop, ctx.cfg["constraints"])
        if not chk["passed"] and not include_constraint_failures:
            continue
        env = envs.get(loc_key(lst["lat"], lst["lon"])) if lst["lat"] is not None else None
        ev = evaluate(env, ctx.cfg["evaluation"], prop) if chk["passed"] else {"decision": "FILTERED", "reasons": chk["failures"], "quality": None, "scores": {}}
        out.append({"property": prop, "constraints": chk, "environment": env, "evaluation": ev})
    order = {"KEEP": 0, "REJECT": 1, "UNKNOWN": 2, "FILTERED": 3}
    out.sort(key=lambda r: (order.get(r["evaluation"]["decision"], 9), -(r["evaluation"].get("quality") or -1)))
    return out


def flat_row(r: dict[str, Any]) -> dict[str, Any]:
    p, env, ev = r["property"], r["environment"] or {}, r["evaluation"]
    g = lambda sec, k: (env.get(sec) or {}).get(k)
    return {
        "decision": ev["decision"], "quality": ev.get("quality"),
        **{k: v for k, v in (ev.get("scores") or {}).items()},
        "url": p["url"], "building": p.get("building_name"), "title": p.get("title"),
        "rent": p.get("rent"), "management_fee": p.get("management_fee"), "layout": p.get("layout"),
        "floor_area_m2": p.get("floor_area_m2"), "floor": p.get("floor"), "total_floors": p.get("total_floors"),
        "built_year": p.get("built_year"), "address": p.get("address"), "nearest_station": p.get("nearest_station"),
        "walk_minutes": p.get("walk_minutes"), "lat": p.get("lat"), "lon": p.get("lon"), "coord_source": p.get("coord_source"),
        "nearest_major_road_m": g("road", "nearest_major_m"), "nearest_major_road_type": g("road", "nearest_major_type"),
        "motorway_m": g("road", "motorway_m"), "trunk_m": g("road", "trunk_m"), "primary_m": g("road", "primary_m"),
        "secondary_m": g("road", "secondary_m"), "nearest_railway_m": g("rail", "surface_m"),
        "tracks_at_nearest": g("rail", "tracks_at_nearest"), "track_len_500": g("rail", "track_len_500"),
        "nearest_station_m": g("station", "nearest_m"), "nearest_station_passengers": g("station", "nearest_passengers"),
        "max_station_passengers_800": g("station", "max_passengers_800"),
        "major_intersection_m": g("intersection", "major_m"),
        "nightlife_250": g("poi", "nightlife_250"), "nightlife_500": g("poi", "nightlife_500"),
        "commercial_250": g("poi", "commercial_250"), "commercial_500": g("poi", "commercial_500"),
        "landuse_commercial_250": g("landuse", "commercial_retail_250"), "landuse_residential_250": g("landuse", "residential_250"),
        "reasons": " | ".join(ev.get("reasons") or []), "warnings": " | ".join(ev.get("warnings") or []),
    }


def export(ctx: Context, rows: list[dict[str, Any]]) -> dict[str, str]:
    out_json = ctx.data_dir / "results.json"
    out_csv = ctx.data_dir / "results.csv"
    out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    flat = [flat_row(r) for r in rows]
    if flat:
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(flat[0].keys()))
            w.writeheader()
            w.writerows(flat)
    return {"json": str(out_json), "csv": str(out_csv)}


def run_all(ctx: Context, skip_acquire: bool = False) -> dict[str, Any]:
    started = time.time()
    ctx.reload_config()
    stats: dict[str, Any] = {}
    if not skip_acquire:
        stats["lists"] = acquire_lists(ctx)
        ctx.progress(f"lists done: {stats['lists']}")
        stats["details"] = acquire_details(ctx)
        ctx.progress(f"details done: {stats['details']}")
    stats["coords"] = resolve_coords(ctx)
    ctx.progress(f"coords done: {stats['coords']}")
    stats["enrich"] = enrich(ctx)
    ctx.progress(f"enrich done: {stats['enrich']}")
    rows = results(ctx)
    dec = defaultdict(int)
    for r in rows:
        dec[r["evaluation"]["decision"]] += 1
    stats["decisions"] = dict(dec)
    stats["exports"] = export(ctx, rows)
    ctx.store.record_run(started, stats)
    ctx.progress(f"run finished in {time.time() - started:.0f}s: {dict(dec)}")
    return stats
