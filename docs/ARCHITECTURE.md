# Architecture

## Components and responsibilities

| Component | Tech | Responsibility | Exposure |
|---|---|---|---|
| **WordPress + WooCommerce** | `wordpress:6.8-apache` + WooCommerce + WP-CLI | The store: storefront, cart, COD checkout, wp-admin | Ingress `store.localhost` |
| **MariaDB** | `mariadb:11.4` StatefulSet | Store database (PVC) | ClusterIP, WordPress + setup Job only |
| **Store setup Job** | same image, Helm `post-install/post-upgrade` hook | Installs WP, activates WooCommerce, enables COD, creates the REST key for the MCP server, seeds products. Idempotent | none |
| **WooCommerce MCP server** | Python, MCP SDK (FastMCP), Streamable HTTP | Exposes products/orders as MCP tools; talks to WooCommerce REST API | ClusterIP, gateway only |
| **fx-rates MCP** | Python | Currency conversion; requires an `X-API-Key` header credential | ClusterIP, gateway only |
| **team-notes MCP** | Python + SQLite | Shared notebook; no credential (shows the optional-credential path) | ClusterIP, gateway only |
| **Gateway** | FastAPI + MCP SDK (client *and* server) | Auth (sessions, API keys), upstream registry, health/discovery, tool aggregation + routing, timeouts, audit log, metrics, chat agent | Ingress `app.localhost/api`, `/mcp` |
| **PostgreSQL** | `postgres:16-alpine` StatefulSet | Gateway data: users, invites, API-key hashes, servers (encrypted creds), audit log | ClusterIP, gateway only |
| **Dashboard** | React + Vite + TS on nginx-unprivileged | Admin + member UI | Ingress `app.localhost/` |

## How a tool call travels (client → store → back)

Example: user B in Claude Code asks *"what orders came in today?"*

```
Claude Code                       Gateway pod                                     WooCommerce MCP pod        WordPress pod
-----------                       -----------                                     -------------------        -------------
POST app.localhost/mcp
  Authorization: Bearer mgw_…
  {"method":"tools/call",
   "params":{"name":"woocommerce__list_orders","arguments":{"period":"today"}}}
        │  (Traefik Ingress, path /mcp → gateway Service :8000)
        ▼
  MCPAuthApp
    sha256(key) → api_keys row → not revoked → user active      (401 otherwise)
    Principal{user B, role member, source=mcp} stored in ASGI scope
        ▼
  StreamableHTTPSessionManager (stateless) → low-level MCP Server → call_tool handler
        ▼
  UpstreamManager.call_tool
    split "woocommerce__list_orders" → server "woocommerce", tool "list_orders"
    server exists + enabled? tool admin-only? rate limit?          (error result otherwise)
    decrypt credential (Fernet) → "Authorization: Bearer <store mcp token>"
    SSRF re-check of the URL
    with fail_after(20s):
       open MCP session ──────────── POST http://urumi-store-mcp.urumi.svc:8080/mcp ──►  BearerTokenGuard
       initialize                                                                        FastMCP list_orders
       tools/call list_orders {"period":"today"}                                         period → after/before in store TZ
                                                                                          GET /wp-json/wc/v3/orders ───────► WooCommerce REST
                                                                                              Basic auth ck/cs              (X-Forwarded-Proto: https)
                                                                       ◄──────────── CallToolResult (JSON summary) ◄─────── orders JSON
    write audit row (user B, woocommerce, list_orders, duration, success)
    Prometheus counters/histogram
        ▼
  CallToolResult passed back unchanged (content + structuredContent)
◄───────
```

