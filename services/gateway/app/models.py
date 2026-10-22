"""Database tables.

users          people who can log in to the dashboard (role: admin | member)
invites        one-time invitation links created by admins
api_keys       per-user gateway keys; only a SHA-256 hash is stored
upstream_servers  registered MCP servers; credentials are encrypted at rest
audit_logs     one row per tool call (who, which server/tool, when, how long, outcome)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_MEMBER)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="user", foreign_keys="ApiKey.user_id")

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(20), default=ROLE_MEMBER)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))           # first chars, shown in the UI ("mgw_ab12...")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="api_keys", foreign_keys=[user_id])


AUTH_NONE = "none"
AUTH_BEARER = "bearer"
AUTH_HEADER = "header"


class UpstreamServer(Base):
    __tablename__ = "upstream_servers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True)   # tool prefix: <slug>__<tool>
    display_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_type: Mapped[str] = mapped_column(String(10), default=AUTH_NONE)
    auth_header_name: Mapped[str] = mapped_column(String(100), default="")
    credential_encrypted: Mapped[str] = mapped_column(Text, default="")   # Fernet token, never returned by the API
    admin_only_tools_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def admin_only_tools(self) -> list[str]:
        try:
            return list(json.loads(self.admin_only_tools_json or "[]"))
        except ValueError:
            return []

    @admin_only_tools.setter
    def admin_only_tools(self, value: list[str]) -> None:
        self.admin_only_tools_json = json.dumps(sorted(set(value)))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    user_email: Mapped[str] = mapped_column(String(255), default="")
    source: Mapped[str] = mapped_column(String(10), default="mcp")          # mcp (external client) | chat
    api_key_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    server_slug: Mapped[str] = mapped_column(String(32), default="", index=True)
    tool_name: Mapped[str] = mapped_column(String(128), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    arguments: Mapped[str] = mapped_column(Text, default="")              # truncated JSON
