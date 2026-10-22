"""Audit log + dashboard overview stats. Admins see everything, members see their own calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import get_session
from ..models import AuditLog, UpstreamServer, User
from .deps import current_user
from .schemas import AuditOut

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit", response_model=list[AuditOut])
async def list_audit(
    user_id: int | None = None,
    server: str | None = None,
    outcome: str | None = Query(None, pattern="^(success|error)$"),
    source: str | None = Query(None, pattern="^(mcp|chat)$"),
    before_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    viewer: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[AuditOut]:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if not viewer.is_admin:
        q = q.where(AuditLog.user_id == viewer.id)
    elif user_id is not None:
        q = q.where(AuditLog.user_id == user_id)
    if server:
        q = q.where(AuditLog.server_slug == server)
    if outcome:
        q = q.where(AuditLog.success.is_(outcome == "success"))
    if source:
        q = q.where(AuditLog.source == source)
    if before_id:
        q = q.where(AuditLog.id < before_id)
    rows = (await db.scalars(q)).all()
    return [AuditOut(id=r.id, created_at=r.created_at, user_id=r.user_id, user_email=r.user_email, source=r.source,
                     server=r.server_slug, tool=r.tool_name, duration_ms=r.duration_ms, success=r.success,
                     error=r.error, arguments=r.arguments) for r in rows]


@router.get("/stats")
async def stats(request: Request, viewer: User = Depends(current_user), db: AsyncSession = Depends(get_session)) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    q = (select(AuditLog.server_slug, func.count(), func.sum(case((AuditLog.success.is_(False), 1), else_=0)),
                func.avg(AuditLog.duration_ms))
         .where(AuditLog.created_at >= since).group_by(AuditLog.server_slug))
    if not viewer.is_admin:
        q = q.where(AuditLog.user_id == viewer.id)
    per_server = [{"server": s, "calls": c, "errors": int(e or 0), "avg_ms": int(a or 0)}
                  for s, c, e, a in (await db.execute(q)).all()]
    manager = request.app.state.manager
    servers = (await db.scalars(select(UpstreamServer))).all()
    status_counts: dict[str, int] = {}
    tool_count = 0
    for s in servers:
        st = manager.state_for(s)
        status_counts[st.status] = status_counts.get(st.status, 0) + 1
        if st.status == "connected":
            tool_count += len(st.tools)
    return {"calls_24h": sum(p["calls"] for p in per_server), "errors_24h": sum(p["errors"] for p in per_server),
            "per_server": per_server, "servers": status_counts, "tools": tool_count}


@router.get("/config")
async def public_config(settings: Settings = Depends(get_settings)) -> dict:
    """Non-secret values the dashboard needs (e.g. the MCP URL for copy-paste snippets)."""
    base = settings.public_base_url.rstrip("/")
    return {"mcp_url": f"{base}/mcp", "llm_provider": settings.llm_provider, "llm_model": settings.llm_model,
            "chat_enabled": bool(settings.llm_api_key) or settings.llm_provider == "openai" and bool(settings.llm_base_url)}
