"""How Regent runs ``claude`` (reasoning and coding workers) as the person, without their setup.

Workers must be signed in as the person but must not pick up their Claude Code settings, hooks,
plugins or MCP servers. With a token in the environment (``CLAUDE_CODE_OAUTH_TOKEN`` from
``claude setup-token``, ``ANTHROPIC_API_KEY``, or a provider gateway) a worker gets a home of its
own. Otherwise the person's own login is used where it lives (a file under ``~/.claude`` or the
macOS keychain) — never a copy, which would go stale when Claude Code renews it — and their
settings and MCP servers are switched off by flags instead.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PASS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "AWS_REGION", "AWS_PROFILE",
        "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION", "CLAUDE_CONFIG_DIR",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
        "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "LANG", "USER", "LOGNAME")
_SIGNED_IN_BY_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                     "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
ISOLATE = ["--setting-sources", "project,local", "--strict-mcp-config"]


def claude_env(home: Path) -> tuple[dict[str, str], list[str]]:
    """(environment, extra arguments) for one ``claude`` run whose private home would be ``home``."""
    env = {k: os.environ[k] for k in PASS if k in os.environ}
    exe = shutil.which("claude")
    env["PATH"] = os.pathsep.join(dict.fromkeys(p for p in (str(Path(exe).parent) if exe else "", "/opt/node22/bin",
                                                            "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin")
                                                if p))
    if any(os.environ.get(k) for k in _SIGNED_IN_BY_ENV):
        env["HOME"] = str(home)
        return env, []
    env["HOME"] = str(Path.home())
    return env, list(ISOLATE)
