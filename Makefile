# ---------------------------------------------------------------------------
# Urumi MCP Gateway - one-command local workflow (k3d + Helm)
#   make up            cluster + images + deploy (first time: ~5-10 min)
#   make llm-secret KEY=...   enable the chat agent
#   make e2e           run the end-to-end check
# ---------------------------------------------------------------------------
CLUSTER   ?= urumi
NAMESPACE ?= urumi
RELEASE   ?= urumi
TAG       ?= dev
CHART     := deploy/helm/urumi-platform
VALUES    ?= $(CHART)/values-local.yaml
IMAGES    := store woocommerce-mcp demo-upstreams gateway dashboard

.PHONY: help up cluster images import deploy hosts llm-secret passwords status logs e2e test down clean-data

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

up: cluster images import deploy ## Create cluster, build + import images, install the chart
	@$(MAKE) --no-print-directory passwords

cluster: ## Create the local k3d cluster (Traefik ingress on localhost:80/443)
	@k3d cluster list $(CLUSTER) >/dev/null 2>&1 || \
	  k3d cluster create $(CLUSTER) --agents 0 -p "80:80@loadbalancer" -p "443:443@loadbalancer" --wait
	@kubectl config use-context k3d-$(CLUSTER)

images: ## Build all container images
	docker build -t urumi/store:$(TAG)           services/store
	docker build -t urumi/woocommerce-mcp:$(TAG) services/woocommerce-mcp
	docker build -t urumi/demo-upstreams:$(TAG)  services/demo-upstreams
	docker build -t urumi/gateway:$(TAG)         services/gateway
	docker build -t urumi/dashboard:$(TAG)       services/dashboard

import: ## Load the local images into the k3d cluster (no registry needed)
	k3d image import -c $(CLUSTER) $(foreach i,$(IMAGES),urumi/$(i):$(TAG))

deploy: ## helm upgrade --install with local values
	helm dependency update $(CHART)
	helm upgrade --install $(RELEASE) $(CHART) -n $(NAMESPACE) --create-namespace -f $(VALUES) --wait --timeout 15m

hosts: ## Add store.localhost / app.localhost to /etc/hosts (needs sudo)
	@grep -q "app.localhost" /etc/hosts || echo "127.0.0.1 store.localhost app.localhost" | sudo tee -a /etc/hosts

llm-secret: ## Create/replace the LLM API key Secret: make llm-secret KEY=xxx
	@test -n "$(KEY)" || (echo "usage: make llm-secret KEY=your-api-key" && exit 1)
	kubectl -n $(NAMESPACE) create secret generic $(RELEASE)-llm --from-literal=api-key=$(KEY) --dry-run=client -o yaml | kubectl apply -f -
	kubectl -n $(NAMESPACE) rollout restart deploy/$(RELEASE)-gateway

passwords: ## Print the generated admin passwords
	@echo "Dashboard  http://app.localhost   admin@urumi.dev / $$(kubectl -n $(NAMESPACE) get secret $(RELEASE)-platform-secrets -o jsonpath='{.data.admin-password}' | base64 -d)"
	@echo "WordPress  http://store.localhost/wp-admin   admin / $$(kubectl -n $(NAMESPACE) get secret $(RELEASE)-store-secrets -o jsonpath='{.data.wp-admin-password}' | base64 -d)"

status: ## Pods, services, ingresses, volumes
	kubectl -n $(NAMESPACE) get pods,svc,ingress,pvc,networkpolicy

logs: ## Tail gateway logs
	kubectl -n $(NAMESPACE) logs -f deploy/$(RELEASE)-gateway

e2e: ## Run the end-to-end check against http://app.localhost
	@test -d .venv || (python3 -m venv .venv && .venv/bin/pip install -q -r scripts/requirements.txt)
	.venv/bin/python scripts/e2e_check.py --base http://app.localhost --admin-email admin@urumi.dev \
	  --admin-password "$$(kubectl -n $(NAMESPACE) get secret $(RELEASE)-platform-secrets -o jsonpath='{.data.admin-password}' | base64 -d)"

test: ## Unit/integration tests (no cluster needed)
	@test -d .venv-test || (python3 -m venv .venv-test && .venv-test/bin/pip install -q -r services/gateway/requirements.txt -r services/gateway/requirements-dev.txt)
	cd services/gateway && ../../.venv-test/bin/python -m pytest -q
	cd services/woocommerce-mcp && ../../.venv-test/bin/python -m pytest -q

down: ## Delete the whole k3d cluster
	k3d cluster delete $(CLUSTER)
