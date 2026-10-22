# Learning guide: understand this whole project from zero

This guide assumes **no background**. Read it top to bottom once, then use the Q&A at the end to
practise for the interview. Each part has a "where in the code" pointer so you can open the real file.

---

## Part 1: The story in plain words

You run an online **shop**. You want **AI assistants** to be able to *do things* in it ("list today's orders",
"create a product"), and you want your **team** to use those AI powers safely: everyone has their own key,
admins can cancel keys, and every action is written in a logbook.

| Real-world analogy | In this project |
|---|---|
| The shop | WordPress + WooCommerce (+ MariaDB database) |
| Buttons the AI may press | MCP **tools** (`list_orders`, `create_product`, …) |
| The device offering buttons | an MCP **server** (we wrote the WooCommerce one) |
| The phone using buttons | an MCP **client** (Claude Code, Cursor, our chat agent) |
| A receptionist at the entrance | the **gateway**: checks badges, forwards requests, writes the logbook |
| ID badge | an **API key** (`mgw_…`) |
| The receptionist's master keys | **upstream credentials** (team members never see them) |
| Visitor logbook | the **audit log** |
| The building manager | **Kubernetes**: runs everything, restarts what crashes |
| The building blueprint with blanks | **Helm chart** + values files |

---

## Part 2: Basic words

* **Server**: a computer program that waits for requests and answers them.
* **Database**: a program that stores data in tables (MariaDB for the shop, PostgreSQL for the gateway).
* **HTTP**: the language browsers and servers speak. A request has a *method* (GET = read, POST = send),
  a *path* (`/mcp`), *headers* (like `Authorization: Bearer mgw_…`) and maybe a *body* (JSON).
* **JSON**: text format for data: `{"name": "Hoodie", "price": 999}`.
* **API**: a set of URLs a program offers to other programs (WooCommerce REST API, our `/api/...`).
* **Port**: a numbered door on a computer. Each program listens behind one (gateway 8000, MCP 8080).
* **Container**: a sealed box with a program and everything it needs. **Image** = the recipe, **container** = a running copy.
* **Docker**: the tool that builds images (from a `Dockerfile`) and runs containers.

---

## Part 3: MCP (Model Context Protocol)

* An AI model alone can only produce text. **Tools** let it act. MCP is a **standard** way to offer tools,
  like USB-C for AI: any MCP client can use any MCP server.
* Messages are **JSON-RPC**: `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`.
* The 3 steps every session goes through:
  1. `initialize`: client and server say hello and agree on a protocol version.
  2. `tools/list`: "what buttons do you have?" → names, descriptions, **input schema** (what arguments each needs).
  3. `tools/call`: "press `list_orders` with `{period: "today"}`" → result content.
* If a tool fails, the result has `isError: true` and a message. The AI reads it and can explain it.
* **Streamable HTTP** = sending those JSON messages with HTTP POST to one URL (`/mcp`).

**Where in the code:** `services/woocommerce-mcp/app/server.py`. Each `@mcp.tool` function is a button.

---

## Part 4: The gateway (the heart)

The gateway plays **two roles at once**:
* to Claude Code / Cursor / chat agent it is **one MCP server** (`/mcp`)
* to WooCommerce / fx-rates / team-notes it is an **MCP client**

What it does on each request (`services/gateway/app/`):
1. **Check the badge** (`mcp_endpoint.py`): hash the API key, find it in the DB, reject if unknown, revoked
   or the user is disabled (HTTP 401).
2. **List tools** (`upstreams.py → list_tools`): merge all *enabled + connected* servers' tools, rename
   them `server__tool`, hide admin-only tools from members.
3. **Route a call** (`upstreams.py → call_tool`): split `woocommerce__list_orders` into server + tool,
   decrypt that server's credential, open a connection, call, **with a 20-second timeout**.
4. **Write the logbook** (`AuditLog` row): who, server, tool, when, how long, success or error.

Background job: the **health loop** pings every server every 15s (`initialize` + `tools/list`) and remembers
the status (Connected / Unreachable) and its tools. That's why a dead server never slows down tool listing.
We just skip it.

