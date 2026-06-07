"""Shared helpers for document store contract tests."""

from __future__ import annotations

import hashlib
import struct

from src import redis_layer as R
from src.documents.models import DocumentChunk, DocumentMetadata, utc_now
from src.documents.store import META_PREFIX, REGISTRY_KEY, delete_document, ensure_indexes, save_document


def fake_embedding(text: str, dim: int = R.EMBED_DIM) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < dim:
        for index in range(0, len(digest), 4):
            chunk = digest[index : index + 4]
            if len(chunk) < 4:
                chunk = chunk.ljust(4, b"\0")
            values.append(struct.unpack("<I", chunk)[0] / 2**32)
            if len(values) >= dim:
                break
        digest = hashlib.sha256(digest).digest()
    return values[:dim]


def reset_document_store() -> None:
    ensure_indexes()
    for doc_id in list(R.client().smembers(REGISTRY_KEY)):
        delete_document(doc_id)


def seed_document(
    *,
    filename: str,
    kind: str,
    source_category: str,
    text: str,
    connector_id: str | None = None,
    vendor: str | None = None,
    confidence: float = 0.95,
) -> DocumentMetadata:
    ensure_indexes()
    doc_id = f"doc-test-{hashlib.sha1(filename.encode()).hexdigest()[:10]}"
    now = utc_now()
    chunks_raw = [text] if text else ["empty"]
    chunks = [
        DocumentChunk(
            chunk_id=f"{doc_id}:chunk:0",
            doc_id=doc_id,
            text=chunks_raw[0],
            index=0,
            filename=filename,
            kind=kind,
            source_category=source_category,
            connector_id=connector_id,
            vendor=vendor,
            confidence=confidence,
            uploaded_at=now,
        )
    ]
    meta = DocumentMetadata(
        doc_id=doc_id,
        filename=filename,
        kind=kind,
        checksum=hashlib.sha256(text.encode()).hexdigest(),
        source_category=source_category,
        connector_id=connector_id,
        vendor=vendor,
        confidence=confidence,
        chunk_count=1,
        uploaded_at=now,
        excerpt=text[:500],
    )
    embeddings = [fake_embedding(chunks[0].text)]
    save_document(meta, chunks, embeddings)
    return meta
