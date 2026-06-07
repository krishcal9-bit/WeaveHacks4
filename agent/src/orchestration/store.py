"""
orchestration/store.py — the ``atlas:orch:*`` Redis layer.

Builds ONLY on the stable public API of ``src.redis_layer`` (``client``,
``embed_texts``, ``to_bytes``, ``get_json``/``set_json``, ``keys``, ``publish``)
and owns the ``atlas:orch:*`` subtree:

  * durable, branchable, time-travelable debate CHECKPOINTS  (RedisJSON)
  * episodic MEMORY vector index (HNSW / COSINE / 1536-dim over HASH) for precedent recall
  * versioned TOPOLOGY + RUN JSON stores, each with a RediSearch index
  * an append-only EVENT stream and a sub-agent message BUS (consumer groups)
  * pub/sub fan-out on ``atlas:orch:bus``

Connection + index creation are lazy, so importing this module touches nothing;
only calling a function opens the (process-wide) Redis connection. Every write is
guarded by ``namespace.is_orch_key`` so the store can only ever touch its own subtree.
"""

import json
from typing import Any

from src import redis_layer as R
from src.orchestration import models as M
from src.orchestration import namespace as ns

EMBED_DIM = R.EMBED_DIM  # 1536


def _client():
    return R.client()


def _guard(key: str) -> str:
    if not ns.is_orch_key(key):
        raise ValueError(f"orchestration store refused a non-orch key: {key!r}")
    return key


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def _flatten(data: dict) -> dict:
    return {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in data.items()}


