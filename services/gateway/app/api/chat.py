"""POST /api/chat: run the agent for the logged-in user."""

from __future__ import annotations

import logging

import httpx

from fastapi import APIRouter, Depends, HTTPException

from ..agent import AgentError, run_agent
from ..config import Settings, get_settings
from ..models import User
from .deps import current_user
from .schemas import ChatIn

router = APIRouter(prefix="/api", tags=["chat"])
log = logging.getLogger("gateway.chat")


@router.post("/chat")
async def chat(body: ChatIn, user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> dict:
    try:
        return await run_agent(settings, user.id, user.name, user.role, [m.model_dump() for m in body.messages])
    except Exception as exc:  # noqa: BLE001
        root = _root_cause(exc)
        if isinstance(root, AgentError):
            raise HTTPException(502, str(root)) from exc
        log.exception("chat failed")
        if isinstance(root, httpx.TimeoutException):
            detail = "The LLM API did not answer in time. Try again."
        elif isinstance(root, httpx.ConnectError):
            detail = f"Cannot reach the LLM API or the gateway's own MCP endpoint ({root.__class__.__name__}). Check the pod's internet access."
        else:
            detail = f"Chat agent failed: {root.__class__.__name__}: {str(root)[:200]}"
        raise HTTPException(502, detail) from exc


def _root_cause(exc: BaseException) -> BaseException:
    """anyio task groups wrap errors in ExceptionGroups; dig out the real one."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc
