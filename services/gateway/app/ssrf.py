"""Server-side request forgery (SSRF) protection for admin-supplied upstream URLs.

An admin (or a stolen admin session) could register "http://169.254.169.254/..." (cloud
metadata) or "http://kubernetes.default.svc" and use the gateway to probe internal systems.
Rules:
  1. Only http/https.
  2. Hostnames matching ALLOWED_UPSTREAM_HOSTS (default: our own namespace's Services) may be
     private: these are the in-cluster MCP servers we deploy.
  3. Everything else must resolve only to public IPs: no loopback, private, link-local
     (includes 169.254.169.254), CGNAT, multicast or reserved ranges.
The check runs when a server is saved and again before every probe/call, because DNS
answers can change (DNS rebinding).
"""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import socket
from urllib.parse import urlsplit

BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata", "kubernetes", "kubernetes.default",
                     "kubernetes.default.svc", "kubernetes.default.svc.cluster.local"}


class UnsafeUrlError(ValueError):
    pass


def _ip_is_private(ip: ipaddress._BaseAddress) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10")))


async def validate_upstream_url(url: str, allowed_patterns: list[str], block_private: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrlError("URL must start with http:// or https://")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeUrlError("URL has no hostname")
    if parts.username or parts.password:
        raise UnsafeUrlError("Put credentials in the credential field, not in the URL")
    if host in BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(f"Host '{host}' is not allowed")
    if any(fnmatch.fnmatch(host, pattern) for pattern in allowed_patterns):
        return  # explicitly allowed internal service
    if not block_private:
        return

    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeUrlError(f"Cannot resolve host '{host}'") from exc
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]

    for ip in addresses:
        if _ip_is_private(ip):
            raise UnsafeUrlError(
                f"Host '{host}' resolves to a private/internal address ({ip}). "
                "Only public URLs or allowed in-cluster services can be registered.")
