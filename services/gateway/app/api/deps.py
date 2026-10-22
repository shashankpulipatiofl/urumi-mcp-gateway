"""FastAPI dependencies: who is logged in to the dashboard, and are they an admin?"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import get_session
from ..models import User
from ..security import SESSION_AUDIENCE, decode_token

SESSION_COOKIE = "mgw_session"


async def current_user(request: Request, db: AsyncSession = Depends(get_session),
                       settings: Settings = Depends(get_settings)) -> User:
    token = request.cookies.get(SESSION_COOKIE, "")
    user_id = decode_token(settings.secret_key, token, SESSION_AUDIENCE) if token else None
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
    # CSRF defence for cookie auth: state-changing requests must carry a custom header, which a
    # cross-site HTML form cannot send (and SameSite=Strict already blocks the cookie cross-site).
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "dashboard":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing X-Requested-With header")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user
