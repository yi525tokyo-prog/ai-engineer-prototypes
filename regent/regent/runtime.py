"""Process-wide services: provider registry, tool registry, global brain."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from regent.config import settings
from regent.models.registry import ProviderRegistry
from regent.tools.registry import ToolRegistry


@dataclass
class Services:
    providers: ProviderRegistry
    tools: ToolRegistry

    @property
    def workspace(self):
        settings.workspace.mkdir(parents=True, exist_ok=True)
        return settings.workspace


_services: Services | None = None
_lock = threading.Lock()


def get_services() -> Services:
    global _services
    with _lock:
        if _services is None:
            _services = Services(providers=ProviderRegistry(), tools=ToolRegistry())
        return _services


def set_services(s: Services | None) -> None:
    global _services
    with _lock:
        _services = s
