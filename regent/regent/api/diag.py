"""What went wrong lately, readable without the server's console (a hosted Regent has none)."""

from __future__ import annotations

import collections
import logging
import threading
import time

_RECENT: collections.deque = collections.deque(maxlen=300)
_lock = threading.Lock()


class _Keep(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:  # noqa: BLE001
            text = record.getMessage()
        with _lock:
            _RECENT.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                            "level": record.levelname, "where": record.name, "text": text[-4000:]})


def install() -> None:
    root = logging.getLogger("regent")
    if not any(isinstance(h, _Keep) for h in root.handlers):
        h = _Keep()
        h.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)


def recent(n: int = 100) -> list[dict]:
    with _lock:
        return list(_RECENT)[-n:]
