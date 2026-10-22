#!/usr/bin/env bash
# Switch the chat agent to Groq (and keep it there on future deploys of this script).
#   bash scripts/use-groq.sh                 # uses the key already stored in the urumi-llm Secret
#   bash scripts/use-groq.sh gsk_your_key    # also stores/replaces the key
set -euo pipefail
cd "$(dirname "$0")/.."
NS=urumi; REL=urumi; CHART=deploy/helm/urumi-platform
if [ -n "${1:-}" ]; then
  kubectl -n $NS create secret generic $REL-llm --from-literal=api-key="$1" --dry-run=client -o yaml | kubectl apply -f -
fi
helm dependency update $CHART >/dev/null
helm upgrade --install $REL $CHART -n $NS -f $CHART/values-local.yaml -f $CHART/values-groq.yaml --wait --timeout 15m
kubectl -n $NS rollout restart deploy/$REL-gateway
kubectl -n $NS rollout status deploy/$REL-gateway
echo "Chat agent now uses Groq ($(grep '^  model:' $CHART/values-groq.yaml | awk '{print $2}'))."
