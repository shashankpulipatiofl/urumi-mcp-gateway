"""Dashboard login/logout, current user, and accepting an invitation."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import get_session
from ..models import ApiKey, Invite, User
from ..security import create_session_token, hash_password, hash_token, verify_password
from .deps import SESSION_COOKIE, current_user
from .schemas import AcceptInviteIn, LoginIn, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session(response: Response, settings: Settings, user: User) -> None:
    token = create_session_token(settings.secret_key, user.id, settings.session_hours)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", secure=settings.cookie_secure,
                        max_age=settings.session_hours * 3600, path="/")


async def user_out(db: AsyncSession, user: User) -> UserOut:
    active = await db.scalar(select(func.count()).select_from(ApiKey)
                             .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None)))
    return UserOut(id=user.id, email=user.email, name=user.name, role=user.role, is_active=user.is_active,
                   created_at=user.created_at, active_keys=active or 0)


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, response: Response, db: AsyncSession = Depends(get_session),
                settings: Settings = Depends(get_settings)) -> UserOut:
    user = await db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    _set_session(response, settings, user)
    return await user_out(db, user)


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)) -> UserOut:
    return await user_out(db, user)


@router.get("/invite/{token}")
async def invite_info(token: str, db: AsyncSession = Depends(get_session)) -> dict:
    invite = await _valid_invite(db, token)
    return {"email": invite.email, "role": invite.role}


@router.post("/accept-invite", response_model=UserOut)
async def accept_invite(body: AcceptInviteIn, response: Response, db: AsyncSession = Depends(get_session),
                        settings: Settings = Depends(get_settings)) -> UserOut:
    invite = await _valid_invite(db, body.token)
    if await db.scalar(select(User).where(User.email == invite.email)):
        raise HTTPException(409, "A user with this email already exists")
    user = User(email=invite.email, name=body.name.strip(), role=invite.role,
                password_hash=hash_password(body.password))
    db.add(user)
    invite.accepted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    _set_session(response, settings, user)
    return await user_out(db, user)


async def _valid_invite(db: AsyncSession, token: str) -> Invite:
    invite = await db.scalar(select(Invite).where(Invite.token_hash == hash_token(token)))
    now = datetime.now(timezone.utc)
    if invite is None or invite.accepted_at or invite.revoked_at:
        raise HTTPException(404, "Invite not found or already used")
    expires = invite.expires_at if invite.expires_at.tzinfo else invite.expires_at.replace(tzinfo=timezone.utc)
    if expires < now:
        raise HTTPException(410, "Invite expired")
    return invite