def _parse_fields(fields: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in fields.items():
        try:
            out[k] = json.loads(v)
        except Exception:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# Indices + migrations
# --------------------------------------------------------------------------- #
def _index_types():
    try:
        from redis.commands.search.index_definition import IndexDefinition, IndexType
    except Exception:  # older redis-py module name
        from redis.commands.search.indexDefinition import (  # type: ignore
            IndexDefinition,
            IndexType,
        )
    return IndexDefinition, IndexType


def _create_index(index: str, schema, prefix: str, *, hash_type: bool) -> None:
    import redis

    IndexDefinition, IndexType = _index_types()
    index_type = IndexType.HASH if hash_type else IndexType.JSON
    try:
        _client().ft(index).create_index(
            schema,
            definition=IndexDefinition(prefix=[prefix], index_type=index_type),
        )
    except redis.exceptions.ResponseError as exc:
        if "Index already exists" not in str(exc):
            raise


def _ensure_memory_index() -> None:
    from redis.commands.search.field import TagField, TextField, VectorField

    schema = (
        TextField("text"),
        TagField("decision_type"),
        TagField("company_id"),
        TextField("created_at"),
        VectorField(
            "embedding",
            "HNSW",
            {"TYPE": "FLOAT32", "DIM": EMBED_DIM, "DISTANCE_METRIC": "COSINE"},
        ),
    )
    _create_index(ns.MEMORY_INDEX, schema, ns.MEMORY_PREFIX, hash_type=True)


def _ensure_topology_index() -> None:
    from redis.commands.search.field import NumericField, TagField, TextField

    schema = (
        TextField("$.name", as_name="name"),
        TagField("$.decision_type", as_name="decision_type"),
        NumericField("$.version", as_name="version"),
        TextField("$.description", as_name="description"),
        TextField("$.created_at", as_name="created_at"),
    )
    _create_index(ns.TOPOLOGY_INDEX, schema, ns.TOPOLOGY_PREFIX, hash_type=False)


def _ensure_run_index() -> None:
    from redis.commands.search.field import NumericField, TagField, TextField

    schema = (
        TagField("$.decision_type", as_name="decision_type"),
        TextField("$.decision", as_name="decision"),
        TextField("$.topology_name", as_name="topology_name"),
        NumericField("$.cost_usd", as_name="cost_usd"),
        NumericField("$.latency_ms", as_name="latency_ms"),
        TextField("$.created_at", as_name="created_at"),
    )
    _create_index(ns.RUN_INDEX, schema, ns.RUN_PREFIX, hash_type=False)


def ensure_indices() -> None:
    _ensure_memory_index()
    _ensure_topology_index()
    _ensure_run_index()


def _drop_indices() -> None:
    for idx in (ns.MEMORY_INDEX, ns.TOPOLOGY_INDEX, ns.RUN_INDEX):
        try:
            _client().ft(idx).dropindex(delete_documents=False)
        except Exception:
            pass


def schema_version() -> int:
    raw = _client().get(ns.SCHEMA_VERSION_KEY)
    return int(raw) if raw else 0


def run_migrations() -> dict:
    """Idempotent: rebuild indices when SCHEMA_VERSION bumps, then ensure them."""
    current = schema_version()
    rebuilt = False
    if current != ns.SCHEMA_VERSION:
        _drop_indices()
        rebuilt = True
    ensure_indices()
    _client().set(ns.SCHEMA_VERSION_KEY, ns.SCHEMA_VERSION)
    return {"from": current, "to": ns.SCHEMA_VERSION, "rebuilt": rebuilt}


# --------------------------------------------------------------------------- #
# Checkpoints — durable / branchable / time-travelable debate state
# --------------------------------------------------------------------------- #
def save_checkpoint(
    thread_id: str,
    state: Any,
    *,
    parent_id: str | None = None,
    label: str = "",
    node: str = "",
) -> str:
    ckpt_id = M.new_id("ckpt")
    ts = M.now_iso()
    snapshot = {
        "checkpoint_id": ckpt_id,
        "thread_id": thread_id,
        "parent_id": parent_id,
        "label": label,
        "node": node,
        "created_at": ts,
        "state": _jsonable(state),
    }
    R.set_json(_guard(ns.checkpoint_key(thread_id, ckpt_id)), snapshot)

    index_key = _guard(ns.thread_key(thread_id))
    index = R.get_json(index_key) or {
        "thread_id": thread_id,
        "head": None,
        "checkpoints": [],
        "created_at": ts,
    }
    index["checkpoints"].append(
        {"checkpoint_id": ckpt_id, "created_at": ts, "label": label, "node": node, "parent_id": parent_id}
    )
    index["head"] = ckpt_id
    index["updated_at"] = ts
    R.set_json(index_key, index)
    return ckpt_id


def load_checkpoint(thread_id: str, checkpoint_id: str) -> dict | None:
    return R.get_json(ns.checkpoint_key(thread_id, checkpoint_id))


def list_checkpoints(thread_id: str) -> list[dict]:
    index = R.get_json(ns.thread_key(thread_id)) or {}
    return index.get("checkpoints", [])


def latest_checkpoint(thread_id: str) -> dict | None:
    index = R.get_json(ns.thread_key(thread_id)) or {}
    head = index.get("head")
    return load_checkpoint(thread_id, head) if head else None


def branch_checkpoint(
    thread_id: str,
    checkpoint_id: str,
    *,
    new_thread_id: str | None = None,
    label: str = "branch",
) -> str | None:
    """Fork a new thread seeded with a copy of an existing checkpoint's state."""
    snapshot = load_checkpoint(thread_id, checkpoint_id)
    if not snapshot:
        return None
    new_thread = new_thread_id or M.new_id("thread")
    save_checkpoint(
        new_thread,
        snapshot.get("state", {}),
        parent_id=f"{thread_id}:{checkpoint_id}",
        label=label,
        node=snapshot.get("node", ""),
    )
    return new_thread


# --------------------------------------------------------------------------- #
# Episodic memory — vector recall of prior decisions cited as precedent
# --------------------------------------------------------------------------- #
def remember(record: M.EpisodicMemoryRecord) -> str:
    text = record.embedding_text()
    vector = R.embed_texts([text])[0]
    _client().hset(
        _guard(ns.memory_key(record.id)),
        mapping={
            "text": text,
            "decision_type": record.decision_type or "general",
            "company_id": record.company_id or "northwind",
            "created_at": record.created_at,
            "record_json": record.model_dump_json(),
            "embedding": R.to_bytes(vector),
        },
    )
    return record.id


def recall(query: str, k: int = 4, decision_type: str | None = None) -> list[dict]:
    """Hybrid (filter + KNN) recall of prior decisions from episodic memory."""
    from redis.commands.search.query import Query

    qvec = R.to_bytes(R.embed_texts([query])[0])
    prefilter = f"@decision_type:{{{decision_type}}}" if decision_type else "*"
    q = (
        Query(f"{prefilter}=>[KNN {k} @embedding $vec AS score]")
        .sort_by("score")
        .return_fields("record_json", "decision_type", "created_at", "score")
        .paging(0, k)
        .dialect(2)
    )
    res = _client().ft(ns.MEMORY_INDEX).search(q, query_params={"vec": qvec})
    out: list[dict] = []
    for doc in res.docs:
        try:
            record = json.loads(doc.record_json)
        except Exception:
            record = {"id": doc.id}
        record["_score"] = float(doc.score)
        out.append(record)
    return out


# --------------------------------------------------------------------------- #
# Topology store (versioned, searchable)
# --------------------------------------------------------------------------- #
def save_topology(topology: M.Topology) -> str:
    R.set_json(_guard(ns.topology_key(topology.id)), topology.model_dump(mode="json"))
    return topology.id


def get_topology(topology_id: str) -> M.Topology | None:
    doc = R.get_json(ns.topology_key(topology_id))
    return M.Topology(**doc) if doc else None


def list_topologies(decision_type: str | None = None) -> list[M.Topology]:
    out: list[M.Topology] = []
    for key in R.keys(f"{ns.TOPOLOGY_PREFIX}*"):
        doc = R.get_json(key)
        if not doc:
            continue
        if decision_type and doc.get("decision_type") not in (decision_type, "general"):
            continue
        out.append(M.Topology(**doc))
    return out


# --------------------------------------------------------------------------- #
# Run / trace store
# --------------------------------------------------------------------------- #
def save_trace(trace: M.OrchestrationTrace) -> str:
    R.set_json(_guard(ns.run_key(trace.run_id)), trace.model_dump(mode="json"))
    return trace.run_id


def get_trace(run_id: str) -> dict | None:
    return R.get_json(ns.run_key(run_id))


def list_traces(limit: int = 25) -> list[dict]:
    docs = []
    for key in R.keys(f"{ns.RUN_PREFIX}*"):
        doc = R.get_json(key)
        if doc:
            docs.append(doc)
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return docs[:limit]


# --------------------------------------------------------------------------- #
# Eval + promotion store
# --------------------------------------------------------------------------- #
def save_eval(result: M.OrchestrationEvalResult) -> str:
    R.set_json(_guard(ns.eval_key(result.eval_id)), result.model_dump(mode="json"))
    return result.eval_id


def list_evals(limit: int = 25) -> list[dict]:
    docs = [R.get_json(k) for k in R.keys(f"{ns.EVAL_PREFIX}*")]
    docs = [d for d in docs if d]
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return docs[:limit]


def set_promotion(topology_id: str, record: dict) -> None:
    R.set_json(_guard(ns.promotion_key(topology_id)), record)


def get_promotion(topology_id: str) -> dict | None:
    return R.get_json(ns.promotion_key(topology_id))


def save_hierarchical(trace: M.HierarchicalTrace) -> str:
    R.set_json(_guard(ns.hrun_key(trace.run_id)), trace.model_dump(mode="json"))
    return trace.run_id


def get_hierarchical(run_id: str) -> dict | None:
    return R.get_json(ns.hrun_key(run_id))


def list_hierarchical(limit: int = 25) -> list[dict]:
    docs = [R.get_json(k) for k in R.keys(f"{ns.HRUN_PREFIX}*")]
    docs = [d for d in docs if d]
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return docs[:limit]


# --------------------------------------------------------------------------- #
# Event stream + sub-agent message bus (consumer groups) + pub/sub fan-out
# --------------------------------------------------------------------------- #
def emit_event(event: dict) -> str:
    return _client().xadd(ns.EVENT_STREAM_KEY, _flatten(event))


def read_events(count: int = 25) -> list[dict]:
    rows = _client().xrevrange(ns.EVENT_STREAM_KEY, count=count)
    events = []
    for event_id, fields in rows:
        parsed = {"_id": event_id, **_parse_fields(fields)}
        events.append(parsed)
    return events


def ensure_bus_group() -> None:
    try:
        _client().xgroup_create(ns.BUS_STREAM_KEY, ns.BUS_GROUP, id="0", mkstream=True)
    except Exception as exc:  # BUSYGROUP = already created
        if "BUSYGROUP" not in str(exc):
            raise


def bus_send(message: dict) -> str:
    return _client().xadd(ns.BUS_STREAM_KEY, _flatten(message))


def bus_consume(consumer: str, count: int = 10, block: int | None = None) -> list[dict]:
    """Read + ack messages off the sub-agent bus for one consumer in the group."""
    ensure_bus_group()
    resp = _client().xreadgroup(
        ns.BUS_GROUP, consumer, {ns.BUS_STREAM_KEY: ">"}, count=count, block=block
    )
    out: list[dict] = []
    if not resp:
        return out
    for _stream, entries in resp:
        for entry_id, fields in entries:
            out.append({"_id": entry_id, **_parse_fields(fields)})
            _client().xack(ns.BUS_STREAM_KEY, ns.BUS_GROUP, entry_id)
    return out


def publish_bus(payload: dict) -> None:
    """Pub/sub fan-out on atlas:orch:bus (for parallel sub-debate coordination)."""
    R.publish(ns.PUBSUB_CHANNEL, payload)


# --------------------------------------------------------------------------- #
# Introspection — powers /api/orchestration/map and health
# --------------------------------------------------------------------------- #
def _index_info() -> dict:
    info: dict[str, Any] = {}
    for idx in (ns.MEMORY_INDEX, ns.TOPOLOGY_INDEX, ns.RUN_INDEX):
        try:
            data = _client().ft(idx).info()
            num = data.get("num_docs") if isinstance(data, dict) else None
            info[idx] = {"num_docs": int(num) if num is not None else None}
        except Exception:
            info[idx] = {"num_docs": None, "exists": False}
    return info


def orch_overview() -> dict:
    def count(prefix: str) -> int:
        return len(R.keys(f"{prefix}*"))

    return {
        "namespace": ns.ORCH,
        "schema_version": schema_version(),
        "counts": {
            "topologies": count(ns.TOPOLOGY_PREFIX),
            "runs": count(ns.RUN_PREFIX),
            "memory": count(ns.MEMORY_PREFIX),
            "threads": count(ns.THREAD_PREFIX),
            "checkpoints": count(ns.CKPT_PREFIX),
            "evals": count(ns.EVAL_PREFIX),
            "promotions": count(ns.PROMOTION_PREFIX),
        },
        "indices": _index_info(),
        "key_map": ns.key_map(),
    }


def orch_analytics(limit: int = 200) -> dict:
    """Aggregate analytics over persisted orchestration runs (Redis reads only —
    no model calls): cost, latency, tokens, convergence rate, decision/stop mix,
    red-team robustness, and per-topology usage. Powers /api/orchestration/observability."""
    from collections import Counter

    traces = list_traces(limit)
    n = len(traces)
    if not n:
        return {"runs": 0}
    cost = sum(float(t.get("cost_usd") or 0) for t in traces)
    latency = [int(t.get("latency_ms") or 0) for t in traces]
    rounds = [len(t.get("rounds") or []) for t in traces]
    converged = sum(1 for t in traces if (t.get("convergence") or {}).get("converged"))
    with_rt = [t for t in traces if t.get("red_team")]
    rt_satisfied = sum(1 for t in with_rt if (t.get("red_team") or {}).get("satisfied"))
    return {
        "runs": n,
        "cost_usd": {"total": round(cost, 4), "avg": round(cost / n, 4)},
        "latency_ms": {"avg": round(sum(latency) / n), "max": max(latency)},
        "tokens": {
            "input": sum(int(t.get("input_tokens") or 0) for t in traces),
            "output": sum(int(t.get("output_tokens") or 0) for t in traces),
        },
        "rounds": {"avg": round(sum(rounds) / n, 2), "max": max(rounds)},
        "convergence_rate": round(converged / n, 3),
        "red_team": {
            "runs_with_red_team": len(with_rt),
            "satisfied_rate": round(rt_satisfied / len(with_rt), 3) if with_rt else None,
        },
        "decision_mix": dict(Counter((t.get("recommendation") or {}).get("decision") or "?" for t in traces)),
        "stop_reason_mix": dict(Counter(t.get("stop_reason") or "?" for t in traces)),
        "by_topology": dict(Counter(t.get("topology_name") or "?" for t in traces)),
    }
