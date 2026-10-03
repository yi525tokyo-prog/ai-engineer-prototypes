"""Generic link navigation: reach live content by following anchors, not URL templates.

``find_link`` scores anchors by how well their visible text matches a term
(exact > contains > short-form), same-host, and optional path hints. It is
how the acquisition engine goes from a portal's entry page to "Tokyo" to
"Suginami" without knowing the site's URL scheme.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin, urlparse

from lxml import html as LH


def _n(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def short_forms(term: str) -> list[str]:
    t = _n(term)
    out = [t]
    m = re.match(r"^(.+?)(都|府|県)$", t)
    if m and m.group(1) not in ("京",):
        out.append(m.group(1))
    return out


def anchors(html: str, base: str) -> list[tuple[str, str]]:
    try:
        doc = LH.fromstring(html)
    except Exception:
        return []
    out = []
    for a in doc.xpath("//a[@href]"):
        href = a.get("href") or ""
        if href.startswith(("javascript", "#", "mailto:", "tel:")):
            continue
        out.append((_n(a.text_content()), urljoin(base, href)))
    return out


def find_link(html: str, base: str, term: str, *, path_hints: list[str] | None = None,
              avoid: list[str] | None = None, same_host: bool = True) -> str | None:
    ranked = find_links(html, base, term, path_hints=path_hints, avoid=avoid, same_host=same_host)
    return ranked[0] if ranked else None


def find_links(html: str, base: str, term: str, *, path_hints: list[str] | None = None,
               avoid: list[str] | None = None, same_host: bool = True) -> list[str]:
    """All anchors matching ``term``, best first (deduplicated by URL)."""
    forms = short_forms(term)
    host = urlparse(base).netloc
    scored: dict[str, float] = {}
    for text, url in anchors(html, base):
        if not text:
            continue
        s = 0.0
        if text in forms:
            s += 5
        elif any(f and f in text for f in forms):
            s += 2.5 - min(len(text), 40) / 40
        else:
            continue
        if urlparse(url).netloc == host:
            s += 2
        elif same_host:
            continue
        for h in path_hints or []:
            if h in url:
                s += 1.5
        for a in avoid or []:
            if a in url:
                s -= 3
        if url != base and s > scored.get(url, -99):
            scored[url] = s
    return [u for u, _ in sorted(scored.items(), key=lambda x: -x[1])]


def next_page(html: str, base: str) -> str | None:
    try:
        doc = LH.fromstring(html)
    except Exception:
        return None
    for a in doc.xpath("//a[@rel='next'][@href]|//link[@rel='next'][@href]"):
        return urljoin(base, a.get("href"))
    for text, url in anchors(html, base):
        if text in ("次へ", "次のページ", "次ページ", ">", "»", "›", "Next", "次へ>", "次の20件", "次の30件") \
                or re.fullmatch(r"次の?\d*件?[>＞]?", text or ""):
            return url
    return None
