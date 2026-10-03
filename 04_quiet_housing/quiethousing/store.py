"""SQLite persistence: listings, raw-page cache, geocode cache, environment profiles.

Raw HTML is stored gzip-compressed so parsers can be improved and re-run
without hitting goodroom again. Environment profiles are keyed by a rounded
location (~1m) + feature-extractor version, so rooms in the same building
share one computation and an extractor change invalidates cleanly.
"""
from __future__ import annotations

import gzip
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS page_cache (
    url TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    status INTEGER NOT NULL,
    body BLOB
);
CREATE TABLE IF NOT EXISTS listings (
    estate_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    region TEXT,
    first_seen REAL,
    last_seen REAL,
    active INTEGER DEFAULT 1,
    list_json TEXT,
    detail_json TEXT,
    detail_fetched_at REAL,
    lat REAL,
    lon REAL,
    coord_source TEXT,
    coord_check_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(active);
CREATE TABLE IF NOT EXISTS geocode_cache (
    query TEXT PRIMARY KEY,
    provider TEXT,
    fetched_at REAL,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS environment (
    loc_key TEXT PRIMARY KEY,
    lat REAL, lon REAL,
    version TEXT,
    computed_at REAL,
    profile_json TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL, finished_at REAL,
    stats_json TEXT
);
"""


def loc_key(lat: float, lon: float) -> str:
    return f"{lat:.5f},{lon:.5f}"


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # --- page cache -----------------------------------------------------
    def get_page(self, url: str, max_age_s: float | None) -> tuple[int, str] | None:
        with self._lock:
            row = self.conn.execute("SELECT fetched_at, status, body FROM page_cache WHERE url=?", (url,)).fetchone()
        if not row:
            return None
        if max_age_s is not None and time.time() - row["fetched_at"] > max_age_s:
            return None
        body = gzip.decompress(row["body"]).decode("utf-8") if row["body"] else ""
        return row["status"], body

    def put_page(self, url: str, status: int, body: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO page_cache(url, fetched_at, status, body) VALUES (?,?,?,?)",
                (url, time.time(), status, gzip.compress(body.encode("utf-8"))),
            )
            self.conn.commit()

    # --- listings -------------------------------------------------------
    def upsert_list_stub(self, stub: dict[str, Any], seen_at: float) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO listings(estate_id, url, region, first_seen, last_seen, active, list_json)
                   VALUES (?,?,?,?,?,1,?)
                   ON CONFLICT(estate_id) DO UPDATE SET
                     url=excluded.url, region=excluded.region, last_seen=excluded.last_seen,
                     active=1, list_json=excluded.list_json""",
                (stub["estate_id"], stub["url"], stub.get("region"), seen_at, seen_at, json.dumps(stub, ensure_ascii=False)),
            )

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def mark_inactive_except(self, region: str, seen_after: float) -> int:
        """Listings of a fully-crawled region not seen in this crawl are no longer on goodroom."""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE listings SET active=0 WHERE region=? AND (last_seen IS NULL OR last_seen < ?) AND active=1",
                (region, seen_after),
            )
            self.conn.commit()
            return cur.rowcount

    def set_detail(self, estate_id: str, detail: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE listings SET detail_json=?, detail_fetched_at=? WHERE estate_id=?",
                (json.dumps(detail, ensure_ascii=False), time.time(), estate_id),
            )
            self.conn.commit()

    def set_coords(self, estate_id: str, lat: float | None, lon: float | None, source: str | None, check: dict | None) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE listings SET lat=?, lon=?, coord_source=?, coord_check_json=? WHERE estate_id=?",
                (lat, lon, source, json.dumps(check, ensure_ascii=False) if check else None, estate_id),
            )
            self.conn.commit()

    def listings(self, active_only: bool = True) -> list[dict[str, Any]]:
        q = "SELECT * FROM listings" + (" WHERE active=1" if active_only else "")
        with self._lock:
            rows = self.conn.execute(q).fetchall()
        return [self._row_to_listing(r) for r in rows]

    def listing(self, estate_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self.conn.execute("SELECT * FROM listings WHERE estate_id=?", (estate_id,)).fetchone()
        return self._row_to_listing(r) if r else None

    @staticmethod
    def _row_to_listing(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "estate_id": r["estate_id"],
            "url": r["url"],
            "region": r["region"],
            "active": bool(r["active"]),
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "list": json.loads(r["list_json"]) if r["list_json"] else {},
            "detail": json.loads(r["detail_json"]) if r["detail_json"] else None,
            "detail_fetched_at": r["detail_fetched_at"],
            "lat": r["lat"],
            "lon": r["lon"],
            "coord_source": r["coord_source"],
            "coord_check": json.loads(r["coord_check_json"]) if r["coord_check_json"] else None,
        }

    # --- geocode cache --------------------------------------------------
    def get_geocode(self, query: str) -> tuple[bool, Any]:
        with self._lock:
            r = self.conn.execute("SELECT result_json FROM geocode_cache WHERE query=?", (query,)).fetchone()
        if not r:
            return False, None
        return True, json.loads(r["result_json"])

    def put_geocode(self, query: str, provider: str, result: Any) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO geocode_cache(query, provider, fetched_at, result_json) VALUES (?,?,?,?)",
                (query, provider, time.time(), json.dumps(result, ensure_ascii=False)),
            )
            self.conn.commit()

    # --- environment ----------------------------------------------------
    def get_environment(self, lat: float, lon: float, version: str) -> dict[str, Any] | None:
        with self._lock:
            r = self.conn.execute(
                "SELECT profile_json FROM environment WHERE loc_key=? AND version=?", (loc_key(lat, lon), version)
            ).fetchone()
        return json.loads(r["profile_json"]) if r else None

    def put_environment(self, lat: float, lon: float, version: str, profile: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO environment(loc_key, lat, lon, version, computed_at, profile_json) VALUES (?,?,?,?,?,?)",
                (loc_key(lat, lon), lat, lon, version, time.time(), json.dumps(profile, ensure_ascii=False)),
            )
            self.conn.commit()

    def environments(self, keys: Iterable[str], version: str) -> dict[str, dict[str, Any]]:
        keys = list(set(keys))
        out: dict[str, dict[str, Any]] = {}
        with self._lock:
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                q = f"SELECT loc_key, profile_json FROM environment WHERE version=? AND loc_key IN ({','.join('?' * len(chunk))})"
                for r in self.conn.execute(q, [version, *chunk]):
                    out[r["loc_key"]] = json.loads(r["profile_json"])
        return out

    # --- runs -----------------------------------------------------------
    def record_run(self, started: float, stats: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO runs(started_at, finished_at, stats_json) VALUES (?,?,?)",
                (started, time.time(), json.dumps(stats, ensure_ascii=False)),
            )
            self.conn.commit()

    def last_run(self) -> dict[str, Any] | None:
        with self._lock:
            r = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return {"started_at": r["started_at"], "finished_at": r["finished_at"], "stats": json.loads(r["stats_json"])} if r else None
