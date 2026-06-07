"""
Full demo reset — clear ephemeral runtime state and live-reseed Redis.

Used by POST /api/demo/reset so the Settings reset button restores:
  • uploaded connector / reconciliation state
  • council command-panel draft state
  • debate activity streams (decisions, audit, eval packets, …)
  • runtime approvals, obligations, plans, memos, documents
  • agent improvement / reliability overlays
  • the seeded Northwind system of record (via src.data.seed.seed)
"""

from __future__ import annotations

import os
from typing import Any

from src import agui_commands as AGUI
from src import redis_layer as R
from src import redis_store as S
from src import redis_models as M
from src.data.seed import seed
from src.documents import store as DOC_STORE
from src.integrations import service as OPS

# Append-only streams populated during live demo runs (not the seeded JSON corpora).
RUNTIME_STREAMS: tuple[str, ...] = (
    "decisions",
    "scenarios",
    "evals",
    "reconciliation",
    "audit",
    "commands",
    "agent_improvements",
    "eval_packets",
    "promotions",
    "role_distinction_evals",
    "plans",
    "portfolios",
    "stress",
)

# Ephemeral Redis keys written during debates, uploads, planning, or eval runs.
RUNTIME_KEY_PATTERNS: tuple[str, ...] = (
    f"{R.NS}:evaluation:*",
    f"{R.NS}:memo:*",
    f"{R.NS}:plan:*",
    f"{R.NS}:plans:index",
    f"{R.NS}:stress:*",
    f"{R.APPROVAL_PREFIX}*",
    f"{R.OBLIGATION_PREFIX}*",
    f"{M.SCENARIO_PREFIX}*",
    f"{M.KNOWLEDGE_PREFIX}*",
    f"{R.NS}:documents:*",
    f"{R.NS}:command_state:*",
    M.RELIABILITY_LATEST_KEY,
)


def _orchestrator_enabled() -> bool:
    return os.getenv("ATLAS_ORCHESTRATOR", "").strip().lower() in ("1", "true", "yes", "on")


def clear_runtime_state() -> dict[str, int]:
    """Delete ephemeral demo/runtime Redis keys (streams + scratch namespaces)."""
    deleted: dict[str, int] = {}

    for stream in RUNTIME_STREAMS:
        deleted[f"stream:{stream}"] = R.clear_stream(stream)

    for pattern in RUNTIME_KEY_PATTERNS:
        deleted[pattern] = R.delete_keys_matching(pattern)

    deleted["documents"] = DOC_STORE.clear_all_documents()
    deleted["cache"] = S.cache_invalidate("*")

    if _orchestrator_enabled():
        from src.orchestration import namespace as orch_ns

        deleted[f"{orch_ns.ORCH}:*"] = R.delete_keys_matching(f"{orch_ns.ORCH}:*")

    return deleted


def full_demo_reset(*, verbose: bool = False) -> dict[str, Any]:
    """Clear uploaded + runtime demo state, then live-reseed Redis."""
    deleted: dict[str, int] = {}

    ops_payload = OPS.reset_demo_state()
    deleted.update(ops_payload.get("deleted") or {})

    command_key = AGUI.command_state_key()
    deleted[command_key] = R.delete_key(command_key)
    deleted.update(clear_runtime_state())

    reseed = seed(verbose=verbose)

    return {
        "status": "reset",
        "scope": "full",
        "deleted": deleted,
        "connectors": ops_payload.get("connectors") or [],
        "confidence": ops_payload.get("confidence") or {},
        "command_state": AGUI.default_command_state(),
        "reseed": reseed,
    }
