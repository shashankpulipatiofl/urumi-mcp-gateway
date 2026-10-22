"""A tiny async client for the WooCommerce REST API (wc/v3).

How we talk to the store, and why
---------------------------------
We use the official WooCommerce REST API over plain HTTP *inside the cluster*:

* It is the supported, versioned interface for products and orders, so we do not
  depend on WordPress database internals.
* It needs no extra plugin. The API key pair is created by our setup Job.
* WP-CLI would need `kubectl exec` rights into the WordPress pod, which is far
  more privilege than an MCP server should have.

WooCommerce only allows HTTP Basic auth with API keys when it thinks the request
is HTTPS. TLS terminates at the Ingress, so in-cluster traffic is plain HTTP. We
send `X-Forwarded-Proto: https`, the standard reverse-proxy header, and
wp-config is set to honour it. The Service is reachable only from inside the
cluster, and NetworkPolicy limits WordPress to the ingress controller and this
MCP server, so this is equivalent to the usual "TLS terminated at the proxy" setup.
"""

from __future__ import annotations

from typing import Any

import httpx


class WooCommerceError(Exception):
    """A clear, human-readable error that becomes an MCP tool error."""


class WooClient:
    def __init__(self, base_url: str, consumer_key: str, consumer_secret: str, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            auth=httpx.BasicAuth(consumer_key, consumer_secret),
            headers={
                "X-Forwarded-Proto": "https",
                "Accept": "application/json",
                "User-Agent": "urumi-woocommerce-mcp/1.0",
            },
            timeout=httpx.Timeout(timeout, connect=5.0),
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json: dict[str, Any] | None = None) -> Any:
        url = f"/wp-json/wc/v3{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            response = await self._client.request(method, url, params=clean_params, json=json)
        except httpx.TimeoutException as exc:
            raise WooCommerceError("The store did not respond in time (timeout). Is WordPress running?") from exc
        except httpx.TransportError as exc:
            raise WooCommerceError(f"Cannot reach the store: {exc.__class__.__name__}") from exc

        if response.status_code >= 400:
            message = response.text[:300]
            try:
                body = response.json()
                message = body.get("message", message)
            except ValueError:
                pass
            if response.status_code in (401, 403):
                raise WooCommerceError(f"The store rejected our API credentials (HTTP {response.status_code}): {message}")
            if response.status_code == 404:
                raise WooCommerceError(f"Not found (HTTP 404): {message}")
            raise WooCommerceError(f"WooCommerce error (HTTP {response.status_code}): {message}")
        try:
            return response.json()
        except ValueError as exc:
            raise WooCommerceError("The store returned a non-JSON response. Are pretty permalinks enabled?") from exc

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        return await self.request("POST", path, json=body)

    async def put(self, path: str, body: dict[str, Any]) -> Any:
        return await self.request("PUT", path, json=body)
