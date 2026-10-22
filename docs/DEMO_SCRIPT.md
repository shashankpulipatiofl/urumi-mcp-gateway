# Demo script (definition of done, two users)

**Cast:** **Alice** = user A = admin (`admin@urumi.dev`). **Bob** = user B = member (invited during the demo).
Run `make passwords` to get Alice's password. Keep two browser windows: a normal one for Alice and a private
one for Bob.

> Tip: run `kubectl -n urumi get pods -w` in a side terminal throughout. It makes the Kubernetes part visible.

## 0. Show the system (1 min)
* `make status`: namespace, pods, Services (all ClusterIP), 2 Ingresses, PVCs, NetworkPolicies.
* Dashboard → **MCP Servers**: WooCommerce (pre-registered), fx-rates, team-notes, all **Connected**, tools listed.

> Before recording: place a fresh order on the storefront so "today's orders" is not empty, and make sure the
> chat agent answers (Groq is fastest: `bash scripts/use-groq.sh`).

## 1. Store (2 min)
1. Open http://store.localhost → *Shop* → add **Classic Cotton T-Shirt** to cart → Checkout →
   fill an address → **Cash on delivery** → *Place order*.
2. Open http://store.localhost/wp-admin → WooCommerce → **Orders**: the order is there.

## 2. Chat agent as user A (Alice) (2 min)
1. Dashboard → **Chat Agent**: *"Create a product called Urumi Hoodie priced at 1499"*
   → tool chip `woocommerce__create_product`.
2. Refresh the storefront → **Urumi Hoodie** is listed.
3. Buy the hoodie on the storefront by hand (COD).
4. Chat: *"What orders came in today?"* → `woocommerce__list_orders {period: today}` → shows the order as a table.
   Point at the timing line under the answer: the gateway + tools take ~0.1 s, the rest is the LLM.
5. **Audit Log**: both calls, user `admin@urumi.dev`, source *chat agent*.

## 3. Team: invite Bob (1 min)
1. **Team** → invite `bob@urumi.dev` as **member** → copy the link → open it in the private window → set name + password.
2. As Bob → **API Keys** → *Generate key* → copy the **Claude Code** snippet.
   Point out: the key is shown once and stored hashed.

## 4. External MCP client as user B (Bob) (3 min)
```bash
claude mcp add --transport http urumi-gateway http://app.localhost/mcp --header "Authorization: Bearer mgw_…"
claude          # start a NEW session after adding the server, then /mcp shows "urumi-gateway ✔ 14 tools"
```
(or `npx @modelcontextprotocol/inspector` → Streamable HTTP → URL + header)
1. Tools of every enabled server are visible: `woocommerce__*`, `fx-rates__*`, `team-notes__*`.
   `woocommerce__create_product` is **not** listed for Bob (admin-only tool permission).
2. Ask: *"What orders came in today?"* → same answer as the chat agent.
3. **Two upstreams in one prompt:** *"List today's orders, convert the total to USD and save a short summary
   as a team note tagged daily."* → `woocommerce__list_orders` + `fx-rates__convert_currency` + `team-notes__add_note`.

## 5. Team and control (3 min)
1. **Revoke Bob's key** (Alice → API Keys → *Show every user's keys* → Revoke). Bob's next call fails with
   **401 "This API key has been revoked"**.
2. **Disable fx-rates** (MCP Servers → Disable). Chat agent: *"convert 100 INR to USD"* → it no longer has
   the tool. External client: re-list tools (Inspector *List Tools* / Claude Code `/mcp` reconnect) → `fx-rates__*` gone.
   Enable it again.
3. **Kill an upstream pod:**
   ```bash
   kubectl -n urumi scale deploy/urumi-team-notes --replicas=0   # "dead", stays dead for the demo
   # or: kubectl -n urumi delete pod -l app.kubernetes.io/component=team-notes  (Kubernetes recreates it in seconds)
   ```
   Within 15s the dashboard shows **team-notes: Unreachable** with the reason. WooCommerce + fx-rates keep
   working in chat and over MCP. Scale back to 1 → it turns **Connected** again.
4. *(optional)* **Timeout:** chat *"call fx-rates slow_operation for 30 seconds"* → clear timeout error after 20s.
5. **Audit Log**: every call above, filter by user (Alice vs Bob) and source (chat vs MCP client); failed calls show the error.

## 6. Talking points for the rest of the video
* **Kubernetes:** [KUBERNETES.md](KUBERNETES.md): namespaces, Secrets, volumes, Ingress, probes, limits, what is public.
* **MCP internals:** [ARCHITECTURE.md](ARCHITECTURE.md): the request trace, collisions, timeouts, dead upstream.
* **Security:** [SECURITY.md](SECURITY.md): key hashing, credential encryption, member vs admin, malicious server.
* **Chat agent:** the tool loop + internal token = acts as the logged-in user.
* **Local → VPS:** [DEPLOY_VPS.md](DEPLOY_VPS.md): same chart, `values-prod.yaml`.

Automated version of most of this: `make e2e`.
