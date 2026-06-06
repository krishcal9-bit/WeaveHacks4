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

for command in npm uv docker; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Missing required command: ${command}" >&2
    exit 1
  fi
done

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running. Start Docker Desktop before live setup." >&2
  exit 1
fi

if [[ ! -f "${REPO_ROOT}/frontend/package-lock.json" ]]; then
  echo "Missing frontend/package-lock.json; npm ci requires the lockfile." >&2
  exit 1
fi

uv run --directory "${REPO_ROOT}/agent" python - <<'PY'
import socket

for host in ("api.openai.com", "api.wandb.ai"):
    try:
        socket.getaddrinfo(host, 443)
    except OSError as exc:
        raise SystemExit(f"Sponsor host is not resolvable: {host} ({exc})")

from src import redis_layer as R

client = R.client()
client.ping()
client.execute_command("JSON.GET", "__atlas:missing__")
client.execute_command("FT._LIST")
print("Sponsor network preflight passed: api.openai.com and api.wandb.ai resolve.")
print("Redis Stack preflight passed: PING, RedisJSON, RediSearch.")
PY

echo "Live preflight passed: root .env keys present, tools available, Redis Stack ready."