**Name collisions:** two servers with a `ping` tool become `woocommerce__ping` and `fx-rates__ping`.

---

## Part 5: Security ideas used

* **Hashing** = one-way scrambling. We store `sha256(api_key)`, never the key. Login passwords use
  **scrypt**, a deliberately slow hash, because humans pick guessable passwords.
* **Encryption** = two-way, with a secret key. Upstream credentials must be *used*, so we encrypt them
  (Fernet) and decrypt only in memory when calling the upstream.
* **JWT** = a signed token. The dashboard session cookie holds one, and so does the chat agent's 5-minute internal token.
* **httpOnly cookie** = JavaScript can't read it, so a script injected into the page can't steal the session.
* **CSRF** defence = state-changing requests must carry `X-Requested-With: dashboard`, which another website can't add.
* **SSRF** = tricking a server into calling internal addresses. We block private IPs and cloud metadata URLs
  when an admin registers a server.
* **Roles**: admin manages servers and team. Member uses tools (minus admin-only ones).

---

## Part 6: Kubernetes objects used (each in one line)

| Object | Plain meaning | Our example |
|---|---|---|
| **Pod** | one running instance of a container (or a few) | a gateway pod |
| **Deployment** | "keep N copies of this pod running; replace dead ones; update gradually" | gateway, dashboard, MCP servers, WordPress |
| **StatefulSet** | like a Deployment, but each pod keeps its own disk and a stable name (good for databases) | MariaDB, PostgreSQL |
| **Service** | a stable internal name + IP in front of pods (pods come and go, the Service name stays) | `urumi-store-mcp:8080` |
| **ClusterIP** | Service type reachable **only inside** the cluster | all our Services |
| **Ingress** | the front door: routes outside HTTP traffic by hostname/path to Services | `app.localhost/mcp → gateway` |
| **Ingress controller** | the program implementing Ingress rules (Traefik in k3s) | Traefik |
| **PersistentVolumeClaim (PVC)** | "I need 2 GB of disk that survives restarts" | WordPress files, DB data |
| **Secret** | where passwords/keys live (injected into pods as env vars) | `urumi-platform-secrets` |
| **ConfigMap** | non-secret configuration | the list of seed servers |
| **Job** | a pod that runs once to completion | store setup |
| **NetworkPolicy** | firewall rules between pods | only the gateway may call MCP servers |
| **Namespace** | a folder that groups resources | `urumi` |
| **Readiness probe** | "is this pod ready for traffic?" If not, the Service stops sending to it | gateway `/readyz` checks the DB |
| **Liveness probe** | "is this pod stuck?" If yes, Kubernetes restarts it | gateway `/healthz` |
| **requests / limits** | CPU/RAM guaranteed / maximum allowed | gateway 100m/192Mi → 1 CPU/512Mi |

**k3d** = k3s (light Kubernetes) running inside Docker on your laptop. **k3s** on the VPS is the same thing,
which is why the same charts work in both places.

---

## Part 7: Helm

* A **chart** = a folder of YAML templates with blanks like `{{ .Values.gateway.host }}`.
* **values.yaml** = default answers for the blanks. **values-local.yaml / values-prod.yaml** override
  some answers per environment. That's the whole "local vs production" story.
* `helm upgrade --install urumi <chart> -f values-local.yaml` = fill in the blanks and apply. Each run is a
  numbered **revision**, and `helm rollback urumi 3` goes back to revision 3.
* **Subchart**: our umbrella chart `urumi-platform` includes the reusable `woocommerce-store` chart.
* **Hook**: the store setup Job is annotated `post-install,post-upgrade`, so Helm runs it after installing.
* **`lookup`**: our secret templates first read the existing Secret from the cluster, so upgrades
  never generate new passwords by accident.

---

## Part 8: Read the code in this order

