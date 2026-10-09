"""Run Regent hosted (``python -m regent.cloud``): the same Regent, kept safe on a disk that may vanish.

A hosted container's disk is lost whenever the container is replaced. So this wrapper:
- restores the person's Regent home from the last backup before Regent starts;
- starts Regent itself (the page, the API and the loop) and keeps it running;
- saves the home again a little after anything in it changes, and once more when asked to stop.

Backups go to ``REGENT_BACKUP_URL``. On Cloudflare that is a host only this container can reach:
its own Worker answers it and keeps the backup in R2 (see deploy/cloudflare). Elsewhere it can be
any store that takes GET/PUT, authorised by ``REGENT_BACKUP_SECRET``. The database is copied with SQLite's own backup, so a save
taken while Regent is writing is still consistent.
"""

from __future__ import annotations

import io
import os
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import httpx

SKIP_DIRS = {".home", "__pycache__", ".cache", ".pytest_cache"}
SKIP_SUFFIXES = (".pyc", ".db-wal", ".db-shm", ".db-journal", ".sock", ".pid")
MAX_BYTES = 95 * 1024 * 1024          # one backup must fit in one request


def _log(msg: str) -> None:
    print(f"[regent.cloud] {msg}", flush=True)


def _files(home: Path):
    for root, dirs, names in os.walk(home):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            p = Path(root) / n
            if not n.endswith(SKIP_SUFFIXES) and p.is_file() and not p.is_symlink():
                yield p


def fingerprint(home: Path) -> tuple:
    """What changes whenever anything worth saving changes. A database's write-ahead log counts: SQLite
    commits land there and reach the database file only at a checkpoint, maybe days later."""
    out = []
    for p in _files(home):
        for f in (p, p.with_name(p.name + "-wal")) if p.suffix == ".db" else (p,):
            try:
                st = f.stat()
            except FileNotFoundError:
                continue
            out.append((str(f.relative_to(home)), st.st_size, st.st_mtime_ns))
    return tuple(sorted(out))


def pack(home: Path) -> bytes:
    """A gzip'd tar of the home, with every SQLite database copied consistently."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar, tempfile.TemporaryDirectory() as tmp:
        for p in _files(home):
            rel = str(p.relative_to(home))
            try:
                if p.suffix == ".db":
                    snap = Path(tmp) / "snap.db"
                    snap.unlink(missing_ok=True)
                    src, dst = sqlite3.connect(p), sqlite3.connect(snap)
                    try:
                        src.backup(dst)
                    finally:
                        src.close()
                        dst.close()
                    tar.add(snap, arcname=rel)
                else:
                    tar.add(p, arcname=rel)
            except (FileNotFoundError, sqlite3.Error) as e:      # a file that went away mid-walk
                _log(f"skipped {rel}: {e}")
    return buf.getvalue()


def unpack(data: bytes, home: Path) -> int:
    home.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile():
                tar.extract(m, home, filter="data")
                n += 1
    return n


class Backups:
    def __init__(self, url: str, secret: str = ""):
        self.url, self.headers = url, ({"x-regent-backup": secret} if secret else {})

    def get(self) -> bytes | None:
        r = httpx.get(self.url, headers=self.headers, timeout=120)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def put(self, data: bytes) -> None:
        r = httpx.put(self.url, headers=self.headers, content=data, timeout=300)
        r.raise_for_status()


def main() -> None:
    home = Path(os.environ.setdefault("REGENT_HOME", "/data/regent"))
    port = os.environ.get("PORT", "8080")
    url, secret = os.environ.get("REGENT_BACKUP_URL", ""), os.environ.get("REGENT_BACKUP_SECRET", "")
    backups = Backups(url, secret) if url else None
    every = int(os.environ.get("REGENT_BACKUP_EVERY", "60"))

    if backups is None:
        _log("no backup location configured: data lives only as long as this container")
    else:
        for attempt in range(5):
            try:
                data = backups.get()
                if data is None:
                    _log("no earlier backup: starting fresh")
                else:
                    _log(f"restored {unpack(data, home)} files ({len(data) // 1024} KiB)")
                break
            except Exception as e:  # noqa: BLE001 - keep trying; never start over someone's data by accident
                _log(f"could not read the backup yet ({e}); retrying")
                time.sleep(3 * (attempt + 1))
        else:
            sys.exit("could not reach the backup store; not starting, so nothing is overwritten")

    cmd = [sys.executable, "-m", "regent", "--host", "0.0.0.0", "--port", port, "--no-browser"]
    proc = subprocess.Popen(cmd)
    stop = threading.Event()
    last = fingerprint(home) if backups else ()

    def save(reason: str) -> None:
        nonlocal last
        fp = fingerprint(home)
        if fp == last:
            return
        data = pack(home)
        if len(data) > MAX_BYTES:
            _log(f"backup is {len(data) // 2**20} MiB, over the limit; not saved")
            return
        try:
            backups.put(data)
            last = fp
            _log(f"saved ({reason}, {len(data) // 1024} KiB)")
        except Exception as e:  # noqa: BLE001
            _log(f"save failed ({e}); will retry")

    def on_term(*_: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    while not stop.is_set():
        stop.wait(every)
        if backups and not stop.is_set():
            save("periodic")
        if proc.poll() is not None and not stop.is_set():
            _log(f"Regent exited ({proc.returncode}); starting it again")
            proc = subprocess.Popen(cmd)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    if backups:
        save("stopping")
    sys.exit(proc.returncode or 0)


if __name__ == "__main__":
    main()
