"""The gateway's MCP *server* side: one Streamable HTTP endpoint at /mcp.

Request flow for an external client (Claude Code, Cursor, MCP Inspector):

  POST /mcp  Authorization: Bearer mgw_...      (JSON-RPC: initialize / tools/list / tools/call)
     |
     v
  MCPAuthApp  -- hash the key, look it up, reject revoked keys / disabled users (401)
     |           stores the resolved Principal in the ASGI scope
     v
  StreamableHTTPSessionManager (stateless)  -- parses JSON-RPC, runs the low-level Server
     |
     v
  list_tools / call_tool handlers -- read the Principal, delegate to UpstreamManager

Stateless mode means no MCP session is kept in memory between HTTP requests, so any gateway
replica can serve any request (horizontal scaling without sticky sessions).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from sqlalchemy import select
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from . import metrics
from .config import Settings
from .db import sessionmaker
from .models import ApiKey, User
from .security import API_KEY_PREFIX, INTERNAL_AUDIENCE, decode_token, hash_token
from .upstreams import Principal, UpstreamManager

log = logging.getLogger("gateway.mcp")


async def resolve_principal(settings: Settings, authorization: str) -> tuple[Principal | None, str]:
    """Return (principal, reason). Accepts a user API key or a short-lived internal chat token."""
    if not authorization.lower().startswith("bearer "):
        return None, "missing_token"
    token = authorization[7:].strip()
    async with sessionmaker()() as db:
        if token.startswith(API_KEY_PREFIX):
            key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_token(token)))
            if key is None:
                return None, "unknown_key"
            if key.revoked_at is not None:
                return None, "revoked_key"
            user = await db.get(User, key.user_id)
            if user is None or not user.is_active:
                return None, "inactive_user"
            now = datetime.now(timezone.utc)
            last = key.last_used_at.replace(tzinfo=timezone.utc) if key.last_used_at and key.last_used_at.tzinfo is None else key.last_used_at
            if last is None or now - last > timedelta(seconds=60):   # throttle writes
                key.last_used_at = now
                await db.commit()
            return Principal(user.id, user.email, user.role, source="mcp", api_key_id=key.id), "ok"

        user_id = decode_token(settings.secret_key, token, INTERNAL_AUDIENCE)
        if user_id is None:
            return None, "invalid_token"
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            return None, "inactive_user"
        return Principal(user.id, user.email, user.role, source="chat"), "ok"


def build_mcp_server(manager: UpstreamManager) -> Server:
    server: Server = Server(
        "urumi-mcp-gateway",
        version="1.0.0",
        instructions=("Team MCP gateway. Tools are named <server>__<tool>; the prefix tells you which "
                      "upstream server runs the tool."),
    )

    def current_principal() -> Principal:
        request = server.request_context.request
        principal = getattr(request.state, "principal", None) if request is not None else None
        if principal is None:  # should be impossible: MCPAuthApp runs first
            raise PermissionError("unauthenticated")
        return principal

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return await manager.list_tools(current_principal())

    # validate_input=False: the upstream validates its own arguments and returns precise errors
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return await manager.call_tool(current_principal(), name, arguments or {})

    return server


class MCPAuthApp:
    """ASGI app mounted at /mcp: authenticate, then hand the request to the MCP session manager."""

    def __init__(self, settings: Settings, session_manager: StreamableHTTPSessionManager) -> None:
        self.settings = settings
        self.session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        principal, reason = await resolve_principal(self.settings, headers.get("authorization", ""))
        if principal is None:
            metrics.AUTH_FAILURES.labels(reason).inc()
            messages = {
                "missing_token": "Missing 'Authorization: Bearer <api key>' header",
                "unknown_key": "Invalid API key",
                "revoked_key": "This API key has been revoked",
                "inactive_user": "This user account is disabled",
                "invalid_token": "Invalid or expired token",
            }
            response = JSONResponse({"error": "unauthorized", "detail": messages.get(reason, reason)},
                                    status_code=401, headers={"WWW-Authenticate": 'Bearer realm="mcp-gateway"'})
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["principal"] = principal
        await self.session_manager.handle_request(scope, receive, send)


def build_session_manager(server: Server) -> StreamableHTTPSessionManager:
    from mcp.server.transport_security import TransportSecuritySettings

    return StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=False,  # stream responses as SSE, the default Streamable HTTP behaviour
        # Host-header (DNS rebinding) checks target localhost dev servers; our endpoint sits behind an
        # Ingress with a real hostname and every request is authenticated with a bearer key instead.
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
