"""Gateway entry point: FastAPI app = dashboard REST API + MCP endpoint + health/metrics.

Routes
  /api/*      dashboard API (cookie session)            -> exposed via Ingress
  /mcp        MCP Streamable HTTP endpoint (API key)     -> exposed via Ingress
  /healthz    liveness probe                             -> cluster only
  /readyz     readiness probe (checks the database)      -> cluster only
  /metrics    Prometheus metrics                         -> cluster only
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.routing import Route

from . import db
from .api import audit, auth, chat, keys, members, servers
from .config import get_settings
from .mcp_endpoint import MCPAuthApp, build_mcp_server, build_session_manager
from .security import CredentialCipher
from .seed import seed_admin, seed_servers
from .upstreams import UpstreamManager

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gateway")

if not settings.secret_key:
    log.warning("SECRET_KEY is empty: generating a random one (sessions reset on restart; dev only)")
    settings.secret_key = secrets.token_urlsafe(32)
if not settings.encryption_keys:
    log.warning("ENCRYPTION_KEYS is empty: generating a random one (stored credentials unreadable after restart; dev only)")
    settings.encryption_keys = secrets.token_urlsafe(32)

cipher = CredentialCipher(settings.encryption_keys)
manager = UpstreamManager(settings, cipher)
mcp_server = build_mcp_server(manager)
session_manager = build_session_manager(mcp_server)


async def _wait_for_database() -> None:
    for attempt in range(1, 61):
        try:
            async with db.sessionmaker()() as s:
                await s.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001
            log.info("waiting for database (%s/60): %s", attempt, exc.__class__.__name__)
            await asyncio.sleep(2)
    raise RuntimeError("database not reachable")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_engine(settings.database_url)
    await _wait_for_database()
    await db.create_schema()
    await seed_admin(settings)
    await seed_servers(settings, cipher)
    app.state.manager = manager
    async with session_manager.run():
        health_task = asyncio.create_task(manager.health_loop(), name="upstream-health")
        try:
            yield
        finally:
            manager.stop()
            health_task.cancel()
            await db.dispose()


app = FastAPI(title="Urumi MCP Gateway", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

for module in (auth, servers, members, keys, audit, chat):
    app.include_router(module.router)

# Exact-path route (no trailing-slash redirect): MCP clients POST to ".../mcp".
app.router.routes.append(Route("/mcp", endpoint=MCPAuthApp(settings, session_manager)))


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
async def readyz():
    try:
        async with db.sessionmaker()() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "not-ready", "reason": exc.__class__.__name__}, status_code=503)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level=settings.log_level.lower(),
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    run()
