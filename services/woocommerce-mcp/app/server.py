"""WooCommerce MCP server: exposes the store's products and orders as MCP tools.

Transport: MCP over Streamable HTTP at POST /mcp (stateless, JSON responses).
Auth:      a static bearer token that only the gateway knows (MCP_AUTH_TOKEN).
Health:    GET /healthz (liveness) and GET /readyz (readiness) for Kubernetes probes.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .woo_client import WooClient, WooCommerceError

log = logging.getLogger("woocommerce-mcp")

OrderStatus = Literal["pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"]
OrderStatusFilter = Literal["any", "pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"]
REAL_ORDER_STATUSES = ("pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed")
Period = Literal["today", "yesterday", "last_7_days", "last_30_days", "all"]


# --------------------------------------------------------------------------------------
# Small helpers that turn big WooCommerce JSON objects into compact, model-friendly ones
# --------------------------------------------------------------------------------------
def _public_link(url: str | None, public_base: str) -> str | None:
    """WooCommerce builds links from the request it sees (internal Service, forced https).
    Rewrite them onto the public storefront URL so humans can click them."""
    if not url or not public_base:
        return url
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    return public_base + parts.path + (f"?{parts.query}" if parts.query else "")


def _product_summary(p: dict[str, Any], public_base: str = "") -> dict[str, Any]:
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "sku": p.get("sku") or None,
        "price": p.get("price"),
        "regular_price": p.get("regular_price"),
        "sale_price": p.get("sale_price") or None,
        "status": p.get("status"),
        "stock_status": p.get("stock_status"),
        "stock_quantity": p.get("stock_quantity"),
        "permalink": _public_link(p.get("permalink"), public_base),
    }


def _order_summary(o: dict[str, Any]) -> dict[str, Any]:
    billing = o.get("billing") or {}
    return {
        "id": o.get("id"),
        "number": o.get("number"),
        "status": o.get("status"),
        "total": o.get("total"),
        "currency": o.get("currency"),
        "date_created": o.get("date_created"),
        "customer": " ".join(x for x in [billing.get("first_name"), billing.get("last_name")] if x) or None,
        "email": billing.get("email") or None,
        "payment_method": o.get("payment_method_title"),
        "items": [
            {"product_id": li.get("product_id"), "name": li.get("name"), "quantity": li.get("quantity"),
             "total": li.get("total")}
            for li in o.get("line_items", [])
        ],
    }


def _period_bounds(period: str, tz_name: str) -> tuple[str | None, str | None]:
    """Return (after, before) ISO strings in the store's local time for a named period."""
    if period == "all":
        return None, None
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start, end = start_today, start_today + timedelta(days=1)
    elif period == "yesterday":
        start, end = start_today - timedelta(days=1), start_today
    elif period == "last_7_days":
        start, end = start_today - timedelta(days=6), start_today + timedelta(days=1)
    else:  # last_30_days
        start, end = start_today - timedelta(days=29), start_today + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%S"
    # WooCommerce interprets after/before in the site's timezone when no offset is given.
    return start.strftime(fmt), end.strftime(fmt)


