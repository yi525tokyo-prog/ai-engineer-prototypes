"""Polite HTTP client: per-host rate limiting, retries with backoff, page cache."""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

import requests

from .store import Store

log = logging.getLogger(__name__)
USER_AGENT = "quiethousing/0.1 (personal housing research; low-rate crawler)"


class FetchError(RuntimeError):
    pass


class Fetcher:
    def __init__(self, store: Store | None, min_interval_s: float = 1.5, timeout_s: float = 60, retries: int = 3):
        self.store = store
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.5"})
        self._last_request: dict[str, float] = {}
        self.network_requests = 0
        self.cache_hits = 0

    def _throttle(self, host: str, interval: float) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def request(self, method: str, url: str, *, interval_s: float | None = None, **kw: Any) -> requests.Response:
        host = urlparse(url).netloc
        last_exc: Exception | None = None
        t = kw.pop("timeout", self.timeout_s)
        for attempt in range(self.retries):
            self._throttle(host, self.min_interval_s if interval_s is None else interval_s)
            try:
                self.network_requests += 1
                resp = self.session.request(method, url, timeout=(min(15, t), t), **kw)
                if resp.status_code in (429, 502, 503, 504):
                    raise FetchError(f"HTTP {resp.status_code}")
                return resp
            except (requests.RequestException, FetchError) as e:
                last_exc = e
                if attempt == self.retries - 1:
                    break
                backoff = 2 ** (attempt + 1)
                log.warning("fetch %s failed (%s); retry in %ss", url, e, backoff)
                time.sleep(backoff)
        raise FetchError(f"{url}: {last_exc}")

    def get_text(self, url: str, max_age_s: float | None = None, use_cache: bool = True) -> tuple[int, str]:
        """GET with page cache. max_age_s=None means any cached copy is fine."""
        if use_cache and self.store is not None:
            cached = self.store.get_page(url, max_age_s)
            if cached is not None:
                self.cache_hits += 1
                return cached
        resp = self.request("GET", url)
        resp.encoding = resp.encoding or "utf-8"
        text = resp.text
        if self.store is not None and resp.status_code in (200, 404, 410):
            self.store.put_page(url, resp.status_code, text)
        return resp.status_code, text
