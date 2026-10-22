"""First-start bootstrap: create the first admin and register the default upstream servers.

SEED_SERVERS_JSON contains no secrets, so it can live in a ConfigMap. Each entry names the
*environment variable* holding its credential ("credential_env"), and that variable is filled
from a Kubernetes Secret. Seeding never overwrites a server an admin has already edited.
"""

from __future__ import annotations

import json
import logging
import os

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import Settings
from .db import sessionmaker
from .models import ROLE_ADMIN, UpstreamServer, User
from .security import CredentialCipher, hash_password

log = logging.getLogger("gateway.seed")


async def seed_admin(settings: Settings) -> None:
    async with sessionmaker()() as db:
        if await db.scalar(select(User).where(User.role == ROLE_ADMIN)):
            return
        if not settings.admin_password:
            log.warning("No admin exists and ADMIN_PASSWORD is empty: skipping admin bootstrap")
            return
        db.add(User(email=settings.admin_email.lower(), name=settings.admin_name, role=ROLE_ADMIN,
                    password_hash=hash_password(settings.admin_password)))
        try:
            await db.commit()
            log.info("created bootstrap admin %s", settings.admin_email)
        except IntegrityError:   # another replica won the race
            await db.rollback()


async def seed_servers(settings: Settings, cipher: CredentialCipher) -> None:
    if not settings.seed_servers_json.strip():
        return
    try:
        entries = json.loads(settings.seed_servers_json)
    except ValueError:
        log.error("SEED_SERVERS_JSON is not valid JSON; skipping")
        return
    async with sessionmaker()() as db:
        for entry in entries:
            if await db.scalar(select(UpstreamServer).where(UpstreamServer.slug == entry["slug"])):
                continue
            credential = os.environ.get(entry.get("credential_env", ""), "") if entry.get("credential_env") else ""
            server = UpstreamServer(
                slug=entry["slug"], display_name=entry.get("display_name", entry["slug"]), url=entry["url"],
                description=entry.get("description", ""), enabled=entry.get("enabled", True),
                auth_type=entry.get("auth_type", "none"), auth_header_name=entry.get("auth_header_name", ""),
                credential_encrypted=cipher.encrypt(credential),
            )
            server.admin_only_tools = entry.get("admin_only_tools", [])
            db.add(server)
            try:
                await db.commit()
                log.info("registered seed server %s -> %s", server.slug, server.url)
            except IntegrityError:
                await db.rollback()
