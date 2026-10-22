#!/usr/bin/env python3
"""End-to-end check of the gateway, following the assignment's definition of done.

Usage:
  python scripts/e2e_check.py --base http://app.localhost --admin-email ... --admin-password ...

Steps: admin login -> invite user B -> B accepts and creates an API key -> both users list
tools over MCP -> tool calls (incl. two upstreams) -> admin-only tool hidden from B ->
revoke B's key -> disable a server -> audit log attribution.
Needs: pip install "mcp==1.30.0" httpx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

HDR = {"X-Requested-With": "dashboard"}
OK, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
failures = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"  [{OK if cond else FAIL}] {label}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        failures += 1


async def mcp_tools(mcp_url: str, key: str) -> list[str]:
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}) as http:
        async with streamable_http_client(mcp_url, http_client=http) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return [t.name for t in (await s.list_tools()).tools]


async def mcp_call(mcp_url: str, key: str, name: str, args: dict):
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}) as http:
        async with streamable_http_client(mcp_url, http_client=http) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await s.call_tool(name, args)


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://app.localhost")
    p.add_argument("--admin-email", required=True)
    p.add_argument("--admin-password", required=True)
    p.add_argument("--member-email", default=None, help="default: a fresh bob.<timestamp>@urumi.dev each run")
    a = p.parse_args()
    if not a.member_email:
        import time
        a.member_email = f"bob.{int(time.time())}@urumi.dev"
    base, mcp_url = a.base.rstrip("/"), a.base.rstrip("/") + "/mcp"

    admin = httpx.AsyncClient(base_url=base, headers=HDR)
    member = httpx.AsyncClient(base_url=base, headers=HDR)

    print("1. Admin (user A) logs in and sees the registered servers")
    r = await admin.post("/api/auth/login", json={"email": a.admin_email, "password": a.admin_password})
    check(r.status_code == 200, "admin login", r.text)
    servers = (await admin.get("/api/servers")).json()
    for s in servers:
        print(f"     - {s['slug']:<12} {s['status']:<12} {len(s['tools'])} tools")
    check(any(s["slug"] == "woocommerce" for s in servers), "WooCommerce server is pre-registered")
    check(sum(1 for s in servers if s["status"] == "connected") >= 3, ">= 3 upstreams connected")
    check(all("credential" not in json.dumps(s).lower().replace("has_credential", "") for s in servers),
          "no credential values in API responses")

    print("2. Admin invites user B; B accepts and generates an API key")
    r = await admin.post("/api/invites", json={"email": a.member_email, "role": "member"})
    if r.status_code == 409:
        print("     (member already exists, logging in is not possible without password: re-run on a fresh DB)")
        return 1
    token = r.json()["invite_url"].rsplit("/", 1)[-1]
    r = await member.post("/api/auth/accept-invite", json={"token": token, "name": "Bob", "password": "bobpass123"})
    check(r.status_code == 200 and r.json()["role"] == "member", "invite accepted as member", r.text)
    key_b = (await member.post("/api/keys", json={"name": "claude-code"})).json()
    check(key_b["key"].startswith("mgw_"), "member key generated (shown once)")
    key_a = (await admin.post("/api/keys", json={"name": "admin-cli"})).json()["key"]

    print("3. Both users connect over MCP with only the gateway URL + their key")
    tools_a = await mcp_tools(mcp_url, key_a)
    tools_b = await mcp_tools(mcp_url, key_b["key"])
    print(f"     A sees {len(tools_a)} tools, B sees {len(tools_b)} tools")
    check({t.split("__")[0] for t in tools_b} >= {"woocommerce", "fx-rates", "team-notes"}, "B sees tools of every server")
    check("woocommerce__ping" in tools_a and "fx-rates__ping" in tools_a, "name collision 'ping' resolved by prefix")
    check("woocommerce__create_product" in tools_a and "woocommerce__create_product" not in tools_b,
          "admin-only tool visible to A, hidden from B")

    print("4. Tool calls")
    res = await mcp_call(mcp_url, key_b["key"], "woocommerce__list_orders", {"period": "today"})
    check(not res.isError, "B: woocommerce__list_orders(today)", res.content[0].text[:200])
    res = await mcp_call(mcp_url, key_b["key"], "fx-rates__convert_currency",
                         {"amount": 1998, "from_currency": "INR", "to_currency": "USD"})
    check(not res.isError, "B: fx-rates__convert_currency (second upstream)", res.content[0].text[:200])
    res = await mcp_call(mcp_url, key_b["key"], "woocommerce__create_product", {"name": "x", "regular_price": 1})
    check(res.isError and "restricted" in res.content[0].text, "B calling admin-only tool directly is rejected")

    print("5. Revoke B's key -> next call rejected")
    await admin.delete(f"/api/keys/{key_b['id']}")
    async with httpx.AsyncClient() as raw:   # plain JSON-RPC request, to see the HTTP status clearly
        r = await raw.post(mcp_url, headers={"Authorization": f"Bearer {key_b['key']}",
                                             "Accept": "application/json, text/event-stream"},
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    check(r.status_code == 401 and "revoked" in r.text, "revoked key rejected (HTTP 401)", f"{r.status_code} {r.text}")

    print("6. Disable fx-rates -> its tools disappear")
    fx = next(s for s in servers if s["slug"] == "fx-rates")
    await admin.patch(f"/api/servers/{fx['id']}", json={"enabled": False})
    tools_a2 = await mcp_tools(mcp_url, key_a)
    check(not any(t.startswith("fx-rates__") for t in tools_a2), "fx-rates tools gone after disable")
    res = await mcp_call(mcp_url, key_a, "fx-rates__ping", {})
    check(res.isError, "calling a disabled server's tool is rejected")
    await admin.patch(f"/api/servers/{fx['id']}", json={"enabled": True})

    print("7. Audit log attribution")
    logs = (await admin.get("/api/audit?limit=50")).json()
    users = {l["user_email"] for l in logs}
    check(a.member_email in users and a.admin_email.lower() in users, "calls attributed to both users")
    for l in logs[:8]:
        print(f"     {l['created_at'][:19]} {l['user_email']:<18} {l['source']:<4} {l['server']}/{l['tool']:<22} "
              f"{l['duration_ms']:>5}ms {'ok' if l['success'] else 'ERR ' + l['error'][:50]}")

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
