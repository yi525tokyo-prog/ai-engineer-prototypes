"""Vetted code templates the local strategist can use to build missing tools.

A built tool is a standalone script ``tool.py`` exposing actions via
``python tool.py <action> '<json inputs>'`` and printing a JSON result, plus a
``test_tool.py`` that must pass before the capability is marked available.
Remote providers can synthesize arbitrary tools with the same contract.
"""

from __future__ import annotations

INVOICE_TOOL = r'''
"""Invoice generator (built by Regent during capability acquisition)."""
import json
import sys
from datetime import date
from pathlib import Path


def generate(inputs):
    items = inputs.get("items") or [{"description": "Services", "amount": inputs.get("amount", 0)}]
    total = round(sum(float(i["amount"]) for i in items), 2)
    tax_rate = float(inputs.get("tax_rate", 0.10))
    tax = round(total * tax_rate, 2)
    number = inputs.get("number") or f"INV-{date.today():%Y%m%d}-001"
    currency = inputs.get("currency", "JPY")
    lines = [f"INVOICE {number}", f"Date: {date.today().isoformat()}", f"Bill to: {inputs.get('client', 'Client')}", ""]
    for i in items:
        lines.append(f"{i['description']:<40} {float(i['amount']):>12,.0f} {currency}")
    lines += ["", f"{'Subtotal':<40} {total:>12,.0f} {currency}",
              f"{'Tax (' + str(int(tax_rate * 100)) + '%)':<40} {tax:>12,.0f} {currency}",
              f"{'Total':<40} {total + tax:>12,.0f} {currency}"]
    out_dir = Path(inputs.get("out_dir", "invoices"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{number}.txt"
    path.write_text("\n".join(lines) + "\n")
    return {"path": str(path.resolve()), "number": number, "subtotal": total, "tax": tax, "total": total + tax}


ACTIONS = {"generate": generate}

if __name__ == "__main__":
    action, raw = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "{}")
    print(json.dumps(ACTIONS[action](json.loads(raw))))
'''

INVOICE_TEST = r'''
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def test_generate(tmp_path):
    inputs = {"client": "ACME", "items": [{"description": "Work", "amount": 1000}], "out_dir": str(tmp_path)}
    out = subprocess.run([sys.executable, str(HERE / "tool.py"), "generate", json.dumps(inputs)],
                         capture_output=True, text=True, check=True).stdout
    res = json.loads(out)
    assert res["total"] == 1100
    assert Path(res["path"]).read_text().startswith("INVOICE")
'''


def invoice_generate() -> dict:
    return {
        "files": {"tool.py": INVOICE_TOOL.lstrip(), "test_tool.py": INVOICE_TEST.lstrip()},
        "tool_name": "invoice",
        "actions": {"generate": {"description": "Generate a text invoice", "authority": "AUTO",
                                 "input_schema": {"client": "str", "items": "list", "currency": "str"},
                                 "output_schema": {"path": "str", "total": "float"}}},
        "capabilities": ["invoice.generate"],
        "test": "test_tool.py",
    }


TEMPLATES = {"invoice.generate": invoice_generate}
