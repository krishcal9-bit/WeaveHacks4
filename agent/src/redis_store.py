"""
Financial-OS Redis operations for Atlas.

This module turns Redis Stack into the **core financial operating database**:
declarative search/vector indexes, typed JSON collection helpers, vector RAG
with metadata filters, idempotent namespaced migrations, and explicit
introspection for health/preflight output.

It is built strictly on top of the *stable public API* of
:mod:`src.redis_layer` (``client``/``set_json``/``get_json``/``keys``/
``append_event``/``publish``/``embed_texts``/``to_bytes``/``cache_*``). It does
**not** modify ``redis_layer`` itself — sibling workstreams (governance,
connector ingestion) also write that module, so the financial-OS schema lives
here and in :mod:`src.redis_models` to avoid stepping on their namespaces.

Owned Redis structures (see :func:`src.redis_models.redis_key_map`):
  • JSON      atlas:{company,vendor,department,invoice,po,contract,arr,scenario}:*
  • Indexes   atlas:idx:{vendors,departments,invoices,purchase_orders,contracts,scenarios,knowledge}
  • Vector    atlas:knowledge:* (HASH + HNSW/COSINE)  → policies/decisions/clauses/findings
  • Streams   atlas:stream:{decisions,scenarios,evals}
  • Cache     atlas:cache:*  (TTL + invalidation)
  • Meta      atlas:meta:financial_schema_version, atlas:meta:financial_seed
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Sequence

import redis

from src import redis_layer as R
from src import redis_models as M
from src.redis_models import IndexSpec


# --------------------------------------------------------------------------- #
# Typed JSON collection helpers (system of record)
# --------------------------------------------------------------------------- #
def set_doc(key: str, obj: Any) -> None:
    """Store a JSON document (Pydantic model or dict)."""
    payload = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    R.set_json(key, payload)


def get_doc(key: str) -> dict | None:
    doc = R.get_json(key)
    return doc if isinstance(doc, dict) else None


def get_model(key: str, model: type[M.BaseModel]) -> Any | None:
    raw = get_doc(key)
    return model.model_validate(raw) if raw else None


def delete(key: str) -> int:
    return int(R.client().delete(key))


def count_keys(pattern: str) -> int:
    total = 0
    for _ in R.client().scan_iter(match=pattern, count=500):
        total += 1
    return total


def scan_collection(prefix: str, limit: int = 1000) -> list[dict]:
    """All JSON docs under a prefix, sorted by key for stable output."""
    out: list[dict] = []
    for key in sorted(R.keys(f"{prefix}*"))[:limit]:
        doc = get_doc(key)
        if doc is not None:
            out.append(doc)
    return out


def store_many(prefix: str, docs: Iterable[Any], id_field: str = "id") -> int:
    """Idempotently upsert a collection of JSON docs keyed by ``id_field``."""
    count = 0
    for doc in docs:
        payload = doc.model_dump(mode="json") if hasattr(doc, "model_dump") else dict(doc)
        doc_id = payload.get(id_field)
        if doc_id is None:
            raise ValueError(f"document missing '{id_field}': {payload!r}")
        R.set_json(f"{prefix}{doc_id}", payload)
        count += 1
    return count


# --------------------------------------------------------------------------- #
# Declarative index management (idempotent + recreate-on-version)
# --------------------------------------------------------------------------- #
def _definition(spec: IndexSpec):
    try:
        from redis.commands.search.index_definition import IndexDefinition, IndexType
    except Exception:  # older redis-py module name
        from redis.commands.search.indexDefinition import IndexDefinition, IndexType  # type: ignore

    index_type = IndexType.JSON if spec.on == "JSON" else IndexType.HASH
    return IndexDefinition(prefix=[spec.prefix], index_type=index_type)


def _field(fs: M.FieldSpec):
    from redis.commands.search.field import (
        NumericField,
        TagField,
        TextField,
        VectorField,
    )

    aliased = fs.path != fs.name  # JSON path + alias vs. plain HASH field
    if fs.type == "text":
        return TextField(fs.path, as_name=fs.name, sortable=fs.sortable) if aliased else TextField(fs.name, sortable=fs.sortable)
    if fs.type == "tag":
        return TagField(fs.path, as_name=fs.name) if aliased else TagField(fs.name)
    if fs.type == "numeric":
        return NumericField(fs.path, as_name=fs.name, sortable=fs.sortable) if aliased else NumericField(fs.name, sortable=fs.sortable)
    if fs.type == "vector":
        opts = dict(fs.options or {})
        algo = opts.pop("ALGO", "HNSW")
        return VectorField(fs.name, algo, opts)
    raise ValueError(f"unknown field type: {fs.type}")


def list_indexes() -> list[str]:
    try:
        return [str(name) for name in R.client().execute_command("FT._LIST")]
    except Exception:
        return []


def drop_index(name: str, *, delete_documents: bool = False) -> bool:
    try:
        args = ["FT.DROPINDEX", name]
        if delete_documents:
            args.append("DD")
        R.client().execute_command(*args)
        return True
    except redis.exceptions.ResponseError:
        return False


def ensure_index(spec: IndexSpec, *, recreate: bool = False) -> str:
    """Create a search/vector index from its spec. Returns created/recreated/exists."""
    existing = spec.name in list_indexes()
    if existing and not recreate:
        return "exists"
    if existing and recreate:
        drop_index(spec.name, delete_documents=False)
    fields = tuple(_field(f) for f in spec.fields)
    try:
        R.client().ft(spec.name).create_index(fields, definition=_definition(spec))
    except redis.exceptions.ResponseError as exc:
        if "Index already exists" in str(exc):
            return "exists"
        raise
    return "recreated" if existing else "created"


def ensure_all_indexes(*, recreate: bool = False) -> dict[str, str]:
    return {spec.name: ensure_index(spec, recreate=recreate) for spec in M.ALL_INDEX_SPECS}


def index_info(name: str) -> dict[str, Any]:
    return _pairs_to_dict(R.client().execute_command("FT.INFO", name))


def index_doc_count(name: str) -> int:
    try:
        info = index_info(name)
        raw = info.get("num_docs") or info.get("num_records") or 0
        return int(float(str(raw)))
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# Structured search over JSON indexes
# --------------------------------------------------------------------------- #
def _escape_tag(value: str) -> str:
    out = str(value)
    for ch in ("-", " ", ".", ":", "/", "@", "|", "{", "}", "(", ")", "'", '"'):
        out = out.replace(ch, f"\\{ch}")
    return out


def _filter_expr(filters: dict[str, Iterable[str] | str] | None) -> str:
    if not filters:
        return "*"
    clauses: list[str] = []
    for field_name, value in filters.items():
        values = [value] if isinstance(value, str) else list(value)
        values = [v for v in values if v not in (None, "")]
        if not values:
            continue
        joined = "|".join(_escape_tag(v) for v in values)
        clauses.append(f"@{field_name}:{{{joined}}}")
    return " ".join(clauses) if clauses else "*"


def search_index(
    name: str,
    query: str = "*",
    *,
    filters: dict[str, Iterable[str] | str] | None = None,
    sort_by: str | None = None,
    ascending: bool = True,
    limit: int = 50,
) -> list[dict]:
    """Generic structured search over a JSON index → parsed docs."""
    from redis.commands.search.query import Query

    if query and query != "*" and filters:
        expr = f"({query}) {_filter_expr(filters)}"
    elif query and query != "*":
        expr = query
    else:
        expr = _filter_expr(filters)
    q = Query(expr or "*").paging(0, limit)
    if sort_by:
        q = q.sort_by(sort_by, asc=ascending)
    res = R.client().ft(name).search(q)
    out: list[dict] = []
    for doc in res.docs:
        raw = getattr(doc, "json", None)
        if raw:
            try:
                out.append(json.loads(raw))
                continue
            except Exception:
                pass
        out.append({"id": doc.id})
    return out


# --------------------------------------------------------------------------- #
# Vector RAG over the knowledge corpus (metadata filters + rerank shape)
# --------------------------------------------------------------------------- #
def upsert_knowledge(doc: "M.KnowledgeDoc | dict", embedding: Sequence[float]) -> None:
    payload = doc.model_dump(mode="json") if isinstance(doc, M.KnowledgeDoc) else dict(doc)
    tags = payload.get("tags") or []
    mapping = {
        "text": payload.get("text", ""),
        "title": payload.get("title", ""),
        "kind": payload.get("kind", "policy"),
        "source_id": payload.get("source_id", "") or payload.get("id", ""),
        "category": payload.get("category", ""),
        "severity": payload.get("severity", ""),
        "effective_date": payload.get("effective_date", ""),
        "tags": ",".join(tags) if isinstance(tags, list) else str(tags),
        "embedding": R.to_bytes(embedding),
    }
    R.client().hset(M.knowledge_key(payload["id"]), mapping=mapping)


def seed_knowledge(docs: Sequence["M.KnowledgeDoc | dict"]) -> int:
    """Embed (one batched OpenAI call) and upsert a knowledge corpus."""
    items = [d.model_dump(mode="json") if isinstance(d, M.KnowledgeDoc) else dict(d) for d in docs]
    texts = [f"{d.get('title', '')}. {d.get('text', '')}".strip() for d in items]
    embeddings = R.embed_texts(texts)
    for item, emb in zip(items, embeddings):
        upsert_knowledge(item, emb)
    return len(items)


_KNOWLEDGE_RETURN_FIELDS = (
    "title",
    "text",
    "kind",
    "source_id",
    "category",
    "severity",
    "effective_date",
    "tags",
    "vector_score",
)


def search_knowledge(
    query_text: str,
    k: int = 4,
    *,
    kinds: Iterable[str] | None = None,
    filters: dict[str, Iterable[str] | str] | None = None,
) -> list[dict]:
    """Semantic KNN over the knowledge corpus with optional metadata prefilters.

    Returns reranking-friendly rows: similarity ``score`` (1 - cosine distance),
    raw ``distance``, ``rank``, and every metadata field a reranker might use.
    """
    from redis.commands.search.query import Query

    combined: dict[str, Iterable[str] | str] = dict(filters or {})
    if kinds:
        combined["kind"] = list(kinds)
    prefilter = _filter_expr(combined)
    try:
        qvec = R.to_bytes(R.embed_texts([query_text])[0])
        q = (
            Query(f"({prefilter})=>[KNN {k} @embedding $vec AS vector_score]")
            .sort_by("vector_score")
            .return_fields(*_KNOWLEDGE_RETURN_FIELDS)
            .paging(0, k)
            .dialect(2)
        )
        res = R.client().ft(M.KNOWLEDGE_INDEX).search(q, query_params={"vec": qvec})
    except Exception as exc:
        raise RuntimeError(f"Redis vector knowledge search unavailable: {exc}") from exc

    rows: list[dict] = []
    for rank, d in enumerate(res.docs):
        distance = float(getattr(d, "vector_score", 0.0) or 0.0)
        tags = getattr(d, "tags", "") or ""
        rows.append(
            {
                "id": getattr(d, "id", None),
                "title": getattr(d, "title", ""),
                "kind": getattr(d, "kind", ""),
                "source_id": getattr(d, "source_id", ""),
                "category": getattr(d, "category", ""),
                "severity": getattr(d, "severity", ""),
                "effective_date": getattr(d, "effective_date", ""),
                "tags": [t for t in tags.split(",") if t],
                "text": getattr(d, "text", ""),
                "distance": round(distance, 6),
                "score": round(1.0 - distance, 6),
                "rank": rank + 1,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Streams (replay) + cache invalidation
# --------------------------------------------------------------------------- #
def replay_events(stream: str, count: int = 200) -> list[dict]:
    """Chronological stream events (oldest → newest) for deterministic replay."""
    rows = R.client().xrange(f"{R.NS}:stream:{stream}", count=count)
    events: list[dict] = []
    for event_id, fields in rows:
        parsed: dict[str, Any] = {"_id": event_id}
        for key, value in fields.items():
            try:
                parsed[key] = json.loads(value)
            except Exception:
                parsed[key] = value
        events.append(parsed)
    return events


def stream_len(stream: str) -> int:
    try:
        return int(R.client().xlen(f"{R.NS}:stream:{stream}"))
    except Exception:
        return 0


def cache_delete(key: str) -> int:
    return int(R.client().delete(f"{M.CACHE_PREFIX}{key}"))


def cache_invalidate(pattern: str = "*") -> int:
    """Delete cached keys matching ``atlas:cache:<pattern>``."""
    deleted = 0
    for full_key in R.client().scan_iter(match=f"{M.CACHE_PREFIX}{pattern}", count=500):
        deleted += int(R.client().delete(full_key))
    return deleted


def cached(key: str, producer: Callable[[], Any], ttl: int = 300) -> Any:
    hit = R.cache_get(key)
    if hit is not None:
        return hit
    value = producer()
    R.cache_set(key, value, ttl=ttl)
    return value


# --------------------------------------------------------------------------- #
# Namespaced migrations
# --------------------------------------------------------------------------- #
def schema_version() -> int:
    try:
        raw = R.client().get(M.SCHEMA_VERSION_KEY)
        return int(raw) if raw else 0
    except Exception:
        return 0


def set_schema_version(version: int) -> None:
    R.client().set(M.SCHEMA_VERSION_KEY, version)


def run_migrations(*, force: bool = False) -> dict[str, Any]:
    """Idempotently bring the financial-OS indexes to ``SCHEMA_VERSION``.

    Rebuilds (drop + recreate) the financial-OS indexes when the stored version
    is behind the code version (or ``force``); otherwise just ensures they
    exist. Only touches financial-OS indexes — the legacy policy index and the
    governance/connector indexes are left untouched.
    """
    current = schema_version()
    rebuild = force or current < M.SCHEMA_VERSION
    index_actions = ensure_all_indexes(recreate=rebuild)
    set_schema_version(M.SCHEMA_VERSION)
    return {
        "from_version": current,
        "to_version": M.SCHEMA_VERSION,
        "rebuilt": rebuild,
        "indexes": index_actions,
    }


# --------------------------------------------------------------------------- #
# Introspection — explicit health / preflight output
# --------------------------------------------------------------------------- #
def _pairs_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if not isinstance(value, (list, tuple)):
        return {}
    parsed: dict[str, Any] = {}
    iterator = iter(value)
    for key in iterator:
        try:
            item = next(iterator)
        except StopIteration:
            break
        parsed[str(key)] = item
    return parsed


def modules() -> dict[str, str]:
    rows = R.client().execute_command("MODULE", "LIST")
    found: dict[str, str] = {}
    for row in rows or []:
        parsed = _pairs_to_dict(row)
        name = str(parsed.get("name") or parsed.get(b"name") or "").lower()
        version = str(parsed.get("ver") or parsed.get(b"ver") or "unknown")
        if name:
            found[name] = version
    return found


def collection_counts() -> dict[str, int]:
    return {label: count_keys(pattern) for label, pattern in M.SEEDED_COLLECTIONS.items()}


def index_report() -> list[dict[str, Any]]:
    present = set(list_indexes())
    report: list[dict[str, Any]] = []
    for spec in M.ALL_INDEX_SPECS:
        report.append(
            {
                "name": spec.name,
                "exists": spec.name in present,
                "on": spec.on,
                "vector": spec.is_vector,
                "num_docs": index_doc_count(spec.name) if spec.name in present else 0,
                "description": spec.description,
            }
        )
    return report


def stream_report() -> dict[str, int]:
    return {name: stream_len(name) for name in M.ALL_STREAMS}


def knowledge_count() -> int:
    return count_keys(f"{M.KNOWLEDGE_PREFIX}*")


def scenario_count() -> int:
    return count_keys(f"{M.SCENARIO_PREFIX}*")


def redis_overview() -> dict[str, Any]:
    """One call powering health, preflight, and the /api/redis-map endpoint."""
    return {
        "schema_version": {"current": schema_version(), "target": M.SCHEMA_VERSION},
        "modules": modules(),
        "collections": collection_counts(),
        "indexes": index_report(),
        "streams": stream_report(),
        "knowledge_docs": knowledge_count(),
        "scenarios": scenario_count(),
        "map": M.redis_key_map(),
    }
