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


def upsert_policy(doc_id: str, text: str, kind: str, title: str, embedding: list[float]) -> None:
    client().hset(
        f"{POLICY_PREFIX}{doc_id}",
        mapping={"text": text, "kind": kind, "title": title, "embedding": to_bytes(embedding)},
    )


def search_policies(query_text: str, k: int = 4) -> list[dict]:
    """Semantic KNN over finance policies & past decisions (vector RAG)."""
    from redis.commands.search.query import Query

    try:
        qvec = to_bytes(embed_texts([query_text])[0])
        q = (
            Query(f"*=>[KNN {k} @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("title", "text", "kind", "score")
            .paging(0, k)
            .dialect(2)
        )
        res = client().ft(POLICY_INDEX).search(q, query_params={"vec": qvec})
        return [
            {"title": d.title, "kind": d.kind, "text": d.text, "score": float(d.score)}
            for d in res.docs
        ]
    except Exception as exc:
        # RAG should never hard-fail the agent; degrade gracefully.
        return [{"title": "", "kind": "error", "text": f"(policy search unavailable: {exc})", "score": 0.0}]


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
# Cache — computed metrics / canonical demo replays
# --------------------------------------------------------------------------- #
def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    client().set(f"{NS}:cache:{key}", json.dumps(value), ex=ttl)


def cache_get(key: str) -> Any:
    raw = client().get(f"{NS}:cache:{key}")
    return json.loads(raw) if raw else None
