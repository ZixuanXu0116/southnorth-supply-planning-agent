"""The planner's case server, started once with the pages (a deployed app, docs/DEPLOYMENT.md).

Without it, every planner run starts a fresh case server as the harness's child: a new Python process, new
warehouse sessions, and empty caches, which in the lakehouse costs seconds per run. Here the pages start the
case server once on localhost over Streamable HTTP (decision record 010), with a service token generated
at start and never stored, and point the harness at it (``SNM_CASE_SERVER_URL``, ``SNM_CASE_SERVER_TOKEN``)
in the environment its runs inherit. If the server has stopped, the next planner run starts it again.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
import urllib.request

HEALTH_TIMEOUT_SECONDS = 90


class ResidentCaseServer:
    def __init__(
        self,
        port: int,
        roles: str = "purchasing",
        python: str = sys.executable,
        module: str = "snm_cases.server",
    ) -> None:
        self.port = port
        self.roles = roles
        self.python = python
        self.module = module
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def start(self) -> ResidentCaseServer:
        token = secrets.token_urlsafe(32)
        env = {
            **os.environ,
            "SNM_MCP_TRANSPORT": "http",
            "SNM_MCP_HOST": "127.0.0.1",
            "SNM_MCP_PORT": str(self.port),
            "SNM_SERVICE_TOKEN": token,
            "SNM_PLANNER_ROLES": self.roles,
        }
        # The server holds no model settings or key (as the harness's stdio child would not).
        for name in [
            n for n in env if n.startswith(("SNM_ANTHROPIC", "ANTHROPIC", "SNM_MODEL", "SNM_EFFORT"))
        ]:
            del env[name]
        self.process = subprocess.Popen([self.python, "-m", self.module], env=env)
        deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"the case server exited with code {self.process.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.5)
        else:
            self.stop()
            raise RuntimeError("the case server did not answer within the timeout")
        os.environ["SNM_CASE_SERVER_URL"] = self.url
        os.environ["SNM_CASE_SERVER_TOKEN"] = token
        return self

    def ensure(self) -> None:
        """Start the server again if it has stopped (called before each planner run)."""
        if self.process is None or self.process.poll() is not None:
            self.start()

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
