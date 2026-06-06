#!/usr/bin/env bash
set -euo pipefail

# Import finance-operations feeds and run reconciliation against the seeded
# company system of record. Connectors are file-based and independent of the
# LLM/Weave stack, so this only requires REDIS_URL (not the sponsor model keys).
#
#   scripts/import-operations.sh             # import env-configured connectors, then reconcile
#   scripts/import-operations.sh --demo      # import the bundled Acme demo fixtures, then reconcile
#   scripts/import-operations.sh --connector invoices --file /path/to/ap.csv
#
# Any arguments are forwarded to the connector CLI's `import` subcommand.

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

if [[ -z "${REDIS_URL:-}" ]]; then
  echo "Missing required env key: REDIS_URL" >&2
  exit 1
fi

echo "[import-operations] Importing finance-operations connectors..."
uv run --directory "${REPO_ROOT}/agent" python -m src.integrations.cli import "$@"

echo "[import-operations] Reconciling imported data against the company system of record..."
uv run --directory "${REPO_ROOT}/agent" python -m src.integrations.cli reconcile

echo "[import-operations] Done. Query /api/connectors, /api/sources, /api/reconciliation for results."
