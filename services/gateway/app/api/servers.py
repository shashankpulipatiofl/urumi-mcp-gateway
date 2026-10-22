"""Upstream MCP server registry: list (everyone), add/edit/disable/remove (admins)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import get_session
from ..models import AUTH_NONE, UpstreamServer, User
from ..ssrf import UnsafeUrlError, validate_upstream_url
from ..upstreams import UpstreamManager, exposed_name
from .deps import current_user, require_admin
from .schemas import ServerIn, ServerOut, ServerPatch, ToolOut

router = APIRouter(prefix="/api/servers", tags=["servers"])


def manager_of(request: Request) -> UpstreamManager:
    return request.app.state.manager


def to_out(server: UpstreamServer, manager: UpstreamManager, viewer: User) -> ServerOut:
    state = manager.state_for(server)
    restricted = set(server.admin_only_tools)
    tools = [ToolOut(name=exposed_name(server.slug, t.name), original_name=t.name, description=t.description or "",
                     admin_only=t.name in restricted)
             for t in state.tools if viewer.is_admin or t.name not in restricted]
    info = f"{state.server_name} {state.server_version or ''}".strip() if state.server_name else None
    return ServerOut(
        id=server.id, slug=server.slug, display_name=server.display_name, description=server.description,
        url=server.url if viewer.is_admin else None, enabled=server.enabled, auth_type=server.auth_type,
        auth_header_name=server.auth_header_name if viewer.is_admin else "",
        has_credential=bool(server.credential_encrypted), admin_only_tools=server.admin_only_tools,
        status=state.status, error=state.error if viewer.is_admin else (state.error and "Upstream unavailable"),
        checked_at=state.checked_at, latency_ms=state.latency_ms, server_info=info,
        tools=tools if server.enabled else [],
    )


async def _check_url(url: str, settings: Settings) -> None:
    try:
        await validate_upstream_url(url, settings.allowed_host_patterns, settings.block_private_upstreams)
    except UnsafeUrlError as exc:
        raise HTTPException(422, str(exc)) from exc


def _check_auth(auth_type: str, header: str) -> None:
    if auth_type == "header" and not header.strip():
        raise HTTPException(422, "auth_header_name is required when auth_type is 'header'")
    if header and header.strip().lower() in ("host", "content-length", "content-type", "mcp-session-id"):
        raise HTTPException(422, f"Header '{header}' cannot be used for credentials")


@router.get("", response_model=list[ServerOut])
async def list_servers(request: Request, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_session)) -> list[ServerOut]:
    servers = (await db.scalars(select(UpstreamServer).order_by(UpstreamServer.id))).all()
    return [to_out(s, manager_of(request), user) for s in servers]


@router.post("", response_model=ServerOut, status_code=201)
async def create_server(body: ServerIn, request: Request, admin: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_session),
                        settings: Settings = Depends(get_settings)) -> ServerOut:
    if await db.scalar(select(UpstreamServer).where(UpstreamServer.slug == body.slug)):
        raise HTTPException(409, f"A server with slug '{body.slug}' already exists")
    await _check_url(body.url, settings)
    _check_auth(body.auth_type, body.auth_header_name)
    manager = manager_of(request)
    server = UpstreamServer(slug=body.slug, display_name=body.display_name, url=body.url,
                            description=body.description, enabled=body.enabled, auth_type=body.auth_type,
                            auth_header_name=body.auth_header_name.strip(),
                            credential_encrypted=manager.cipher.encrypt(body.credential or "")
                            if body.auth_type != AUTH_NONE else "")
    server.admin_only_tools = body.admin_only_tools
    db.add(server)
    await db.commit()
    await db.refresh(server)
    await manager.refresh_server(server)   # probe right away so the UI shows status + tools
    return to_out(server, manager, admin)


@router.patch("/{server_id}", response_model=ServerOut)
async def update_server(server_id: int, body: ServerPatch, request: Request, admin: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_session),
                        settings: Settings = Depends(get_settings)) -> ServerOut:
    server = await db.get(UpstreamServer, server_id)
    if server is None:
        raise HTTPException(404, "Server not found")
    manager = manager_of(request)
    if body.url is not None and body.url != server.url:
        await _check_url(body.url, settings)
        server.url = body.url
    for field in ("display_name", "description", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(server, field, value)
    if body.auth_type is not None:
        server.auth_type = body.auth_type
    if body.auth_header_name is not None:
        server.auth_header_name = body.auth_header_name.strip()
    _check_auth(server.auth_type, server.auth_header_name)
    if body.credential is not None:   # None = keep existing secret
        server.credential_encrypted = manager.cipher.encrypt(body.credential)
    if server.auth_type == AUTH_NONE:
        server.credential_encrypted = ""
    if body.admin_only_tools is not None:
        server.admin_only_tools = body.admin_only_tools
    await db.commit()
    await db.refresh(server)
    await manager.refresh_server(server)
    return to_out(server, manager, admin)


@router.post("/{server_id}/refresh", response_model=ServerOut)
async def refresh_server(server_id: int, request: Request, user: User = Depends(require_admin),
                         db: AsyncSession = Depends(get_session)) -> ServerOut:
    server = await db.get(UpstreamServer, server_id)
    if server is None:
        raise HTTPException(404, "Server not found")
    await manager_of(request).refresh_server(server)
    return to_out(server, manager_of(request), user)


@router.delete("/{server_id}")
async def delete_server(server_id: int, request: Request, _: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_session)) -> dict:
    server = await db.get(UpstreamServer, server_id)
    if server is None:
        raise HTTPException(404, "Server not found")
    await db.delete(server)
    await db.commit()
    manager_of(request).states.pop(server_id, None)
    return {"deleted": True}
