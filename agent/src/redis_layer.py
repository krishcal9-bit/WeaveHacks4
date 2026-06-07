"""
Redis layer for Atlas — the financial system of record + agent memory.

Redis is used extensively and is load-bearing here:
  • RedisJSON documents .......... the company's financial system of record
  • RediSearch index (vendors) ... structured queries over contracts/vendors
  • Vector index (policies) ...... semantic RAG over finance policy & past decisions
  • Streams ...................... append-only decision/debate event log
  • Pub/Sub ...................... live dashboard updates when a decision concludes
  • Plain keys (TTL) ............. caching of computed metrics / canonical demos

Search/vector helpers use lazy imports so the core key/JSON/stream/pubsub API
keeps working even if a RediSearch API detail differs across redis-py versions.
"""

from __future__ import annotations

import json
import os
import struct
import time
from typing import Any

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
NS = "atlas"

VENDOR_PREFIX = f"{NS}:vendor:"
POLICY_PREFIX = f"{NS}:policy:"
VENDOR_INDEX = f"{NS}:idx:vendors"
POLICY_INDEX = f"{NS}:idx:policies"

# Finance-operations connector namespaces (data ingestion + reconciliation).
#   atlas:source:<connector_id> ..... JSON provenance/metadata for an imported feed
#   atlas:dataset:<connector_id> .... JSON validated record payload for that feed
#   atlas:reconciliation:latest ..... JSON the most recent reconciliation report
#   atlas:stream:reconciliation ..... append-only reconciliation run log (provenance)
SOURCE_PREFIX = f"{NS}:source:"
DATASET_PREFIX = f"{NS}:dataset:"
RECON_LATEST = f"{NS}:reconciliation:latest"
RECON_STREAM = "reconciliation"

# Governance namespace — board policy rules, approval requests, obligations.
#   atlas:govpolicy:<id> ......... JSON structured board/finance policy rule
#   atlas:approval:<id> .......... JSON the governed approval request (route + audit state)
#   atlas:obligation:<id> ........ JSON a post-decision obligation (queryable for monitoring)
#   atlas:approval_matrix:northwind  JSON the company's approval matrix (thresholds → approvers)
#   atlas:idx:govpolicies ........ RediSearch over policy rules (lookup by category/threshold)
#   atlas:stream:audit ........... append-only immutable governance audit trail
GOVPOLICY_PREFIX = f"{NS}:govpolicy:"
APPROVAL_PREFIX = f"{NS}:approval:"
OBLIGATION_PREFIX = f"{NS}:obligation:"
GOVPOLICY_INDEX = f"{NS}:idx:govpolicies"
APPROVAL_INDEX = f"{NS}:idx:approvals"
OBLIGATION_INDEX = f"{NS}:idx:obligations"
MATRIX_KEY = f"{NS}:approval_matrix:northwind"
AUDIT_STREAM = "audit"

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = 1536

_client: redis.Redis | None = None


def client() -> redis.Redis:
    """Process-wide Redis client (decoded responses)."""
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def ping() -> bool:
    try:
        return bool(client().ping())
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# JSON documents (RedisJSON) — the financial system of record
# --------------------------------------------------------------------------- #
def set_json(key: str, obj: Any) -> None:
    client().json().set(key, "$", obj)


def get_json(key: str, path: str = "$") -> Any:
    res = client().json().get(key, path)
    if path == "$" and isinstance(res, list):
        return res[0] if res else None
    return res


def keys(pattern: str) -> list[str]:
    return list(client().scan_iter(match=pattern))


