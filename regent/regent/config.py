"""Runtime configuration, read from the environment once."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: _env(
            "REGENT_DATABASE_URL", "postgresql+psycopg://regent:regent@localhost:5432/regent"
        )
    )
    workspace: Path = field(
        default_factory=lambda: Path(_env("REGENT_WORKSPACE", str(ROOT / "var" / "workspace"))).resolve()
    )
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    xai_api_key: str = field(default_factory=lambda: _env("XAI_API_KEY"))
    search_api_key: str = field(default_factory=lambda: _env("REGENT_SEARCH_API_KEY"))
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    global_brain_url: str = field(default_factory=lambda: _env("REGENT_GLOBAL_BRAIN_URL"))
    public_api_url: str = field(default_factory=lambda: _env("REGENT_PUBLIC_API_URL", "http://localhost:8000"))
    background_loop: bool = field(default_factory=lambda: _env("REGENT_BACKGROUND_LOOP", "1") == "1")
    loop_interval_s: float = field(default_factory=lambda: float(_env("REGENT_LOOP_INTERVAL", "2.0")))
    browser_executable: str = field(default_factory=lambda: _env("REGENT_BROWSER_EXECUTABLE"))


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
