"""Prometheus metrics, served on GET /metrics (cluster-internal; not routed by the Ingress)."""

from prometheus_client import Counter, Gauge, Histogram

TOOL_CALLS = Counter("mcp_gateway_tool_calls_total", "Tool calls through the gateway",
                     ["server", "tool", "outcome", "source"])
TOOL_LATENCY = Histogram("mcp_gateway_tool_call_seconds", "Tool call latency (gateway -> upstream -> back)",
                         ["server"], buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30))
UPSTREAM_UP = Gauge("mcp_gateway_upstream_up", "1 if the upstream answered the last health probe", ["server"])
UPSTREAM_TOOLS = Gauge("mcp_gateway_upstream_tools", "Number of tools exposed by the upstream", ["server"])
AUTH_FAILURES = Counter("mcp_gateway_auth_failures_total", "Rejected MCP requests", ["reason"])
