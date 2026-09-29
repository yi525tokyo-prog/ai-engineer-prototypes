"""Browser automation.

``PlaywrightDriver`` drives real Chromium. ``HttpDriver`` is a degraded fallback
(fetch + static HTML inspection, no JS / clicks) used when Playwright cannot
launch. Both run the same step language and the same blocker detection.

Regent never attempts to bypass CAPTCHA or identity checks: when a blocker is
detected, the run stops and returns ``status="blocked"`` so the executor can
raise a bounded human interrupt and re-run the steps once it is resolved.

Step language::

    {"do": "navigate", "url": "..."}
    {"do": "inspect"}                                  # title, text, links, forms, blockers
    {"do": "click", "selector": "#confirm"}
    {"do": "type", "selector": "input[name=q]", "text": "..."}
    {"do": "extract", "selector": "#note", "as": "note"}
    {"do": "wait", "selector": "#done"} | {"do": "wait", "ms": 500}
    {"do": "screenshot", "path": "shot.png"}
    {"do": "download", "selector": "a#file", "path": "out.bin"}
    {"do": "upload", "selector": "input[type=file]", "path": "file.txt"}
"""

from __future__ import annotations

import glob
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from regent.config import settings
from regent.schemas import Blocked

CAPTCHA_PATTERNS = [
    r"g-recaptcha", r"h-captcha", r"cf-turnstile", r"id=[\"'][^\"']*captcha", r"class=[\"'][^\"']*captcha",
    r"name=[\"'][^\"']*captcha", r"verify (that )?you are (a )?human", r"i'?m not a robot", r"data-captcha",
]
LOGIN_PATTERNS = [r"type=[\"']password[\"']"]
PAYMENT_PATTERNS = [r"autocomplete=[\"']cc-number", r"name=[\"']card-?number", r"id=[\"']card-?number"]
IDENTITY_PATTERNS = [r"navigator\.credentials", r"webauthn", r"signature-pad", r"data-requires=[\"']biometric"]


def detect_blockers(html: str, url: str | None = None, status: int | None = None) -> Blocked | None:
    low = html.lower()
    if status == 429:
        return Blocked(type="rate_limit", detail="HTTP 429", url=url)
    for p in CAPTCHA_PATTERNS:
        m = re.search(p, low)
        if m:
            return Blocked(type="captcha", detail=f"CAPTCHA detected ({m.group(0)[:40]})", url=url)
    for p in IDENTITY_PATTERNS:
        if re.search(p, low):
            return Blocked(type="biometric", detail="identity / signature confirmation required", url=url)
    for p in PAYMENT_PATTERNS:
        if re.search(p, low):
            return Blocked(type="payment", detail="payment card entry required", url=url)
    for p in LOGIN_PATTERNS:
        if re.search(p, low):
            return Blocked(type="login", detail="login wall", url=url)
    return None


