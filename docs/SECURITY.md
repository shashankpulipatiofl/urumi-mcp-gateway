# Security posture

## Secrets: where every credential lives

| Secret | Stored as | Who can read it |
|---|---|---|
| Dashboard passwords | scrypt hash (salted, memory-hard) in Postgres | nobody |
| **Gateway API keys** (`mgw_…`, 256-bit random) | **SHA-256 hash** + display prefix in Postgres. Plaintext shown once | nobody after creation |
| **Upstream credentials** (store MCP token, fx API key, any added server's token) | **Fernet-encrypted** (AES-128-CBC + HMAC) in Postgres; key from a K8s Secret; MultiFernet supports rotation | gateway process memory, only while building the outgoing request |
| Session tokens | HS256 JWT in an **httpOnly, SameSite=Strict** cookie (`Secure` in prod) | browser can't read it from JS |
| Internal chat tokens | 5-minute JWT, audience `mcp-internal` | minted and consumed inside the gateway |
| WooCommerce REST key pair | K8s Secret → env of MCP server + setup Job; WooCommerce stores the key hashed | store MCP pod |
| DB passwords, signing + encryption keys, LLM key | Kubernetes Secrets (generated once by Helm with `lookup`, `resource-policy: keep`; prod uses `existingSecret`) | the pods that mount them |

No secret is in source code, images or values files (the LLM key is created with `kubectl create secret`).

**Why SHA-256 for API keys, not bcrypt?** Keys are 256 bits of randomness, so brute force is impossible
regardless of hash speed. A fast hash allows an indexed O(1) lookup on every request. Passwords are
low-entropy, which is why they use scrypt.

## Upstream credentials never leave the server

* The REST API's response models have no credential field, only `has_credential: true/false`. On edit, a blank
  credential means "keep". Members don't even see upstream URLs.
* MCP clients only ever see `<slug>__<tool>` definitions. The gateway adds the upstream header itself.
* Tests assert that the plaintext credentials never appear in `/api/servers` responses.

## Roles: what a member can and cannot do

| Action | admin | member |
|---|---|---|
| Use tools over MCP / chat | ✅ all | ✅ except tools marked **admin-only** (hidden from `tools/list` *and* rejected if called directly) |
| See servers, status, tools | ✅ with URLs + errors | ✅ names/status/tools only |
| Add / edit / disable / remove servers | ✅ | ❌ 403 |
| Invite users, change roles, disable users | ✅ (can't demote/disable self or the last admin) | ❌ |
| Create / revoke own keys | ✅ | ✅ |
| Issue / revoke other users' keys | ✅ | ❌ |
| Audit log | everyone's calls | own calls only |

Default tool permissions: `woocommerce__create_product` and `woocommerce__update_order_status` are admin-only
(configurable per server in the dashboard or `gateway.storeAdminOnlyTools`). Role and active state are
re-checked **on every call**, so demoting or disabling someone takes effect immediately. Disabling a user also
revokes all of their keys.

## What if an admin adds a malicious or compromised MCP server?

Threats and mitigations:

1. **SSRF via the URL** (`http://169.254.169.254/…`, `http://kubernetes.default.svc`, `http://10.x…`).
   The URL must be http(s) and must resolve only to **public** IPs, unless the host matches
   `ALLOWED_UPSTREAM_HOSTS`, which Helm sets to `*.<release-namespace>.svc.cluster.local` (our own upstreams
   only). Loopback, RFC1918, link-local/metadata, CGNAT and reserved ranges are blocked, as are credentials in
   URLs and the Kubernetes API hostnames. The check runs on save **and before every connection** (DNS
   rebinding). No redirects are followed.
2. **Credential theft.** A malicious server only receives *its own* credential. The gateway never forwards the
   user's API key, cookies or other servers' credentials upstream.
3. **Tool poisoning / prompt injection** (a tool description or result that tells the model to do something
   else). The gateway can't fully prevent this, which is why adding servers is admin-only. Mitigations:
   descriptions are prefixed with the server name so the source is visible, tool results are shown in the chat
   UI, sensitive tools can be admin-only, the chat agent's loop is capped at 8 steps, and every call is audited.
   Disabling the server removes all its tools instantly.
4. **Name squatting.** A new server can't shadow `woocommerce__create_product`, because every tool is
   namespaced by its own unique slug.
5. **Resource abuse / hanging responses.** Hard timeouts on probes and calls, response handling is async, and
   one bad upstream can't stall the others.
6. **Network reach.** The gateway pod's *egress* is open (it must reach public MCP servers and the LLM).
   Ingress to internal services is closed by NetworkPolicy, so a malicious upstream can't call back into
   our databases or MCP servers.

## Network isolation (enforced, not just documented)

* **Public:** only the Ingress controller (Traefik) on `store.<domain>` and `app.<domain>` (`/`, `/api`, `/mcp`).
* **Default-deny ingress** for every pod in the namespace (`<release>-default-deny-ingress`), then explicit allows:
  * gateway + dashboard ← Ingress controller only
  * upstream MCP servers (incl. the store's) ← pods labelled `urumi.dev/mcp-client=true` (the gateway) only
  * Postgres ← gateway only · MariaDB ← WordPress + setup Job only · WordPress ← Ingress controller + its own MCP server
* k3s/k3d enforce NetworkPolicies out of the box (embedded kube-router controller).

Proof, from inside the cluster:
```bash
# from a random pod: blocked (times out)
kubectl -n urumi run probe --rm -it --image=curlimages/curl --restart=Never -- \
  curl -m 5 http://urumi-store-mcp:8080/healthz
# from the gateway pod: allowed
kubectl -n urumi exec deploy/urumi-gateway -- python -c \
  "import httpx;print(httpx.get('http://urumi-store-mcp:8080/healthz').text)"
```

## Container and cluster hardening

* Our images run as **non-root** (uid 10001 / nginx 101), `readOnlyRootFilesystem`, all capabilities dropped,
  `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`.
* `automountServiceAccountToken: false` on every pod: no component talks to the Kubernetes API, so none gets
  API credentials (least-privilege RBAC: there's nothing to grant).
* Dashboard: strict CSP, `X-Frame-Options: DENY`, `nosniff`. Cookie API calls need an `X-Requested-With`
  header (CSRF defence on top of SameSite=Strict).
* Per-user rate limit on tool calls (default 120/min).

## Known limitations

* Audit rows store tool arguments (truncated to 2 KB), which may include customer data. Restrict DB access
  and consider redaction rules for production.
* WordPress/Apache and MariaDB use their upstream images' default users (Apache must bind :80 as root before
  dropping to `www-data`).
* Rate limiting is per gateway replica.
