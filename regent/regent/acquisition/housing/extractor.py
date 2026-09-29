"""HousingExtractor: turns any housing web page into claims, without per-site templates.

Strategies, tried in order and all site-agnostic:

1. **Listing index segmentation** -- find the *minimal* DOM elements whose text
   carries a unit signature (rent + layout + floor area). Each is a unit row.
   Its enclosing building is the nearest ancestor whose text carries an address
   or station-walk signature. Rows sharing a building container share a
   building mention. Fields are read with labels when present, positionally
   otherwise (confidence differs accordingly).
2. **Detail page** -- label/value pairs from th/td, dt/dd and inline "label value"
   text, mapped through a housing vocabulary (賃料, 管理費, 敷金, 礼金, 築年月, 構造,
   入居, 情報更新日, 次回更新予定日, 所在地, 交通 ...). Also finds the "original
   listing" link (情報掲載元) used for operator verification and detects
   "listing ended" pages (availability=false).
3. **Market tables** -- area x layout rent grids (相場) become area-level claims.
4. **Offer cards** -- share-house / monthly offers (rent + place signature
   without a layout) become unit offers of that housing type.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import html as LH

from regent.acquisition.housing import text as T
from regent.acquisition.types import ClaimIn, FetchedDocument, Mention

LABELS: list[tuple[str, str]] = [
    (r"^賃料\s*[(/（]\s*管理費", "rent_fee"), (r"^敷\s*[/／]\s*礼|^敷金\s*[/／]\s*礼金", "deposit_key"),
    (r"^(賃料|家賃|月額賃料)", "rent"), (r"^(管理費|共益費|管理費等)", "management_fee"),
    (r"^敷金", "deposit"), (r"^礼金", "key_money"), (r"^(保証金)", "guarantee_deposit"),
    (r"^間取り$|^間取$|^間取り(?!詳細)", "layout"), (r"^(専有面積|面積|占有面積)", "area"),
    (r"^(所在地|住所)", "address"), (r"^(交通|アクセス|最寄り駅|駅徒歩)", "access"),
    (r"^(築年月|築年数|建築年月|竣工)", "built"), (r"^構造", "structure"), (r"^(階建|建物階数)", "floors_total"), (r"^(所在階|階数|階)$", "floors"),
    (r"^(入居|入居時期|入居可能日|入居日)", "move_in"), (r"^(情報更新日|更新日)", "info_updated_at"),
    (r"^(次回更新予定日|次回更新日)", "next_update_at"), (r"^条件", "conditions"),
    (r"^(設備|部屋の特徴|特徴|設備・条件)", "features"), (r"^(取引態様)", "transaction_type"),
    (r"^(契約期間)", "lease_term"), (r"^(保証会社)", "guarantor"), (r"^(部屋番号|号室)", "room_number"),
    (r"^(建物名|物件名)", "name"), (r"^(周辺環境|周辺施設|周辺情報)", "surroundings"),
    (r"^(総戸数)", "total_units"), (r"^(ほか初期費用)", "other_initial_cost"), (r"^(更新料)", "renewal_fee"),
]
ENDED = re.compile(r"(掲載(?:を)?終了|募集(?:を)?終了|成約済|この物件は現在.*?掲載されておりません|お探しの物件は見つかりません)")
DETAIL_HREF = re.compile(r"(jnc_|/room/|detail|shosai|/b-\d|bukken|/house/\d|/property/|/rent/\d)", re.I)
AGENT = re.compile(r"(株式会社|有限会社|\(株\)|㈱|（株）|支店|営業所|不動産|ショップ|ハウジング|ホーム(ズ|メイト)|店$|センター$)")
POI = re.compile(r"(スーパー|コンビニ|ドラッグストア|図書館|大学|病院|郵便局|銀行|公園|小学校|中学校|保育園|幼稚園|飲食店|ショッピング|区役所|市役所)"
                 r"[^\d]{0,24}?(\d{1,4})\s*m")


def _t(e) -> str:
    return T.norm(e.text_content())


class HousingExtractor:
    name = "housing-extractor-v1"

    def __init__(self, default_pref: str = "東京都"):
        self.default_pref = default_pref

    # ------------------------------------------------------------ entry

    def extract(self, doc: FetchedDocument, purpose: str = "") -> list[Mention]:
        if not doc.html:
            return []
        try:
            tree = LH.fromstring(doc.html)
        except Exception:
            return []
        for bad in tree.xpath("//script|//style|//noscript|//template|//svg"):
            bad.drop_tree()
        out: list[Mention] = []
        out += self.market_tables(tree, doc, purpose)
        units = self.listing_units(tree, doc)
        if not units:
            vac = self.vacancy_cards(tree, doc)
            if vac:
                return out + vac
        if len(units) < 2:
            d = self.detail(tree, doc)
            if d is not None:
                units = [d]          # a detail page's own summary row is not a second listing
            elif not units and not out:
                units = self.offer_cards(tree, doc, purpose)
        return out + units

    def has_content(self, text: str) -> bool:
        """Static-HTML check used by the fetcher to decide on browser rendering: are there
        *records* (money near a floor area), not just price filter menus?"""
        t = T.norm(text)
        recs = re.findall(r"(?:\d[\d,.]*\s*万?円|[¥￥]\s?\d[\d,]+)[^円]{0,120}?\d{1,3}(?:\.\d{1,2})?\s*(?:m2|m²|㎡)"
                          r"|\d{1,3}(?:\.\d{1,2})?\s*(?:m2|m²|㎡)[^円]{0,120}?(?:\d[\d,.]*\s*万?円|[¥￥]\s?\d[\d,]+)", t)
        tables = len(re.findall(r"[区市]\s*\d{1,3}(?:\.\d)?\s*万円", t))
        detail = ("賃料" in t or "家賃" in t) and "間取" in t and bool(T.AREA.search(t))
        return len(recs) >= 3 or tables >= 5 or detail

    # ------------------------------------------------------ 1. listing index

    @staticmethod
    def page_segment(tree) -> str:
        head = T.norm(" ".join(tree.xpath("//title/text()")) + " " + " ".join(
            T.norm(h.text_content()) for h in tree.xpath("//h1")[:2]))
        if re.search(r"マンスリー|ウィークリー", head):
            return "monthly"
        if re.search(r"シェアハウス|ソーシャルレジデンス|ゲストハウス", head):
            return "share_house"
        return "rent"

    def listing_units(self, tree, doc: FetchedDocument) -> list[Mention]:
        now = doc.fetched_at
        segment = self.page_segment(tree)
        cands = []
        for e in tree.iter():
            if not isinstance(e.tag, str) or e.tag in ("html", "body"):
                continue
            t = _t(e)
            if len(t) > 900:
                continue
            if T.AREA.search(t) and T.LAYOUT.search(t) and re.search(r"\d(?:\.\d+)?\s*万円|\d{2,3},\d{3}\s*円|[¥￥]\s?\d{2,3},\d{3}", t):
                cands.append(e)
        cs = set(cands)
        minimal = [e for e in cands if not any(d in cs for d in e.iterdescendants())]
        # a row that contains *two* unit signatures is a container, not a row
        minimal = [e for e in minimal if len(T.AREA.findall(_t(e))) == 1]
        buildings: dict[int, Mention] = {}
        out = []
        for u in minimal:
            container = self._building_container(u)
            bkey = id(container) if container is not None else id(u)
            if bkey not in buildings:
                buildings[bkey] = self._building_mention(container, [x for x in minimal], doc)
            b = buildings[bkey]
            if container is None or not b.value("address"):
                inner = self._building_mention(u, [], doc)   # building facts may live inside the row itself
                if inner.value("address"):
                    b = inner
            m = self._unit_mention(u, b, doc, now)
            if m is not None:
                if segment != "rent":
                    m.claims.append(ClaimIn("housing_type", segment, 0.85, f"page is a {segment} listing", now, self.name))
                out.append(m)
        return out

    def _building_container(self, u):
        e = u.getparent()
        while e is not None and e.tag not in ("body", "html"):
            t = _t(e)
            if len(t) > 12000:
                return None
            if T.WALK.search(t) or (T.address(t) and re.search(r"丁目|\d-\d|[町村]", t)):
                return e
            e = e.getparent()
        return None

    def _building_mention(self, container, unit_rows: list, doc: FetchedDocument) -> Mention:
        if container is None:
            return Mention(entity_type="building", claims=[], url=doc.final_url, raw_text="")
        rows = set(unit_rows)
        parts = []
        for node in container.iter():
            if node in rows:
                continue
            if node.text and not any(a in rows for a in node.iterancestors()):
                parts.append(node.text)
            if node.tail and node is not container and not any(a in rows for a in node.iterancestors()) \
                    and node not in rows:
                parts.append(node.tail)
        btext = T.norm(" ".join(parts))
        name = self._name(container, rows)
        claims: list[ClaimIn] = []
        ev = btext[:300]
        now = doc.fetched_at
        addr = T.address(btext, self.default_pref)
        if name and not T.is_placeholder_name(name):
            claims.append(ClaimIn("name", name, 0.85, name, now, self.name))
        if addr:
            claims.append(ClaimIn("address", addr, 0.9, addr, now, self.name))
        st = T.stations(btext)
        if st:
            claims.append(ClaimIn("stations", st, 0.9, ev, now, self.name))
        by = T.built_year(btext, now.date())
        if by:
            claims.append(ClaimIn("built_year", by, 0.85, ev, now, self.name))
        ft = T.floors_total(btext)
        if ft:
            claims.append(ClaimIn("floors_total", ft, 0.85, ev, now, self.name))
        for bt in T.BUILDING_TYPES:
            if bt in btext:
                claims.append(ClaimIn("building_type", bt.replace("賃貸", ""), 0.8, bt, now, self.name))
                break
        return Mention(entity_type="building", claims=claims, url=doc.final_url, raw_text=btext[:600],
                       key_fields=self._building_keys(name, addr, st, by, ft))

    def _building_keys(self, name, addr, st, by, ft) -> dict:
        parts = T.parse_address(addr)
        return {"name_key": "" if T.is_placeholder_name(name) else T.name_key(name), "address": addr,
                "city": parts.get("city"), "town": parts.get("town"), "chome": parts.get("chome"),
                "built_year": by, "floors_total": ft,
                "nearest": (min(st, key=lambda x: x["walk_min"]) if st else None)}

    def _name(self, container, rows) -> str | None:
        for e in container.iter():
            if not isinstance(e.tag, str) or e in rows or any(a in rows for a in e.iterancestors()):
                continue
            cls = (e.get("class") or "") + " " + (e.get("id") or "")
            if e.tag in ("h1", "h2", "h3", "h4") or re.search(r"(title|name|bukken|building|heading)", cls, re.I):
                t = _t(e)
                if 2 <= len(t) <= 60 and not re.search(r"万円|徒歩|歩\d|m2|m²|\d{3,}-\d", t) and not AGENT.search(t):
                    t = re.sub(r"^(賃貸マンション|賃貸アパート|マンション|アパート|一戸建て)\s*", "", t)
                    t = re.sub(r"\s*\d{1,2}階建.*$", "", t)
                    if t and not re.fullmatch(r"[\d\s]+", t):
                        return t[:60]
        return None

    def _unit_mention(self, u, building: Mention, doc: FetchedDocument, now: datetime) -> Mention | None:
        t = _t(u)
        fields = self._money_fields(t)
        if not fields.get("rent"):
            return None
        claims = []
        conf_lbl = 0.95 if fields.get("_labeled") else 0.82
        ev = t[:240]
        for k in ("rent", "management_fee", "deposit", "key_money", "one_off_fee"):
            if fields.get(k) is not None:
                claims.append(ClaimIn(k, fields[k], conf_lbl if k == "rent" else conf_lbl - 0.07, ev, now, self.name))
        lay, ar, fl = T.layout(t), T.area(t), T.floor(t)
        if lay:
            claims.append(ClaimIn("layout", lay, 0.95, ev, now, self.name))
        if ar:
            claims.append(ClaimIn("area_m2", ar, 0.95, ev, now, self.name))
        if fl is not None:
            claims.append(ClaimIn("floor", fl, 0.9, ev, now, self.name))
        rn = self._room_number(t)
        if rn:
            claims.append(ClaimIn("room_number", rn, 0.7, ev, now, self.name))
        mi = T.move_in(t)
        if mi:
            claims.append(ClaimIn("move_in", mi, 0.85, ev, now, self.name))
        claims.append(ClaimIn("availability", True, 0.7, "listed on a live index page", now, self.name))
        link = self._detail_link(u, doc) or (self._detail_link(u.getparent(), doc) if u.getparent() is not None else None)
        return Mention(entity_type="unit", claims=claims, url=doc.final_url, parent=building, raw_text=t[:400],
                       links={"detail_url": link} if link else {},
                       key_fields={"floor": fl, "room_number": rn, "layout": lay, "area_m2": ar,
                                   "rent": fields.get("rent")})

    def _money_fields(self, t: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        labeled = False
        for lab, key in (("(?:賃料|家賃)(?:/管理費等?)?", "rent"), ("(?:管理費|共益費)", "management_fee"),
                         ("敷金", "deposit"), ("礼金", "key_money")):
            m = re.search(lab + r"[:：\s]*(" + T.MONEY_TOKEN.pattern + ")", t)
            if m and key == "rent":
                toks = T.money_tokens(t[m.start(1):m.start(1) + 40])
                if toks and toks[0][0] == "yen":
                    out["rent"] = T.to_yen(toks[0], None)
                    labeled = True
                    if len(toks) > 1 and "管理費" in m.group(0):
                        out["management_fee"] = T.to_yen(toks[1], out["rent"])
        toks = T.money_tokens(t)
        if "rent" not in out and any(k == "yen/月" for k, _, _ in toks):
            # periodized pricing (monthly rentals): rent is the first per-month amount,
            # the next per-month amount is utilities; one-off amounts are fees
            monthly = [x for x in toks if x[0] == "yen/月"]
            out["rent"] = T.to_yen(monthly[0], None)
            if len(monthly) > 1 and monthly[1][1] < out["rent"] * 0.5:
                out["management_fee"] = T.to_yen(monthly[1], None)
            once = next((x for x in toks if x[0] == "yen/回"), None)
            if once:
                out["one_off_fee"] = round(once[1])
            out["_labeled"] = True
            return out
        toks = [x for x in toks if not x[0].startswith("yen/") or x[0] == "yen/月"]
        if "rent" not in out:
            first = next((i for i, x in enumerate(toks) if x[0] in ("yen", "yen/月") and x[1] and x[1] >= 10000), None)
            if first is None:
                return {}
            out["rent"] = T.to_yen(toks[first], None)
            rest = toks[first + 1:]
        else:
            idx = next((i for i, x in enumerate(toks) if x[0] == "yen" and T.to_yen(x, None) == out["rent"]), 0)
            rest = toks[idx + 1:]
            if "management_fee" in out and rest:
                rest = rest[1:]
        rent = out["rent"]
        seq = ["management_fee", "deposit", "key_money"]
        if "management_fee" in out:
            seq = ["deposit", "key_money"]
        for key, tok in zip(seq, rest):
            v = T.to_yen(tok, rent)
            if key == "management_fee" and v is not None and v > rent * 0.5:
                break
            out[key] = v
        out["_labeled"] = labeled
        return out

    @staticmethod
    def _room_number(t: str) -> str | None:
        m = re.search(r"(?:^|\s)(\d{3,5})(?:号室)?(?=\s)", " " + t + " ")
        if m and not re.search(m.group(1) + r"\s*(円|万|m|階)", t):
            return m.group(1).lstrip("0") or None
        m = re.search(r"(\d{2,5})号室", t)
        return m.group(1) if m else None

    def _detail_link(self, e, doc: FetchedDocument) -> str | None:
        if e is None:
            return None
        best, score = None, 0
        for a in e.xpath(".//a[@href]"):
            href = a.get("href") or ""
            if href.startswith(("javascript", "#", "tel:", "mailto:")):
                continue
            s = 0
            if DETAIL_HREF.search(href):
                s += 2
            if "詳細" in _t(a):
                s += 2
            if urlparse(urljoin(doc.final_url, href)).netloc == urlparse(doc.final_url).netloc:
                s += 1
            if re.search(r"(inquir|contact|toiawase|favorite|kentou|clip)", href, re.I):
                s -= 3
            if s > score:
                best, score = urljoin(doc.final_url, href), s
        return best

    # ---------------------------------------------------------- 2. detail

    def pairs(self, tree) -> dict[str, str]:
        out: dict[str, str] = {}
        for th in tree.xpath("//th|//dt"):
            label = _t(th)
            if not label or len(label) > 16:
                continue
            sib = th.getnext()
            while sib is not None and sib.tag not in ("td", "dd"):
                sib = sib.getnext()
            if sib is None:
                continue
            val = _t(sib)
            for pat, key in LABELS:
                if re.search(pat, label) and key not in out:
                    out[key] = val[:400]
        return out

    def detail(self, tree, doc: FetchedDocument) -> Mention | None:
        text = T.norm(tree.text_content())
        now = doc.fetched_at
        p = self.pairs(tree)
        ended = ENDED.search(text[:20000])
        if ended and len(p) < 3:
            return Mention(entity_type="unit", url=doc.final_url, raw_text=ended.group(0),
                           claims=[ClaimIn("availability", False, 0.85, ended.group(0), now, self.name)],
                           key_fields={})
        # inline "label value" fallback for pages without table markup
        for pat, key in LABELS:
            if key in p:
                continue
            m = re.search("(" + pat.strip("^$") + r")\s*[:：]?\s*(\S[^:：]{0,60}?)(?=\s(?:[^\s]{1,8})\s*[:：]|\s{2}|$)",
                          text[:15000])
            if m and key in ("built", "structure", "move_in", "info_updated_at", "next_update_at", "floors"):
                p[key] = m.group(2)
        if not (p.get("rent") or re.search(r"\d(?:\.\d+)?\s*万円", text)) or not (p.get("layout") or p.get("area")):
            return None
        claims: list[ClaimIn] = []
        bclaims: list[ClaimIn] = []

        def add(lst, attr, val, conf, ev):
            if val is not None and val != "":
                lst.append(ClaimIn(attr, val, conf, str(ev)[:200], now, self.name))

        rent_src = p.get("rent_fee") or p.get("rent") or ""
        toks = T.money_tokens(rent_src) or T.money_tokens(text[:3000])
        rent = T.to_yen(toks[0], None) if toks else None
        add(claims, "rent", rent, 0.95 if rent_src else 0.75, rent_src or "page")
        if p.get("rent_fee") and len(toks) > 1 and "management_fee" not in p:
            add(claims, "management_fee", T.to_yen(toks[1], rent), 0.93, p["rent_fee"])
        if p.get("deposit_key"):
            dk = T.money_tokens(p["deposit_key"])
            for key, tok in zip(("deposit", "key_money"), dk):
                if key not in p:
                    add(claims, key, T.to_yen(tok, rent), 0.9, p["deposit_key"])
        if p.get("management_fee") is not None:
            mt = T.money_tokens(p["management_fee"])
            add(claims, "management_fee", T.to_yen(mt[0], rent) if mt else None, 0.93, p["management_fee"])
        for key in ("deposit", "key_money"):
            if p.get(key) is not None:
                mt = T.money_tokens(p[key])
                add(claims, key, T.to_yen(mt[0], rent) if mt else None, 0.9, p[key])
        add(claims, "layout", T.layout(p.get("layout", "")) if p.get("layout") else None, 0.95, p.get("layout"))
        add(claims, "area_m2", T.area(p.get("area", "")) if p.get("area") else None, 0.95, p.get("area"))
        if p.get("floors"):
            f = T.norm(p["floors"])
            add(claims, "floor", T.floor(f), 0.9, f)
        ft_src = T.norm(p.get("floors_total", "") or p.get("floors", ""))
        add(bclaims, "floors_total", T.floors_total(ft_src), 0.9, ft_src)
        if not p.get("floors") and p.get("floors_total"):
            add(claims, "floor", T.floor(ft_src.split("/")[0]), 0.85, ft_src)
        if p.get("room_number"):
            add(claims, "room_number", re.sub(r"\D", "", p["room_number"]) or None, 0.9, p["room_number"])
        if p.get("move_in"):
            add(claims, "move_in", T.move_in(p["move_in"]) or p["move_in"][:40], 0.9, p["move_in"])
        for key in ("info_updated_at", "next_update_at"):
            if p.get(key):
                add(claims, key, T.date_ymd(p[key]), 0.95, p[key])
        for key in ("conditions", "lease_term", "guarantor", "transaction_type", "renewal_fee", "other_initial_cost"):
            if p.get(key):
                add(claims, key, p[key][:200], 0.85, p[key])
        feats = " ".join(p.get(k, "") for k in ("features", "conditions")) + " " + text[:6000]
        if re.search(r"(インターネット無料|ネット使用料不要|インターネット使用料無料|Wi-?Fi無料)", feats):
            add(claims, "internet", "included", 0.85, "features list")
        elif re.search(r"(光ファイバー|インターネット対応|高速インターネット)", feats):
            add(claims, "internet", "available", 0.75, "features list")
        pois = [{"kind": m.group(1), "distance_m": int(m.group(2))}
                for m in POI.finditer(p.get("surroundings", "") or "")]
        if pois:
            add(claims, "nearby_pois", pois, 0.8, p.get("surroundings", "")[:200])
        if not ended:
            add(claims, "availability", True, 0.8, "detail page live")
        addr = T.address(p.get("address", "") or text[:4000], self.default_pref)
        add(bclaims, "address", addr, 0.95 if p.get("address") else 0.7, p.get("address") or addr)
        st = T.stations(p.get("access", "") or text[:4000])
        add(bclaims, "stations", st or None, 0.9, p.get("access", "")[:200])
        by = T.built_year(p.get("built", "") or "", now.date()) if p.get("built") else None
        add(bclaims, "built_year", by, 0.93, p.get("built"))
        if p.get("structure"):
            add(bclaims, "structure", p["structure"].split(" ")[0][:20], 0.9, p["structure"])
        name = p.get("name") or self._title_name(tree)
        if name:
            rm = re.search(r"\s*(\d{2,5})号室\s*$", name)
            if rm:
                name = name[:rm.start()].strip()
                if not p.get("room_number"):
                    add(claims, "room_number", rm.group(1), 0.85, rm.group(0))
        if name and not T.is_placeholder_name(name) and not AGENT.search(name):
            add(bclaims, "name", name[:60], 0.8, name)
        building = Mention(entity_type="building", claims=bclaims, url=doc.final_url,
                           key_fields=self._building_keys(name, addr, st, by, T.floors_total(p.get("floors", "") or "")))
        links = {}
        for a in tree.xpath("//a[@href]"):
            if re.search(r"(掲載元|情報提供元|元の物件|物件はこちら)", _t(a)):
                links["source_url"] = urljoin(doc.final_url, a.get("href"))
                break
        return Mention(entity_type="unit", claims=claims, url=doc.final_url, parent=building, links=links,
                       raw_text=" | ".join(f"{k}={v[:40]}" for k, v in p.items())[:600],
                       key_fields={"floor": T.floor(p.get("floors", "") or ""), "layout": T.layout(p.get("layout", "")),
                                   "area_m2": T.area(p.get("area", "")), "rent": rent,
                                   "room_number": re.sub(r"\D", "", p.get("room_number", "")) or None})

    @staticmethod
    def _title_name(tree) -> str | None:
        for xp in ("//h1", "//title"):
            for e in tree.xpath(xp):
                t = _t(e)
                t = re.split(r"[|｜(（【]", t)[0].strip()
                t = re.sub(r"(の賃貸.*|賃貸(マンション|アパート).*)$", "", t)
                if 2 <= len(t) <= 50:
                    return t
        return None

    # ---------------------------------------------------------- 3. market

    def market_tables(self, tree, doc: FetchedDocument, purpose: str = "") -> list[Mention]:
        out = []
        now = doc.fetched_at
        page_text = T.norm(tree.text_content()[:30000])
        segment = "share_house" if re.search(r"シェアハウス", page_text) else (
            "monthly" if re.search(r"マンスリー", page_text) else "")
        for table in tree.xpath("//table"):
            rows = [[_t(c) for c in r.xpath("./th|./td")] for r in table.xpath(".//tr")]
            if len(rows) < 3:
                continue
            header = rows[0]
            body = [r for r in rows[1:] if r and re.fullmatch(r"[^\s\d]{1,6}[区市町村]", r[0] or "")]
            if len(body) < 3:
                continue
            money_cols = sum(1 for c in (body[0][1:] if body else []) if T.money_tokens(c))
            for r in body:
                claims = []
                for i, cell in enumerate(r[1:], start=1):
                    mt = T.money_tokens(cell)
                    if not mt or mt[0][0] != "yen":
                        continue
                    col = header[i] if i < len(header) else ""
                    slug = re.sub(r"[^0-9A-Za-z]+", "_", T.norm(col).replace("ワンルーム", "1R")).strip("_").lower()
                    if not re.search(r"\d", slug) and money_cols > 1:
                        continue   # several unlabeled money columns: uninterpretable, skip rather than guess
                    key = "market_rent_" + (slug if re.search(r"\d", slug) else (segment or "avg"))
                    claims.append(ClaimIn(key, T.to_yen(mt[0], None), 0.9, f"{r[0]} {col}: {cell}", now, self.name))
                if claims:
                    area_name = self.default_pref + r[0] if not r[0].startswith(T.PREFS) else r[0]
                    out.append(Mention(entity_type="area", claims=claims, url=doc.final_url,
                                       key_fields={"area": area_name}, raw_text=" ".join(r)[:200]))
        # list-style market data ("中央区 81,400 円 対象物件を見る"), only when looking for market data
        if not out and purpose == "market":
            t = T.norm(tree.text_content())
            if "相場" in t:
                for m in re.finditer(r"([^\s\d]{1,5}[区市])\s*(\d{1,3}(?:,\d{3})+)\s*円", t):
                    area_name = self.default_pref + m.group(1)
                    out.append(Mention(entity_type="area", url=doc.final_url, key_fields={"area": area_name},
                                       raw_text=m.group(0),
                                       claims=[ClaimIn("market_rent_" + (segment or "avg"), int(m.group(2).replace(",", "")), 0.85,
                                                       m.group(0), now, self.name)]))
        return out

    # --------------------------------------------------- 3b. vacancy cards

    def vacancy_cards(self, tree, doc: FetchedDocument) -> list[Mention]:
        """Building cards that state a vacancy count ("空室状況 0") -- e.g. public housing
        operators. Zero vacancies is evidence too: it tells Regent this route is closed now."""
        now = doc.fetched_at
        cands = []
        for e in tree.iter():
            if not isinstance(e.tag, str):
                continue
            t = _t(e)
            if len(t) > 1500:
                continue
            if re.search(r"空室(?:状況|数)\s*[:：]?\s*\d+", t) and (T.WALK.search(t) or T.address(t)):
                cands.append(e)
        cs = set(cands)
        minimal = [e for e in cands if not any(d in cs for d in e.iterdescendants())]
        out = []
        for e in minimal[:60]:
            t = _t(e)
            n = int(re.search(r"空室(?:状況|数)\s*[:：]?\s*(\d+)", t).group(1))
            addr = T.address(t, self.default_pref)
            st = T.stations(re.sub(r"(\d+)[~〜～]\d+分", r"\1分", t))
            name = self._name(e, set())
            claims = [ClaimIn("vacancies", n, 0.85, re.search(r"空室(?:状況|数)[^\d]{0,3}\d+", t).group(0), now, self.name)]
            if name:
                claims.append(ClaimIn("name", name, 0.85, name, now, self.name))
            if addr:
                claims.append(ClaimIn("address", addr, 0.9, addr, now, self.name))
            if st:
                claims.append(ClaimIn("stations", st, 0.85, t[:200], now, self.name))
            ft = T.floors_total(t)
            if ft:
                claims.append(ClaimIn("floors_total", ft, 0.8, t[:120], now, self.name))
            out.append(Mention(entity_type="building", claims=claims, url=doc.final_url, raw_text=t[:400],
                               key_fields=self._building_keys(name, addr, st, None, ft)))
        return out

    # ---------------------------------------------------------- 4. offers

    def offer_cards(self, tree, doc: FetchedDocument, purpose: str) -> list[Mention]:
        text = T.norm(tree.text_content()[:20000])
        htype = "share_house" if re.search(r"シェアハウス|ソーシャルレジデンス|ゲストハウス", text) else (
            "monthly" if re.search(r"マンスリー|ウィークリー", text) else "")
        if not htype:
            return []
        now = doc.fetched_at
        cands = []
        for e in tree.iter():
            if not isinstance(e.tag, str):
                continue
            t = _t(e)
            if len(t) > 700:
                continue
            if e.tag in ("tr", "td", "th") or any(a.tag == "table" for a in e.iterancestors()):
                continue
            addr = T.address(t)
            town_level = bool(addr and T.parse_address(addr).get("town"))
            if re.search(r"平均|相場|以下|以上|最大|割引|ポイント", t):
                continue
            if re.search(r"\d{1,3}(?:,\d{3})+\s*円|\d(?:\.\d)?\s*万円|[¥￥]\s?\d{2,3},\d{3}", t) \
                    and (T.WALK.search(t) or town_level):
                cands.append(e)
        cs = set(cands)
        minimal = [e for e in cands if not any(d in cs for d in e.iterdescendants())]
        out = []
        for e in minimal[:80]:
            t = _t(e)
            toks = [x for x in T.money_tokens(t) if x[0] in ("yen", "yen/月") and x[1] and x[1] >= 15000]
            if not toks:
                continue
            addr = T.address(t, self.default_pref)
            st = T.stations(t)
            name = self._name(e, set())
            b = Mention(entity_type="building", url=doc.final_url, raw_text=t[:300],
                        claims=[c for c in [
                            ClaimIn("name", name, 0.75, name or "", now, self.name) if name else None,
                            ClaimIn("address", addr, 0.8, addr or "", now, self.name) if addr else None,
                            ClaimIn("stations", st, 0.85, t[:160], now, self.name) if st else None,
                            ClaimIn("building_type", htype, 0.85, htype, now, self.name)] if c is not None],
                        key_fields=self._building_keys(name, addr, st, None, None))
            claims = [ClaimIn("rent", round(toks[0][1]), 0.8, t[:200], now, self.name),
                      ClaimIn("housing_type", htype, 0.85, htype, now, self.name),
                      ClaimIn("availability", "空室" in t or True, 0.75 if "空室" in t else 0.6,
                              "marked 空室 (vacant)" if "空室" in t else "listed as an open offer", now, self.name)]
            fl = T.floor(t)
            if fl is not None:
                claims.append(ClaimIn("floor", fl, 0.8, t[:200], now, self.name))
            ar = T.area(t)
            if ar:
                claims.append(ClaimIn("area_m2", ar, 0.85, t[:200], now, self.name))
            out.append(Mention(entity_type="unit", claims=claims, url=doc.final_url, parent=b, raw_text=t[:400],
                               links={"detail_url": self._detail_link(e, doc)} if self._detail_link(e, doc) else {},
                               key_fields={"rent": toks[0][1], "area_m2": ar, "layout": htype, "floor": None,
                                           "room_number": None}))
        return out