class _Doc(HTMLParser):
    """Tiny DOM index for the HTTP fallback: ids, classes, tags, text, links, forms."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = []
        self.links: list[dict[str, str]] = []
        self.forms: list[dict[str, Any]] = []
        self.title = ""
        self._in_title = False
        self.text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        node = {"tag": tag, "id": a.get("id"), "class": (a.get("class") or "").split(), "attrs": a, "text": []}
        self.nodes.append(node)
        if tag not in ("br", "img", "input", "meta", "link", "hr"):
            self.stack.append(node)
        if tag == "a" and a.get("href"):
            self.links.append({"href": a["href"], "text": ""})
        if tag == "form":
            self.forms.append({"action": a.get("action"), "method": a.get("method", "get")})
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        t = data.strip()
        if not t:
            return
        self.text_parts.append(t)
        for n in self.stack:
            n["text"].append(t)
        if self.links and self.stack and self.stack[-1]["tag"] == "a":
            self.links[-1]["text"] += t

    def select(self, selector: str) -> list[dict[str, Any]]:
        sel = selector.strip()
        out = []
        for n in self.nodes:
            if sel.startswith("#") and n["id"] == sel[1:]:
                out.append(n)
            elif sel.startswith(".") and sel[1:] in n["class"]:
                out.append(n)
            elif re.fullmatch(r"[a-z0-9]+", sel) and n["tag"] == sel:
                out.append(n)
            else:
                m = re.fullmatch(r"\[([\w-]+)=[\"']?([^\"'\]]+)[\"']?\]", sel)
                if m and n["attrs"].get(m.group(1)) == m.group(2):
                    out.append(n)
        return out


def _page_summary_from_html(html: str, url: str) -> dict[str, Any]:
    d = _Doc()
    d.feed(html)
    text = " ".join(d.text_parts)
    return {"url": url, "title": d.title.strip(), "text": text[:4000], "links": d.links[:50], "forms": d.forms}


class BrowserRun:
    def __init__(self) -> None:
        self.outputs: dict[str, Any] = {}
        self.trace: list[dict[str, Any]] = []
        self.blocker: Blocked | None = None
        self.error: str | None = None
        self.url: str | None = None


class HttpDriver:
    name = "http-fallback"

    def run(self, steps: list[dict[str, Any]], workdir: Path) -> BrowserRun:
        run = BrowserRun()
        html, status = "", 200
        with httpx.Client(follow_redirects=True, timeout=20) as client:
            for step in steps:
                do = step.get("do")
                try:
                    if do == "navigate":
                        r = client.get(step["url"])
                        html, status, run.url = r.text, r.status_code, str(r.url)
                        run.trace.append({"do": do, "url": run.url, "status": status})
                        b = detect_blockers(html, run.url, status)
                        if b:
                            run.blocker = b
                            return run
                    elif do == "inspect":
                        run.outputs["page"] = _page_summary_from_html(html, run.url or "")
                        run.trace.append({"do": do})
                    elif do == "extract":
                        d = _Doc()
                        d.feed(html)
                        nodes = d.select(step["selector"])
                        val = " ".join(" ".join(n["text"]) for n in nodes).strip() if nodes else None
                        run.outputs[step.get("as", step["selector"])] = val
                        run.trace.append({"do": do, "selector": step["selector"], "found": bool(nodes)})
                    elif do == "wait":
                        run.trace.append({"do": do, "note": "no-op in http driver"})
                    elif do in ("click", "type", "upload", "screenshot", "download"):
                        run.error = f"http fallback driver cannot '{do}' (Playwright unavailable)"
                        return run
                    else:
                        run.error = f"unknown step {do}"
                        return run
                except Exception as e:
                    run.error = f"{do}: {type(e).__name__}: {e}"
                    return run
        return run


def _chromium_executable() -> str | None:
    if settings.browser_executable:
        return settings.browser_executable
    cands = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return cands[-1] if cands else None


class PlaywrightDriver:
    name = "playwright"

    def __init__(self) -> None:
        from playwright.sync_api import sync_playwright  # noqa: F401  (import check)

    def _launch(self, p):
        try:
            return p.chromium.launch(headless=True)
        except Exception:
            exe = _chromium_executable()
            if not exe:
                raise
            return p.chromium.launch(headless=True, executable_path=exe)

    def run(self, steps: list[dict[str, Any]], workdir: Path) -> BrowserRun:
        from playwright.sync_api import sync_playwright

        run = BrowserRun()
        with sync_playwright() as p:
            browser = self._launch(p)
            try:
                ctx = browser.new_context(accept_downloads=True)
                page = ctx.new_page()
                status: int | None = None
                for step in steps:
                    do = step.get("do")
                    try:
                        if do == "navigate":
                            resp = page.goto(step["url"], wait_until="domcontentloaded", timeout=20000)
                            status = resp.status if resp else None
                            run.url = page.url
                            run.trace.append({"do": do, "url": page.url, "status": status})
                        elif do == "click":
                            page.click(step["selector"], timeout=8000)
                            page.wait_for_load_state("domcontentloaded")
                            run.trace.append({"do": do, "selector": step["selector"]})
                        elif do == "type":
                            page.fill(step["selector"], step.get("text", ""), timeout=8000)
                            run.trace.append({"do": do, "selector": step["selector"]})
                        elif do == "extract":
                            loc = page.locator(step["selector"])
                            val = loc.first.inner_text(timeout=4000) if loc.count() else None
                            run.outputs[step.get("as", step["selector"])] = val
                            run.trace.append({"do": do, "selector": step["selector"], "found": val is not None})
                        elif do == "inspect":
                            run.outputs["page"] = _page_summary_from_html(page.content(), page.url)
                            run.trace.append({"do": do})
                        elif do == "wait":
                            if "selector" in step:
                                page.wait_for_selector(step["selector"], timeout=step.get("timeout_ms", 8000))
                            else:
                                page.wait_for_timeout(step.get("ms", 500))
                            run.trace.append({"do": do})
                        elif do == "screenshot":
                            path = workdir / step.get("path", "screenshot.png")
                            path.parent.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(path), full_page=True)
                            run.outputs.setdefault("screenshots", []).append(str(path))
                            run.trace.append({"do": do, "path": str(path)})
                        elif do == "download":
                            with page.expect_download(timeout=10000) as dl:
                                page.click(step["selector"])
                            path = workdir / step.get("path", dl.value.suggested_filename)
                            dl.value.save_as(str(path))
                            run.outputs.setdefault("downloads", []).append(str(path))
                            run.trace.append({"do": do, "path": str(path)})
                        elif do == "upload":
                            page.set_input_files(step["selector"], str(workdir / step["path"]))
                            run.trace.append({"do": do, "path": step["path"]})
                        else:
                            run.error = f"unknown step {do}"
                            return run
                    except Exception as e:
                        run.error = f"{do}: {type(e).__name__}: {str(e).splitlines()[0][:200]}"
                        return run
                    if do in ("navigate", "click"):
                        b = detect_blockers(page.content(), page.url, status)
                        if b:
                            run.blocker = b
                            return run
            finally:
                browser.close()
        return run


def make_driver() -> Any:
    try:
        return PlaywrightDriver()
    except Exception:
        return HttpDriver()
