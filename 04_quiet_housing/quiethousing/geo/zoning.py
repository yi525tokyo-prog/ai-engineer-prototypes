"""Official land-use zoning (用途地域) from MLIT 国土数値情報 A29.

OSM landuse is almost unmapped in Tokyo (median <1% of a 500 m disc), and OSM
POIs are under-mapped in places (e.g. Tsukishima's monja street). Zoning is
complete, authoritative and legally constrains what can exist nearby:
nightclubs/pachinko are only permitted in commercial-type zones, while
第一種低層住居専用地域 forbids shops beyond small ones and caps height. The
floor-area ratio (容積率) is a solid proxy for built density.

Data is per prefecture (A29-19_{pref}_GML.zip, GeoJSON inside), downloaded
on demand and reduced to a compact cache.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import box, shape
from shapely.strtree import STRtree

from ..http import Fetcher

log = logging.getLogger(__name__)
A29_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/A29/A29-19/A29-19_{code}_GML.zip"

PREF_CODES = {
    "北海道": "01", "青森県": "02", "岩手県": "03", "宮城県": "04", "秋田県": "05", "山形県": "06", "福島県": "07",
    "茨城県": "08", "栃木県": "09", "群馬県": "10", "埼玉県": "11", "千葉県": "12", "東京都": "13", "神奈川県": "14",
    "新潟県": "15", "富山県": "16", "石川県": "17", "福井県": "18", "山梨県": "19", "長野県": "20", "岐阜県": "21",
    "静岡県": "22", "愛知県": "23", "三重県": "24", "滋賀県": "25", "京都府": "26", "大阪府": "27", "兵庫県": "28",
    "奈良県": "29", "和歌山県": "30", "鳥取県": "31", "島根県": "32", "岡山県": "33", "広島県": "34", "山口県": "35",
    "徳島県": "36", "香川県": "37", "愛媛県": "38", "高知県": "39", "福岡県": "40", "佐賀県": "41", "長崎県": "42",
    "熊本県": "43", "大分県": "44", "宮崎県": "45", "鹿児島県": "46", "沖縄県": "47",
}

# A29_004 zoning code -> (Japanese name, group)
ZONES = {
    1: ("第一種低層住居専用地域", "low_residential"),
    2: ("第二種低層住居専用地域", "low_residential"),
    3: ("第一種中高層住居専用地域", "mid_residential"),
    4: ("第二種中高層住居専用地域", "mid_residential"),
    5: ("第一種住居地域", "residential"),
    6: ("第二種住居地域", "residential"),
    7: ("準住居地域", "roadside_residential"),
    21: ("田園住居地域", "low_residential"),
    8: ("近隣商業地域", "neighborhood_commercial"),
    9: ("商業地域", "commercial"),
    10: ("準工業地域", "light_industrial"),
    11: ("工業地域", "industrial"),
    12: ("工業専用地域", "industrial"),
}
GROUPS = ("low_residential", "mid_residential", "residential", "roadside_residential", "neighborhood_commercial", "commercial", "light_industrial", "industrial")


def compact_features(geojson: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for f in geojson.get("features", []):
        p = f.get("properties") or {}
        code = p.get("A29_004")
        if code is None or not f.get("geometry"):
            continue
        out.append({"code": int(code), "far": p.get("A29_007"), "bcr": p.get("A29_006"), "geometry": f["geometry"]})
    return out


class ZoningIndex:
    def __init__(self, features: list[dict[str, Any]]):
        geoms, self.codes, self.far = [], [], []
        for f in features:
            g = shape(f["geometry"])
            if not g.is_valid:
                g = g.buffer(0)
            if g.is_empty:
                continue
            geoms.append(g)
            self.codes.append(int(f["code"]))
            self.far.append(f.get("far"))
        self.geoms = np.array(geoms, dtype=object)
        self.tree = STRtree(self.geoms) if geoms else None
        self.loaded_prefs: set[str] = set()

    @classmethod
    def load(cls, cache_dir: str | Path, fetcher: Fetcher | None, prefectures: set[str]) -> "ZoningIndex":
        feats: list[dict[str, Any]] = []
        loaded = set()
        for pref in sorted(prefectures):
            code = PREF_CODES.get(pref)
            if not code:
                continue
            cache = Path(cache_dir) / f"zoning_a29_{code}.json"
            if not cache.exists():
                if fetcher is None:
                    continue
                url = A29_URL.format(code=code)
                log.info("downloading zoning %s (%s)", pref, url)
                resp = fetcher.request("GET", url, interval_s=0, timeout=300)
                if resp.status_code != 200:
                    log.warning("zoning download failed for %s: HTTP %s", pref, resp.status_code)
                    continue
                zf = zipfile.ZipFile(io.BytesIO(resp.content))
                name = next((n for n in zf.namelist() if n.endswith(f"{code}000.geojson")), None)
                if not name:
                    continue
                raw = zf.read(name)
                if raw[:3] == b"\xef\xbb\xbf":
                    raw = raw[3:]
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(compact_features(json.loads(raw.decode("utf-8"))), ensure_ascii=False), encoding="utf-8")
            feats.extend(json.loads(cache.read_text(encoding="utf-8")))
            loaded.add(pref)
        idx = cls(feats)
        idx.loaded_prefs = loaded
        return idx

    def measure(self, lat: float, lon: float, project_fn, disc: dict[int, Any]) -> dict[str, Any]:
        """Zoning at the point + area share per zoning group and area-weighted FAR in 250/500 m."""
        out: dict[str, Any] = {"point_code": None, "point_name": None, "point_group": None, "point_far": None}
        for r in (250, 500):
            for g in GROUPS:
                out[f"{g}_{r}"] = 0.0
            out[f"far_mean_{r}"] = None
            out[f"covered_{r}"] = 0.0
        if self.tree is None:
            return out
        from .metrics import M_PER_DEG_LAT, _kx

        dlat, dlon = 520 / M_PER_DEG_LAT, 520 / _kx(lat)
        idx = self.tree.query(box(lon - dlon, lat - dlat, lon + dlon, lat + dlat))
        if len(idx) == 0:
            return out
        geoms = project_fn(self.geoms[idx], lon, lat)
        origin = shapely.Point(0.0, 0.0)
        inside = shapely.contains(geoms, origin) | (shapely.distance(geoms, origin) < 1.0)
        hit = [k for k in range(len(idx)) if inside[k]]
        if hit:
            k = hit[0]
            code = self.codes[idx[k]]
            name, group = ZONES.get(code, (str(code), "other"))
            out.update({"point_code": code, "point_name": name, "point_group": group, "point_far": self.far[idx[k]]})
        for r in (250, 500):
            area = disc[r].area
            inter = shapely.intersection(geoms, disc[r])
            areas = shapely.area(inter)
            tot = float(areas.sum())
            far_w = 0.0
            for k in range(len(idx)):
                a = float(areas[k])
                if a <= 0:
                    continue
                code = self.codes[idx[k]]
                g = ZONES.get(code, (None, None))[1]
                if g:
                    out[f"{g}_{r}"] += a / area
                f = self.far[idx[k]]
                if f:
                    far_w += a * float(f)
            for g in GROUPS:
                out[f"{g}_{r}"] = round(out[f"{g}_{r}"], 3)
            out[f"covered_{r}"] = round(tot / area, 3)
            out[f"far_mean_{r}"] = round(far_w / tot) if tot > 0 else None
        return out
