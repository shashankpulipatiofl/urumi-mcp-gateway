# System design and trade-offs (short note)

Longer version with diagrams and the request trace: [ARCHITECTURE.md](ARCHITECTURE.md).

## Architecture choices

| Choice | Why | Trade-off |
|---|---|---|
| **One gateway service** (FastAPI) that is MCP server *and* MCP client, plus the REST API and chat agent | One place for auth, policy, routing and audit; simple to deploy | Larger service; would split the chat agent out at scale |
| **Own WooCommerce MCP server** (Python MCP SDK, Streamable HTTP) calling the WooCommerce REST API | Small, typed tools with clear errors; REST API is the stable WooCommerce contract | Runs its own pod; a WordPress plugin would avoid one hop |
| **Stateless MCP endpoint** (no `Mcp-Session-Id`) | Any gateway replica can serve any request, no sticky sessions | Can't push `tools/list_changed`; clients see changes on their next list |
| **PostgreSQL** for gateway data, **MariaDB** for WordPress | Each app on the DB it is built for | Two databases to back up |
| **Umbrella Helm chart + reusable store chart** | One `helm install`; the store chart is the unit you'd install per store (multi-store) | Subchart values nesting is a bit verbose |
| **Store setup as a Helm hook Job (WP-CLI)** | Fully automatic, idempotent install (COD, products, REST key) | Runs on every upgrade (it is idempotent, so safe) |
| **React + Vite SPA on non-root nginx** | Static, cacheable, tiny attack surface | Separate image from the API |
| **Gemini or any OpenAI-compatible LLM** behind one interface, with model fallbacks | Free tiers work; no lock-in | Function-calling formats differ, converted in `agent.py` |

## Tool aggregation, routing and failure handling

* **Discovery:** a background health loop probes every enabled upstream every 15 s in parallel
  (`initialize` + `tools/list`, 8 s limit) and caches status + tools in memory. `tools/list` reads the cache, so
  listing never waits on a slow upstream.
* **Aggregation and naming:** every tool is exposed as `<server-slug>__<tool>`, with the description prefixed
  `[Server Name]`. Always prefixing (not only on conflict) keeps names stable when servers are added. Tools are
  filtered by the caller's role (admin-only tools hidden for members).
* **Routing:** `tools/call` splits on the first `__` → server + original tool, re-checks enabled / permission /
  rate limit, decrypts that server's credential, re-validates the URL (SSRF) and opens a fresh MCP session
  with a hard 20 s timeout. The result is passed back unchanged and audited.
* **Failures:** a dead upstream is marked *Unreachable* by the next probe and its tools drop out; calls to it
  fail fast with a clear `isError` message; a hanging upstream hits the timeout; other upstreams are unaffected
  because everything is async. Low-level errors are unwrapped into one readable sentence.
* **Trade-off:** a fresh session per call costs one extra round trip (ms inside the cluster) but means no
  connection state to repair after upstream restarts. A pooled session per upstream is the next optimisation.

## What changes for production

Everything changes through `values-prod.yaml` and Secrets; templates are identical
(details: [DEPLOY_VPS.md](DEPLOY_VPS.md)).

* **DNS:** A records for `store.<domain>` and `app.<domain>` → VPS IP (instead of `*.localhost`).
* **Ingress / TLS:** same Traefik Ingresses with real hosts, `tls:` blocks and a cert-manager
  `cluster-issuer` annotation (Let's Encrypt); cookies become `Secure`. Other controllers: set
  `ingress.className` + the NetworkPolicy ingress-controller selector.
* **Storage class:** `local-path` (k3s) with larger PVCs (store 10 Gi, Postgres 5 Gi); on a cloud cluster use
  its block-storage class. Add DB backups (`mariadb-dump`, `pg_dump` CronJobs or volume snapshots).
* **Secrets:** created out of band with `kubectl create secret` (or Sealed Secrets / External Secrets for GitOps)
  and referenced via `existingSecret`; nothing secret in git or values files. Locally, Helm generates them once.
* **Images and scale:** pinned GHCR tags built by CI; gateway and dashboard 2 replicas; demo-only tools off.
* **Upgrade / rollback:** `helm upgrade` (rolling, readiness-gated) and `helm rollback <rev>`; database
  changes need a backup first, because rollback doesn't revert data.
