"""Markdown report for scripts/app_benchmark.py (rendered from its JSON)."""

from __future__ import annotations

import json
import sys
from typing import Any


def render(out: dict[str, Any]) -> str:
    return "# Application capability benchmark\n\n```json\n" + json.dumps(
        {k: out.get(k) for k in ("started", "seconds")}, indent=1) + "\n```\n"


if __name__ == "__main__":
    print(render(json.load(open(sys.argv[1]))))
