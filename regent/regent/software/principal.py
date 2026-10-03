"""What Regent can observe about its principal without asking.

Regent runs on the principal's machine, so the identities configured there (the git author,
the owners of the repositories Regent itself was cloned from) are evidence about which
accounts are the principal's -- weak evidence, recorded with where it came from. It is used
to recognise the principal's own deployments (e.g. an API hosted under their account name),
never to act as them.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from regent.config import ROOT


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=Path(ROOT), capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


@lru_cache(maxsize=1)
def evidence() -> dict[str, Any]:
    accounts: dict[str, str] = {}
    email = _git("config", "user.email")
    if email and "@" in email and not email.endswith(("noreply.github.com", "anthropic.com")):
        accounts[email.split("@")[0].lower()] = "git user.email local part"
    remote = _git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]+([^/]+)/", remote) or re.search(r"/git/([^/]+)/", remote)
    if m:
        accounts.setdefault(m.group(1).lower(), "owner of the repository Regent runs from")
    handles = set(accounts)
    for h in list(handles):
        base = re.sub(r"[-_](prog|dev|code|hq|app|io)$", "", h)
        if base != h:
            accounts.setdefault(base, f"variant of {h}")
    return {"accounts": accounts}


def accounts() -> list[str]:
    return list(evidence()["accounts"])


def owns_host(host: str) -> str | None:
    """Evidence that a hostname belongs to the principal (their account name in it)."""
    labels = host.lower().split(".")
    for a, why in evidence()["accounts"].items():
        if a in labels:
            return f"'{a}' in hostname ({why})"
    return None
