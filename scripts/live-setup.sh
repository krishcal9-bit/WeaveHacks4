#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

"${SCRIPT_DIR}/start-redis-stack.sh"
npm ci --prefix "${REPO_ROOT}/frontend"
uv sync --directory "${REPO_ROOT}/agent"
"${SCRIPT_DIR}/live-preflight.sh"
"${SCRIPT_DIR}/seed-live.sh"
"${SCRIPT_DIR}/live-preflight.sh"

echo "Atlas live setup is ready. Run scripts/dev-live.sh to start FastAPI and Next.js."
