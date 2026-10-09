"""Start Regent: ``python -m regent`` (or ``./start``).

One process: the web page you use, the API behind it, and the loop that keeps working while you
are away. Everything lives in ``~/.regent`` (or ``REGENT_HOME``): a SQLite database, what Regent
learns, the applications it builds and their data. Nothing to configure; nothing else to start.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="regent", description="Start Regent and open it in your browser.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("REGENT_PORT", "7777")))
    ap.add_argument("--host", default=os.environ.get("REGENT_HOST", "127.0.0.1"),
                    help="127.0.0.1 (default) keeps Regent reachable from this computer only")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    a = ap.parse_args(argv)

    home = Path(os.environ.get("REGENT_HOME", Path.home() / ".regent")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("REGENT_WORKSPACE", str(home / "workspace"))
    os.environ.setdefault("REGENT_DATABASE_URL", f"sqlite:///{home / 'regent.db'}")
    os.environ.setdefault("REGENT_BACKGROUND_LOOP", "1")
    os.environ.setdefault("REGENT_LOOP_INTERVAL", "3")
    # a coding assistant may be used, but every build still asks you first, in the page
    os.environ.setdefault("REGENT_CODING_AGENT", "claude-code")
    os.environ.setdefault("REGENT_PUBLIC_API_URL", f"http://{a.host}:{a.port}")

    if not _free(a.port) and a.host in ("127.0.0.1", "localhost"):
        import httpx

        url = f"http://127.0.0.1:{a.port}"
        try:
            running = httpx.get(url + "/api/home", timeout=3, trust_env=False).status_code == 200
        except Exception:
            running = False
        if running:
            print(f"Regent is already running: {url}")
            if not a.no_browser:
                webbrowser.open(url)
            return
        for _ in range(40):                 # an earlier Regent may still be shutting down
            time.sleep(0.5)
            if _free(a.port):
                break
        else:
            sys.exit(f"Port {a.port} is used by another program. Start Regent on another one: ./start --port 7778")

    print("Starting Regent…")
    print(f"  your data: {home}")
    if shutil.which("claude") is None:
        print("  note: Claude Code (the `claude` command) is not installed or not on PATH. Regent needs it to\n"
              "        understand requests; the page will say so until it is available.")
    import uvicorn

    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '127.0.0.1') else a.host}:{a.port}"

    def _open() -> None:
        import httpx

        for _ in range(100):
            try:
                if httpx.get(url + "/api/home", timeout=2, trust_env=False).status_code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        print(f"\nRegent is running: {url}\n(press Ctrl+C to stop; work in progress resumes next time)\n")
        if not a.no_browser:
            webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()
    uvicorn.run("regent.api.app:app", host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
