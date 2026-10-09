"""Credentials the principal hands Regent.

A credential is looked up in the process environment first, then in a file-backed store
(``<workspace>/secrets.json``, mode 0600) that a resolved credential interrupt writes to.
Regent never logs or projects a secret's value: the world model only learns the fact
``credential.<NAME> = "present"``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from regent.config import settings


def _path() -> Path:
    return settings.workspace / "secrets.json"


def _load() -> dict[str, str]:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except ValueError:
        return {}


def get(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or _load().get(name) or None


def present(name: str) -> bool:
    return get(name) is not None


def put(name: str, value: str) -> None:
    data = _load()
    data[name] = value.strip()
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    os.chmod(p, 0o600)


def missing(names: list[str]) -> list[str]:
    return [n for n in names if not present(n)]