# --------------------------------------------------------------------------- #
# Embeddings (OpenAI) → FLOAT32 bytes for RediSearch vector fields
# --------------------------------------------------------------------------- #
def embed_texts(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    from src.env import load_env

    load_env()
    resp = OpenAI().embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def to_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


# --------------------------------------------------------------------------- #
# RediSearch: vendor index (over JSON docs)
# --------------------------------------------------------------------------- #
def ensure_vendor_index() -> None:
    from redis.commands.search.field import NumericField, TagField, TextField

    try:
        from redis.commands.search.index_definition import (
            IndexDefinition,
            IndexType,
        )
    except Exception:  # older redis-py module name
        from redis.commands.search.indexDefinition import (  # type: ignore
            IndexDefinition,
            IndexType,
        )

    schema = (
        TextField("$.name", as_name="name"),
        TagField("$.category", as_name="category"),
        NumericField("$.annual_cost", as_name="annual_cost"),
        TagField("$.status", as_name="status"),
        TextField("$.renewal_date", as_name="renewal_date"),
        TextField("$.notes", as_name="notes"),
    )
    try:
        client().ft(VENDOR_INDEX).create_index(
            schema,
            definition=IndexDefinition(prefix=[VENDOR_PREFIX], index_type=IndexType.JSON),
        )
    except redis.exceptions.ResponseError as exc:
        if "Index already exists" not in str(exc):
            raise


def search_vendors(query: str = "*", limit: int = 25) -> list[dict]:
    from redis.commands.search.query import Query

    res = client().ft(VENDOR_INDEX).search(Query(query).paging(0, limit))
    out = []
    for doc in res.docs:
        try:
            out.append(json.loads(doc.json))
        except Exception:
            out.append({"id": doc.id})
    return out


# --------------------------------------------------------------------------- #
# RediSearch: policy/decision vector index (over HASH docs) → semantic RAG
# --------------------------------------------------------------------------- #
def ensure_policy_index() -> None:
    from redis.commands.search.field import TagField, TextField, VectorField

    try:
        from redis.commands.search.index_definition import (
            IndexDefinition,
            IndexType,
        )
    except Exception:
        from redis.commands.search.indexDefinition import (  # type: ignore
            IndexDefinition,
            IndexType,
        )

    schema = (
        TextField("text"),
        TagField("kind"),
        TextField("title"),
        TagField("source_id"),
        VectorField(
            "embedding",
            "HNSW",
            {"TYPE": "FLOAT32", "DIM": EMBED_DIM, "DISTANCE_METRIC": "COSINE"},
        ),
    )
    try:
        client().ft(POLICY_INDEX).create_index(
            schema,
            definition=IndexDefinition(prefix=[POLICY_PREFIX], index_type=IndexType.HASH),
        )
    except redis.exceptions.ResponseError as exc:
        if "Index already exists" not in str(exc):
            raise


def upsert_policy(doc_id: str, text: str, kind: str, title: str, embedding: list[float], source_id: str | None = None) -> None:
    client().hset(
        f"{POLICY_PREFIX}{doc_id}",
        mapping={"source_id": source_id or doc_id, "text": text, "kind": kind, "title": title, "embedding": to_bytes(embedding)},
    )


def search_policies(query_text: str, k: int = 4) -> list[dict]:
    """Semantic KNN over finance policies & past decisions (vector RAG)."""
    from redis.commands.search.query import Query

    try:
        qvec = to_bytes(embed_texts([query_text])[0])
        q = (
            Query(f"*=>[KNN {k} @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("source_id", "title", "text", "kind", "score")
            .paging(0, k)
            .dialect(2)
        )
        res = client().ft(POLICY_INDEX).search(q, query_params={"vec": qvec})
        rows: list[dict] = []
        for d in res.docs:
            doc_id = str(getattr(d, "id", "") or "")
            source_id = getattr(d, "source_id", "") or doc_id.rsplit(":", 1)[-1]
            rows.append({
                "policy_id": source_id,
                "source_id": source_id,
                "title": d.title,
                "kind": d.kind,
                "text": d.text,
                "score": float(d.score),
            })
        return rows
    except Exception as exc:
        raise RuntimeError(f"Redis vector policy search unavailable: {exc}") from exc


# --------------------------------------------------------------------------- #
# Streams — append-only decision / debate event log
# --------------------------------------------------------------------------- #
def append_event(stream: str, data: dict) -> str:
    flat = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in data.items()}
    return client().xadd(f"{NS}:stream:{stream}", flat)


def read_events(stream: str, count: int = 25) -> list[dict]:
    rows = client().xrevrange(f"{NS}:stream:{stream}", count=count)
    events = []
    for event_id, fields in rows:
        parsed: dict[str, Any] = {"_id": event_id}
        for k, v in fields.items():
            try:
                parsed[k] = json.loads(v)
            except Exception:
                parsed[k] = v
        events.append(parsed)
    return events


# --------------------------------------------------------------------------- #
# Pub/Sub — live dashboard updates
# --------------------------------------------------------------------------- #
def publish(channel: str, payload: dict) -> None:
    client().publish(f"{NS}:{channel}", json.dumps(payload))


# --------------------------------------------------------------------------- #
# Governance — policy rules (RediSearch lookup), approvals & obligations (JSON)
# --------------------------------------------------------------------------- #
def _index_definition():
    try:
        from redis.commands.search.index_definition import IndexDefinition, IndexType
    except Exception:  # older redis-py module name
        from redis.commands.search.indexDefinition import (  # type: ignore
            IndexDefinition,
            IndexType,
        )
    return IndexDefinition, IndexType


def _create_json_index(index: str, prefix: str, schema) -> None:
    IndexDefinition, IndexType = _index_definition()
    try:
        client().ft(index).create_index(
            schema,
            definition=IndexDefinition(prefix=[prefix], index_type=IndexType.JSON),
        )
    except redis.exceptions.ResponseError as exc:
        if "Index already exists" not in str(exc):
            raise


def ensure_govpolicy_index() -> None:
    """RediSearch over structured board-policy rules so agents and the engine can
    look up applicable controls by category, threshold, or scope."""
    from redis.commands.search.field import NumericField, TagField, TextField

    schema = (
        TextField("$.title", as_name="title"),
        TextField("$.text", as_name="text"),
        TextField("$.control_id", as_name="control_id"),
        TextField("$.evidence_required[*]", as_name="evidence_required"),
        TextField("$.audit_requirements[*]", as_name="audit_requirements"),
        TagField("$.category", as_name="category"),
        TagField("$.severity", as_name="severity"),
        TagField("$.applies_to[*]", as_name="applies_to"),
        TagField("$.approval_route[*]", as_name="approval_route"),
        TagField("$.data_sensitivity[*]", as_name="data_sensitivity"),
        NumericField("$.amount_threshold", as_name="amount_threshold"),
        NumericField("$.runway_floor_months", as_name="runway_floor_months"),
        NumericField("$.margin_floor", as_name="margin_floor"),
        NumericField("$.notice_period_days", as_name="notice_period_days"),
    )
    _create_json_index(GOVPOLICY_INDEX, GOVPOLICY_PREFIX, schema)


def ensure_approval_index() -> None:
    """RediSearch over approval requests — filter by status, department, risk, amount."""
    from redis.commands.search.field import NumericField, TagField, TextField

    schema = (
        TextField("$.title", as_name="title"),
        TagField("$.status", as_name="status"),
        TagField("$.department", as_name="department"),
        TagField("$.risk_tier", as_name="risk_tier"),
        TagField("$.data_sensitivity", as_name="data_sensitivity"),
        NumericField("$.amount_annualized", as_name="amount_annualized"),
        TextField("$.created_at", as_name="created_at"),
    )
    _create_json_index(APPROVAL_INDEX, APPROVAL_PREFIX, schema)


def ensure_obligation_index() -> None:
    """RediSearch over obligations — power the monitoring view (upcoming/overdue)."""
    from redis.commands.search.field import TagField, TextField

    schema = (
        TagField("$.status", as_name="status"),
        TagField("$.kind", as_name="kind"),
        TagField("$.owner_role", as_name="owner_role"),
        TextField("$.due_date", as_name="due_date"),
        TextField("$.request_id", as_name="request_id"),
    )
    _create_json_index(OBLIGATION_INDEX, OBLIGATION_PREFIX, schema)


def ensure_governance_indices() -> None:
    ensure_govpolicy_index()
    ensure_approval_index()
    ensure_obligation_index()


def search_json_index(index: str, query: str = "*", limit: int = 50, sort_by: str | None = None) -> list[dict]:
    """Run a RediSearch query over a JSON index and return the parsed JSON docs."""
    from redis.commands.search.query import Query

    q = Query(query).paging(0, limit)
    if sort_by:
        q = q.sort_by(sort_by)
    res = client().ft(index).search(q)
    out: list[dict] = []
    for doc in res.docs:
        try:
            out.append(json.loads(doc.json))
        except Exception:
            out.append({"id": doc.id})
    return out


def search_govpolicies(query: str = "*", limit: int = 25) -> list[dict]:
    """Structured policy-rule lookup (RediSearch). Falls back to a key scan if the
    index is unavailable so policy reads never hard-fail the governance path."""
    try:
        return search_json_index(GOVPOLICY_INDEX, query, limit)
    except Exception:
        return list_json(GOVPOLICY_PREFIX, limit)


def list_json(prefix: str, limit: int = 200) -> list[dict]:
    """Scan + load every JSON doc under a key prefix (small-N governance reads)."""
    out: list[dict] = []
    for key in keys(f"{prefix}*"):
        doc = get_json(key)
        if doc is not None:
            out.append(doc)
        if len(out) >= limit:
            break
    return out


def delete_key(key: str) -> int:
    return int(client().delete(key))


def unlink_keys(keys: list[str], *, batch: int = 512) -> int:
    """UNLINK (non-blocking delete) an explicit key list in batched round-trips.

    Batching collapses N per-key deletes into ceil(N/batch) commands, which is
    what makes bulk teardown (e.g. the demo reset) fast.
    """
    if not keys:
        return 0
    c = client()
    deleted = 0
    for i in range(0, len(keys), batch):
        deleted += int(c.unlink(*keys[i : i + batch]))
    return deleted


def delete_keys_matching(pattern: str) -> int:
    """Delete every key matching a SCAN pattern (counted, batched UNLINK)."""
    c = client()
    deleted = 0
    chunk: list[str] = []
    for key in c.scan_iter(match=pattern, count=1000):
        chunk.append(key)
        if len(chunk) >= 512:
            deleted += int(c.unlink(*chunk))
            chunk.clear()
    if chunk:
        deleted += int(c.unlink(*chunk))
    return deleted


def clear_stream(stream: str) -> int:
    """Delete an append-only stream key so it can be reseeded from scratch."""
    return int(client().unlink(f"{NS}:stream:{stream}"))


def clear_streams(streams: list[str]) -> dict[str, int]:
    """Delete many stream keys in one pipelined round-trip (per-stream counts)."""
    if not streams:
        return {}
    pipe = client().pipeline(transaction=False)
    for stream in streams:
        pipe.unlink(f"{NS}:stream:{stream}")
    return {stream: int(res) for stream, res in zip(streams, pipe.execute())}


# --------------------------------------------------------------------------- #
# Cache — computed metrics / canonical demo replays
# --------------------------------------------------------------------------- #
def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    client().set(f"{NS}:cache:{key}", json.dumps(value), ex=ttl)


def cache_get(key: str) -> Any:
    raw = client().get(f"{NS}:cache:{key}")
    return json.loads(raw) if raw else None
