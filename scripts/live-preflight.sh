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
for key in \
  OPENAI_API_KEY \
  LLM_PROVIDER \
  LLM_MODEL \
  LLM_REASONING_EFFORT \
  LLM_TEXT_VERBOSITY \
  OPENAI_SERVICE_TIER \
  OPENAI_REALTIME_MODEL \
  OPENAI_REALTIME_REASONING_EFFORT \
  OPENAI_REALTIME_VOICE \
  WANDB_API_KEY \
  WANDB_PROJECT \
  REDIS_URL; do
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
import os
import socket

for host in ("api.openai.com", "api.wandb.ai"):
    try:
        socket.getaddrinfo(host, 443)
    except OSError as exc:
        raise SystemExit(f"Sponsor host is not resolvable: {host} ({exc})")

from src import redis_layer as R
from openai import OpenAI

client = R.client()
client.ping()
client.execute_command("JSON.GET", "__atlas:missing__")
client.execute_command("FT._LIST")
OpenAI().models.retrieve(os.environ["LLM_MODEL"])
OpenAI().models.retrieve(os.environ["OPENAI_REALTIME_MODEL"])
if os.environ["OPENAI_SERVICE_TIER"] != "priority":
    raise SystemExit(f"OPENAI_SERVICE_TIER must be priority for the live council demo, got {os.environ['OPENAI_SERVICE_TIER']!r}")
print("Sponsor network preflight passed: api.openai.com and api.wandb.ai resolve.")
print("OpenAI model preflight passed: reasoning and realtime models are resolvable.")
print("Redis Stack preflight passed: PING, RedisJSON, RediSearch.")
PY

# Finance-operations connectors are OPTIONAL: report their configuration but never
# fail the core preflight when they are absent (they default to "not configured").
uv run --directory "${REPO_ROOT}/agent" python - <<'PY' || echo "Connector report skipped (non-fatal)."
try:
    from src.integrations import service as OPS

    statuses = OPS.connector_statuses()
    configured = [s for s in statuses if s["configured"]]
    imported = [s for s in statuses if s.get("status") in ("imported", "partial", "skipped_unchanged") and (s.get("record_count") or 0) > 0]
    recon = OPS.reconciliation_summary()
    print("Finance-operations connectors (optional; not required for the core demo):")
    for s in statuses:
        flag = "configured" if s["configured"] else "not configured"
        print(f"  - {s['source_type']:<18} [{flag:<14}] env {s['env_var']}  status={s['status']}")
    recon_status = recon.get("status") if recon else "not run"
    print(
        f"Connectors: {len(configured)} configured, {len(imported)} source(s) imported, "
        f"confidence {OPS.import_confidence().score}/100. Reconciliation: {recon_status}."
    )
except Exception as exc:  # never fail the core preflight on the optional layer
    print(f"Connector report unavailable (non-fatal): {exc}")
PY

# Financial-OS preflight (REQUIRED): Redis Stack modules, financial search/vector
# indexes, seeded counts, stream length, vector docs, and scenario-branch creation.
"${SCRIPT_DIR}/financial-os-preflight.sh"

echo "Live preflight passed: root .env keys present, tools available, OpenAI models resolvable, Redis Stack ready."
