"""fx-rates: a currency-conversion MCP server (upstream #2).

* Requires a static header credential (`X-API-Key`). The gateway stores it encrypted and
  adds it to every upstream call; users never see it.
* Uses live ECB rates from api.frankfurter.app when the cluster has internet access, and
  falls back to a bundled reference table otherwise, so demos never depend on Wi-Fi.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

import anyio
import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from .common import build_app, new_server, run

# Reference rates: 1 unit of currency = X EUR (approximate, for offline fallback only).
REFERENCE_IN_EUR = {
    "EUR": 1.0, "USD": 0.92, "GBP": 1.17, "INR": 0.0110, "JPY": 0.0061, "AUD": 0.61,
    "CAD": 0.67, "SGD": 0.69, "AED": 0.25, "CHF": 1.05, "CNY": 0.13,
}
REFERENCE_DATE = "reference-table"

LIVE_URL = os.environ.get("FX_LIVE_URL", "https://api.frankfurter.app")
USE_LIVE = os.environ.get("FX_USE_LIVE", "true").lower() == "true"
DEMO_TOOLS = os.environ.get("DEMO_TOOLS", "false").lower() == "true"

mcp = new_server("fx-rates", "Currency conversion tools. Use convert_currency to express order totals in other currencies.")


async def _live_rate(src: str, dst: str) -> tuple[float, str] | None:
    if not USE_LIVE:
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{LIVE_URL}/latest", params={"from": src, "to": dst})
            r.raise_for_status()
            data = r.json()
            return float(data["rates"][dst]), f"ECB via frankfurter.app ({data.get('date')})"
    except Exception:  # noqa: BLE001 - any failure means "use the fallback table"
        return None


def _reference_rate(src: str, dst: str) -> float:
    if src not in REFERENCE_IN_EUR or dst not in REFERENCE_IN_EUR:
        raise ToolError(f"Unsupported currency. Supported: {', '.join(sorted(REFERENCE_IN_EUR))}")
    return REFERENCE_IN_EUR[src] / REFERENCE_IN_EUR[dst]


@mcp.tool(description="Check that the fx-rates server is alive.")
async def ping() -> dict[str, Any]:
    return {"server": "fx-rates", "ok": True, "live_rates_enabled": USE_LIVE}


@mcp.tool(description="Convert an amount between two currencies (ISO codes like INR, USD, EUR).")
async def convert_currency(
    amount: Annotated[float, Field(ge=0, description="Amount to convert")],
    from_currency: Annotated[str, Field(min_length=3, max_length=3, description="Source ISO code, e.g. INR")],
    to_currency: Annotated[str, Field(min_length=3, max_length=3, description="Target ISO code, e.g. USD")],
) -> dict[str, Any]:
    src, dst = from_currency.upper(), to_currency.upper()
    if src == dst:
        rate, source = 1.0, "identity"
    else:
        live = await _live_rate(src, dst)
        rate, source = live if live else (_reference_rate(src, dst), REFERENCE_DATE)
    return {"amount": amount, "from": src, "to": dst, "rate": round(rate, 6),
            "converted": round(amount * rate, 2), "source": source}


@mcp.tool(description="List exchange rates for a base currency against the supported currencies.")
async def list_rates(base: Annotated[str, Field(min_length=3, max_length=3, description="Base ISO code")] = "INR") -> dict:
    b = base.upper()
    if b not in REFERENCE_IN_EUR:
        raise ToolError(f"Unsupported currency {b}")
    return {"base": b, "source": REFERENCE_DATE,
            "rates": {c: round(_reference_rate(b, c), 6) for c in sorted(REFERENCE_IN_EUR) if c != b}}


if DEMO_TOOLS:
    @mcp.tool(description="DEMO ONLY: sleep N seconds before answering, to show gateway timeouts.")
    async def slow_operation(seconds: Annotated[float, Field(ge=0, le=120)] = 30) -> dict[str, Any]:
        await anyio.sleep(seconds)
        return {"slept": seconds}


app = build_app(mcp, auth_header="X-API-Key", auth_value=os.environ.get("FX_API_KEY", ""))

if __name__ == "__main__":
    run(app)
