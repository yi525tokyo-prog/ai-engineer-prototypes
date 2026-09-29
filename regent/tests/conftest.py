"""Test harness.

Tests run against PostgreSQL (database ``regent_test``) when reachable, else a
temporary SQLite file. Every test gets a fresh schema, workspace and service
registry. ``live_server`` starts the real API (uvicorn) in-process for tests
that need the browser to reach the simulated client portal.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

PG_URL = os.environ.get("REGENT_TEST_DATABASE_URL",
                        "postgresql+psycopg://regent:regent@localhost:5432/regent_test")


def _pg_available() -> bool:
    try:
        import psycopg

        with psycopg.connect(PG_URL.replace("postgresql+psycopg", "postgresql"), connect_timeout=2):
            return True
    except Exception:
        return False


_TMP = Path(os.environ.get("REGENT_TEST_TMP", "/tmp/regent-tests"))
_TMP.mkdir(parents=True, exist_ok=True)
DB_URL = PG_URL if _pg_available() else f"sqlite:///{_TMP / 'regent_test.db'}"
os.environ["REGENT_DATABASE_URL"] = DB_URL
os.environ["REGENT_BACKGROUND_LOOP"] = "0"
for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "REGENT_SEARCH_API_KEY", "GITHUB_TOKEN",
          "STRIPE_API_KEY"):
    os.environ.pop(k, None)

from regent import config  # noqa: E402

config.reload_settings()

from regent import db as dbm  # noqa: E402
from regent.runtime import get_services, set_services  # noqa: E402
from regent.core.human import interrupts as _interrupts  # noqa: E402

_interrupts.PAGE_CHECK_INTERVAL_S = 0.0  # tests resume immediately


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config.settings, "workspace", ws)
    return ws


@pytest.fixture()
def db(workspace):
    # Wait for any loop run the API scheduled as a background task (previous test) before
    # dropping tables underneath it.
    from regent.api import app as appmod

    time.sleep(0.05)
    with appmod._run_lock:
        dbm.configure(DB_URL)
        dbm.init_db(drop=True)
    set_services(None)
    s = dbm.session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def services(db):
    return get_services()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    import uvicorn

    from regent.api.app import app

    port = _free_port()
    config.settings.public_api_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)
