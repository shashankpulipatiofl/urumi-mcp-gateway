"""Gateway behaviour tests against real HTTP upstreams (see conftest.py)."""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def _session_call(base, key, fn):
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30) as http:
        async with streamable_http_client(f"{base}/mcp", http_client=http) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await fn(s)


def list_tools(base, key):
    return asyncio.run(_session_call(base, key, lambda s: s.list_tools())).tools


def call(base, key, name, args=None):
    return asyncio.run(_session_call(base, key, lambda s: s.call_tool(name, args or {})))


def new_member(stack, admin, email):
    token = admin.post("/api/invites", json={"email": email, "role": "member"}).json()["invite_url"].rsplit("/", 1)[1]
    c = httpx.Client(base_url=stack["base"], headers={"X-Requested-With": "dashboard"}, timeout=30)
    assert c.post("/api/auth/accept-invite", json={"token": token, "name": email, "password": "password123"}).status_code == 200
    return c


def test_servers_registered_and_status(admin):
    servers = {s["slug"]: s for s in admin.get("/api/servers").json()}
    assert set(servers) == {"alpha", "beta", "slow"}
    assert servers["alpha"]["status"] == "connected"
    assert servers["beta"]["has_credential"] is True
    body = admin.get("/api/servers").text
    assert "alpha-token" not in body and "beta-key" not in body, "credentials must never reach the browser"


def test_aggregation_and_collision_scheme(stack, admin):
    key = admin.post("/api/keys", json={"name": "t"}).json()["key"]
    names = {t.name for t in list_tools(stack["base"], key)}
    assert {"alpha__ping", "beta__ping", "slow__ping", "alpha__danger"} <= names
    res = call(stack["base"], key, "beta__echo", {"text": "hello"})
    assert not res.isError and '"server": "beta"' in res.content[0].text


def test_api_key_stored_hashed(stack, admin):
    import sqlite3, os
    created = admin.post("/api/keys", json={"name": "hash-check"}).json()
    db = os.environ["DATABASE_URL"].split("///", 1)[1]
    rows = sqlite3.connect(db).execute("SELECT key_hash, prefix FROM api_keys").fetchall()
    assert all(created["key"] not in (h, p) for h, p in rows)
    assert any(created["key"].startswith(p) for _, p in rows)


def test_member_permissions_and_revocation(stack, admin):
    bob = new_member(stack, admin, "bob@test.dev")
    key = bob.post("/api/keys", json={"name": "cursor"}).json()
    names = {t.name for t in list_tools(stack["base"], key["key"])}
    assert "alpha__danger" not in names and "alpha__ping" in names            # hidden
    res = call(stack["base"], key["key"], "alpha__danger")
    assert res.isError and "restricted to admins" in res.content[0].text      # rejected if called directly
    assert bob.get("/api/users").status_code == 403                            # members cannot manage team
    assert bob.post("/api/servers", json={"slug": "x-x", "display_name": "x", "url": "http://1.1.1.1/mcp"}).status_code == 403
    admin.delete(f"/api/keys/{key['id']}")
    r = httpx.post(f"{stack['base']}/mcp", headers={"Authorization": f"Bearer {key['key']}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401 and "revoked" in r.text


def test_disable_server_hides_tools(stack, admin):
    key = admin.post("/api/keys", json={"name": "t"}).json()["key"]
    beta = next(s for s in admin.get("/api/servers").json() if s["slug"] == "beta")
    admin.patch(f"/api/servers/{beta['id']}", json={"enabled": False})
    try:
        names = {t.name for t in list_tools(stack["base"], key)}
        assert not any(n.startswith("beta__") for n in names)
        assert call(stack["base"], key, "beta__ping").isError
    finally:
        admin.patch(f"/api/servers/{beta['id']}", json={"enabled": True})


def test_slow_upstream_times_out_without_affecting_others(stack, admin):
    key = admin.post("/api/keys", json={"name": "t"}).json()["key"]
    started = time.time()
    res = call(stack["base"], key, "slow__ping")
    assert res.isError and "timeout" in res.content[0].text and time.time() - started < 4.5
    assert not call(stack["base"], key, "alpha__ping").isError


def test_dead_upstream_marked_unreachable(stack, admin):
    key = admin.post("/api/keys", json={"name": "t"}).json()["key"]
    r = admin.post("/api/servers", json={"slug": "ghost", "display_name": "Ghost",
                                         "url": "http://127.0.0.1:1/mcp"})
    assert r.status_code == 201 and r.json()["status"] == "unreachable"
    assert "unreachable" in r.json()["error"]
    names = {t.name for t in list_tools(stack["base"], key)}
    assert not any(n.startswith("ghost__") for n in names) and "alpha__ping" in names
    res = call(stack["base"], key, "ghost__ping")
    assert res.isError and "unreachable" in res.content[0].text
    admin.delete(f"/api/servers/{r.json()['id']}")


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data", "http://10.0.0.5/mcp",
                                 "http://localhost:8000/mcp", "file:///etc/passwd",
                                 "http://kubernetes.default.svc/api"])
def test_ssrf_protection(admin, url):
    r = admin.post("/api/servers", json={"slug": "evil", "display_name": "Evil", "url": url})
    assert r.status_code == 422, r.text


def test_chat_agent_uses_gateway_as_user(stack, admin):
    r = admin.post("/api/chat", json={"messages": [{"role": "user", "content": "echo please"}]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert {c["name"] for c in data["tool_calls"]} >= {"alpha__echo", "beta__echo"}
    logs = admin.get("/api/audit?source=chat").json()
    assert logs and all(l["user_email"] == "alice@test.dev" for l in logs)


def test_audit_log_records_every_call(stack, admin):
    logs = admin.get("/api/audit?limit=200").json()
    assert any(not l["success"] and "timeout" in l["error"] for l in logs)
    assert any(l["user_email"] == "bob@test.dev" for l in logs)
    assert all({"user_email", "server", "tool", "duration_ms", "success", "created_at"} <= set(l) for l in logs)


def test_csrf_header_required(admin):
    r = admin.post("/api/keys", json={"name": "x"}, headers={"X-Requested-With": "evil"})
    assert r.status_code == 403
