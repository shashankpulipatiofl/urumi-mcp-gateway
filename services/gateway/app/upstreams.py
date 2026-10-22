"""The upstream manager: the gateway's MCP *client* side.

Responsibilities
----------------
1. Health + discovery: every HEALTH_INTERVAL seconds, probe each enabled server
   (initialize -> tools/list) and cache its tools and status (connected / unreachable).
   Tool listing reads this cache, so a slow or dead upstream never slows down tools/list.
2. Aggregation: expose tools as "<server-slug>__<tool-name>". Two servers can both have a
   "ping" tool and they become "woocommerce__ping" and "fx-rates__ping". No collisions,
   and the prefix also tells the model and the human which server will run the tool.
3. Routing: "fx-rates__convert_currency" -> server "fx-rates", tool "convert_currency".
4. Isolation: every upstream call runs in its own connection with a hard timeout. A failure
   becomes a clear error *result* for that one call; other servers are unaffected.
5. Credentials: decrypted only in memory, only to build the outgoing request headers.

Connection model: a fresh MCP session per call (initialize + call). It costs one extra
round trip but is stateless: no shared connection to break, nothing to rebalance when we run
several gateway replicas, and a dead upstream cannot leave a half-open session behind.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import anyio
import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from . import metrics
from .config import Settings
from .db import sessionmaker
from .models import AUTH_BEARER, AUTH_HEADER, AuditLog, UpstreamServer, User
from .security import CredentialCipher
from .ssrf import UnsafeUrlError, validate_upstream_url

log = logging.getLogger("gateway.upstreams")

SEPARATOR = "__"
_VALID_TOOL_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
MAX_TOOL_NAME = 64  # common client limit (Claude, OpenAI, Gemini)


@dataclass
class Principal:
    """Who is calling: resolved from an API key or an internal chat token."""
    user_id: int
    email: str
    role: str
    source: str = "mcp"          # "mcp" for external clients, "chat" for the dashboard agent
    api_key_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass
class UpstreamState:
    status: str = "unknown"                      # connected | unreachable | disabled | unknown
    tools: list[types.Tool] = field(default_factory=list)
    error: str | None = None
    checked_at: datetime | None = None
    latency_ms: int | None = None
    server_name: str | None = None               # name the upstream reports in initialize
    server_version: str | None = None
    exposed_to_original: dict[str, str] = field(default_factory=dict)
    config_version: str = ""


def exposed_name(slug: str, tool: str) -> str:
    name = f"{slug}{SEPARATOR}{_VALID_TOOL_CHARS.sub('_', tool)}"
    if len(name) > MAX_TOOL_NAME:  # keep it unique but short enough for every client
        import hashlib
        name = name[: MAX_TOOL_NAME - 9] + "_" + hashlib.sha1(tool.encode()).hexdigest()[:8]
    return name


def describe_error(exc: BaseException, server: str, timeout: float | None = None) -> str:
    """Turn low-level exceptions (often wrapped in anyio ExceptionGroups) into one clear sentence."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    if isinstance(exc, TimeoutError):
        return f"Upstream '{server}' did not respond within {timeout or '?'}s (timeout)"
    if isinstance(exc, httpx.ConnectTimeout):
        return f"Upstream '{server}' is unreachable (connect timeout)"
    if isinstance(exc, httpx.ConnectError):
        return f"Upstream '{server}' is unreachable (connection refused or DNS failure)"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return f"Upstream '{server}' rejected the gateway's credential (HTTP {code})"
        return f"Upstream '{server}' returned HTTP {code}"
    if isinstance(exc, httpx.TimeoutException):
        return f"Upstream '{server}' timed out"
    if isinstance(exc, UnsafeUrlError):
        return f"Upstream '{server}' URL blocked by SSRF policy: {exc}"
    if isinstance(exc, (httpx.TransportError, anyio.ClosedResourceError, anyio.BrokenResourceError)):
        return f"Upstream '{server}' connection failed ({exc.__class__.__name__})"
    if isinstance(exc, ExceptionGroup):
        return f"Upstream '{server}' failed"
    text = str(exc) or exc.__class__.__name__
    return f"Upstream '{server}' error: {text[:300]}"


