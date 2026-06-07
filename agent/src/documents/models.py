"""Typed contracts for uploaded documents, parse jobs, and retrieval filters."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ParseJobStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    DETECTING_TYPE = "detecting_type"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    PERSISTING = "persisting"
    INDEXING = "indexing"
    RECONCILING = "reconciling"
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class DocumentSourceCategory(str, enum.Enum):
    VENDOR_CONTRACT = "vendor_contract"
    INVOICE = "invoice"
    PROCUREMENT_NOTE = "procurement_note"
    HEADCOUNT_SHEET = "headcount_sheet"
    BOARD_APPROVAL = "board_approval"
    MISC_NOTE = "misc_note"
    LEDGER_EXPORT = "ledger_export"
    POLICY_DOC = "policy_doc"
    SECURITY_EVIDENCE = "security_evidence"
    CRM_EXPORT = "crm_export"
    FINANCING_MEMO = "financing_memo"


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str
    filename: str
    kind: str
    checksum: str
    source_category: str
    connector_id: Optional[str] = None
    vendor: Optional[str] = None
    parse_job_id: Optional[str] = None
    upload_batch_id: Optional[str] = None
    extraction_status: str = "ready"
    confidence: float = 1.0
    chunk_count: int = 0
    uploaded_at: str
    document_date: Optional[str] = None
    errors: list[str] = Field(default_factory=list)
    excerpt: str = ""


class DocumentChunk(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    doc_id: str
    text: str
    index: int
    filename: str
    kind: str
    source_category: str
    connector_id: Optional[str] = None
    vendor: Optional[str] = None
    parse_job_id: Optional[str] = None
    upload_batch_id: Optional[str] = None
    confidence: float = 1.0
    uploaded_at: str
    document_date: Optional[str] = None


class ParseJobRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str
    filename: str
    status: ParseJobStatus
    detected_kind: Optional[str] = None
    doc_id: Optional[str] = None
    connector_id: Optional[str] = None
    upload_batch_id: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class DocumentRetrievalFilter(BaseModel):
    """Optional combinable filters for source-aware chunk retrieval."""

    model_config = ConfigDict(extra="ignore")

    kinds: list[str] = Field(default_factory=list)
    connector_id: Optional[str] = None
    vendor: Optional[str] = None
    source_categories: list[str] = Field(default_factory=list)
    parse_job_id: Optional[str] = None
    upload_batch_id: Optional[str] = None
    min_confidence: Optional[float] = None
    max_freshness_days: Optional[int] = None
    uploaded_after: Optional[str] = None
    uploaded_before: Optional[str] = None
    document_date_after: Optional[str] = None
    document_date_before: Optional[str] = None


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