1. `services/woocommerce-mcp/app/woo_client.py`: how we call WooCommerce (REST API + Basic auth + `X-Forwarded-Proto`).
2. `services/woocommerce-mcp/app/server.py`: the 7 tools.
3. `services/gateway/app/models.py`: the 5 database tables.
4. `services/gateway/app/security.py`: hashing, encryption, tokens.
5. `services/gateway/app/upstreams.py`: health loop, aggregation, routing, timeouts, audit. **Most important file.**
6. `services/gateway/app/mcp_endpoint.py`: the `/mcp` endpoint + API-key auth.
7. `services/gateway/app/agent.py`: the chat tool loop.
8. `services/gateway/app/api/*.py`: dashboard REST endpoints.
9. `services/dashboard/src/pages/*.tsx`: the UI.
10. `deploy/helm/woocommerce-store/templates/*` then `deploy/helm/urumi-platform/templates/*`.

---

## Part 9: Interview questions with answers

**Q: Walk me through what happens when Claude Code calls a tool.**
Claude Code POSTs a JSON-RPC `tools/call` to `app.<domain>/mcp` with `Authorization: Bearer mgw_…`. Traefik
routes `/mcp` to the gateway Service. The gateway hashes the key, finds it, checks it isn't revoked and the user
is active. It splits the tool name `woocommerce__list_orders` into server + tool, checks the server is enabled,
the tool isn't admin-only for a member, and the rate limit. It decrypts the server's credential, opens an MCP
session to `urumi-store-mcp…:8080/mcp` (allowed by NetworkPolicy because the gateway pod has the
`mcp-client` label), does initialize + tools/call with a 20s timeout. The MCP server converts it into a
WooCommerce REST call, gets JSON back and returns a compact result. The gateway writes an audit row and passes
the result back unchanged.

**Q: Why did you choose the REST API to talk to WooCommerce?**
It's the official, versioned interface for products and orders, it needs no extra plugin, and permissions are
scoped by an API key. Direct SQL would couple us to internal tables and bypass WooCommerce logic (stock,
hooks, emails). WP-CLI would require exec rights into the WordPress pod, far too much privilege for an MCP server.

**Q: WooCommerce refuses Basic auth over HTTP. How did you handle that?**
WooCommerce only accepts key/secret Basic auth when it thinks the request is HTTPS. In the cluster we talk
plain HTTP to the ClusterIP Service, so we send `X-Forwarded-Proto: https` and configure wp-config to honour
it. That's the standard "TLS terminated at the proxy" setup. It's safe because the WordPress Service is only
reachable from the Ingress controller and our MCP server (NetworkPolicy) and the key pair is still required.

**Q: How do you handle two servers exposing the same tool name?**
Every tool is always prefixed: `<server-slug>__<tool>`. Slugs are unique and can't contain `__`, so I split on
the first `__`. Prefixing always (not only on conflict) keeps names stable when new servers are added.

**Q: What happens if an upstream is slow or dead?**
Listing uses a cache filled by a background health loop, so it never waits. A dead server's tools simply
disappear and it shows Unreachable with the reason. Each call has a hard timeout (anyio `fail_after`) and
returns a clear `isError` result. Everything is async, so other servers keep working.

**Q: Why open a new MCP session for every call?**
It's stateless and robust: nothing to repair when an upstream restarts, and nothing to share between gateway
replicas. The cost is one extra round trip inside the cluster. Connection pooling is the next optimisation if
latency matters.

**Q: How are API keys stored? Why SHA-256 and not bcrypt?**
Only the SHA-256 hash plus a short display prefix. The key is 256 random bits, so brute force is impossible
even with a fast hash, and a fast hash lets us do an indexed lookup on every request. Passwords are
low-entropy, so they use scrypt.

**Q: How are upstream credentials protected?**
Encrypted at rest with Fernet (key in a K8s Secret, MultiFernet for rotation), decrypted only in memory to
build the outgoing header, never returned by the API (only `has_credential`), never sent to MCP clients.

**Q: How does the chat agent act as the logged-in user?**
The backend mints a 5-minute JWT for that user and the agent connects to the gateway's own `/mcp` as an MCP
client. Same auth, same role filtering, same audit, with `source=chat`. There's no special path.

**Q: Explain the tool loop.**
Send the conversation and tool definitions to the LLM. If it answers with function calls, execute them through
the gateway, append the results, and ask again. Stop when it answers with text, or after 8 rounds.

