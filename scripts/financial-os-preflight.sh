#!/usr/bin/env bash
# Financial-OS preflight (Goal 3): validate that Redis Stack is the live financial
# operating database — modules, search/vector indexes, seeded collection counts,
# stream length, vector docs, and live scenario-branch creation. Any failure exits
# non-zero. Run after scripts/seed-live.sh; invoked by scripts/live-preflight.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

uv run --directory "${REPO_ROOT}/agent" python - <<'PY'
from src import redis_models as M
from src import redis_store as S
from src import scenario_engine as E

errors: list[str] = []

mods = S.modules()
if not any(("json" in n or "rejson" in n) for n in mods):
    errors.append("RedisJSON module missing")
if not any("search" in n for n in mods):
    errors.append("RediSearch module missing")

report = {r["name"]: r for r in S.index_report()}
expected_indexes = [spec.name for spec in M.ALL_INDEX_SPECS]
for name in expected_indexes:
    row = report.get(name)
    if not row or not row["exists"]:
        errors.append(f"index missing: {name}")
    elif row["num_docs"] < 1:
        errors.append(f"index {name} has 0 indexed docs")

counts = S.collection_counts()
for label in ("company", "vendors", "departments", "invoices", "purchase_orders", "contracts", "arr_movements", "knowledge"):
    if counts.get(label, 0) < 1:
        errors.append(f"collection '{label}' is empty (run scripts/seed-live.sh)")

streams = S.stream_report()
if streams.get("decisions", 0) < 1:
    errors.append("decisions stream is empty")

if S.knowledge_count() < 1:
    errors.append("knowledge vector corpus is empty")

# Live scenario-branch creation: fork the company, compute metrics, confirm it is
# retrievable through the RediSearch index, then clean it up.
try:
    scenario = E.create_scenario(
        "preflight-check",
        [{"type": "hire", "roles": 1, "monthly_cost": 12000}],
        tags=["preflight"],
    )
    if scenario.projected.runway_months is None or scenario.projected.monthly_net_burn <= 0:
        errors.append("scenario metrics not computed")
    if not any(d.get("id") == scenario.id for d in E.list_scenarios(limit=200)):
        errors.append("created scenario not retrievable via the search index")
    E.delete_scenario(scenario.id)
except Exception as exc:
    errors.append(f"scenario branch creation failed: {exc}")

if errors:
    raise SystemExit("Financial-OS preflight FAILED:\n  - " + "\n  - ".join(errors))

idx_summary = ", ".join(f"{name.rsplit(':', 1)[-1]}={report[name]['num_docs']}" for name in expected_indexes)
print("Financial-OS preflight passed:")
print(f"  Redis Stack modules: {', '.join(sorted(mods))}")
print(f"  indexes ({len(expected_indexes)}): {idx_summary}")
print(f"  collections: {counts}")
print(f"  streams: {streams}")
print(f"  knowledge vector docs: {S.knowledge_count()} | scenario branches: {S.scenario_count()}")
print("  scenario branch creation: OK (forked, computed runway/burn-multiple, indexed, deleted)")
PY
