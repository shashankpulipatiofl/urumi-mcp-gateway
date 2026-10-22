"""Test harness: real HTTP servers on random ports.

* three tiny upstream MCP servers ("alpha" with a bearer token, "beta" with a header key,
  both exposing a colliding `ping` tool; "slow" sleeps longer than the gateway timeout)
* a fake LLM (tests/fake_llm.py)
* the gateway itself, configured through environment variables like in Kubernetes
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerThread(threading.Thread):
    def __init__(self, app, port: int) -> None:
        super().__init__(daemon=True)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def run(self) -> None:
        asyncio.run(self.server.serve())

    def stop(self) -> None:
        self.server.should_exit = True


def wait_http(url: str, timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=1)
            return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError(f"{url} did not come up")


def make_upstream(name: str, header: str = "", value: str = "", delay: float = 0):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    mcp = FastMCP(name, stateless_http=True, json_response=True,
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @mcp.tool(description=f"ping {name}")
    async def ping() -> dict:
        if delay:
            await asyncio.sleep(delay)
        return {"server": name}

    @mcp.tool(description=f"echo from {name}")
    async def echo(text: str) -> dict:
        return {"server": name, "text": text}

    @mcp.tool(description="secret admin operation")
    async def danger() -> dict:
        return {"done": True}

    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/healthz", lambda r: JSONResponse({"ok": True})))
    if not header:
        return app

    async def guarded(scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            if dict(scope["headers"]).get(header.lower().encode(), b"") != value.encode():
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await app(scope, receive, send)

    return guarded


@pytest.fixture(scope="session")
def stack():
    ports = {n: free_port() for n in ("alpha", "beta", "slow", "llm", "gw")}
    threads = {
        "alpha": ServerThread(make_upstream("alpha", "Authorization", "Bearer alpha-token"), ports["alpha"]),
        "beta": ServerThread(make_upstream("beta", "X-API-Key", "beta-key"), ports["beta"]),
        "slow": ServerThread(make_upstream("slow", delay=5), ports["slow"]),
    }
    import fake_llm
    threads["llm"] = ServerThread(fake_llm.app, ports["llm"])
    for t in threads.values():
        t.start()
    for n in ("alpha", "beta", "slow"):
        wait_http(f"http://127.0.0.1:{ports[n]}/healthz")

    seed = [
        {"slug": "alpha", "display_name": "Alpha", "url": f"http://127.0.0.1:{ports['alpha']}/mcp",
         "auth_type": "bearer", "credential_env": "T_ALPHA", "admin_only_tools": ["danger"]},
        {"slug": "beta", "display_name": "Beta", "url": f"http://127.0.0.1:{ports['beta']}/mcp",
         "auth_type": "header", "auth_header_name": "X-API-Key", "credential_env": "T_BETA"},
        {"slug": "slow", "display_name": "Slow", "url": f"http://127.0.0.1:{ports['slow']}/mcp"},
    ]
    db_path = Path(os.environ.get("TMPDIR", "/tmp")) / f"gw-test-{os.getpid()}.db"
    db_path.unlink(missing_ok=True)
    os.environ.update({
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}", "SECRET_KEY": "test-secret-0123456789-abcdefghijklmnop", "ENCRYPTION_KEYS": "k1",
        "ADMIN_EMAIL": "alice@test.dev", "ADMIN_PASSWORD": "alicepass123", "ADMIN_NAME": "Alice",
        "SEED_SERVERS_JSON": json.dumps(seed), "T_ALPHA": "alpha-token", "T_BETA": "beta-key",
        "ALLOWED_UPSTREAM_HOSTS": "127.0.0.1", "HEALTH_INTERVAL_SECONDS": "1", "CALL_TIMEOUT_SECONDS": "2",
        "LIST_TIMEOUT_SECONDS": "2", "LLM_PROVIDER": "gemini", "LLM_API_KEY": "fake",
        "LLM_BASE_URL": f"http://127.0.0.1:{ports['llm']}/v1beta",
        "SELF_MCP_URL": f"http://127.0.0.1:{ports['gw']}/mcp", "RATE_LIMIT_PER_MINUTE": "1000",
    })
    from app.main import app as gateway_app
    threads["gw"] = ServerThread(gateway_app, ports["gw"])
    threads["gw"].start()
    base = f"http://127.0.0.1:{ports['gw']}"
    wait_http(f"{base}/healthz")
    time.sleep(3.5)  # first health probes (the slow server needs list_timeout to fail)
    yield {"base": base, "ports": ports, "threads": threads}
    for t in threads.values():
        t.stop()


@pytest.fixture
def admin(stack):
    c = httpx.Client(base_url=stack["base"], headers={"X-Requested-With": "dashboard"}, timeout=30)
    assert c.post("/api/auth/login", json={"email": "alice@test.dev", "password": "alicepass123"}).status_code == 200
    return c
