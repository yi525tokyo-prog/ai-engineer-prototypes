"""Pure HTML parsers for goodroom (www.goodrooms.jp).

goodroom serves server-rendered HTML (Rails/HAML). There is no public JSON API,
but the markup uses stable BEM-ish class names, so we parse with anchored
regexes instead of pulling in a DOM library. Every parser is a pure function
(html -> dict) so it can be tested against saved fixtures.

Pages used:
  * search list   /{region}/search/estate_list/?rent_count=100&p=N
  * detail        /{region}/detail/{large_area_cd}/{estate_id}/
  * map           /{region}/detail/{large_area_cd}/{estate_id}/map/
                  (contains `mapInitialize(lat, lon, zoom)` -> exact coordinates)
"""
from __future__ import annotations

import html as _html
import re
from typing import Any

BASE_URL = "https://www.goodrooms.jp"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
DETAIL_PATH_RE = re.compile(r"/([a-z_]+)/detail/(\d{3})/(\d+)/")


def clean_text(fragment: str | None) -> str:
    """Strip tags, unescape entities, collapse whitespace."""
    if not fragment:
        return ""
    text = _TAG_RE.sub(" ", fragment)
    text = _html.unescape(text).replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def parse_yen(text: str | None) -> int | None:
    """'163,000円' -> 163000; '6.6万円' -> 66000; 'なし' -> 0; garbage -> None."""
    if text is None:
        return None
    t = clean_text(text)
    if not t:
        return None
    if t in ("なし", "無", "-", "0円"):
        return 0
    m = re.search(r"([\d.]+)\s*万円", t)
    if m:
        return int(round(float(m.group(1)) * 10000))
    m = re.search(r"([\d,]+)\s*円", t)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def parse_area_m2(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"([\d.]+)\s*(?:㎡|m2|m²)", text)
    return float(m.group(1)) if m else None


def parse_walk(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"徒歩\s*(\d+)\s*分", text)
    return int(m.group(1)) if m else None


def split_prefecture(addr: str) -> tuple[str | None, str | None]:
    """'東京都大田区' -> ('東京都', '大田区'); '神奈川県横浜市中区' -> ('神奈川県', '横浜市中区')."""
    m = re.match(r"^(東京都|北海道|(?:京都|大阪)府|.{2,3}県)(.*)$", addr.strip())
    if not m:
        return None, addr.strip() or None
    city = m.group(2).strip() or None
    return m.group(1), city


def floor_from_room(room: str | None) -> tuple[int | None, str | None]:
    """Infer floor from goodroom's '号室／階' cell.

    Returns (floor, source). Explicit 'N階' wins; otherwise Japanese room
    numbering (room 304 -> 3F, 0204 -> 2F, 1201 -> 12F). Non-numeric room
    labels ('ベーシック号室') give (None, None).
    """
    if not room:
        return None, None
    t = clean_text(room)
    m = re.search(r"(?:地下|B)\s*(\d+)\s*階", t)
    if m:
        return -int(m.group(1)), "explicit"
    m = re.search(r"(\d+)\s*階", t)
    if m:
        return int(m.group(1)), "explicit"
    m = re.search(r"(\d{3,4})\s*号室", t)
    if m:
        num = m.group(1).lstrip("0") or "0"
        if len(num) >= 3:
            return int(num[:-2]), "room_number"
    return None, None


# ---------------------------------------------------------------------------
# search list page
# ---------------------------------------------------------------------------
_CARD_RE = re.compile(r"<article class='searchEstateList'>(.*?)</article>", re.S)


def _field(block: str, cls: str) -> str | None:
    # class may carry modifiers: 'searchEstateList__title--new', 'searchEstateList__spec searchEstateList__spec--long'
    m = re.search(r"class='" + re.escape(cls) + r"(?:--[\w-]+)?(?: [^']*)?'>(.*?)</(?:div|h2|span)>", block, re.S)
    return m.group(1) if m else None


