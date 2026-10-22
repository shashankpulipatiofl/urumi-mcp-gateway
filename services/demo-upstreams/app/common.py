"""Shared plumbing for the demo upstream servers: header auth guard, health routes, runner."""

from __future__ import annotations

import hmac
import logging
import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send


def new_server(name: str, instructions: str) -> FastMCP:
    return FastMCP(
        name=name,
        instructions=instructions,
        stateless_http=True,
        json_response=True,
        # Cluster-internal server, called via its Service DNS name (see woocommerce-mcp for details)
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


class HeaderAuthGuard:
    """Require `<header>: <expected>` on /mcp. If expected is empty, auth is off."""

    def __init__(self, app: ASGIApp, header: str, expected: str) -> None:
        self.app = app
        self.header = header.lower().encode()
        self.expected = expected.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.expected and scope["path"].startswith("/mcp"):
            provided = dict(scope["headers"]).get(self.header, b"")
            if not hmac.compare_digest(provided, self.expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(mcp: FastMCP, auth_header: str = "", auth_value: str = "") -> ASGIApp:
    app = mcp.streamable_http_app()

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": mcp.name})

    app.router.routes.append(Route("/healthz", healthz))
    app.router.routes.append(Route("/readyz", healthz))
    if auth_header and auth_value:
        return HeaderAuthGuard(app, auth_header, auth_value)
    return app


def run(app: ASGIApp) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), log_level="info")
