"""Parse uploaded files into indexed Redis documents with streaming job status."""

from __future__ import annotations

import asyncio
from typing import Optional

from src import redis_layer as R
from src.documents.categories import infer_source_category, infer_vendor
from src.documents.extract import chunk_text, extract_text
from src.documents.models import (
    DocumentChunk,
    DocumentMetadata,
    ParseJobRecord,
    ParseJobStatus,
    utc_now,
)
from src.documents.store import (
    checksum,
    create_batch_id,
    create_doc_id,
    create_job_id,
    get_parse_job,
    save_document,
    save_parse_job,
    update_parse_job_status,
)
from src.env import redact_secrets
from src.integrations.file_validation import (
    CONNECTOR_UPLOAD_KINDS,
    UploadValidationCode,
    UploadValidationError,
    validate_upload,
)


DOCUMENT_UPLOAD_KINDS = frozenset({"csv", "json", "jsonl", "xlsx", "xls", "docx", "pdf", "txt", "md"})


def create_parse_job(
    *,
    filename: str,
    connector_id: Optional[str] = None,
    upload_batch_id: Optional[str] = None,
) -> ParseJobRecord:
    now = utc_now()
    record = ParseJobRecord(
        job_id=create_job_id(),
        filename=filename,
        status=ParseJobStatus.UPLOADED,
        connector_id=connector_id,
        upload_batch_id=upload_batch_id or create_batch_id(),
        created_at=now,
        updated_at=now,
        timeline=[{"status": ParseJobStatus.UPLOADED.value, "at": now}],
    )
    return save_parse_job(record)


def _allowed_kinds(connector_id: Optional[str]) -> frozenset[str]:
    if connector_id:
        return CONNECTOR_UPLOAD_KINDS
    return DOCUMENT_UPLOAD_KINDS


def run_parse_pipeline(
    job_id: str,
    raw: bytes,
    *,
    filename: str,
    content_type: Optional[str] = None,
    connector_id: Optional[str] = None,
    reconcile: bool = True,
) -> DocumentMetadata:
    """Run the full parse pipeline synchronously (also used by background tasks)."""
    from src.integrations import service as OPS

    job = get_parse_job(job_id)
    if job is None:
        raise KeyError(f"parse job not found: {job_id}")

    try:
        update_parse_job_status(job_id, ParseJobStatus.DETECTING_TYPE)
        allowed = _allowed_kinds(connector_id)
        detected_kind, _source_format = validate_upload(
            raw,
            filename=filename,
            content_type=content_type,
            allowed_kinds=allowed,
        )
        update_parse_job_status(job_id, ParseJobStatus.DETECTING_TYPE, detected_kind=detected_kind)

        update_parse_job_status(job_id, ParseJobStatus.EXTRACTING)
        text, confidence = extract_text(raw, detected_kind)
        if not text.strip():
            raise UploadValidationError(
                code=UploadValidationCode.EMPTY_FILE,
                message="No extractable text found in upload.",
                detected_kind=detected_kind,
            )

        update_parse_job_status(job_id, ParseJobStatus.VALIDATING)
        source_category = infer_source_category(filename, detected_kind, connector_id=connector_id)
        vendor = infer_vendor(filename, text)
        doc_id = create_doc_id()
        now = utc_now()
        excerpt = text[:1200]

        update_parse_job_status(job_id, ParseJobStatus.PERSISTING, doc_id=doc_id)
        chunks_raw = chunk_text(text)
        chunk_models: list[DocumentChunk] = []
        for index, chunk in enumerate(chunks_raw):
            chunk_models.append(
                DocumentChunk(
                    chunk_id=f"{doc_id}:chunk:{index}",
                    doc_id=doc_id,
                    text=chunk,
                    index=index,
                    filename=filename,
                    kind=detected_kind,
                    source_category=source_category.value,
                    connector_id=connector_id,
                    vendor=vendor,
                    parse_job_id=job_id,
                    upload_batch_id=job.upload_batch_id,
                    confidence=confidence,
                    uploaded_at=now,
                )
            )

        update_parse_job_status(job_id, ParseJobStatus.INDEXING)
        embeddings = R.embed_texts([chunk.text for chunk in chunk_models]) if chunk_models else []
        meta = DocumentMetadata(
            doc_id=doc_id,
            filename=filename,
            kind=detected_kind,
            checksum=checksum(raw),
            source_category=source_category.value,
            connector_id=connector_id,
            vendor=vendor,
            parse_job_id=job_id,
            upload_batch_id=job.upload_batch_id,
            extraction_status="ready",
            confidence=confidence,
            chunk_count=len(chunk_models),
            uploaded_at=now,
            excerpt=excerpt,
        )
        save_document(meta, chunk_models, embeddings)

        from src.documents.activity import indexed_activity, publish_document_activity

        indexed_event = indexed_activity(
            filename=filename,
            doc_id=doc_id,
            source_category=source_category.value,
            chunk_count=len(chunk_models),
        )
        publish_document_activity(indexed_event)

        final_status = ParseJobStatus.READY
        if confidence < 0.8:
            final_status = ParseJobStatus.NEEDS_REVIEW

        if reconcile and connector_id:
            update_parse_job_status(job_id, ParseJobStatus.RECONCILING, doc_id=doc_id)
            report = OPS.run_reconciliation()
            from src.documents.activity import discrepancy_created_activity, publish_document_activity

            for disc in (report.discrepancies if report else [])[:6]:
                severity = str(getattr(disc.severity, "value", disc.severity))
                if severity == "info":
                    continue
                publish_document_activity(
                    discrepancy_created_activity(
                        doc_id=doc_id,
                        filename=filename,
                        title=disc.title,
                        severity=severity,
                    )
                )

        update_parse_job_status(job_id, final_status, doc_id=doc_id, detected_kind=detected_kind)
        return meta
    except UploadValidationError as exc:
        update_parse_job_status(
            job_id,
            ParseJobStatus.FAILED,
            error=exc.message,
            error_code=exc.code.value,
        )
        raise
    except Exception as exc:
        update_parse_job_status(
            job_id,
            ParseJobStatus.FAILED,
            error=redact_secrets(exc),
            error_code="pipeline_error",
        )
        raise


async def run_parse_pipeline_async(**kwargs) -> DocumentMetadata:
    return await asyncio.to_thread(run_parse_pipeline, **kwargs)


def enqueue_document_from_connector_upload(
    *,
    raw: bytes,
    filename: str,
    connector_id: str,
    content_type: Optional[str] = None,
) -> ParseJobRecord:
    """Create a parse job for connector uploads; caller runs pipeline in background."""
    job = create_parse_job(filename=filename, connector_id=connector_id)
    return job
