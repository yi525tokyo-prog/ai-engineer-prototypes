"""Record / replay real fetched pages.

``export_fixtures`` copies documents Regent actually fetched from the public web
(the gzipped cache written by ``Fetcher._record``) plus each host's robots.txt
into a fixture directory. ``ReplayTransport`` serves them back to the same
``Fetcher`` through httpx, so tests exercise the real extraction, navigation,
resolution and funnel code on real pages without network access.

Recorded pages are *not* edited except that ``<script>``/``<style>``/``<svg>``
bodies and comments are removed to keep fixtures small (the extractor strips
them anyway). URLs that were never recorded answer 503; blocked responses are
replayed with their original status.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

_STRIP = re.compile(r"<(style|svg|noscript)\b[^>]*>.*?</\1\s*>|<!--.*?-->", re.S | re.I)
_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
_DATA_SCRIPT = re.compile(r"^\s*(?:(?:window\.[\w$.\[\]'\"]+|var\s+\w+|self\.\w+)\s*=\s*)?[\{\[]", re.S)


def slim(html: str) -> str:
    """Drop styles, SVG and executable code; keep structured data (JSON-LD, application/json,
    embedded app state) -- extractors read listings from those."""
    def keep(m: re.Match) -> str:
        attrs, body = m.group(1), m.group(2)
        if re.search(r"application/(?:ld\+)?json|__NEXT_DATA__", attrs, re.I) or _DATA_SCRIPT.match(body):
            return m.group(0)
        return ""
    return _SCRIPT.sub(keep, _STRIP.sub("", html))


class ReplayTransport(httpx.BaseTransport):
    def __init__(self, root: Path | str):
        self.root = Path(root)
        idx = json.loads((self.root / "index.json").read_text())
        self.pages: dict[str, dict[str, Any]] = idx.get("pages", {})
        self.robots: dict[str, str] = idx.get("robots", {})
        self.requests: list[str] = []
        # sites put volatile session parameters in links (?lid=<random>): same host+path is the same page
        self.by_path: dict[str, list[str]] = {}
        for u in self.pages:
            p = urlparse(u)
            self.by_path.setdefault(f"{p.netloc}{p.path}", []).append(u)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        u = urlparse(url)
        if u.path == "/robots.txt":
            txt = self.robots.get(u.netloc)
            return httpx.Response(200 if txt is not None else 404, text=txt or "", request=request)
        p = self.pages.get(url)
        if p is None:
            same = self.by_path.get(f"{u.netloc}{u.path}") or []
            if same:
                want = set(u.query.split("&"))
                best = max(same, key=lambda x: len(want & set(urlparse(x).query.split("&"))))
                p = self.pages[best]
        if p is None:
            # 503, not 404: a URL that was never recorded says nothing about the listing (a 404
            # would read as "listing ended" to a recheck)
            return httpx.Response(503, text="<html><body>not recorded</body></html>", request=request,
                                  headers={"content-type": "text/html"})
        final = p.get("final_url")
        if final and final != url and final in self.pages:
            # reproduce the recorded redirect: relative links must resolve against the final URL
            return httpx.Response(302, headers={"location": final}, request=request)
        body = gzip.decompress((self.root / p["file"]).read_bytes()) if p.get("file") else b""
        headers = {"content-type": "text/html; charset=utf-8", **(p.get("headers") or {})}
        headers.pop("content-encoding", None)
        headers.pop("content-length", None)
        headers.pop("transfer-encoding", None)
        return httpx.Response(p.get("status", 200), content=body, request=request, headers=headers)


def export_fixtures(db, dest: Path | str, *, request_ids: list[str] | None = None,
                    url_filter=None, keep_scripts: bool = False) -> dict[str, int]:
    """Write fixtures for documents fetched by the given requests (best rendering per URL)."""
    from sqlalchemy import select

    from regent.acquisition.tables import AcqDocument, AcqSource

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    q = select(AcqDocument).order_by(AcqDocument.fetched_at)
    if request_ids:
        q = q.where(AcqDocument.request_id.in_(request_ids))
    best: dict[str, AcqDocument] = {}
    for d in db.scalars(q):
        if url_filter and not url_filter(d.url):
            continue
        cur = best.get(d.url)
        # prefer the rendering that yielded records (e.g. the browser-rendered copy of a JS page)
        if cur is None or (d.mentions or 0, d.status == 200) > (cur.mentions or 0, cur.status == 200):
            best[d.url] = d
    index: dict[str, Any] = {"pages": {}, "robots": {}}
    for url, d in best.items():
        entry: dict[str, Any] = {"status": d.status, "render": d.render, "purpose": d.purpose,
                                 "fetched_at": d.fetched_at.isoformat(), "final_url": d.final_url}
        if d.headers:
            entry["headers"] = {k: v for k, v in d.headers.items() if k not in ("set-cookie", "date", "cf-ray",
                                                                                 "report-to", "nel")}
        if d.cache_path and Path(d.cache_path).exists():
            html = gzip.decompress(Path(d.cache_path).read_bytes()).decode("utf-8", "ignore")
            name = f"{d.content_hash[:20]}.html.gz"
            body = html if keep_scripts else slim(html)
            (dest / name).write_bytes(gzip.compress(body.encode("utf-8"), 9))
            entry["file"] = name
        index["pages"][url] = entry
        if d.final_url and d.final_url != url:
            index["pages"].setdefault(d.final_url, {**entry, "final_url": d.final_url})
    for host in {urlparse(u).netloc for u in index["pages"]}:
        src = db.get(AcqSource, host)
        if src is not None and src.robots_txt:
            index["robots"][host] = src.robots_txt
    (dest / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True))
    return {"pages": len(index["pages"]), "robots": len(index["robots"])}
