#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
CONTAINER_NAME="${ATLAS_REDIS_CONTAINER:-atlas-redis-stack}"
IMAGE="${ATLAS_REDIS_IMAGE:-redis/redis-stack-server:latest}"
REDIS_PORT="${ATLAS_REDIS_PORT:-6379}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running. Start Docker Desktop, then rerun scripts/start-redis-stack.sh." >&2
  exit 1
fi

if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  echo "Redis Stack container '${CONTAINER_NAME}' is already running."
elif docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  echo "Starting existing Redis Stack container '${CONTAINER_NAME}'."
  docker start "${CONTAINER_NAME}" >/dev/null
else
  echo "Pulling ${IMAGE}."
  docker pull "${IMAGE}" >/dev/null
  echo "Creating Redis Stack container '${CONTAINER_NAME}' on localhost:${REDIS_PORT}."
  docker run -d \
    --name "${CONTAINER_NAME}" \
    -p "${REDIS_PORT}:6379" \
    "${IMAGE}" >/dev/null
fi

for _ in {1..30}; do
  if docker exec "${CONTAINER_NAME}" redis-cli ping >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

docker exec "${CONTAINER_NAME}" redis-cli ping >/dev/null
docker exec "${CONTAINER_NAME}" redis-cli JSON.GET __atlas:missing__ >/dev/null
docker exec "${CONTAINER_NAME}" redis-cli FT._LIST >/dev/null

echo "Redis Stack is live with RedisJSON and RediSearch enabled."
