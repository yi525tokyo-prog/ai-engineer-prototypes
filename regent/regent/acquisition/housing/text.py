"""Japanese housing text normalization and field parsers (site-agnostic)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

MONEY_TOKEN = re.compile(
    r"(?P<man>\d+(?:\.\d+)?)\s*万(?:円)?(?![人件戸台回])"
    r"|[¥￥]\s?(?P<yenp>\d{1,3}(?:,\d{3})+|\d{4,})"
    r"|(?P<yen>\d{1,3}(?:,\d{3})+|\d+)\s*円"
    r"|(?P<months>\d+(?:\.\d+)?)\s*[ヶヵケかカ]月"
    r"|(?<![\w/])(?P<none>-|なし|無し|無|ー)(?=\s|/|$)"
)
AREA = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*(?:m2|m²|㎡|平米)")
LAYOUT = re.compile(r"(ワンルーム|[1-9]\s?S?L?D?K(?![A-Za-z])|[1-9]R(?![A-Za-z]))")
FLOOR = re.compile(r"(?<![\d階])(B?\d{1,2})階(?!建)")
FLOORS_TOTAL = re.compile(r"(?:地上)?(\d{1,2})階建")
BUILT_YM = re.compile(r"(\d{4})年\s*(\d{1,2})月")
BUILT_AGE = re.compile(r"築\s*(\d{1,3})\s*年")
WALK = re.compile(r"(?:「)?([^\s/「」()\[\]]{1,14}?)(?:」)?駅(?:」)?\s*(?:から)?\s*(?:徒歩|歩)\s*(\d{1,2})\s*分"
                  r"|線\s+([^\s/「」()\[\]\d]{1,10})\s+(?:徒歩)?\s*(\d{1,2})\s*分")
BUS = re.compile(r"バス")
ADDRESS = re.compile(
    r"((?:東京都|北海道|(?:京都|大阪)府|[^\s\d]{2,3}県)?"
    r"[^\s\d()（）「」/]{1,6}?[区市郡](?:[^\s\d()（）「」/]{1,6}?[区町村])?"
    r"(?:[^\s\d()（）「」/、,・]{1,10}"
    r"(?:[一二三四五六七八九十]{1,3}丁目|\d{1,2}丁目(?:\d{1,4}(?:-\d{1,4})?)?|\d{1,2}(?:-\d{1,4}){0,2})?)?)"
)
PREFS = ("東京都", "北海道", "京都府", "大阪府")
BUILDING_TYPES = ("賃貸マンション", "賃貸アパート", "マンション", "アパート", "一戸建て", "テラスハウス", "タウンハウス",
                  "シェアハウス", "ソーシャルレジデンス", "マンスリーマンション")


def norm(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()


PERIOD = re.compile(r"^\s*(?:/|／|per\s*)?\s*(日|月|週|回|年|泊)")


def money_tokens(s: str) -> list[tuple[str, float | None, str]]:
    """Ordered money-ish tokens: (kind, value, raw) with kind in yen|yen/<period>|months|none.
    A period suffix ("12,800円/日", "384,000円/月", "21,780円/回") is part of the token."""
    out = []
    for m in MONEY_TOKEN.finditer(s):
        per = PERIOD.match(s[m.end():m.end() + 6])
        suffix = f"/{per.group(1)}" if per and (m.group("man") or m.group("yen") or m.group("yenp")) else ""
        if m.group("man"):
            out.append(("yen" + suffix, float(m.group("man")) * 10000, m.group(0)))
        elif m.group("yenp"):
            out.append(("yen" + suffix, float(m.group("yenp").replace(",", "")), m.group(0)))
        elif m.group("yen"):
            out.append(("yen" + suffix, float(m.group("yen").replace(",", "")), m.group(0)))
        elif m.group("months"):
            out.append(("months", float(m.group("months")), m.group(0)))
        else:
            out.append(("none", 0.0, m.group(0)))
    return out


def to_yen(tok: tuple[str, float | None, str] | None, rent: float | None) -> float | None:
    if tok is None:
        return None
    kind, v, _ = tok
    if kind in ("yen", "yen/月"):
        return round(v or 0)
    if kind.startswith("yen/"):
        return None      # daily / weekly / one-off amounts are not monthly amounts
    if kind == "none":
        return 0
    if kind == "months" and rent:
        return round((v or 0) * rent)
    return None


def layout(s: str) -> str | None:
    m = LAYOUT.search(s)
    if not m:
        return None
    v = m.group(1).replace(" ", "")
    return "1R" if v == "ワンルーム" else v


def area(s: str) -> float | None:
    m = AREA.search(s)
    return float(m.group(1)) if m else None


def floor(s: str) -> int | None:
    m = FLOOR.search(s)
    if not m:
        return None
    v = m.group(1)
    return -int(v[1:]) if v.startswith("B") else int(v)


def floors_total(s: str) -> int | None:
    m = FLOORS_TOTAL.search(s)
    return int(m.group(1)) if m else None


def built_year(s: str, today: date | None = None) -> int | None:
    today = today or date.today()
    if "新築" in s:
        return today.year
    m = BUILT_AGE.search(s)
    if m:
        return today.year - int(m.group(1))
    m = re.search(r"築年月\s*(\d{4})年", s) or re.search(r"(\d{4})年\s*\d{1,2}月\s*(?:\(|（)?築", s)
    if m:
        return int(m.group(1))
    return None


def stations(s: str) -> list[dict]:
    out, seen = [], set()
    for seg in re.split(r"(?<=分)", s):
        if BUS.search(seg):
            continue
        for m in WALK.finditer(seg):
            raw, mins = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            name = re.sub(r"^.*[/／]", "", raw).strip("「」 ")
            name = re.sub(r"^(JR|ＪＲ|東京メトロ|都営|京王|小田急|西武|東急|東武|京急|京成)?.*?線\s*", "", name) or name
            name = re.sub(r"駅$", "", name)
            if not name or len(name) > 10 or name in seen:
                continue
            seen.add(name)
            out.append({"station": name, "walk_min": int(mins)})
    return out


def kanji_chome(s: str) -> str:
    def rep(m: re.Match) -> str:
        k = m.group(1)
        n = 0
        if k == "十":
            n = 10
        elif k.startswith("十"):
            n = 10 + KANJI_NUM.get(k[1], 0)
        elif k.endswith("十"):
            n = KANJI_NUM.get(k[0], 0) * 10
        elif "十" in k:
            a, b = k.split("十")
            n = KANJI_NUM.get(a, 0) * 10 + KANJI_NUM.get(b, 0)
        else:
            n = KANJI_NUM.get(k, 0)
        return f"{n}丁目"
    return re.sub(r"([一二三四五六七八九十]{1,3})丁目", rep, s)


def address(s: str, default_pref: str = "") -> str | None:
    s = norm(s)
    for m in ADDRESS.finditer(s):
        a = m.group(1)
        if not re.search(r"[区市郡町村]", a) or len(a) < 3:
            continue
        a = kanji_chome(a).replace(" ", "")
        if default_pref and not a.startswith(PREFS) and not re.match(r"^[^\s]{2,3}県", a):
            a = default_pref + a
        return a
    return None


def parse_address(a: str | None) -> dict:
    """{'pref','city','town','chome','rest'} from a normalized address."""
    if not a:
        return {}
    m = re.match(r"^(東京都|北海道|京都府|大阪府|[^\s]{2,3}県)?((?:.+?市.+?区)|(?:.+?郡.+?[町村])|(?:.+?[区市町村]))?(.*)$", a)
    if not m:
        return {"rest": a}
    pref, city, rest = m.group(1) or "", m.group(2) or "", m.group(3) or ""
    town, chome = rest, None
    cm = re.match(r"^(.*?)(\d{1,2})丁目(.*)$", rest)
    if cm:
        town, chome, rest = cm.group(1), int(cm.group(2)), cm.group(3)
    else:
        dm = re.match(r"^(.*?[^\d-])(\d{1,2})(?:-(.*))?$", rest)
        if dm:
            town, chome, rest = dm.group(1), int(dm.group(2)), dm.group(3) or ""
        else:
            rest = ""
    return {"pref": pref, "city": city, "town": town.strip("-"), "chome": chome, "rest": rest}


def address_key(a: str | None) -> str:
    """General-to-specific key: "東京都|中野区|弥生町|3|12|5". "弥生町3" and "弥生町3丁目12-5"
    share a prefix, so they are compatible granularities of one address, not a conflict."""
    p = parse_address(norm(kanji_chome(a or "")).replace(" ", ""))
    if not p:
        return ""
    rest = re.sub(r"番地?|号", "-", p.get("rest") or "")
    toks = [p.get("pref") or "", p.get("city") or "", (p.get("town") or "").strip("-")]
    if p.get("chome") is not None:
        toks.append(str(p["chome"]))
    toks += [t for t in re.split(r"[-‐－の]", rest) if t]
    while toks and not toks[-1]:
        toks.pop()
    return "|".join(toks)


def move_in(s: str) -> str | None:
    if re.search(r"即(?:入居)?(?:可)?", s):
        return "immediate"
    m = re.search(r"(?<!\d)(\d{4}|['’]?\d{2})年\s*(\d{1,2})月\s*(上旬|中旬|下旬)?", s)
    if m:
        y = m.group(1).lstrip("'’")
        y = f"20{y}" if len(y) == 2 else y
        day = {"上旬": 5, "中旬": 15, "下旬": 25}.get(m.group(3) or "", 1)
        return f"{y}-{int(m.group(2)):02d}-{day:02d}"
    if "相談" in s:
        return "negotiable"
    return None


def name_key(n: str | None) -> str:
    if not n:
        return ""
    n = norm(n).lower()
    for t in BUILDING_TYPES:
        n = n.replace(t.lower(), "")
    n = re.sub(r"[\s・･\-‐ー－_,.。、'\"“”()（）【】\[\]]", "", n)
    return n


def is_placeholder_name(n: str | None) -> bool:
    """Portals that hide names show '<ward> <town>(<station>駅)' or UI labels instead."""
    if not n:
        return True
    n = norm(n)
    return bool(re.search(r"[区市][^\s]{0,8}\d*丁目|駅\)$|^\S+[区市]\s|物件\d*件|\d+件|掲載|一覧|検索"
                          r"|^(?:\S*線\s*)?\S+駅$|^(?:初期費用|空室情報|周辺環境|お問い?合わせ|賃料|家賃|管理費|敷金|礼金"
                          r"|間取り?|専有面積|詳細|物件詳細|基本情報|物件情報|物件概要|おすすめ|新着)$"
                          r"|お電話|こちら|お問い?合わ?せ|クリック|ログイン|会員登録", n))


def date_ymd(s: str) -> str | None:
    m = re.search(r"(\d{4})[/年.-](\d{1,2})[/月.-](\d{1,2})", s)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
