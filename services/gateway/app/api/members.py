"""Team management (admins): users, invitations, roles."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import get_session
from ..models import ROLE_ADMIN, ApiKey, Invite, User
from ..security import generate_invite_token
from .auth import user_out
from .deps import require_admin
from .schemas import InviteIn, InviteOut, UserOut, UserPatch

router = APIRouter(prefix="/api", tags=["members"])


@router.get("/users", response_model=list[UserOut])
async def list_users(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)) -> list[UserOut]:
    users = (await db.scalars(select(User).order_by(User.id))).all()
    return [await user_out(db, u) for u in users]


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: int, body: UserPatch, admin: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_session)) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    demoting = (body.role == "member" and user.role == ROLE_ADMIN) or (body.is_active is False and user.is_admin)
    if demoting:
        if user.id == admin.id:
            raise HTTPException(400, "You cannot demote or disable yourself")
        admins = await db.scalar(select(func.count()).select_from(User)
                                 .where(User.role == ROLE_ADMIN, User.is_active.is_(True)))
        if (admins or 0) <= 1:
            raise HTTPException(400, "At least one active admin is required")
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:   # disabling a user also revokes all their keys
            now = datetime.now(timezone.utc)
            for key in (await db.scalars(select(ApiKey).where(ApiKey.user_id == user.id,
                                                              ApiKey.revoked_at.is_(None)))).all():
                key.revoked_at = now
    await db.commit()
    return await user_out(db, user)


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)) -> list[InviteOut]:
    invites = (await db.scalars(select(Invite).where(Invite.revoked_at.is_(None)).order_by(Invite.id.desc()))).all()
    return [InviteOut(id=i.id, email=i.email, role=i.role, created_at=i.created_at, expires_at=i.expires_at,
                      accepted_at=i.accepted_at) for i in invites]


@router.post("/invites", response_model=InviteOut, status_code=201)
async def create_invite(body: InviteIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_session),
                        settings: Settings = Depends(get_settings)) -> InviteOut:
    if await db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "This person is already a member")
    token, token_hash = generate_invite_token()
    invite = Invite(email=body.email, role=body.role, token_hash=token_hash, created_by_id=admin.id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=settings.invite_days))
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    # No email server in this project: the admin copies the one-time link and sends it.
    url = f"{settings.public_base_url.rstrip('/')}/invite/{token}"
    return InviteOut(id=invite.id, email=invite.email, role=invite.role, created_at=invite.created_at,
                     expires_at=invite.expires_at, accepted_at=None, invite_url=url)


@router.delete("/invites/{invite_id}")
async def revoke_invite(invite_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_session)) -> dict:
    invite = await db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(404, "Invite not found")
    invite.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return {"revoked": True}
