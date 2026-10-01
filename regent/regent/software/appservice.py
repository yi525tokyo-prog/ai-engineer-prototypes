"""Running applications Regent owns: start, stop, health, logs, live and staging instances.

An application version lives in its own workspace (the code a worker wrote) and is started from
its ``regent.json`` manifest. Its state lives in a data directory Regent owns, so Regent can back
it up, copy it into a staging instance to test a new version against real data, and restore it if
an upgrade fails. The live instance is restarted by the maintenance pass when it is down.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from regent.config import settings

_RUNNING: dict[str, "AppService"] = {}


def apps_root() -> Path:
    p = settings.workspace / "apps"
    p.mkdir(parents=True, exist_ok=True)
    return p


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def manifest(workspace: Path) -> dict[str, Any]:
    p = workspace / "regent.json"
    if not p.exists():
        raise ValueError("no regent.json manifest")
    m = json.loads(p.read_text())
    for k in ("start", "test", "health"):
        if not m.get(k):
            raise ValueError(f"regent.json lacks '{k}'")
    if "{port}" not in m["start"] and "PORT" not in m["start"]:
        # the port may also be read from the PORT environment variable
        m.setdefault("port_from_env", True)
    return m


def runtime_env(workspace: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """A small environment: tools, proxy for package installs, an isolated HOME. No Regent secrets
    except the ones passed explicitly (the application's own credential)."""
    keep = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy", "SSL_CERT_FILE",
            "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "PIP_CERT", "LANG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    if "SSL_CERT_FILE" in env:
        env.setdefault("REQUESTS_CA_BUNDLE", env["SSL_CERT_FILE"])
        env.setdefault("PIP_CERT", env["SSL_CERT_FILE"])
    home = workspace / ".home"
    home.mkdir(exist_ok=True)
    env.update({"PATH": os.pathsep.join(["/opt/node22/bin", "/usr/local/bin", "/usr/bin", "/bin"]),
                "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra or {})
    return env


def run(cmd: str, workspace: Path, *, timeout: int, env: dict[str, str] | None = None) -> dict[str, Any]:
    t0 = time.time()
    try:
        p = subprocess.run(cmd, shell=True, cwd=workspace, env=env or runtime_env(workspace), capture_output=True,
                           text=True, timeout=timeout)
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return {"cmd": cmd, "ok": p.returncode == 0, "code": p.returncode, "seconds": round(time.time() - t0, 1),
                "tail": out[-4000:]}
    except subprocess.TimeoutExpired as e:
        return {"cmd": cmd, "ok": False, "code": None, "seconds": timeout, "tail": f"timed out after {timeout}s\n"
                f"{(e.stdout or b'')[-2000:]!r}"}


@dataclass
class AppService:
    slug: str
    role: str                           # live | staging
    workspace: Path
    data_dir: Path
    credential: str | None = None
    port: int = 0
    proc: subprocess.Popen | None = None
    log_path: Path | None = None
    started_at: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.slug}:{self.role}"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout_s: int = 90) -> dict[str, Any]:
        m = manifest(self.workspace)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.port = self.port or free_port()
        env = runtime_env(self.workspace, {"PORT": str(self.port), "DATA_DIR": str(self.data_dir),
                                           "HOST": "127.0.0.1"})
        if self.credential:
            env["APP_PASSPHRASE"] = self.credential
        cmd = m["start"].replace("{port}", str(self.port)).replace("{data_dir}", str(self.data_dir))
        self.log_path = self.workspace / f".run-{self.role}.log"
        log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(cmd, shell=True, cwd=self.workspace, env=env, stdout=log, stderr=log,
                                     start_new_session=True)
        self.started_at = time.time()
        health = m["health"]
        deadline = time.time() + timeout_s
        last = ""
        while time.time() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                r = httpx.get(self.url + health, timeout=3, trust_env=False)
                if r.status_code == 200:
                    _RUNNING[self.key] = self
                    ev = {"event": "started", "port": self.port, "seconds": round(time.time() - self.started_at, 1)}
                    self.history.append(ev)
                    return {"ok": True, **ev}
                last = f"health {r.status_code}"
            except httpx.HTTPError as e:
                last = type(e).__name__
            time.sleep(0.5)
        tail = self.logs()
        self.stop()
        return {"ok": False, "error": f"did not become healthy ({last}); exit={self.proc.poll() if self.proc else None}",
                "log_tail": tail}

    def healthy(self) -> bool:
        if self.proc is None or self.proc.poll() is not None:
            return False
        try:
            return httpx.get(self.url + manifest(self.workspace)["health"], timeout=3, trust_env=False).status_code == 200
        except Exception:
            return False

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        _RUNNING.pop(self.key, None)
        self.history.append({"event": "stopped"})

    def restart(self) -> dict[str, Any]:
        port = self.port
        self.stop()
        self.port = port
        return self.start()

    def logs(self, n: int = 3000) -> str:
        try:
            return self.log_path.read_text(errors="ignore")[-n:] if self.log_path else ""
        except OSError:
            return ""


def get(slug: str, role: str = "live") -> AppService | None:
    return _RUNNING.get(f"{slug}:{role}")


def stop_all() -> None:
    for s in list(_RUNNING.values()):
        s.stop()


def backup(data_dir: Path, label: str) -> Path:
    dest = data_dir.parent / "backups" / f"{data_dir.name}-{label}-{int(time.time())}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if data_dir.exists():
        shutil.copytree(data_dir, dest)
    else:
        dest.mkdir()
    return dest


def restore(backup_dir: Path, data_dir: Path) -> None:
    if data_dir.exists():
        shutil.rmtree(data_dir)
    shutil.copytree(backup_dir, data_dir)
