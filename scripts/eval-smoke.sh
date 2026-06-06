#!/usr/bin/env bash
# Atlas — W&B Weave eval smoke check.
# Creates and lists eval metadata (eval packets, replay sets, promotion
# candidates, gate decisions) and never prints WANDB_API_KEY or any secret.
#
#   scripts/eval-smoke.sh                       # full metadata smoke check
#   scripts/eval-smoke.sh packets               # list recent eval packets
#   scripts/eval-smoke.sh replay-sets --create  # build + publish a replay set
#   scripts/eval-smoke.sh promotions            # list candidates + gate decisions
#   scripts/eval-smoke.sh gate --candidate cand-treasury-treasury-v4-liquidity-stress --live
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
for key in OPENAI_API_KEY WANDB_API_KEY WANDB_PROJECT REDIS_URL; do
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

if (( $# == 0 )); then
  set -- smoke
fi

exec uv run --directory "${REPO_ROOT}/agent" python -m src.eval_cli "$@"