# --------------------------------------------------------------------------------------
# Bearer-token guard: a plain ASGI middleware in front of the MCP app
# --------------------------------------------------------------------------------------
class BearerTokenGuard:
    """Rejects any /mcp request without the right bearer token. Health endpoints stay open."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode() if token else b""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.expected and scope["path"].startswith("/mcp"):
            provided = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(provided, self.expected):
                response = JSONResponse({"error": "unauthorized", "detail": "missing or invalid bearer token"},
                                        status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# --------------------------------------------------------------------------------------
# The MCP server and its tools
# --------------------------------------------------------------------------------------
def build_mcp(settings: Settings, woo: WooClient) -> FastMCP:
    mcp = FastMCP(
        name="woocommerce",
        instructions=(
            f"Tools for the {settings.store_label}: list/create products and list/inspect/update orders. "
            f"Dates use the store timezone ({settings.store_timezone})."
        ),
        stateless_http=True,   # every request is independent -> any replica can serve it
        json_response=True,    # plain JSON responses instead of an SSE stream (simpler to debug)
        # DNS-rebinding protection is meant for servers on a developer laptop. This server is only
        # reachable inside the cluster (ClusterIP + NetworkPolicy) and is called via its Service DNS
        # name, so we turn the localhost-only Host check off.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def call(coro):
        try:
            return await coro
        except WooCommerceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(description="Check that the WooCommerce MCP server and the store are reachable.")
    async def ping() -> dict[str, Any]:
        info = await call(woo.get("/system_status", _fields="environment"))
        env = (info or {}).get("environment", {})
        return {"server": "woocommerce", "store": settings.store_label, "ok": True,
                "woocommerce_version": env.get("version"), "wordpress_version": env.get("wp_version")}

    @mcp.tool(description="List products in the store. Supports text search and paging.")
    async def list_products(
        search: Annotated[str | None, Field(description="Text to search for in product names")] = None,
        status: Annotated[Literal["any", "publish", "draft", "pending", "private"],
                          Field(description="Filter by publish status")] = "any",
        per_page: Annotated[int, Field(ge=1, le=100, description="Results per page")] = 20,
        page: Annotated[int, Field(ge=1, description="Page number, starting at 1")] = 1,
    ) -> dict[str, Any]:
        products = await call(woo.get("/products", search=search, status=status, per_page=per_page, page=page))
        return {"count": len(products), "page": page, "products": [_product_summary(p, settings.store_public_url) for p in products]}

    @mcp.tool(description="Get one product by its numeric ID.")
    async def get_product(product_id: Annotated[int, Field(ge=1, description="WooCommerce product ID")]) -> dict:
        return _product_summary(await call(woo.get(f"/products/{product_id}")), settings.store_public_url)

    @mcp.tool(description="Create a simple product. It is published immediately unless status='draft'.")
    async def create_product(
        name: Annotated[str, Field(min_length=1, max_length=200, description="Product name")],
        regular_price: Annotated[float, Field(ge=0, description="Price in the store currency, e.g. 499 or 19.99")],
        description: Annotated[str | None, Field(description="Long description (HTML allowed)")] = None,
        short_description: Annotated[str | None, Field(description="Short description")] = None,
        sku: Annotated[str | None, Field(description="Unique stock keeping unit")] = None,
        stock_quantity: Annotated[int | None, Field(ge=0, description="Units in stock; enables stock tracking")] = None,
        status: Annotated[Literal["publish", "draft"], Field(description="publish = visible on storefront")] = "publish",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "type": "simple",
            "status": status,
            "regular_price": f"{regular_price:.2f}",
        }
        if description:
            body["description"] = description
        if short_description:
            body["short_description"] = short_description
        if sku:
            body["sku"] = sku
        if stock_quantity is not None:
            body["manage_stock"] = True
            body["stock_quantity"] = stock_quantity
        product = await call(woo.post("/products", body))
        return {"created": True, "product": _product_summary(product, settings.store_public_url)}

    @mcp.tool(description=(
        "List orders, newest first. Use period='today' to answer 'what orders came in today'. "
        "Optionally filter by status."))
    async def list_orders(
        period: Annotated[Period, Field(description="Time window in the store's timezone")] = "all",
        status: Annotated[OrderStatusFilter, Field(description="Order status filter")] = "any",
        per_page: Annotated[int, Field(ge=1, le=100, description="Results per page")] = 20,
        page: Annotated[int, Field(ge=1, description="Page number, starting at 1")] = 1,
    ) -> dict[str, Any]:
        after, before = _period_bounds(period, settings.store_timezone)
        # "any" would include WooCommerce's internal "checkout-draft" orders (abandoned carts
        # from the block checkout). Ask only for real order statuses.
        status_filter = ",".join(REAL_ORDER_STATUSES) if status == "any" else status
        orders = await call(woo.get("/orders", status=status_filter, after=after, before=before, per_page=per_page,
                                    page=page, orderby="date", order="desc"))
        summaries = [_order_summary(o) for o in orders]
        total = sum(float(o["total"] or 0) for o in summaries)
        return {"period": period, "timezone": settings.store_timezone, "count": len(summaries),
                "sum_of_totals": round(total, 2), "orders": summaries}

    @mcp.tool(description="Get full details of one order, including items, addresses and notes.")
    async def get_order(order_id: Annotated[int, Field(ge=1, description="WooCommerce order ID")]) -> dict:
        order = await call(woo.get(f"/orders/{order_id}"))
        notes = await call(woo.get(f"/orders/{order_id}/notes"))
        summary = _order_summary(order)
        summary.update({
            "billing": order.get("billing"),
            "shipping": order.get("shipping"),
            "customer_note": order.get("customer_note") or None,
            "date_paid": order.get("date_paid"),
            "date_completed": order.get("date_completed"),
            "notes": [{"date": n.get("date_created"), "note": n.get("note")} for n in notes],
        })
        return summary

    @mcp.tool(description="Change an order's status, e.g. processing -> completed. Optionally add an order note.")
    async def update_order_status(
        order_id: Annotated[int, Field(ge=1, description="WooCommerce order ID")],
        status: Annotated[OrderStatus, Field(description="New status")],
        note: Annotated[str | None, Field(description="Optional private note to add to the order")] = None,
    ) -> dict[str, Any]:
        before = await call(woo.get(f"/orders/{order_id}"))
        updated = await call(woo.put(f"/orders/{order_id}", {"status": status}))
        if note:
            await call(woo.post(f"/orders/{order_id}/notes", {"note": note, "customer_note": False}))
        return {"order_id": order_id, "previous_status": before.get("status"), "status": updated.get("status")}

    return mcp


def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> ASGIApp:
    woo = WooClient(settings.store_url, settings.consumer_key, settings.consumer_secret,
                    settings.request_timeout, transport=transport)
    mcp = build_mcp(settings, woo)
    app: Starlette = mcp.streamable_http_app()

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.router.routes.append(Route("/healthz", healthz))
    app.router.routes.append(Route("/readyz", healthz))
    return BearerTokenGuard(app, settings.mcp_auth_token)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    if not settings.mcp_auth_token:
        log.warning("MCP_AUTH_TOKEN is empty: the MCP endpoint is unauthenticated (dev mode only!)")
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
