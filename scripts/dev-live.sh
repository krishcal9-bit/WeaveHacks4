#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing root .env. Create ${ENV_FILE} from agent/.env.example and fill sponsor keys." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

missing=()
for key in OPENAI_API_KEY WANDB_API_KEY REDIS_URL; do
  if [[ -z "${!key:-}" ]]; then
    missing+=("${key}")
  fi
done

if (( ${#missing[@]} > 0 )); then
  printf 'Missing required live env keys:' >&2
  printf ' %s' "${missing[@]}" >&2
  printf '\n' >&2
  exit 1
fi

BACKEND_PORT="${PORT:-8123}"
FRONTEND_PORT="${NEXT_PORT:-3000}"
export AGENT_URL="${AGENT_URL:-http://localhost:${BACKEND_PORT}}"
export NEXT_PUBLIC_AGENT_URL="${NEXT_PUBLIC_AGENT_URL:-${AGENT_URL}}"

cleanup() {
  if [[ -n "${AGENT_PID:-}" ]] && kill -0 "${AGENT_PID}" >/dev/null 2>&1; then
    kill "${AGENT_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

PORT="${BACKEND_PORT}" uv run --directory "${REPO_ROOT}/agent" python main.py &
AGENT_PID=$!

echo "FastAPI agent started on ${AGENT_URL}."
echo "Next.js dev server starting on http://localhost:${FRONTEND_PORT}."
# Next.js only — dev:ui runs concurrently with dev:agent and would bind 8123 again.
PORT="${FRONTEND_PORT}" npm --prefix "${REPO_ROOT}/frontend" exec -- next dev
