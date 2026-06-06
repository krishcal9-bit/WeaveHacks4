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

uv run --directory "${REPO_ROOT}/agent" python - <<'PY'
import socket

for host in ("api.openai.com", "api.wandb.ai"):
    try:
        socket.getaddrinfo(host, 443)
    except OSError as exc:
        raise SystemExit(f"Sponsor host is not resolvable: {host} ({exc})")
PY

uv run --directory "${REPO_ROOT}/agent" python -m src.data.seed
