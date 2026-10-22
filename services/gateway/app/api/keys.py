"""Gateway API keys.

* Any user: create / list / revoke their own keys.
* Admin: list everyone's keys, issue a key for a member, revoke any key.
The plaintext key is returned once; the database only keeps its SHA-256 hash.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import ApiKey, User
from ..security import generate_api_key
from .deps import current_user, require_admin
from .schemas import ApiKeyCreated, ApiKeyIn, ApiKeyOut

router = APIRouter(prefix="/api", tags=["keys"])


def _out(key: ApiKey, email: str) -> ApiKeyOut:
    return ApiKeyOut(id=key.id, user_id=key.user_id, user_email=email, name=key.name, prefix=key.prefix,
                     created_at=key.created_at, last_used_at=key.last_used_at, revoked_at=key.revoked_at)


async def _issue(db: AsyncSession, owner: User, creator: User, name: str) -> ApiKeyCreated:
    plaintext, prefix, key_hash = generate_api_key()
    key = ApiKey(user_id=owner.id, name=name, prefix=prefix, key_hash=key_hash, created_by_id=creator.id)
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return ApiKeyCreated(**_out(key, owner.email).model_dump(), key=plaintext)


@router.get("/keys", response_model=list[ApiKeyOut])
async def list_keys(all: bool = False, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_session)) -> list[ApiKeyOut]:
    query = select(ApiKey, User.email).join(User, User.id == ApiKey.user_id).order_by(ApiKey.id.desc())
    if not (all and user.is_admin):
        query = query.where(ApiKey.user_id == user.id)
    rows = (await db.execute(query)).all()
    return [_out(k, email) for k, email in rows]


@router.post("/keys", response_model=ApiKeyCreated, status_code=201)
async def create_my_key(body: ApiKeyIn, user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_session)) -> ApiKeyCreated:
    return await _issue(db, user, user, body.name)


@router.post("/users/{user_id}/keys", response_model=ApiKeyCreated, status_code=201)
async def issue_key_for_user(user_id: int, body: ApiKeyIn, admin: User = Depends(require_admin),
                             db: AsyncSession = Depends(get_session)) -> ApiKeyCreated:
    owner = await db.get(User, user_id)
    if owner is None or not owner.is_active:
        raise HTTPException(404, "User not found or disabled")
    return await _issue(db, owner, admin, body.name)


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)) -> dict:
    key = await db.get(ApiKey, key_id)
    if key is None or (key.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        await db.commit()
    return {"revoked": True}