class RateLimiter:
    """Sliding-window limit per user (per gateway replica)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        window = self._hits[user_id]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        return True


class UpstreamManager:
    def __init__(self, settings: Settings, cipher: CredentialCipher) -> None:
        self.settings = settings
        self.cipher = cipher
        self.states: dict[int, UpstreamState] = {}
        self.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
        self._stop = anyio.Event()

    # ------------------------------------------------------------------ connections
    def _headers_for(self, server: UpstreamServer) -> dict[str, str]:
        secret = self.cipher.decrypt(server.credential_encrypted)
        if not secret:
            return {}
        if server.auth_type == AUTH_BEARER:
            return {"Authorization": f"Bearer {secret}"}
        if server.auth_type == AUTH_HEADER and server.auth_header_name:
            return {server.auth_header_name: secret}
        return {}

    @asynccontextmanager
    async def open_session(self, server: UpstreamServer, read_timeout: float) -> AsyncIterator[ClientSession]:
        await validate_upstream_url(server.url, self.settings.allowed_host_patterns,
                                    self.settings.block_private_upstreams)
        timeout = httpx.Timeout(read_timeout, connect=self.settings.connect_timeout_seconds)
        async with httpx.AsyncClient(headers=self._headers_for(server), timeout=timeout,
                                     follow_redirects=False) as http:
            async with streamable_http_client(server.url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    yield session

    # ------------------------------------------------------------------ discovery
    async def probe(self, server: UpstreamServer) -> UpstreamState:
        state = UpstreamState(config_version=self._config_version(server))
        started = time.perf_counter()
        try:
            with anyio.fail_after(self.settings.list_timeout_seconds):
                async with self.open_session(server, self.settings.list_timeout_seconds) as session:
                    init = await session.initialize()
                    tools: list[types.Tool] = []
                    cursor = None
                    while True:  # follow pagination
                        page = await session.list_tools(cursor=cursor) if cursor else await session.list_tools()
                        tools.extend(page.tools)
                        cursor = page.nextCursor
                        if not cursor:
                            break
            state.status = "connected"
            state.tools = tools
            state.server_name = init.serverInfo.name
            state.server_version = init.serverInfo.version
            state.exposed_to_original = {exposed_name(server.slug, t.name): t.name for t in tools}
        except BaseException as exc:  # noqa: BLE001 - every failure means "unreachable"
            if isinstance(exc, anyio.get_cancelled_exc_class()) and not isinstance(exc, TimeoutError):
                raise
            state.status = "unreachable"
            state.error = describe_error(exc, server.slug, self.settings.list_timeout_seconds)
            log.warning("probe failed: %s", state.error)
        state.latency_ms = int((time.perf_counter() - started) * 1000)
        state.checked_at = datetime.now(timezone.utc)
        metrics.UPSTREAM_UP.labels(server.slug).set(1 if state.status == "connected" else 0)
        metrics.UPSTREAM_TOOLS.labels(server.slug).set(len(state.tools))
        return state

    @staticmethod
    def _config_version(server: UpstreamServer) -> str:
        return f"{server.url}|{server.auth_type}|{server.auth_header_name}|{server.credential_encrypted}|{server.enabled}"

    async def refresh_server(self, server: UpstreamServer) -> UpstreamState:
        if not server.enabled:
            state = UpstreamState(status="disabled", config_version=self._config_version(server),
                                  checked_at=datetime.now(timezone.utc))
            metrics.UPSTREAM_UP.labels(server.slug).set(0)
        else:
            state = await self.probe(server)
        self.states[server.id] = state
        return state

    async def refresh_all(self) -> None:
        async with sessionmaker()() as db:
            servers = (await db.scalars(select(UpstreamServer))).all()
        known = {s.id for s in servers}
        for stale in set(self.states) - known:   # server deleted (maybe by another replica)
            self.states.pop(stale, None)
        async with anyio.create_task_group() as tg:  # probe all servers in parallel
            for server in servers:
                tg.start_soon(self.refresh_server, server)

    async def health_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.refresh_all()
            except Exception:  # noqa: BLE001 - keep the loop alive no matter what
                log.exception("health loop iteration failed")
            with anyio.move_on_after(self.settings.health_interval_seconds):
                await self._stop.wait()

    def stop(self) -> None:
        self._stop.set()

    def state_for(self, server: UpstreamServer) -> UpstreamState:
        state = self.states.get(server.id)
        if state is None:
            return UpstreamState(status="disabled" if not server.enabled else "unknown")
        if not server.enabled:
            return UpstreamState(status="disabled", checked_at=state.checked_at)
        return state

    # ------------------------------------------------------------------ aggregation
    async def visible_tools(self, principal: Principal) -> list[tuple[UpstreamServer, types.Tool, str]]:
        """(server, upstream tool, exposed name) for every tool this principal may use right now."""
        async with sessionmaker()() as db:
            servers = (await db.scalars(select(UpstreamServer).where(UpstreamServer.enabled.is_(True))
                                        .order_by(UpstreamServer.id))).all()
        result = []
        for server in servers:
            state = self.state_for(server)
            if state.status != "connected":
                continue  # dead upstream: its tools disappear instead of failing the whole list
            restricted = set(server.admin_only_tools)
            for tool in state.tools:
                if tool.name in restricted and not principal.is_admin:
                    continue  # tool permissions: hidden from members
                result.append((server, tool, exposed_name(server.slug, tool.name)))
        return result

    async def list_tools(self, principal: Principal) -> list[types.Tool]:
        tools = []
        for server, tool, name in await self.visible_tools(principal):
            tools.append(types.Tool(
                name=name,
                title=f"{server.display_name}: {tool.title or tool.name}",
                description=f"[{server.display_name}] {tool.description or ''}".strip(),
                inputSchema=tool.inputSchema,
                outputSchema=tool.outputSchema,
                annotations=tool.annotations,
            ))
        return tools

    # ------------------------------------------------------------------ routing
    async def call_tool(self, principal: Principal, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        started = time.perf_counter()
        slug, _, raw_tool = name.partition(SEPARATOR)
        server: UpstreamServer | None = None
        tool_name = raw_tool
        error: str | None = None
        result: types.CallToolResult | None = None

        async with sessionmaker()() as db:
            server = await db.scalar(select(UpstreamServer).where(UpstreamServer.slug == slug))
            # Re-check the user on every call: a deactivated user / demoted admin loses access immediately.
            user = await db.get(User, principal.user_id)

        if user is None or not user.is_active:
            error = "Your account is disabled"
        elif not raw_tool or server is None or not server.enabled:
            error = f"Unknown tool '{name}' (server missing or disabled)"
        else:
            state = self.state_for(server)
            tool_name = state.exposed_to_original.get(name, raw_tool)
            principal.role = user.role
            if tool_name in server.admin_only_tools and not principal.is_admin:
                error = f"Tool '{name}' is restricted to admins"
            elif not self.rate_limiter.allow(principal.user_id):
                error = f"Rate limit exceeded ({self.settings.rate_limit_per_minute} calls/minute)"

        if error is None and server is not None:
            timeout = self.settings.call_timeout_seconds
            try:
                with anyio.fail_after(timeout):
                    async with self.open_session(server, timeout) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments)
                if result.isError:
                    error = _result_text(result)[:500] or "Tool reported an error"
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, anyio.get_cancelled_exc_class()) and not isinstance(exc, TimeoutError):
                    raise
                error = describe_error(exc, server.slug, timeout)
                log.warning("call %s failed: %s", name, error)

        duration_ms = int((time.perf_counter() - started) * 1000)
        await self._audit(principal, slug if server else (slug or "?"), tool_name or name, arguments,
                          duration_ms, error)
        outcome = "success" if error is None else "error"
        metrics.TOOL_CALLS.labels(slug or "?", tool_name or name, outcome, principal.source).inc()
        metrics.TOOL_LATENCY.labels(slug or "?").observe(duration_ms / 1000)

        if result is not None:
            return result  # pass the upstream's result through untouched (content + structuredContent)
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"Gateway error: {error}")],
                                    isError=True)

    async def _audit(self, principal: Principal, server: str, tool: str, arguments: dict[str, Any],
                     duration_ms: int, error: str | None) -> None:
        import json
        try:
            args_text = json.dumps(arguments, default=str)[:2000]
        except (TypeError, ValueError):
            args_text = ""
        try:
            async with sessionmaker()() as db:
                db.add(AuditLog(user_id=principal.user_id, user_email=principal.email, source=principal.source,
                                api_key_id=principal.api_key_id, server_slug=server[:32], tool_name=tool[:128],
                                duration_ms=duration_ms, success=error is None, error=(error or "")[:1000],
                                arguments=args_text))
                await db.commit()
        except Exception:  # noqa: BLE001 - never fail a tool call because the audit write failed
            log.exception("failed to write audit log")


def _result_text(result: types.CallToolResult) -> str:
    return " ".join(c.text for c in result.content if isinstance(c, types.TextContent))
