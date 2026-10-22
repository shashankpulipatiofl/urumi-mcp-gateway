"""WooCommerce MCP tools against a mocked WooCommerce REST API (no WordPress needed)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings  # noqa: E402
from app.server import _period_bounds, build_mcp  # noqa: E402
from app.woo_client import WooClient  # noqa: E402

SEEN: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    SEEN.append(request)
    path = request.url.path.replace("/wp-json/wc/v3", "")
    if request.headers.get("x-forwarded-proto") != "https":
        return httpx.Response(401, json={"message": "need https"})
    if path == "/products" and request.method == "GET":
        return httpx.Response(200, json=[{"id": 1, "name": "Tee", "price": "499", "regular_price": "499",
                                          "status": "publish", "permalink": "https://wordpress.svc/product/tee/"}])
    if path == "/products" and request.method == "POST":
        body = json.loads(request.content)
        return httpx.Response(201, json={"id": 7, "name": body["name"], "regular_price": body["regular_price"],
                                         "price": body["regular_price"], "status": body["status"]})
    if path == "/orders":
        return httpx.Response(200, json=[{"id": 5, "number": "5", "status": "processing", "total": "998.00",
                                          "currency": "INR", "billing": {"first_name": "Riya"}, "line_items": []}])
    if path == "/orders/5" and request.method == "GET":
        return httpx.Response(200, json={"id": 5, "status": "processing", "total": "998.00", "line_items": []})
    if path == "/orders/5" and request.method == "PUT":
        return httpx.Response(200, json={"id": 5, "status": json.loads(request.content)["status"]})
    if path == "/orders/5/notes":
        return httpx.Response(200 if request.method == "GET" else 201, json=[] if request.method == "GET" else {})
    return httpx.Response(404, json={"message": "Invalid ID."})


def mcp():
    s = Settings(store_url="http://wordpress.svc", consumer_key="ck", consumer_secret="cs", mcp_auth_token="t",
                 store_timezone="Asia/Kolkata", request_timeout=5, port=0, store_label="Test",
                 store_public_url="http://store.localhost")
    woo = WooClient(s.store_url, "ck", "cs", transport=httpx.MockTransport(handler))
    return build_mcp(s, woo)


def run(name, args=None):
    async def go():
        return await mcp().call_tool(name, args or {})
    return asyncio.run(go())


def text(result) -> str:
    content = result[0] if isinstance(result, tuple) else result
    return content[0].text


def test_required_tools_exist():
    names = {t.name for t in asyncio.run(mcp().list_tools())}
    assert {"list_products", "create_product", "list_orders", "get_order", "update_order_status"} <= names


def test_list_products_rewrites_public_links_and_sends_auth():
    out = json.loads(text(run("list_products")))
    assert out["products"][0]["permalink"] == "http://store.localhost/product/tee/"
    assert SEEN[-1].headers["authorization"].startswith("Basic ")


def test_create_product_formats_price():
    out = json.loads(text(run("create_product", {"name": "Hoodie", "regular_price": 999})))
    assert out["product"]["regular_price"] == "999.00"


def test_list_orders_today_uses_store_timezone():
    out = json.loads(text(run("list_orders", {"period": "today"})))
    assert out["count"] == 1 and out["sum_of_totals"] == 998.0
    assert "after" in SEEN[-1].url.params and "before" in SEEN[-1].url.params
    assert "checkout-draft" not in SEEN[-1].url.params["status"] and "processing" in SEEN[-1].url.params["status"]


def test_update_order_status():
    out = json.loads(text(run("update_order_status", {"order_id": 5, "status": "completed", "note": "done"})))
    assert out == {"order_id": 5, "previous_status": "processing", "status": "completed"}


def test_errors_are_clear():
    import pytest
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="Not found"):
        run("get_order", {"order_id": 404})


def test_period_bounds():
    after, before = _period_bounds("today", "Asia/Kolkata")
    assert after.endswith("T00:00:00") and before.endswith("T00:00:00")
    assert _period_bounds("all", "UTC") == (None, None)
