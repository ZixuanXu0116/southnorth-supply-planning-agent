"""Serving an MCP server over Streamable HTTP, one server per container (decision record 010).

stdio stays the default (development, tests, evaluation: the harness starts the server as its child). With
``SNM_MCP_TRANSPORT=http`` the server listens on ``SNM_MCP_HOST``:``SNM_MCP_PORT`` at ``/mcp``, and every
request must carry the service token (``Authorization: Bearer <SNM_SERVICE_TOKEN>``), which proves that the
caller is the harness. The harness then states, in request headers, what a child process used to receive in
its environment: the agent run (``X-SNM-Run-Id``, stamped on audit records and case events) and, for the case
server, the role it works for and the bound case of the intake profile. Headers are the caller's statements,
accepted only because the token authenticated the caller; the server's own checks (the role's grants, the SQL
guard, the case server's scope) apply as before. ``/health`` answers without the token, for the container's
health check.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

#: The current MCP request's headers (lower-case names); empty on stdio.
REQUEST_HEADERS: ContextVar[Mapping[str, str]] = ContextVar("snm_request_headers", default={})  # noqa: B039
MIN_TOKEN_CHARS = 32


def transport() -> str:
    """``stdio`` (default) or ``http``."""
    value = os.environ.get("SNM_MCP_TRANSPORT", "stdio")
    if value not in ("stdio", "http"):
        raise SystemExit(f"SNM_MCP_TRANSPORT must be stdio or http, not {value!r}")
    return value


def request_header(name: str) -> str | None:
    """A header of the MCP request being handled (None on stdio or when absent)."""
    return REQUEST_HEADERS.get().get(name.lower())


async def headers_middleware(ctx: Any, call_next: Any) -> Any:
    """Server middleware: makes the request's headers visible to the handler it wraps (same task)."""
    headers = getattr(getattr(ctx, "request", None), "headers", None) or {}
    reset = REQUEST_HEADERS.set({str(key).lower(): str(value) for key, value in headers.items()})
    try:
        return await call_next(ctx)
    finally:
        REQUEST_HEADERS.reset(reset)


class ServiceToken:
    """ASGI wrapper: refuses every HTTP request without the service token, except ``/health``."""

    def __init__(self, app: Any, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)  # lifespan: starts the session manager
            return
        if scope.get("path") == "/health":
            await _respond(send, 200, b"ok")
            return
        supplied = dict(scope.get("headers") or []).get(b"authorization", b"")
        if not hmac.compare_digest(supplied, self.expected):
            await _respond(send, 401, b"unauthorized")
            return
        await self.app(scope, receive, send)


async def _respond(send: Any, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def http_app(server: Any, token: str, host: str = "127.0.0.1") -> ServiceToken:
    """The server's Streamable HTTP app behind the service token. ``SNM_ALLOWED_HOSTS`` (comma-separated
    ``host:port`` patterns, e.g. ``tool-server-ops:*``) turns on the SDK's Host-header check for a
    container that is reached by its service name."""
    from mcp.server.transport_security import TransportSecuritySettings

    allowed = [item.strip() for item in os.environ.get("SNM_ALLOWED_HOSTS", "").split(",") if item.strip()]
    security = TransportSecuritySettings(allowed_hosts=allowed, allowed_origins=[]) if allowed else None
    return ServiceToken(server.streamable_http_app(host=host, transport_security=security), token)


def serve(server: Any) -> None:
    """Run ``server`` on the configured transport."""
    if transport() == "stdio":
        server.run("stdio")
        return
    import uvicorn

    token = os.environ.get("SNM_SERVICE_TOKEN", "")
    if len(token) < MIN_TOKEN_CHARS:
        raise SystemExit(f"SNM_SERVICE_TOKEN of at least {MIN_TOKEN_CHARS} characters is required over HTTP")
    host = os.environ.get("SNM_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("SNM_MCP_PORT", "8000"))
    uvicorn.run(http_app(server, token, host), host=host, port=port, log_level="warning")
