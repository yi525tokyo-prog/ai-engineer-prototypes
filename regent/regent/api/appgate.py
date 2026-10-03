"""Opening the apps Regent built from anywhere, not only from the computer they run on.

On this computer an app opens at its own local address. When Regent is hosted, apps are served on
a second address of their own (the hosting front end marks those requests with
``x-regent-surface: apps``), never on Regent's own address: an app's code then cannot act on the
Regent page with your sign-in. ``/__open/<app>`` picks which app that address shows; every other
request there is passed to that app as if it were opened directly, so apps need nothing special.
"""

from __future__ import annotations

import hmac
import os
import time
from contextvars import ContextVar
from urllib.parse import quote

import httpx
from fastapi import Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

APPS_ORIGIN: ContextVar[str | None] = ContextVar("regent_apps_origin", default=None)
_HOP = {"host", "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
        "proxy-authenticate", "content-length", "content-encoding"}
_PORTS: dict[str, tuple[int, float]] = {}


def open_url(slug: str, local_url: str | None) -> str | None:
    """Where "Open the app" should go for the person looking at the page right now."""
    origin = APPS_ORIGIN.get()
    if not origin:
        return local_url
    key = os.environ.get("REGENT_ACCESS_KEY", "")
    return f"{origin.rstrip('/')}/__open/{quote(slug)}" + (f"?key={quote(key)}" if key else "")


def _port(slug: str, fresh: bool = False) -> int | None:
    hit = _PORTS.get(slug)
    if hit and not fresh and time.time() - hit[1] < 60:
        return hit[0]
    from regent import db as dbm
    from regent.software import appcap
    from regent.software.tables import SwCapability

    with dbm.session() as s:
        for c in s.query(SwCapability).all():
            if (c.spec or {}).get("app") and appcap.app_slug(c) == slug:
                svc = appcap.live(c)
                _PORTS[slug] = (svc.port, time.time())
                return svc.port
    return None


def _page(text: str, status: int) -> Response:
    return Response("<!doctype html><meta name=viewport content='width=device-width'>"
                    f"<body style='font-family:system-ui;padding:2em'>{text}", status,
                    media_type="text/html; charset=utf-8")


async def serve(request: Request) -> Response:
    key = os.environ.get("REGENT_ACCESS_KEY", "")
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    path = request.url.path
    if path.startswith("/__open/"):
        given = request.query_params.get("key") or request.cookies.get("regent_key") or ""
        if key and not hmac.compare_digest(given, key):
            return PlainTextResponse("This Regent is private. Open apps from the Regent page.", 401)
        resp = RedirectResponse("/", 303)
        resp.set_cookie("regent_app", path.removeprefix("/__open/"), httponly=True, samesite="lax", secure=https)
        if key:
            resp.set_cookie("regent_key", key, httponly=True, samesite="lax", secure=https, max_age=60 * 60 * 24 * 365)
        return resp
    if key and not hmac.compare_digest(request.cookies.get("regent_key", ""), key):
        return PlainTextResponse("This Regent is private. Open apps from the Regent page.", 401)
    slug = request.cookies.get("regent_app")
    if not slug:
        return _page("Open an app from the Regent page first.", 404)

    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _HOP
               and not k.lower().startswith(("x-regent-", "cf-")) and k.lower() != "cookie"]
    cookies = "; ".join(f"{k}={v}" for k, v in request.cookies.items() if k not in ("regent_key", "regent_app"))
    if cookies:
        headers.append(("cookie", cookies))
    body = await request.body()
    for attempt in (0, 1):
        port = await run_in_threadpool(_port, slug, attempt == 1)      # may start the app: off the event loop
        if port is None:
            return _page("Regent no longer has this app.", 404)
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False) as c:
                r = await c.request(request.method, request.url.path, params=request.url.query, headers=headers,
                                    content=body)
            break
        except httpx.TransportError:
            if attempt:
                return _page("This app is not answering right now. Try again in a moment.", 502)
    out = Response(r.content, r.status_code)
    for k, v in r.headers.multi_items():
        if k.lower() not in _HOP:
            out.headers.append(k, v)
    return out
