#!/usr/bin/env bash
# One-shot setup + start for macOS.
#   bash ~/Documents/urumi-mcp-gateway/start-mac.sh
# Optional: GEMINI_API_KEY=xxx bash start-mac.sh   (also enables the chat agent)
set -euo pipefail
cd "$(dirname "$0")"
say() { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }

say "1/7 Restoring Makefile and CI workflow"
[ -f RENAME-ME.Makefile.txt ] && mv RENAME-ME.Makefile.txt Makefile
if [ -f RENAME-ME.ci.yml.txt ]; then mkdir -p .github/workflows && mv RENAME-ME.ci.yml.txt .github/workflows/ci.yml; fi

say "2/7 Checking Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is missing. Install it with this command, then run this script again:"
  echo '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1
fi

say "3/7 Installing k3d, kubectl, helm (skipped if present)"
for tool in k3d kubectl helm; do command -v $tool >/dev/null 2>&1 || brew install $tool; done

say "4/7 Docker"
if ! command -v docker >/dev/null 2>&1; then
  brew install --cask docker
fi
if ! docker info >/dev/null 2>&1; then
  open -a Docker || true
  echo "Waiting for Docker to start (accept any Docker prompts on screen)..."
  for i in $(seq 1 90); do docker info >/dev/null 2>&1 && break; sleep 2; done
fi
docker info >/dev/null 2>&1 || { echo "Docker is not running. Open Docker Desktop, wait for it, and re-run."; exit 1; }
MEM=$(docker info --format '{{.MemTotal}}')
if [ "$MEM" -lt 5500000000 ]; then
  echo "WARNING: Docker has only $((MEM/1024/1024)) MB memory. Set >= 6 GB in Docker Desktop > Settings > Resources."
fi

say "5/7 Local domains (may ask for your Mac password)"
grep -q "app.localhost" /etc/hosts || echo "127.0.0.1 store.localhost app.localhost" | sudo tee -a /etc/hosts >/dev/null
echo "store.localhost and app.localhost -> 127.0.0.1"

say "6/7 Cluster + images + Helm install (first time ~10 min)"
make up

if [ -n "${GEMINI_API_KEY:-}" ]; then
  say "Enabling chat agent"
  make llm-secret KEY="$GEMINI_API_KEY"
fi

say "7/7 Done"
make passwords
echo
echo "Storefront : http://store.localhost"
echo "Dashboard  : http://app.localhost"
echo "MCP URL    : http://app.localhost/mcp"
[ -z "${GEMINI_API_KEY:-}" ] && echo "Chat agent : run  make llm-secret KEY=<your Gemini key>  to enable it"
open http://app.localhost || true
