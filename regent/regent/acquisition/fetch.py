"""Fetcher: the only component that touches the network.

Policy (enforced here, not left to callers):

* identifies itself honestly (``RegentBot`` user agent with a contact URL);
* obeys robots.txt per host (cached; 5xx on robots.txt => disallow);
* rate-limits per host (min interval, or the site's Crawl-delay if larger);
* never bypasses CAPTCHA, login walls or access denials -- they are recorded
  as ``blocked`` with the reason, and the source's access outcome is learned;
* escalates to a real browser (Playwright) only when static HTML carries no
  content, using the environment's CA bundle for TLS trust (never disabling it);
* stores the raw page (gzip) for re-extraction, but the system of record is
  the claims extracted from it.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import os
import re
import subprocess
import threading
import time
import unicodedata
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.acquisition.tables import AcqDocument, AcqSource
from regent.acquisition.types import FetchedDocument
from regent.browser.driver import detect_blockers
from regent.config import settings
from regent.ids import new_id, utcnow

USER_AGENT = os.environ.get(
    "REGENT_USER_AGENT",
    "Mozilla/5.0 (compatible; RegentBot/0.1; research agent; +https://github.com/yi525tokyo-prog/ai-engineer-prototypes)",
)
ROBOTS_AGENT = "RegentBot"
MIN_INTERVAL_S = float(os.environ.get("REGENT_FETCH_MIN_INTERVAL", "2.0"))
ROBOTS_TTL = timedelta(hours=24)

_host_locks: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_global_lock = threading.Lock()


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


BLOCK_TAGS = ("td", "th", "tr", "li", "div", "p", "br", "dd", "dt", "dl", "ul", "ol", "table", "tbody", "thead",
              "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer", "label", "option")


def separate_blocks(tree) -> None:
    """Insert whitespace at cell/block boundaries so ``text_content()`` never glues
    neighbouring cells together (a room number "201" followed by a rent "9.6万円" must
    not read as "2019.6万円")."""
    for el in tree.iter(*BLOCK_TAGS):
        el.text = " " + (el.text or "")
        el.tail = " " + (el.tail or "")


def html_to_text(html: str) -> tuple[str, str]:
    """(title, visible text) with scripts/styles removed and NFKC normalization."""
    from lxml import html as LH

    try:
        doc = LH.fromstring(html)
    except Exception:
        return "", normalize_text(re.sub(r"<[^>]+>", " ", html))
    for bad in doc.xpath("//script|//style|//noscript|//template"):
        bad.drop_tree()
    separate_blocks(doc)
    title = normalize_text(" ".join(doc.xpath("//title/text()")))
    return title, normalize_text(doc.text_content())


def _trust_spki() -> str:
    """SPKI pins for exactly the CAs the environment trusts (for Chromium)."""
    bundle = os.environ.get("REGENT_BROWSER_TRUST_BUNDLE") or os.environ.get("SSL_CERT_FILE") \
        or ("/root/.ccr/ca-bundle.crt" if Path("/root/.ccr/ca-bundle.crt").exists() else "")
    if not bundle or not Path(bundle).exists():
        return ""
    cache = settings.workspace / "acq_cache" / "spki.txt"
    if cache.exists() and cache.stat().st_mtime > Path(bundle).stat().st_mtime:
        return cache.read_text()
    pems = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", Path(bundle).read_text(), re.S)
    out = []
    for pem in pems:
        try:
            pub = subprocess.run(["openssl", "x509", "-pubkey", "-noout"], input=pem.encode(), capture_output=True,
                                 timeout=5).stdout
            der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "der"], input=pub, capture_output=True,
                                 timeout=5).stdout
            if der:
                out.append(base64.b64encode(hashlib.sha256(der).digest()).decode())
        except Exception:
            continue
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(",".join(out))
    return ",".join(out)


class Fetcher:
    def __init__(self, db: Session, *, request_id: str | None = None, transport: httpx.BaseTransport | None = None,
                 allow_browser: bool = True, cache_ttl: timedelta = timedelta(hours=1),
                 min_interval_s: float = MIN_INTERVAL_S):
        self.db = db
        self.request_id = request_id
        self.transport = transport
        self.allow_browser = allow_browser and transport is None
        self.cache_ttl = cache_ttl
        self.min_interval_s = min_interval_s
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._client = httpx.Client(follow_redirects=True, timeout=25, transport=transport,
                                    headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.7"})
        self._pw = None
        self._browser = None
        self.cache_dir = settings.workspace / "acq_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- sources

    def source(self, host: str, kind: str = "portal") -> AcqSource:
        s = self.db.get(AcqSource, host)
        if s is None:
            s = AcqSource(host=host, kind=kind, notes={})
            self.db.add(s)
            self.db.flush()
        return s

    def robots(self, url: str) -> urllib.robotparser.RobotFileParser:
        p = urlparse(url)
        host = p.netloc
        if host in self._robots:
            return self._robots[host]
        src = self.source(host)
        rp = urllib.robotparser.RobotFileParser()
        fresh = src.robots_fetched_at and (utcnow() - _aware(src.robots_fetched_at)) < ROBOTS_TTL
        if not fresh:
            try:
                r = self._client.get(f"{p.scheme}://{host}/robots.txt")
                if r.status_code >= 500:
                    src.robots_txt = "User-agent: *\nDisallow: /\n"   # conservative on server error
                elif r.status_code >= 400:
                    src.robots_txt = ""                                # no robots.txt => allowed
                else:
                    src.robots_txt = r.text[:200000]
            except httpx.HTTPError:
                src.robots_txt = "User-agent: *\nDisallow: /\n"
            src.robots_fetched_at = utcnow()
        rp.parse((src.robots_txt or "").splitlines())
        self._robots[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        return self.robots(url).can_fetch(ROBOTS_AGENT, url)

    def _wait_turn(self, host: str) -> None:
        with _global_lock:
            lock = _host_locks.setdefault(host, threading.Lock())
        with lock:
            delay = self.min_interval_s
            rp = self._robots.get(host)
            if rp is not None:
                cd = rp.crawl_delay(ROBOTS_AGENT)
                if cd:
                    delay = max(delay, float(cd))
            wait = _host_last.get(host, 0.0) + delay - time.time()
            if wait > 0:
                time.sleep(wait)
            _host_last[host] = time.time()

    # --------------------------------------------------------------- fetch

    def fetch(self, url: str, *, purpose: str = "", render: str = "auto", kind: str = "portal",
              content_check: Any = None, on_static: Any = None) -> FetchedDocument:
        """Fetch ``url`` under policy. ``content_check(text) -> bool`` decides whether
        static HTML carries the content; if not (and render='auto'), render in a browser."""
        host = urlparse(url).netloc
        src = self.source(host, kind)
        # a recheck/verification exists to re-observe the source: never answer it from cache
        cached = None if purpose in ("recheck", "verify") else self._cached(url)
        if cached is not None:
            return cached
        if (src.blocked or 0) >= 3 and not src.ok and (src.last_status or "").startswith("blocked"):
            # this host has refused the crawler repeatedly and never answered: do not keep knocking
            known = self._record(url, url, host, purpose, 0, "", "static",
                                 blocked={"type": "known_blocked", "detail": f"host refused {src.blocked} times: "
                                                                             f"{src.last_status}"})
            known.from_cache = True          # no request was made: it costs no page budget
            return known
        if not self.allowed(url):
            src.disallowed += 1
            src.last_status = "robots_disallow"
            return self._record(url, url, host, purpose, 0, "", "static", blocked={"type": "robots",
                                "detail": "disallowed by robots.txt"}, robots_allowed=False)
        doc = None
        if render in ("static", "auto") or not self.allow_browser:   # no browser: static is all we have
            doc = self._static(url, host, purpose)
            if on_static is not None:
                on_static(doc)
            if render == "static" or not doc.ok or content_check is None or content_check(doc.text):
                return self._finish(src, doc)
        if self.allow_browser:
            rendered = self._browser_fetch(url, host, purpose)
            if rendered.ok or doc is None:
                return self._finish(src, rendered)
        return self._finish(src, doc)

    def _finish(self, src: AcqSource, doc: FetchedDocument) -> FetchedDocument:
        src.fetches += 1
        src.last_fetch_at = utcnow()
        if doc.ok:
            src.ok += 1
            src.last_status = f"ok {doc.render}"
        elif doc.blocked:
            src.blocked += 1
            src.last_status = f"blocked: {doc.blocked.get('type')}"
        else:
            src.last_status = f"error {doc.status} {doc.error or ''}"[:80]
        self.db.flush()
        return doc

    def _cached(self, url: str) -> FetchedDocument | None:
        since = utcnow() - self.cache_ttl
        row = self.db.scalar(select(AcqDocument).where(AcqDocument.url == url, AcqDocument.status == 200,
                                                       AcqDocument.blocked.is_(None))
                             .order_by(AcqDocument.fetched_at.desc()).limit(1))
        if row is None or _aware(row.fetched_at) < since or not row.cache_path or not Path(row.cache_path).exists():
            return None
        html = gzip.decompress(Path(row.cache_path).read_bytes()).decode("utf-8", "ignore")
        title, text = html_to_text(html)
        return FetchedDocument(id=row.id, url=url, final_url=row.final_url, host=row.host, status=200, html=html,
                               text=text, fetched_at=_aware(row.fetched_at), render=row.render, from_cache=True)

    def _static(self, url: str, host: str, purpose: str) -> FetchedDocument:
        self._wait_turn(host)
        # stateless crawling: sites personalise by cookie (e.g. redirecting an entry page to the
        # last region viewed), which would make one navigation depend on the previous one
        self._client.cookies.clear()
        try:
            r = self._client.get(url)
        except httpx.HTTPError as e:
            return self._record(url, url, host, purpose, 0, "", "static", error=f"{type(e).__name__}: {e}"[:300])
        final = str(r.url)
        if final != url and not self.allowed(final):
            return self._record(url, final, host, purpose, r.status_code, "", "static",
                                blocked={"type": "robots", "detail": f"redirect target disallowed: {final}"},
                                robots_allowed=False)
        html = r.text if "html" in r.headers.get("content-type", "html") or r.text.lstrip().startswith("<") else r.text
        blocked = None
        if r.status_code in (401, 403, 405, 429, 451):
            blocked = {"type": "rate_limit" if r.status_code == 429 else "access_denied",
                       "detail": f"HTTP {r.status_code}"}
        elif r.status_code == 202 and len(html.strip()) < 2000:
            # bot-management interstitial (empty 202 that expects JavaScript): an access control
            blocked = {"type": "challenge", "detail": "HTTP 202 bot challenge"}
        else:
            blocked = _page_blocker(html, final, r.status_code)
        return self._record(url, final, host, purpose, r.status_code, html, "static", blocked=blocked)

    def _browser_fetch(self, url: str, host: str, purpose: str) -> FetchedDocument:
        self._wait_turn(host)
        try:
            page = self._browser_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            html = page.content()
            status = resp.status if resp else 0
            final = page.url
            page.close()
        except Exception as e:
            return self._record(url, url, host, purpose, 0, "", "browser", error=f"browser: {str(e).splitlines()[0][:250]}")
        blocked = _page_blocker(html, final, status)
        if status in (401, 403, 405, 429, 451):
            blocked = {"type": "access_denied", "detail": f"HTTP {status}"}
        return self._record(url, final, host, purpose, status, html, "browser", blocked=blocked)

    def _browser_page(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            from regent.browser.driver import _chromium_executable

            self._pw = sync_playwright().start()
            args = []
            spki = _trust_spki()
            if spki:
                args.append(f"--ignore-certificate-errors-spki-list={spki}")
            kw: dict[str, Any] = {"headless": True, "args": args}
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            if proxy:
                kw["proxy"] = {"server": proxy}
            try:
                self._browser = self._pw.chromium.launch(**kw)
            except Exception:
                exe = _chromium_executable()
                self._browser = self._pw.chromium.launch(executable_path=exe, **kw)
            self._ctx = self._browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
        return self._ctx.new_page()

    def _record(self, url: str, final: str, host: str, purpose: str, status: int, html: str, render: str, *,
                blocked: dict | None = None, error: str | None = None, robots_allowed: bool = True) -> FetchedDocument:
        did = new_id("doc")
        path = ""
        h = hashlib.sha256(html.encode("utf-8", "ignore")).hexdigest() if html else ""
        if html and status == 200 and not blocked:
            p = self.cache_dir / f"{h[:32]}.html.gz"
            if not p.exists():
                p.write_bytes(gzip.compress(html.encode("utf-8", "ignore")))
            path = str(p)
        title, text = html_to_text(html) if html else ("", "")
        self.db.add(AcqDocument(id=did, request_id=self.request_id, url=url, final_url=final, host=host,
                                purpose=purpose, status=status, render=render, robots_allowed=robots_allowed,
                                blocked=blocked, error=error, title=title[:300], content_hash=h,
                                bytes=len(html.encode("utf-8", "ignore")) if html else 0, cache_path=path))
        self.db.flush()
        return FetchedDocument(id=did, url=url, final_url=final or url, host=host, status=status, html=html, text=text,
                               fetched_at=utcnow(), render=render, blocked=blocked, error=error)

    def download(self, url: str, dest: Path, *, kind: str = "public_data") -> Path | None:
        """Binary download (datasets) under the same robots/rate-limit policy."""
        host = urlparse(url).netloc
        src = self.source(host, kind)
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        if not self.allowed(url):
            src.disallowed += 1
            src.last_status = "robots_disallow"
            return None
        self._wait_turn(host)
        try:
            with self._client.stream("GET", url, timeout=120) as r:
                if r.status_code != 200:
                    src.last_status = f"error {r.status_code}"
                    return None
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
                tmp.rename(dest)
        except httpx.HTTPError as e:
            src.last_status = f"error {type(e).__name__}"
            return None
        src.fetches += 1
        src.ok += 1
        src.last_status = "ok download"
        self._record(url, url, host, "dataset", 200, "", "static")
        return dest

    def get_json(self, url: str, *, kind: str = "public_data") -> Any:
        doc = self.fetch(url, purpose="api", kind=kind, render="static")
        if not doc.ok:
            return None
        import json

        try:
            return json.loads(doc.html)
        except ValueError:
            return None

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        finally:
            self._browser = None
            self._pw = None
            self._client.close()


def _page_blocker(html: str, url: str, status: int) -> dict | None:
    """A human-verification wall blocks only when it *replaces* the content; a CAPTCHA
    widget on a content-rich page (e.g. an inquiry form) is not a block."""
    b = detect_blockers(html[:400000], url, status)
    if b is None or b.type not in ("captcha", "biometric"):
        return None
    _, text = html_to_text(html)
    if len(text) > 6000:
        return None
    return b.model_dump()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def absolute(base: str, href: str) -> str:
    return urljoin(base, href)