**Q: Free LLM tiers are slow and often "busy". How did you handle that?**
Each provider has an ordered list of models. A busy (429/5xx) or retired (404) answer switches to the next model
immediately, with no waiting. Only if every model is busy does it wait once and then return a clear message. Hidden
"thinking" is turned to the minimum because these are simple tool lookups. Every answer shows a timing line, which
proves the gateway and tools take about 0.1 s and the rest is the LLM provider. Switching provider (Gemini ↔ Groq)
is a Helm values change (`values-groq.yaml`), not a code change.

**Q: Tell me about a bug you found while testing.**
The chat page went blank only in a newer Chromium-based browser. An error boundary showed `l is not a function`:
a React effect was written as `() => el.scrollIntoView()`, which returns the browser's return value. Newer
Chromium returns a Promise there, so React treated it as a cleanup function and called it. Fix: effects use a
block body and return nothing. Plus an error boundary on every page, so a failure is never a blank screen.

**Q: What can a member do vs an admin?**
Members use tools (except admin-only ones, which are hidden and rejected), manage their own keys and see their
own audit entries. Admins manage servers, team, everyone's keys and see the full audit log.

**Q: What if an admin adds a malicious MCP server?**
SSRF checks block internal/metadata URLs. The server only ever receives its own credential, never user keys.
Its tools are namespaced so it can't shadow other tools. Timeouts stop it hanging the gateway. Prompt
injection through tool descriptions/results can't be fully prevented, so adding servers is admin-only, sources
are visible, sensitive tools can be admin-only, the agent loop is capped, everything is audited, and disabling
the server removes its tools instantly.

**Q: What's public and what's internal? How is it enforced?**
Only two hostnames through the Ingress. Every Service is ClusterIP. A default-deny NetworkPolicy plus explicit
allows: gateway/dashboard only from Traefik, MCP servers only from pods labelled `mcp-client` (the gateway),
databases only from their app. The MCP servers also require a bearer/header credential.

**Q: Readiness vs liveness?**
Readiness decides whether a pod receives traffic (the gateway's checks the DB). Liveness decides whether to
restart it (cheap, no dependencies, so a DB outage doesn't cause restart loops).

**Q: Why a StatefulSet for databases?**
Stable identity and a dedicated PersistentVolumeClaim per pod via `volumeClaimTemplates`, so data stays with
the pod across restarts.

**Q: How do local and production differ?**
Only Helm values: domains, TLS annotations, image registry/tags, `existingSecret`s instead of generated
ones, replicas, storage class/sizes.

**Q: How do you upgrade and roll back?**
`helm upgrade` with a new image tag. Deployments roll out gradually and readiness gates traffic.
`helm history` + `helm rollback <rev>` restore the previous config/images. Back up the store DB before
WooCommerce upgrades, because rolling back a DB schema needs a restore.

**Q: Can you run several gateway replicas?**
Yes. The MCP endpoint is stateless (no session affinity needed), and all state is in Postgres. I tested two
instances: a key revoked on one is rejected by the other immediately. The rate limiter is per replica (Redis
would make it global).

**Q: Why Python for the backend?**
The official MCP Python SDK provides both the client and server sides, FastAPI is async (needed to fan out to
many upstreams with timeouts), and it reads clearly. The SDK version is pinned to the stable 1.x line.

**Q: What would you improve next?**
Multi-store provisioning (the store chart is already reusable), a pooled MCP session per upstream, push
`tools/list_changed` notifications via stateful sessions, OAuth on the gateway, Alembic migrations, a
Redis-backed rate limiter, argument redaction in audit logs, and a tool-search meta-tool for hundreds of tools.

---

## Part 10: Commands cheat sheet

```bash
make up                    # everything, locally
make status                # what is running
kubectl -n urumi get pods -w
kubectl -n urumi logs deploy/urumi-gateway -f
kubectl -n urumi describe pod <name>       # why is a pod not starting?
kubectl -n urumi delete pod -l app.kubernetes.io/component=team-notes   # "kill" an upstream
helm history urumi -n urumi && helm rollback urumi 1 -n urumi
make e2e                   # automated definition-of-done checks
make test                  # unit + integration tests, no cluster needed
bash scripts/use-groq.sh   # switch the chat agent to Groq (fast free LLM)
```
