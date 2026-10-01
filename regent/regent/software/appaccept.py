"""Regent runs the acceptance scenarios itself against a running instance.

API steps go through a plain HTTP client; browser steps through headless Chromium against the
application's real UI (``data-testid`` hooks); ``restart_app`` stops and restarts the process to
prove state survives; negative scenarios prove what must not be possible. The worker's own tests
are run separately -- they are evidence about the worker's intent, these are Regent's verdict.

Authentication follows Regent's runtime contract: ``POST /api/login {"passphrase"}`` returns
``{"token"}``; non-public endpoints require ``Authorization: Bearer <token>``; the UI login form has
``data-testid`` ``login-passphrase`` and ``login-submit``.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from regent.software.appservice import AppService


def _subst(v: Any, vars_: dict[str, Any]) -> Any:
    if isinstance(v, str):
        return re.sub(r"\{(\w+)\}", lambda m: str(vars_.get(m.group(1), m.group(0))), v)
    if isinstance(v, dict):
        return {k: _subst(x, vars_) for k, x in v.items()}
    if isinstance(v, list):
        return [_subst(x, vars_) for x in v]
    return v


def _dig(data: Any, path: str) -> Any:
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def contains(data: Any, expected: dict[str, Any]) -> list[str]:
    """Each expected key/value must appear somewhere in the JSON (strings: case-insensitive substring)."""
    missing = []
    for k, v in (expected or {}).items():
        if not _find(data, k, v):
            missing.append(f"{k}={v!r}")
    return missing


def _find(data: Any, key: str, value: Any) -> bool:
    if isinstance(data, dict):
        if key in data and _match(data[key], value):
            return True
        return any(_find(x, key, value) for x in data.values())
    if isinstance(data, list):
        return any(_find(x, key, value) for x in data)
    return False


def _match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() in actual.lower()
    if isinstance(expected, (int, float)) and not isinstance(expected, bool) and isinstance(actual, (int, float)):
        return float(actual) == float(expected)
    if isinstance(expected, str) and isinstance(actual, (int, float)):
        return expected == str(actual)
    return actual == expected


class Runner:
    def __init__(self, svc: AppService, design: dict[str, Any], passphrase: str | None):
        self.svc = svc
        self.design = design
        self.api = {a["id"]: a for a in design.get("api", [])}
        self.passphrase = passphrase
        self.token: str | None = None
        self.client = httpx.Client(timeout=20, trust_env=False)

    def login(self) -> str | None:
        if not self.passphrase:
            return None
        r = self.client.post(self.svc.url + "/api/login", json={"passphrase": self.passphrase})
        if r.status_code >= 300:
            raise AssertionError(f"login failed: HTTP {r.status_code} {r.text[:200]}")
        self.token = (r.json() or {}).get("token")
        if not self.token:
            raise AssertionError(f"login returned no token: {r.text[:200]}")
        return self.token

    def call(self, api_id: str, path_params: dict | None, body: Any, auth: bool = True) -> httpx.Response:
        a = self.api[api_id]
        path = a["path"]
        for k, v in (path_params or {}).items():
            path = path.replace("{" + k + "}", str(v))
        headers = {}
        if auth and self.passphrase:
            if not self.token:
                self.login()
            headers["Authorization"] = f"Bearer {self.token}"
        return self.client.request(a["method"], self.svc.url + path, json=body if body is not None else None,
                                   headers=headers)

    def run(self, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        results = []
        browser_needed = any(st["do"] in ("goto", "fill", "click", "expect_text", "expect_no_text", "reload",
                                          "login", "logout")
                             for sc in (scenarios or self.design.get("scenarios", [])) for st in sc.get("steps", []))
        pw = page = browser = None
        try:
            if browser_needed:
                from playwright.sync_api import sync_playwright

                from regent.browser.driver import _chromium_executable

                pw = sync_playwright().start()
                try:
                    browser = pw.chromium.launch(headless=True)
                except Exception:
                    browser = pw.chromium.launch(headless=True, executable_path=_chromium_executable())
            for sc in scenarios or self.design.get("scenarios", []):
                ctx = browser.new_context() if browser else None
                page = ctx.new_page() if ctx else None
                results.append(self._scenario(sc, page))
                if ctx:
                    ctx.close()
        finally:
            if browser:
                browser.close()
            if pw:
                pw.stop()
            self.client.close()
        return {"passed": all(r["passed"] for r in results), "scenarios": results,
                "by_id": {r["id"]: r["passed"] for r in results}}

    def _scenario(self, sc: dict[str, Any], page) -> dict[str, Any]:
        vars_: dict[str, Any] = {}
        log = []
        try:
            for i, st in enumerate(sc.get("steps", [])):
                st = _subst(st, vars_)
                note = self._step(st, page, vars_)
                log.append(f"{i}: {st['do']} {st.get('api') or st.get('target') or st.get('path') or ''} ok {note}")
            return {"id": sc["id"], "requirement": sc.get("requirement"), "kind": sc.get("kind"), "passed": True,
                    "log": log[-12:]}
        except Exception as e:
            shot = None
            try:
                if page is not None:
                    shot = page.content()[:1500]
            except Exception:
                pass
            return {"id": sc["id"], "requirement": sc.get("requirement"), "kind": sc.get("kind"), "passed": False,
                    "failed_step": len(log), "error": f"{type(e).__name__}: {str(e)[:500]}", "log": log[-12:],
                    "page": shot}

    def _step(self, st: dict[str, Any], page, vars_: dict[str, Any]) -> str:
        do = st["do"]
        if do == "call":
            r = self.call(st["api"], st.get("path_params"), st.get("body"), auth=st.get("auth", True))
            want = st.get("expect_status")
            if want is not None and r.status_code != int(want):
                raise AssertionError(f"{st['api']}: HTTP {r.status_code}, expected {want}: {r.text[:300]}")
            if want is None and r.status_code >= 400:
                raise AssertionError(f"{st['api']}: HTTP {r.status_code}: {r.text[:300]}")
            data = None
            if r.content:
                try:
                    data = r.json()
                except ValueError:
                    data = None
            if st.get("expect_json_contains"):
                miss = contains(data, st["expect_json_contains"])
                if miss:
                    raise AssertionError(f"{st['api']}: response lacks {miss}: {json.dumps(data)[:300]}")
            for var, path in (st.get("save") or {}).items():
                vars_[var] = _dig(data, path)
                if vars_[var] is None:
                    raise AssertionError(f"{st['api']}: nothing at '{path}' to save as {var}: {json.dumps(data)[:200]}")
            return f"HTTP {r.status_code}"
        if do == "restart_app":
            res = self.svc.restart()
            if not res.get("ok"):
                raise AssertionError(f"restart failed: {res.get('error')} {res.get('log_tail', '')[-300:]}")
            self.token = None
            if page is not None:
                try:
                    page.reload()
                except Exception:
                    pass
            return "restarted"
        if page is None:
            raise AssertionError(f"browser step {do} without a browser")
        if do == "goto":
            page.goto(self.svc.url + (st.get("path") or "/"))
            page.wait_for_load_state("networkidle", timeout=8000)
        elif do == "login":
            page.goto(self.svc.url + (st.get("path") or "/"))
            page.wait_for_selector('[data-testid="login-passphrase"]', timeout=8000)
            page.fill('[data-testid="login-passphrase"]', self.passphrase or "")
            page.click('[data-testid="login-submit"]')
            page.wait_for_load_state("networkidle", timeout=8000)
        elif do == "logout":
            page.context.clear_cookies()
            page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
            page.goto(self.svc.url + "/")
        elif do == "fill":
            page.wait_for_selector(f'[data-testid="{st["target"]}"]', timeout=8000)
            page.fill(f'[data-testid="{st["target"]}"]', st.get("value") or "")
        elif do == "click":
            page.wait_for_selector(f'[data-testid="{st["target"]}"]', timeout=8000)
            page.click(f'[data-testid="{st["target"]}"]')
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
        elif do == "reload":
            page.reload()
            page.wait_for_load_state("networkidle", timeout=8000)
        elif do in ("expect_text", "expect_no_text"):
            text = st.get("text") or st.get("value") or ""
            deadline = time.time() + 6
            seen = False
            while time.time() < deadline:
                seen = text.lower() in page.inner_text("body").lower()
                if seen == (do == "expect_text"):
                    break
                time.sleep(0.3)
            if do == "expect_text" and not seen:
                raise AssertionError(f"text not shown: {text!r}")
            if do == "expect_no_text" and seen:
                raise AssertionError(f"text shown but must not be: {text!r}")
        return ""


def snapshot(svc: AppService, design: dict[str, Any], passphrase: str | None) -> dict[str, Any]:
    """Everything the application's list endpoints return now (used to prove an upgrade kept data)."""
    r = Runner(svc, design, passphrase)
    out = {}
    try:
        for a in design.get("api", []):
            if a["method"] == "GET" and "{" not in a["path"] and not a["path"].rstrip("/").endswith("health"):
                try:
                    resp = r.call(a["id"], None, None, auth=not a.get("public"))
                    if resp.status_code == 200:
                        out[a["path"]] = resp.json()
                except Exception:
                    continue
    finally:
        r.client.close()
    return out


def records(data: Any) -> list[dict[str, Any]]:
    """Flat list of JSON objects with scalar fields (the records an endpoint returned)."""
    out = []
    if isinstance(data, dict):
        scal = {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool)) and v is not None}
        if scal:
            out.append(scal)
        for v in data.values():
            out += records(v)
    elif isinstance(data, list):
        for v in data:
            out += records(v)
    return out


def preserved(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Every record an endpoint returned before must still be returned after, with its fields
    (new fields may be added; volatile timestamps of the update itself are ignored)."""
    lost = []
    for path, data in before.items():
        new = records(after.get(path))
        for rec in records(data):
            keep = {k: v for k, v in rec.items() if not re.search(r"updated|modified|token|expires", k, re.I)}
            if not any(all(n.get(k) == v for k, v in keep.items()) for n in new):
                lost.append(f"{path}: {json.dumps(keep, ensure_ascii=False)[:160]}")
    return lost