def parse_list_page(page_html: str) -> dict[str, Any]:
    """Parse one search-result page into listing stubs + pagination info."""
    listings = []
    for block in _CARD_RE.findall(page_html):
        href = re.search(r"href='(/[a-z_]+/detail/\d{3}/\d+/)'", block)
        if not href:
            continue
        path = href.group(1)
        region, large_cd, estate_id = DETAIL_PATH_RE.search(path).groups()
        price_block = _field(block, "searchEstateList__price") or ""
        rent_text = price_block.split("<span")[0]
        mgmt = re.search(r"管理費\s*([\d,]+円|なし)", clean_text(price_block))
        spec = clean_text(_field(block, "searchEstateList__spec"))
        parts = [p.strip() for p in spec.split("/")] if spec else []
        address_city = parts[0] if parts else None
        layout = area = room = None
        for p in parts[1:]:
            if "㎡" in p:
                area = parse_area_m2(p)
            elif "号室" in p or "階" in p:
                room = p
            elif re.match(r"^(\d[A-Z]+|ワンルーム|1R)", p):
                layout = p
        traffic = clean_text(_field(block, "searchEstateList__traffic"))
        st = re.match(r"(.+?)駅?\s*徒歩\s*(\d+)分\s*(?:\((.*)\))?", traffic or "")
        madori_alt = re.search(r"alt='([^']*)の間取り'", block)
        costs = {}
        for label, val in re.findall(
            r"searchEstateList__cost__detail'>\s*<span>(.)</span>\s*([^<]*)", block
        ):
            costs[{"敷": "deposit", "礼": "key_money", "仲": "brokerage"}.get(label, label)] = val.strip()
        flags_m = re.search(r"searchEstateList__flags'>(.*?)</a>", block, re.S)
        flags = [f for f in re.findall(r"<div class='[^']*'>([^<]+)</div>", flags_m.group(1))] if flags_m else []
        pref, city = split_prefecture(address_city) if address_city else (None, None)
        listings.append(
            {
                "estate_id": estate_id,
                "region": region,
                "large_area_cd": large_cd,
                "url": BASE_URL + path,
                "title": clean_text(_field(block, "searchEstateList__title")) or None,
                "building_name": _html.unescape(madori_alt.group(1)) if madori_alt else None,
                "rent": parse_yen(rent_text),
                "management_fee": parse_yen(mgmt.group(1)) if mgmt else None,
                "address_city": address_city,
                "prefecture": pref,
                "city": city,
                "room": room,
                "layout": layout,
                "floor_area_m2": area,
                "station_text": traffic or None,
                "nearest_station": (st.group(1).strip() + "駅") if st else None,
                "walk_minutes": int(st.group(2)) if st else None,
                "nearest_line": (st.group(3) if st and st.group(3) else None),
                "list_costs": costs,
                "list_flags": [f.strip() for f in flags if f.strip()],
                "is_new": "searchEstateList__info__new" in block,
            }
        )
    total = None
    m = re.search(r"estatesCount--num'>([\d,]+)件", page_html)
    if m:
        total = int(m.group(1).replace(",", ""))
    has_next = bool(re.search(r"<li class='next'>", page_html))
    return {"listings": listings, "total": total, "has_next": has_next}


# ---------------------------------------------------------------------------
# detail page
# ---------------------------------------------------------------------------
def _table_rows(fragment: str) -> dict[str, str]:
    rows = {}
    for th, td in re.findall(r"<th>\s*<h3>(.*?)</h3>\s*</th>\s*<td>(.*?)</td>", fragment, re.S):
        rows[clean_text(th)] = clean_text(td)
    return rows


def _section(page_html: str, start_marker: str, end_marker: str) -> str:
    i = page_html.find(start_marker)
    if i < 0:
        return ""
    j = page_html.find(end_marker, i + len(start_marker))
    return page_html[i : j if j > 0 else len(page_html)]


def parse_detail_page(page_html: str, today_year: int | None = None) -> dict[str, Any]:
    """Parse a goodroom detail page. Missing sections yield None fields."""
    out: dict[str, Any] = {}
    h1 = re.search(r"<h1 class='detail-page-h1'>(.*?)</h1>", page_html, re.S)
    h1t = clean_text(h1.group(1)) if h1 else ""
    title = re.search(r"<div class='estate_title'>(.*?)</div>", page_html, re.S)
    out["title"] = clean_text(title.group(1)) if title else None

    basic = _table_rows(_section(page_html, "id='basic-info'", "</table>"))
    out["rent"] = parse_yen(basic.get("家賃"))
    out["management_fee"] = parse_yen(basic.get("管理費"))
    out["room"] = basic.get("号室／階") or None
    out["layout"] = basic.get("間取") or None
    out["floor_area_m2"] = parse_area_m2(basic.get("広さ"))
    dep = basic.get("敷金／礼金")
    if dep and "/" in dep:
        d, k = [x.strip() for x in dep.split("/", 1)]
        out["deposit"], out["key_money"] = d, k
    else:
        out["deposit"] = out["key_money"] = None
    out["brokerage_fee"] = basic.get("仲介手数料")
    out["guarantee_deposit"] = basic.get("保証金")

    # building name: h1 = "<building><room>/<pref+city>/<station>/<layout> - ..."
    head = h1t.split(" - ")[0]
    segs = head.split("/")
    bname = segs[0] if segs else None
    if bname and out["room"] and bname.endswith(out["room"]):
        bname = bname[: -len(out["room"])]
    elif bname:
        bname = re.sub(r"\S*?号室$", "", bname)
    out["building_name"] = (bname or "").strip() or None

    sub = _section(page_html, "id='sub-info'", "class='company-contact'")
    addr = re.search(r"<div class='address'>(.*?)</div>", sub, re.S)
    address = clean_text(addr.group(1)) if addr else None
    out["address"] = address.replace(" ", "") if address else None
    if out["address"]:
        out["prefecture"], out["city"] = split_prefecture(clean_text(re.sub(r"</a>.*", "", addr.group(1), flags=re.S)))
    stations = []
    for li in re.findall(r"<li>(.*?)</li>", _section(sub, "<ul class='traffic'>", "</ul>"), re.S):
        links = [clean_text(a) for a in re.findall(r"<a[^>]*>(.*?)</a>", li, re.S)]
        txt = clean_text(li)
        walk = parse_walk(txt)
        bus = "バス" in txt
        line = links[0] if len(links) >= 2 else None
        station = links[-1] if links else None
        if station or walk is not None:
            stations.append({"line": line, "station": station, "walk_minutes": walk, "bus": bus, "text": txt})
    out["stations"] = stations

    fac = _section(sub, "<ul class='facility-list'>", "</ul>")
    out["facilities_on"] = re.findall(r"<li class='[^']*\bon'>\s*<img alt='([^']+)'", fac)
    other = re.search(r"<h3>そのほか設備</h3>\s*</div>(.*?)</td>", sub, re.S)
    out["other_facilities"] = [x for x in clean_text(other.group(1)).split("/") if x] if other else []

    note_m = re.search(r"<h3>備考</h3>\s*</div>(.*?)</td>", sub, re.S)
    note_items: list[str] = []
    if note_m:
        raw = re.sub(r"<br\s*/?>", "\n", note_m.group(1))
        for line in clean_text_lines(raw):
            line = line.lstrip("・").strip()
            if line:
                note_items.append(line)
    out["notes"] = note_items
    out.update(parse_note_items(note_items, today_year=today_year))
    return out


