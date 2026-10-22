"""Request/response shapes for the dashboard API (Pydantic models).

Note what is *not* here: no response model contains an upstream credential or an API key
hash. Plaintext API keys appear exactly once, in the response that creates them."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    created_at: datetime
    active_keys: int = 0


class AcceptInviteIn(BaseModel):
    token: str
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    role: Literal["admin", "member"] = "member"

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v:
            raise ValueError("invalid email")
        return v


class InviteOut(BaseModel):
    id: int
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    invite_url: str | None = None   # only present right after creation


class UserPatch(BaseModel):
    role: Literal["admin", "member"] | None = None
    is_active: bool | None = None


class ApiKeyIn(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=100)


class ApiKeyOut(BaseModel):
    id: int
    user_id: int
    user_email: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str   # plaintext, shown once


class ServerIn(BaseModel):
    slug: str = Field(description="Short id used as the tool prefix, e.g. 'woocommerce'")
    display_name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=500)
    description: str = ""
    enabled: bool = True
    auth_type: Literal["none", "bearer", "header"] = "none"
    auth_header_name: str = ""
    credential: str | None = None      # write-only
    admin_only_tools: list[str] = []

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v) or "__" in v:
            raise ValueError("slug must be 3-32 chars: lowercase letters, digits and single dashes")
        return v


class ServerPatch(BaseModel):
    display_name: str | None = None
    url: str | None = None
    description: str | None = None
    enabled: bool | None = None
    auth_type: Literal["none", "bearer", "header"] | None = None
    auth_header_name: str | None = None
    credential: str | None = None      # None = keep, "" = clear, value = replace
    admin_only_tools: list[str] | None = None


class ToolOut(BaseModel):
    name: str            # exposed name (<slug>__<tool>)
    original_name: str
    description: str
    admin_only: bool


class ServerOut(BaseModel):
    id: int
    slug: str
    display_name: str
    description: str
    url: str | None      # hidden from members
    enabled: bool
    auth_type: str
    auth_header_name: str
    has_credential: bool
    admin_only_tools: list[str]
    status: str
    error: str | None
    checked_at: datetime | None
    latency_ms: int | None
    server_info: str | None
    tools: list[ToolOut]


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    user_id: int | None
    user_email: str
    source: str
    server: str
    tool: str
    duration_ms: int
    success: bool
    error: str
    arguments: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)



