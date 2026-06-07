"""Redis-backed document metadata, chunks, parse jobs, and filtered retrieval."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from src import redis_layer as R
from src.documents.models import (
    DocumentChunk,
    DocumentMetadata,
    DocumentRetrievalFilter,
    ParseJobRecord,
    ParseJobStatus,
    utc_now,
)

NS = R.NS
META_PREFIX = f"{NS}:documents:meta:"
CHUNK_PREFIX = f"{NS}:documents:chunk:"
JOB_PREFIX = f"{NS}:documents:parse_job:"
REGISTRY_KEY = f"{NS}:documents:registry"
JOB_REGISTRY_KEY = f"{NS}:documents:job_registry"
CHUNK_INDEX = f"{NS}:idx:docchunks"
MAX_EVIDENCE_CHUNKS = 8


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def ensure_indexes() -> None:
    from redis.commands.search.field import NumericField, TagField, TextField, VectorField

    try:
        from redis.commands.search.index_definition import IndexDefinition, IndexType
    except Exception:
        from redis.commands.search.indexDefinition import IndexDefinition, IndexType  # type: ignore

    schema = (
        TextField("text"),
        TagField("doc_id"),
        TagField("chunk_id"),
        TagField("kind"),
        TagField("source_category"),
        TagField("connector_id"),
        TagField("vendor"),
        TagField("parse_job_id"),
        TagField("upload_batch_id"),
        TextField("filename"),
        NumericField("confidence"),
        NumericField("uploaded_at_ts"),
        NumericField("document_date_ts"),
        VectorField(
            "embedding",
            "HNSW",
            {"TYPE": "FLOAT32", "DIM": R.EMBED_DIM, "DISTANCE_METRIC": "COSINE"},
        ),
    )
    try:
        R.client().ft(CHUNK_INDEX).create_index(
            schema,
            definition=IndexDefinition(prefix=[CHUNK_PREFIX], index_type=IndexType.HASH),
        )
    except Exception as exc:
        if "Index already exists" not in str(exc):
            raise


def _parse_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def save_parse_job(record: ParseJobRecord) -> ParseJobRecord:
    ensure_indexes()
    R.set_json(f"{JOB_PREFIX}{record.job_id}", record.model_dump(mode="json"))
    R.client().sadd(JOB_REGISTRY_KEY, record.job_id)
    return record


def get_parse_job(job_id: str) -> Optional[ParseJobRecord]:
    payload = R.get_json(f"{JOB_PREFIX}{job_id}")
    return ParseJobRecord.model_validate(payload) if payload else None


def list_parse_jobs(limit: int = 25) -> list[ParseJobRecord]:
    ids = list(R.client().smembers(JOB_REGISTRY_KEY))[-limit:]
    out: list[ParseJobRecord] = []
    for job_id in reversed(ids):
        record = get_parse_job(job_id)
        if record:
            out.append(record)
    return out


def update_parse_job_status(
    job_id: str,
    status: ParseJobStatus,
    *,
    error: Optional[str] = None,
    error_code: Optional[str] = None,
    doc_id: Optional[str] = None,
    detected_kind: Optional[str] = None,
) -> ParseJobRecord:
    record = get_parse_job(job_id)
    if record is None:
        raise KeyError(f"parse job not found: {job_id}")
    now = utc_now()
    timeline = list(record.timeline)
    timeline.append({"status": status.value, "at": now, "error": error})
    updated = record.model_copy(
        update={
            "status": status,
            "updated_at": now,
            "timeline": timeline,
            "error": error,
            "error_code": error_code,
            "doc_id": doc_id or record.doc_id,
            "detected_kind": detected_kind or record.detected_kind,
        }
    )
    save_parse_job(updated)
    R.publish(
        "documents",
        {
            "type": "parse_job",
            "job_id": job_id,
            "status": status.value,
            "doc_id": updated.doc_id,
            "error": error,
            "error_code": error_code,
        },
    )
    return updated


def save_document(meta: DocumentMetadata, chunks: list[DocumentChunk], embeddings: list[list[float]]) -> DocumentMetadata:
    ensure_indexes()
    R.set_json(f"{META_PREFIX}{meta.doc_id}", meta.model_dump(mode="json"))
    R.client().sadd(REGISTRY_KEY, meta.doc_id)
    for chunk, embedding in zip(chunks, embeddings, strict=False):
        uploaded_ts = _parse_ts(chunk.uploaded_at) or time.time()
        doc_date_ts = _parse_ts(chunk.document_date) or 0
        mapping = {
            "text": chunk.text,
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.chunk_id,
            "kind": chunk.kind,
            "source_category": chunk.source_category,
            "connector_id": chunk.connector_id or "",
            "vendor": chunk.vendor or "",
            "parse_job_id": chunk.parse_job_id or "",
            "upload_batch_id": chunk.upload_batch_id or "",
            "filename": chunk.filename,
            "confidence": chunk.confidence,
            "uploaded_at_ts": uploaded_ts,
            "document_date_ts": doc_date_ts,
            "embedding": R.to_bytes(embedding),
        }
        R.client().hset(f"{CHUNK_PREFIX}{chunk.chunk_id}", mapping=mapping)
    return meta


def get_document(doc_id: str) -> Optional[DocumentMetadata]:
    payload = R.get_json(f"{META_PREFIX}{doc_id}")
    return DocumentMetadata.model_validate(payload) if payload else None


def list_documents(
    *,
    source_category: Optional[str] = None,
    connector_id: Optional[str] = None,
    q: Optional[str] = None,
    offset: int = 0,
    limit: int = 25,
) -> tuple[list[DocumentMetadata], int]:
    ids = list(R.client().smembers(REGISTRY_KEY))
    out: list[DocumentMetadata] = []
    needle = (q or "").strip().lower()
    for doc_id in ids:
        meta = get_document(doc_id)
        if meta is None:
            continue
        if source_category and meta.source_category != source_category:
            continue
        if connector_id and meta.connector_id != connector_id:
            continue
        if needle and needle not in f"{meta.filename} {meta.source_category} {meta.vendor or ''}".lower():
            continue
        out.append(meta)
    out.sort(key=lambda item: item.uploaded_at, reverse=True)
    total = len(out)
    page = out[offset : offset + min(max(limit, 1), 50)]
    return page, total


def delete_document(doc_id: str) -> bool:
    meta = get_document(doc_id)
    if meta is None:
        return False
    from redis.commands.search.query import Query

    try:
        res = R.client().ft(CHUNK_INDEX).search(Query(f"@doc_id:{{{doc_id}}}").paging(0, 500))
        for doc in res.docs:
            key = str(getattr(doc, "id", "") or "")
            if key:
                R.client().delete(key)
    except Exception:
        pass
    R.client().delete(f"{META_PREFIX}{doc_id}")
    R.client().srem(REGISTRY_KEY, doc_id)
    return True


def clear_all_documents() -> int:
    """Remove every uploaded document, chunk, parse job, and registry entry."""
    removed = 0
    for doc_id in list(R.client().smembers(REGISTRY_KEY)):
        if delete_document(str(doc_id)):
            removed += 1
    removed += R.delete_keys_matching(f"{JOB_PREFIX}*")
    R.client().delete(JOB_REGISTRY_KEY)
    return removed


def _build_filter_query(filters: DocumentRetrievalFilter | None) -> str:
    if filters is None:
        return "*"
    parts: list[str] = []
    if filters.kinds:
        kind_expr = "|".join(filters.kinds)
        parts.append(f"@kind:{{{kind_expr}}}")
    if filters.source_categories:
        cat_expr = "|".join(filters.source_categories)
        parts.append(f"@source_category:{{{cat_expr}}}")
    if filters.connector_id:
        parts.append(f"@connector_id:{{{filters.connector_id}}}")
    if filters.vendor:
        parts.append(f"@vendor:{{{filters.vendor.strip()}}}")
    if filters.parse_job_id:
        parts.append(f"@parse_job_id:{{{filters.parse_job_id}}}")
    if filters.upload_batch_id:
        parts.append(f"@upload_batch_id:{{{filters.upload_batch_id}}}")
    if filters.min_confidence is not None:
        parts.append(f"@confidence:[{filters.min_confidence} inf]")
    if filters.uploaded_after:
        ts = _parse_ts(filters.uploaded_after)
        if ts is not None:
            parts.append(f"@uploaded_at_ts:[{ts} inf]")
    if filters.uploaded_before:
        ts = _parse_ts(filters.uploaded_before)
        if ts is not None:
            parts.append(f"@uploaded_at_ts:[-inf {ts}]")
    if filters.document_date_after:
        ts = _parse_ts(filters.document_date_after)
        if ts is not None:
            parts.append(f"@document_date_ts:[{ts} inf]")
    if filters.document_date_before:
        ts = _parse_ts(filters.document_date_before)
        if ts is not None:
            parts.append(f"@document_date_ts:[-inf {ts}]")
    if not parts:
        return "*"
    return " ".join(parts)


def _freshness_multiplier(uploaded_at_ts: float, max_freshness_days: Optional[int]) -> float:
    if not max_freshness_days:
        return 1.0
    age_days = max(0.0, (time.time() - uploaded_at_ts) / 86400.0)
    if age_days > max_freshness_days:
        return 0.0
    return max(0.2, 1.0 - (age_days / max_freshness_days) * 0.5)


def _row_from_doc(doc: Any) -> dict[str, Any]:
    uploaded_ts = float(getattr(doc, "uploaded_at_ts", 0) or 0)
    confidence = float(getattr(doc, "confidence", 1) or 1)
    vector_score = float(getattr(doc, "score", 0) or 0)
    freshness = _freshness_multiplier(uploaded_ts, None)
    rank = (1.0 - vector_score) * confidence * freshness if vector_score else confidence
    text = getattr(doc, "text", "") or ""
    return {
        "doc_id": getattr(doc, "doc_id", ""),
        "chunk_id": getattr(doc, "chunk_id", ""),
        "filename": getattr(doc, "filename", ""),
        "source_category": getattr(doc, "source_category", ""),
        "kind": getattr(doc, "kind", ""),
        "connector_id": getattr(doc, "connector_id", "") or None,
        "vendor": getattr(doc, "vendor", "") or None,
        "parse_job_id": getattr(doc, "parse_job_id", "") or None,
        "upload_batch_id": getattr(doc, "upload_batch_id", "") or None,
        "confidence": confidence,
        "uploaded_at": datetime.utcfromtimestamp(uploaded_ts).replace(microsecond=0).isoformat() + "Z"
        if uploaded_ts
        else None,
        "excerpt": text[:280],
        "text": text,
        "score": round(rank, 4),
        "vector_score": round(vector_score, 4) if vector_score else None,
    }


def _filter_only_rows(filter_query: str, k: int) -> list[dict[str, Any]]:
    from redis.commands.search.query import Query

    q = (
        Query(filter_query)
        .sort_by("uploaded_at_ts", asc=False)
        .return_fields(
            "doc_id",
            "chunk_id",
            "filename",
            "source_category",
            "kind",
            "connector_id",
            "vendor",
            "parse_job_id",
            "upload_batch_id",
            "confidence",
            "uploaded_at_ts",
            "text",
        )
        .paging(0, k)
    )
    res = R.client().ft(CHUNK_INDEX).search(q)
    return [_row_from_doc(doc) for doc in res.docs]


def search_document_chunks(
    query: str,
    *,
    filters: DocumentRetrievalFilter | None = None,
    k: int = 6,
    max_results: int = MAX_EVIDENCE_CHUNKS,
) -> list[dict[str, Any]]:
    """Hybrid vector + filter retrieval; never returns unbounded chunk dumps."""
    from redis.commands.search.query import Query

    ensure_indexes()
    k = min(max(k, 1), max_results)
    filter_query = _build_filter_query(filters)
    rows: list[dict[str, Any]] = []

    if query.strip():
        qvec = R.to_bytes(R.embed_texts([query])[0])
        base = f"({filter_query})=>[KNN {k} @embedding $vec AS score]"
        q = (
            Query(base)
            .sort_by("score")
            .return_fields(
                "doc_id",
                "chunk_id",
                "filename",
                "source_category",
                "kind",
                "connector_id",
                "vendor",
                "parse_job_id",
                "upload_batch_id",
                "confidence",
                "uploaded_at_ts",
                "text",
                "score",
            )
            .paging(0, k)
            .dialect(2)
        )
        res = R.client().ft(CHUNK_INDEX).search(q, query_params={"vec": qvec})
        rows = [_row_from_doc(doc) for doc in res.docs]
        if not rows:
            rows = _filter_only_rows(filter_query, k)
    else:
        rows = _filter_only_rows(filter_query, k)

    if filters and filters.max_freshness_days is not None:
        rows = [
            row
            for row in rows
            if _freshness_multiplier(
                _parse_ts(row.get("uploaded_at")) or time.time(),
                filters.max_freshness_days,
            )
            > 0
        ]

    rows.sort(key=lambda row: row.get("score") or 0, reverse=True)
    return rows[:max_results]


def list_document_chunks(
    doc_id: str,
    *,
    offset: int = 0,
    limit: int = 20,
    include_text: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    from redis.commands.search.query import Query

    ensure_indexes()
    limit = min(max(limit, 1), 50)
    count_q = Query(f"@doc_id:{{{doc_id}}}").paging(0, 0)
    total = int(R.client().ft(CHUNK_INDEX).search(count_q).total or 0)
    q = (
        Query(f"@doc_id:{{{doc_id}}}")
        .sort_by("chunk_id")
        .return_fields("chunk_id", "text", "source_category", "kind", "confidence")
        .paging(offset, limit)
    )
    res = R.client().ft(CHUNK_INDEX).search(q)
    rows: list[dict[str, Any]] = []
    for doc in res.docs:
        text = doc.text or ""
        row = {
            "chunk_id": doc.chunk_id,
            "excerpt": text[:280],
            "source_category": doc.source_category,
            "kind": doc.kind,
            "confidence": float(doc.confidence),
        }
        if include_text:
            row["text"] = text
        rows.append(row)
    return rows, total


def checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def create_job_id() -> str:
    return _new_id("job")


def create_doc_id() -> str:
    return _new_id("doc")


def create_batch_id() -> str:
    return _new_id("batch")
