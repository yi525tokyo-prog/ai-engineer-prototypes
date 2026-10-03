"""Minimal stdlib HTTP server for inspecting results.

GET  /                      single-page UI
GET  /api/results           all results (constraints + evaluation recomputed with current config)
GET  /api/config            current config
POST /api/config            save config (constraints/evaluation edits) -> results recompute instantly
POST /api/run               start pipeline in background {"acquire": bool}
GET  /api/status            pipeline job status + log tail
GET  /api/features?lat&lon  nearby roads/rails/POIs as GeoJSON for the map (from tile cache only)
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import save_config
from ..geo.features import FeatureSet, parse_elements
from ..geo.metrics import FeatureIndex
from ..geo.overpass import LAYERS, tiles_for
from ..http import FetchError
from ..pipeline import Context, flat_row, results, run_all

STATIC = Path(__file__).parent / "static"


class Job:
    def __init__(self) -> None:
        self.running = False
        self.log: list[str] = []
        self.started: float | None = None
        self.finished: float | None = None
        self.error: str | None = None
        self.lock = threading.Lock()

    def add(self, msg: str) -> None:
        with self.lock:
            self.log.append(time.strftime("%H:%M:%S ") + msg)
            self.log = self.log[-300:]


def compact(r: dict[str, Any]) -> dict[str, Any]:
    row = flat_row(r)
    row["estate_id"] = r["property"]["estate_id"]
    row["reasons_list"] = r["evaluation"].get("reasons") or []
    row["warnings_list"] = r["evaluation"].get("warnings") or []
    row["score_notes"] = r["evaluation"].get("score_notes") or {}
    row["rule_hits"] = r["evaluation"].get("rule_hits") or []
    row["constraint_failures"] = r["constraints"]["failures"]
    row["stations"] = r["property"].get("stations")
    row["environment"] = r["environment"]
    row["floor_source"] = r["property"].get("floor_source")
    row["total_rent"] = r["property"].get("total_rent")
    return row


def features_geojson(ctx: Context, lat: float, lon: float, radius: float = 600) -> dict[str, Any]:
    from shapely.geometry import Point, mapping

    fs, seen = FeatureSet(), set()
    for layer in LAYERS:
        for t in tiles_for(lat, lon, radius):
            try:
                parse_elements(ctx.tiles.get(layer, t, allow_fetch=False)["elements"], fs, seen)
            except FetchError:
                pass
    dlat, dlon = radius / 110574, radius / (111320 * 0.81)
    inb = lambda g: g.intersects(Point(lon, lat).buffer(max(dlat, dlon)))
    feats = []
    for r in fs.roads:
        if r.cls in ("motorway", "trunk", "primary", "secondary", "tertiary") and inb(r.geom):
            feats.append({"type": "Feature", "geometry": mapping(r.geom), "properties": {"kind": "road", "cls": r.cls, "name": r.name or r.ref, "surface": r.surface, "elevated": r.elevated}})
    for r in fs.rails:
        if inb(r.geom):
            feats.append({"type": "Feature", "geometry": mapping(r.geom), "properties": {"kind": "rail", "name": r.name, "surface": r.surface, "elevated": r.elevated, "minor": r.minor}})
    for p in fs.pois:
        if abs(p.lat - lat) < dlat and abs(p.lon - lon) < dlon:
            cat = "nightlife" if "nightlife" in p.categories else "food" if "food" in p.categories else "retail"
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]}, "properties": {"kind": "poi", "cat": cat, "name": p.name, "type": p.kind}})
    for j in FeatureIndex(FeatureSet(roads=fs.roads)).junctions:
        if abs(j["lat"] - lat) < dlat and abs(j["lon"] - lon) < dlon:
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [j["lon"], j["lat"]]}, "properties": {"kind": "junction", "name": " × ".join(j["roads"])}})
    return {"type": "FeatureCollection", "features": feats}


def make_handler(ctx: Context, job: Job):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif u.path == "/api/results":
                    ctx.reload_config()
                    rows = results(ctx, include_constraint_failures=q.get("all", ["0"])[0] == "1")
                    self._json({"rows": [compact(r) for r in rows], "last_run": ctx.store.last_run()})
                elif u.path == "/api/config":
                    ctx.reload_config()
                    self._json(ctx.cfg)
                elif u.path == "/api/status":
                    with job.lock:
                        self._json({"running": job.running, "log": job.log[-60:], "started": job.started, "finished": job.finished, "error": job.error})
                elif u.path == "/api/features":
                    self._json(features_geojson(ctx, float(q["lat"][0]), float(q["lon"][0])))
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # surface errors to the UI instead of hanging
                self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

        def do_POST(self) -> None:
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/api/config":
                ctx.reload_config()
                for sec in ("constraints", "evaluation", "acquisition"):
                    if sec in body:
                        ctx.cfg[sec] = body[sec]
                save_config(ctx.config_path, ctx.cfg)
                self._json({"ok": True})
            elif u.path == "/api/run":
                with job.lock:
                    if job.running:
                        self._json({"ok": False, "error": "already running"}, 409)
                        return
                    job.running, job.error, job.started, job.finished = True, None, time.time(), None
                    job.log = []

                def work() -> None:
                    try:
                        ctx.progress = job.add
                        run_all(ctx, skip_acquire=not body.get("acquire", True))
                    except Exception as e:
                        job.error = str(e)
                        job.add("ERROR " + traceback.format_exc())
                    finally:
                        job.running, job.finished = False, time.time()

                threading.Thread(target=work, daemon=True).start()
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)

    return H


def serve(ctx: Context, host: str = "127.0.0.1", port: int = 8765) -> None:
    job = Job()
    srv = ThreadingHTTPServer((host, port), make_handler(ctx, job))
    print(f"quiethousing UI on http://{host}:{port}")
    srv.serve_forever()