def clean_text_lines(fragment: str) -> list[str]:
    text = _html.unescape(_TAG_RE.sub("\n", fragment)).replace("\xa0", " ")
    return [re.sub(r"[ \t　]+", " ", ln).strip() for ln in text.split("\n") if ln.strip()]


_STRUCTURES = ("鉄筋コン", "鉄骨鉄筋", "鉄骨造", "軽量鉄骨", "木造", "ブロック", "RC", "SRC", "鉄骨")
_DIRECTIONS = {"北", "北東", "東", "南東", "南", "南西", "西", "北西"}


def parse_note_items(items: list[str], today_year: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "built_year": None,
        "built_month": None,
        "building_age_years": None,
        "structure": None,
        "total_floors": None,
        "basement_floors": None,
        "direction": None,
        "fixed_term_lease": False,
        "move_in": None,
        "info_updated": None,
    }
    for it in items:
        m = re.search(r"築年月\s*[:：]\s*(\d{4})年\s*(\d{1,2})?月?", it)
        if m:
            out["built_year"] = int(m.group(1))
            out["built_month"] = int(m.group(2)) if m.group(2) else None
            continue
        m = re.search(r"(?:地上)?(\d+)階建(?:て)?", it)
        if m and out["total_floors"] is None:
            out["total_floors"] = int(m.group(1))
            b = re.search(r"地下(\d+)階", it)
            out["basement_floors"] = int(b.group(1)) if b else None
            continue
        if out["structure"] is None and any(it.startswith(s) for s in _STRUCTURES) and len(it) <= 12:
            out["structure"] = it
            continue
        if it in _DIRECTIONS:
            out["direction"] = it
            continue
        if "定期借家" in it:
            out["fixed_term_lease"] = True
        m = re.match(r"入居\s*[:：]\s*(.*)", it)
        if m:
            out["move_in"] = m.group(1).strip() or None
        m = re.match(r"情報更新日\s*[:：]\s*([\d-]+)", it)
        if m:
            out["info_updated"] = m.group(1)
    if out["built_year"] and today_year:
        out["building_age_years"] = max(0, today_year - out["built_year"])
    return out


# ---------------------------------------------------------------------------
# map page
# ---------------------------------------------------------------------------
_MAPINIT_RE = re.compile(r"mapInitialize\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:,\s*(\d+))?\s*\)")


def parse_map_page(page_html: str) -> dict[str, Any] | None:
    """Extract the listing coordinates goodroom passes to its Google Map."""
    m = _MAPINIT_RE.search(page_html or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (20.0 <= lat <= 46.5 and 122.0 <= lon <= 154.5):  # outside Japan -> junk / placeholder
        return None
    return {"lat": lat, "lon": lon, "zoom": int(m.group(3)) if m.group(3) else None}


def parse_area_codes(page_html: str) -> dict[str, str]:
    """small_area_cd -> label (city/ward), from the search sidebar."""
    return {cd: _html.unescape(lbl).strip() for _, cd, lbl in re.findall(r"<label for='area_(\d+)_(\d+)'>([^<]+)", page_html)}
