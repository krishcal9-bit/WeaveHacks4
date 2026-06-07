"""
``atlas:orch:*`` — namespace + key map for the Atlas orchestration engine.

This module is the single source of truth for every Redis key the orchestration
subsystem owns. It is deliberately **pure** — no Redis connection, no heavy
imports — so it is safe to import offline and unit-test in isolation.

Ownership: the orchestration engine owns the ``atlas:orch:`` subtree ONLY. It
never reads or writes the financial-OS (``atlas:company``, ``atlas:vendor``,
``atlas:po``, ``atlas:idx:vendors|policies|...``), governance
(``atlas:govpolicy|approval|obligation:*``), or decision-stream
(``atlas:stream:decisions``) namespaces owned by sibling workstreams — see
CLAUDE.md and the repo's concurrent-workstreams rules.
"""

from __future__ import annotations

# Root namespace. Mirrors ``redis_layer.NS = "atlas"`` but kept local so this
# module has zero import-time dependency on redis_layer.
NS = "atlas"
ORCH = f"{NS}:orch"

# Schema version — bump to force orchestration index rebuilds on the next seed.
SCHEMA_VERSION = 1
SCHEMA_VERSION_KEY = f"{ORCH}:meta:schema_version"

# --------------------------------------------------------------------------- #
# JSON documents
# --------------------------------------------------------------------------- #
RUN_PREFIX = f"{ORCH}:run:"            # atlas:orch:run:<run_id>        OrchestrationTrace
TOPOLOGY_PREFIX = f"{ORCH}:topology:"  # atlas:orch:topology:<topo_id>  Topology (versioned)
THREAD_PREFIX = f"{ORCH}:thread:"      # atlas:orch:thread:<thread_id>  checkpoint index
EVAL_PREFIX = f"{ORCH}:eval:"          # atlas:orch:eval:<eval_id>      eval result
PROMOTION_PREFIX = f"{ORCH}:promotion:"  # atlas:orch:promotion:<topo_id> promotion record

# --------------------------------------------------------------------------- #
# Checkpoints — durable / branchable / time-travelable debate state
#   atlas:orch:ckpt:<thread_id>:<checkpoint_id>  -> JSON snapshot
# --------------------------------------------------------------------------- #
CKPT_PREFIX = f"{ORCH}:ckpt:"

# --------------------------------------------------------------------------- #
# Episodic memory — vector RAG over prior decisions/outcomes (HASH docs)
# --------------------------------------------------------------------------- #
MEMORY_PREFIX = f"{ORCH}:memory:"      # HASH atlas:orch:memory:<record_id>
MEMORY_INDEX = f"{ORCH}:idx:memory"    # vector (HNSW/COSINE) index over memory hashes

# --------------------------------------------------------------------------- #
# Search indices over JSON docs
# --------------------------------------------------------------------------- #
TOPOLOGY_INDEX = f"{ORCH}:idx:topologies"
RUN_INDEX = f"{ORCH}:idx:runs"

# --------------------------------------------------------------------------- #
# Streams + pub/sub — sub-agent message bus + append-only event log.
# Kept under the atlas:orch: subtree (built directly, not via redis_layer's
# atlas:stream:* helper) so the whole namespace stays self-contained.
# --------------------------------------------------------------------------- #
EVENT_STREAM_KEY = f"{ORCH}:stream:events"  # append-only orchestration event log
BUS_STREAM_KEY = f"{ORCH}:stream:bus"       # sub-agent message bus (consumer groups)
BUS_GROUP = "orch-subagents"                # consumer-group name on the bus stream
# redis_layer.publish(channel) builds ``atlas:<channel>``; "orch:bus" -> atlas:orch:bus
PUBSUB_CHANNEL = "orch:bus"


# --------------------------------------------------------------------------- #
# Key builders
# --------------------------------------------------------------------------- #
def run_key(run_id: str) -> str:
    return f"{RUN_PREFIX}{run_id}"


def topology_key(topology_id: str) -> str:
    return f"{TOPOLOGY_PREFIX}{topology_id}"


def thread_key(thread_id: str) -> str:
    return f"{THREAD_PREFIX}{thread_id}"


def eval_key(eval_id: str) -> str:
    return f"{EVAL_PREFIX}{eval_id}"


def promotion_key(topology_id: str) -> str:
    return f"{PROMOTION_PREFIX}{topology_id}"


def checkpoint_key(thread_id: str, checkpoint_id: str) -> str:
    return f"{CKPT_PREFIX}{thread_id}:{checkpoint_id}"


def checkpoint_scan_pattern(thread_id: str) -> str:
    return f"{CKPT_PREFIX}{thread_id}:*"


def memory_key(record_id: str) -> str:
    return f"{MEMORY_PREFIX}{record_id}"


def is_orch_key(key: str) -> bool:
    """Guard used by the store before any write: only ever touch atlas:orch:*."""
    return key.startswith(f"{ORCH}:")


# --------------------------------------------------------------------------- #
# Introspection — a documented map of the subtree (powers /api/orchestration/map)
# --------------------------------------------------------------------------- #
def key_map() -> dict[str, dict[str, str]]:
    return {
        "json": {
            "runs": f"{RUN_PREFIX}<run_id>",
            "topologies": f"{TOPOLOGY_PREFIX}<topology_id>",
            "threads": f"{THREAD_PREFIX}<thread_id>",
            "evals": f"{EVAL_PREFIX}<eval_id>",
            "promotions": f"{PROMOTION_PREFIX}<topology_id>",
            "checkpoints": f"{CKPT_PREFIX}<thread_id>:<checkpoint_id>",
        },
        "vector": {"memory": f"{MEMORY_PREFIX}<record_id> (indexed by {MEMORY_INDEX})"},
        "search": {"topologies": TOPOLOGY_INDEX, "runs": RUN_INDEX},
        "streams": {"events": EVENT_STREAM_KEY, "bus": f"{BUS_STREAM_KEY} (group {BUS_GROUP})"},
        "pubsub": {"bus": f"{NS}:{PUBSUB_CHANNEL}"},
        "meta": {"schema_version": SCHEMA_VERSION_KEY},
    }
