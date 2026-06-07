"""Structured Redis activity events for uploaded-document workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src import redis_layer as R
from src.env import redact_secrets

DOCUMENT_ACTIVITY_KINDS = frozenset(
    {
        "document_indexed",
        "document_vector_query",
        "document_chunks_retrieved",
        "document_source_used",
        "document_fact_promoted",
        "document_discrepancy_created",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def document_activity(
    kind: str,
    *,
    label: str,
    detail: str,
    role: Optional[str] = None,
    **fields: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "at": _now(),
        "kind": kind,
        "label": label,
        "detail": detail,
    }
    if role:
        payload["role"] = role
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    return payload


def publish_document_activity(event: dict[str, Any]) -> None:
    try:
        R.publish("documents", {"type": "activity", **event})
    except Exception:
        pass


def indexed_activity(
    *,
    filename: str,
    doc_id: str,
    source_category: str,
    chunk_count: int,
) -> dict[str, Any]:
    return document_activity(
        "document_indexed",
        label="Document indexed",
        detail=f"{filename} · {chunk_count} chunk(s) · {source_category}",
        doc_id=doc_id,
        filename=filename,
        source_category=source_category,
        chunk_count=chunk_count,
    )


def vector_query_activity(
    *,
    role: str,
    query: str,
    filters_summary: str,
) -> dict[str, Any]:
    return document_activity(
        "document_vector_query",
        label="Document vector query",
        detail=f"{role}: '{query[:48]}' · {filters_summary}",
        role=role,
        query=query,
        filters_summary=filters_summary,
    )


def chunks_retrieved_activity(
    *,
    role: str,
    count: int,
    chunk_ids: list[str],
    categories: list[str],
) -> dict[str, Any]:
    return document_activity(
        "document_chunks_retrieved",
        label="Document chunks retrieved",
        detail=f"{role}: {count} chunk(s) from {', '.join(sorted(set(categories))[:4]) or 'uploads'}",
        role=role,
        count=count,
        chunk_ids=chunk_ids[:12],
        categories=sorted(set(categories)),
    )


def source_used_activity(
    *,
    role: str,
    doc_id: str,
    chunk_id: str,
    filename: str,
    source_category: str,
) -> dict[str, Any]:
    return document_activity(
        "document_source_used",
        label="Document source used",
        detail=f"{role}: {filename} ({source_category})",
        role=role,
        doc_id=doc_id,
        chunk_id=chunk_id,
        filename=filename,
        source_category=source_category,
    )


def fact_promoted_activity(
    *,
    role: str,
    doc_id: str,
    chunk_id: str,
    excerpt: str,
) -> dict[str, Any]:
    return document_activity(
        "document_fact_promoted",
        label="Document fact promoted",
        detail=f"{role}: {excerpt[:120]}",
        role=role,
        doc_id=doc_id,
        chunk_id=chunk_id,
        excerpt=excerpt[:280],
    )


def discrepancy_created_activity(
    *,
    doc_id: Optional[str],
    filename: Optional[str],
    title: str,
    severity: str,
) -> dict[str, Any]:
    return document_activity(
        "document_discrepancy_created",
        label="Document discrepancy",
        detail=f"{severity}: {title}",
        doc_id=doc_id,
        filename=filename,
        severity=severity,
        title=title,
    )


def filters_summary(filters: Any) -> str:
    parts: list[str] = []
    categories = getattr(filters, "source_categories", None) or []
    kinds = getattr(filters, "kinds", None) or []
    vendor = getattr(filters, "vendor", None)
    if categories:
        parts.append(f"categories={','.join(categories[:4])}")
    if kinds:
        parts.append(f"kinds={','.join(kinds[:4])}")
    if vendor:
        parts.append(f"vendor={vendor}")
    return " · ".join(parts) if parts else "all uploads"


def append_warning(bundle: Any, raw: Any, *, label: str = "Document retrieval") -> None:
    from src.errors import to_executive_error

    err = to_executive_error(raw, context=label)
    bundle.redis_activity.append(
        document_activity(
            "warning",
            label=f"{label} warning",
            detail=err["message"],
            code=err["code"],
            action=err["action"],
        )
    )


def safe_detail(exc: Exception) -> str:
    return redact_secrets(exc)
