"""Source for the seeded 'ledgerline' project (a real, runnable Python project
with a real test suite -- two tests fail because of genuine bugs)."""

FILES = {
    "ledgerline/__init__.py": '"""ledgerline: plain-text ledger import & reconciliation."""\n__version__ = "0.3.0"\n',
    "ledgerline/money.py": '''from decimal import Decimal, ROUND_HALF_UP


def to_minor(amount: str, currency: str = "JPY") -> int:
    """Convert a decimal string to minor units."""
    exp = 0 if currency == "JPY" else 2
    d = Decimal(amount)
    # BUG: rounds with float math instead of ROUND_HALF_UP on Decimal
    return int(round(float(d) * (10 ** exp)))


def split_evenly(total: int, parts: int) -> list[int]:
    base, rem = divmod(total, parts)
    return [base + (1 if i < rem else 0) for i in range(parts)]


def fmt(minor: int, currency: str = "JPY") -> str:
    if currency == "JPY":
        return f"\\u00a5{minor:,}"
    return f"{minor / 100:,.2f} {currency}"
''',
    "ledgerline/parse.py": '''import csv
import io
from datetime import date


def parse_date(s: str) -> date:
    s = s.strip()
    if "/" in s:
        y, m, d = s.split("/")
    else:
        y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def parse_csv(text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        rows.append({"date": parse_date(r["date"]), "desc": r["desc"].strip(), "amount": r["amount"].strip()})
    return rows
''',
    "ledgerline/reconcile.py": '''from .money import to_minor


def reconcile(bank: list[dict], ledger: list[dict], currency: str = "JPY") -> dict:
    """Match bank rows to ledger rows by (date, amount)."""
    key = lambda r: (r["date"], to_minor(r["amount"], currency))  # noqa: E731
    lk = {}
    for r in ledger:
        lk.setdefault(key(r), []).append(r)
    matched, unmatched = [], []
    for r in bank:
        bucket = lk.get(key(r))
        if bucket:
            matched.append((r, bucket.pop()))
        else:
            unmatched.append(r)
    leftovers = [r for rs in lk.values() for r in rs]
    return {"matched": matched, "unmatched_bank": unmatched, "unmatched_ledger": leftovers}
''',
    "tests/test_money.py": '''from ledgerline.money import fmt, split_evenly, to_minor


def test_to_minor_jpy():
    assert to_minor("1200") == 1200


def test_to_minor_usd_half_up():
    # 1.005 must round half-up to 101 cents (float math gives 100)
    assert to_minor("1.005", "USD") == 101


def test_split_evenly():
    assert split_evenly(100, 3) == [34, 33, 33]


def test_fmt_jpy():
    assert fmt(1200) == "\\u00a51,200"


def test_fmt_usd():
    assert fmt(12345, "USD") == "123.45 USD"
''',
    "tests/test_parse.py": '''from datetime import date

from ledgerline.parse import parse_csv, parse_date


def test_parse_iso():
    assert parse_date("2026-09-30") == date(2026, 9, 30)


def test_parse_slash():
    assert parse_date("2026/09/30") == date(2026, 9, 30)


def test_parse_dotted():
    # Japanese bank exports use dotted dates
    assert parse_date("2026.09.30") == date(2026, 9, 30)


def test_parse_csv():
    rows = parse_csv("date,desc,amount\\n2026-09-01, Coffee ,450\\n")
    assert rows[0]["desc"] == "Coffee" and rows[0]["amount"] == "450"
''',
    "tests/test_reconcile.py": '''from datetime import date

from ledgerline.reconcile import reconcile


def test_reconcile_matches():
    d = date(2026, 9, 1)
    bank = [{"date": d, "amount": "450"}, {"date": d, "amount": "1200"}]
    ledger = [{"date": d, "amount": "450"}]
    res = reconcile(bank, ledger)
    assert len(res["matched"]) == 1
    assert len(res["unmatched_bank"]) == 1
''',
    "tests/conftest.py": "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n",
    "README.md": "# ledgerline\n\nPlain-text ledger import & bank reconciliation for freelancers.\n",
}