The chat agent takes **the same path**: it is an MCP client of `http://127.0.0.1:8000/mcp`, using a 5-minute
token minted for the logged-in user (see [Chat agent](#chat-agent)).

## How the WooCommerce MCP server talks to the store (and why REST)

The MCP server calls the **WooCommerce REST API v3** (`/wp-json/wc/v3/products`, `/orders`, …) at the
WordPress Service's in-cluster address (`WOO_STORE_URL`, ClusterIP), with **HTTP Basic auth** using a
WooCommerce consumer key/secret. The setup Job creates that key (read/write) and the same values come from the
`urumi-store-secrets` Secret, so nothing is hardcoded. Each request has a 15 s timeout and errors are turned into
clear messages. Because the traffic stays inside the cluster over plain HTTP, the client sends the standard
reverse-proxy header `X-Forwarded-Proto: https`, which WordPress is configured to honour (WooCommerce only allows
Basic auth over HTTPS).

**Why REST instead of WP-CLI or direct database access:**

| Option | Verdict |
|---|---|
| **REST API (chosen)** | WooCommerce's official, versioned contract. Runs all of WooCommerce's validation, hooks, stock and order logic, exactly like wp-admin. Works over the network, so the MCP server is its own small pod with its own scaling, limits and NetworkPolicy, and only needs an API key with exactly the permissions it uses |
| WP-CLI | Needs to run *inside* the WordPress container (shared filesystem, PHP), so the MCP server would have to exec into the pod (Kubernetes API access) or be bundled with WordPress. Spawning a PHP process per call is slow. Fine for one-off setup, which is why the setup Job uses it |
| Direct SQL on MariaDB | Bypasses WooCommerce's business logic (order totals, stock, HPOS tables, hooks) and couples us to the internal schema. Would also need DB credentials in the MCP server |
| A WordPress plugin | The assignment says not to install an existing MCP plugin; writing one in PHP would also mix the MCP server's lifecycle with WordPress |

## MCP in 60 seconds

* **MCP** (Model Context Protocol) is JSON-RPC 2.0 messages between a *client* (Claude Code, our chat agent,
  the gateway itself) and a *server* that offers tools, resources and prompts.
* **Lifecycle:** `initialize` (version + capabilities handshake) → `notifications/initialized` → requests.
* **Tools:** `tools/list` returns `{name, description, inputSchema (JSON Schema)}`. `tools/call` runs one and
  returns `{content: [...], structuredContent?, isError}`. Tool failures are **results with `isError: true`**,
  not protocol errors, so the model can read them and react.
* **Streamable HTTP transport:** the client POSTs each JSON-RPC message to one URL (`/mcp`). The server
  answers with plain JSON or an SSE stream. Optional `Mcp-Session-Id` header for stateful servers.

**The gateway is both roles:** an MCP *server* to the outside world (one `/mcp` endpoint) and an MCP *client*
to every upstream.

## How the gateway connects to upstreams

* **Discovery (health loop):** every `HEALTH_INTERVAL_SECONDS` (15s), all enabled servers are probed in
  parallel: open a session, `initialize`, `tools/list` (following pagination), with an 8s hard limit each.
  The result (status, tools, server name/version, latency, last error) is cached in memory.
  Admin edits trigger an immediate re-probe.
* **Listing** reads the cache only, so it costs one small DB query plus memory and never waits on an upstream.
* **Calls** open a **fresh MCP session per call** (`initialize` + `tools/call`).
  *Trade-off:* one extra round trip (a few ms inside the cluster) in exchange for no shared connection state,
  nothing to repair after an upstream restart, and trivial horizontal scaling of the gateway.
  A pooled long-lived session per upstream would be the next optimisation.
* All upstream URLs are re-validated against the SSRF policy before every connection (DNS can change).

## Tool names and collisions

**Scheme:** every exposed tool is `<server-slug>__<original-tool-name>`.

* `woocommerce__ping`, `fx-rates__ping`, `team-notes__ping` all exist side by side (deliberately: each demo
  server has a `ping` tool).
* The slug is chosen by the admin, unique, `[a-z0-9-]`, and can't contain `__`, so splitting on the
  **first** `__` is unambiguous even if the upstream tool name itself contains `__`.
* Characters outside `[a-zA-Z0-9_-]` in upstream names become `_`. Names longer than 64 characters (a common
  client limit) are shortened with a hash suffix. The gateway keeps a map from exposed name to original name.
* Descriptions are prefixed with `[Display Name]` so models and humans see which system a tool touches.

*Why always prefix, instead of only on conflict?* Names stay stable: adding a second server never renames
the tools of the first, so prompts, permission lists and audit history keep working.

## Timeouts and dead upstreams

| Situation | Behaviour |
|---|---|
| Upstream pod killed | Next probe fails with "connection refused" → status **Unreachable** with the reason; its tools drop out of `tools/list`; calls to it return `isError: "Upstream 'x' is unreachable (…)"` immediately; other servers unaffected |
| Upstream hangs / slow | Each call has a hard `CALL_TIMEOUT_SECONDS` (20s, `anyio.fail_after`) → `isError: "Upstream 'x' did not respond within 20s (timeout)"`. Probes have their own 8s limit. Everything is async, so one slow upstream doesn't block requests to others |
| Upstream rejects credential | `"Upstream 'x' rejected the gateway's credential (HTTP 401)"` |
| Upstream comes back | Next probe (≤ 15s) marks it **Connected** and its tools return |
| Disabled by admin | Tools disappear from the next `tools/list`; direct calls are rejected |

All failures are audited with the error text. Low-level exceptions (often wrapped in anyio `ExceptionGroup`s)
are unwrapped into one clear sentence by `describe_error()`.

> **Note:** Our MCP endpoint is stateless, so it can't push `notifications/tools/list_changed`. Clients see
> changes on their next `tools/list` (MCP Inspector: *List Tools*; Claude Code: reconnect with `/mcp`).
> Calls to tools that were removed are rejected immediately either way.

## Chat agent

1. The dashboard sends the conversation to `POST /api/chat` (session cookie).
2. The backend mints an **internal token** (HS256 JWT, audience `mcp-internal`, 5 minutes, `sub` = user id).
3. The agent opens a **real MCP client session to the gateway's own `/mcp`** with that token. The
   gateway authenticates it like an API key, so the tool list is filtered by the user's role, and every call
   is audited as that user with `source = chat`.
4. **Tool loop** (`agent.py`):
   ```
   repeat up to LLM_MAX_STEPS (8):
       ask the LLM with (system prompt, conversation, tool declarations)
       if it returns text            -> done
       if it returns function calls  -> execute each via MCP tools/call, append results, loop
   ```
5. The response contains the final text plus a trace of every tool call (name, args, result, duration, ok),
   which the UI shows as expandable chips.

Providers: **Gemini** (native `generateContent` function calling, JSON Schemas converted to Gemini's OpenAPI
subset; default `gemini-2.5-flash-lite` with thinking off) or any **OpenAI-compatible** `/chat/completions`
API, e.g. **Groq** with `openai/gpt-oss-20b` (`values-groq.yaml`). The LLM key lives in a Kubernetes Secret
and is only read by the gateway.

**Resilience and speed (free tiers):** each provider has an ordered model list (`llm.model` +
`llm.fallbackModels`). A busy (429/5xx), retired (404) or malformed-tool-call answer switches to the next
model immediately; only if all are busy does it wait once and retry, then return a clear error. Thinking /
reasoning is set to the minimum because these are simple tool lookups. The response includes
`timing` (total, LLM, tools, model), shown under every chat answer.

**Dashboard robustness:** every page is wrapped in a React error boundary (a failure shows the error text
instead of a blank page), answers are rendered with a small safe Markdown renderer (tables, lists, bold; React
elements only, never raw HTML), and effects never return values (newer Chromium returns a Promise from
`scrollIntoView`, which React would otherwise try to call as a cleanup function).

## Data model (PostgreSQL)

`users` · `invites` (token hash, expiry) · `api_keys` (prefix, **sha256 hash**, revoked_at, last_used_at) ·
`upstream_servers` (slug, url, auth type, **Fernet-encrypted credential**, admin-only tools) ·
`audit_logs` (time, user, source mcp/chat, server, tool, duration, success, error, truncated args).

## Scaling

* MCP endpoint is **stateless**: no session affinity needed, any replica can serve any request
  (`values-prod.yaml` runs 2).
* PostgreSQL is the single source of truth. Every replica runs its own health loop, and a change made on one
  replica is seen by the others on their next DB read (immediately for auth/enabled checks, ≤ 15s for
  discovered tool lists).
* Rate limiting is per replica (in memory). A Redis-backed limiter would make it global.
