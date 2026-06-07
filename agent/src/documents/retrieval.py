"""Decision-type aware document retrieval plans for council evidence gathering."""

from __future__ import annotations

from typing import Any, Optional

from src.documents.models import DocumentRetrievalFilter
from src.structured_models import DecisionType, RoleEvidencePlan


def document_plan_for_decision(
    decision_type: DecisionType | str,
    *,
    role: str,
    entities: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Return document query/category/kind hints for a role and decision type."""
    dt = DecisionType(decision_type) if isinstance(decision_type, str) else decision_type
    vendor = next((entity for entity in (entities or []) if any(ch.isalpha() for ch in entity)), None)

    if dt == DecisionType.vendor_renewal:
        categories = ["vendor_contract", "invoice", "procurement_note"]
        kinds = ["pdf", "docx", "txt", "csv"]
        queries = [
            f"{vendor or 'vendor'} contract renewal terms notice auto-renew pricing",
            f"{vendor or 'vendor'} invoice billing cadence renewal procurement notes",
        ]
        rationale = "Renewals need contract PDFs, invoice evidence, and procurement notes — not hiring or security exports."
    elif dt == DecisionType.hiring_plan:
        categories = ["headcount_sheet", "board_approval"]
        kinds = ["csv", "xlsx", "xls", "pdf", "docx"]
        queries = [
            "headcount plan open reqs start dates fully loaded cost",
            "board approval headcount hiring plan req approvals",
        ]
        rationale = "Hiring decisions need headcount sheets and board approvals, not vendor contracts."
    elif dt == DecisionType.security_blocker:
        categories = ["security_evidence", "policy_doc"]
        kinds = ["json", "pdf", "docx", "txt", "md"]
        queries = [
            "security evidence audit finding control gap blocker remediation",
            "security policy exception approval attestation",
        ]
        rationale = "Security blockers need security evidence and policy docs, not procurement spreadsheets."
    elif dt == DecisionType.financing_scenario:
        categories = ["financing_memo", "board_approval", "policy_doc"]
        kinds = ["pdf", "docx", "txt", "md"]
        queries = [
            "financing bridge term sheet runway covenant board memo",
            "capital allocation treasury downside liquidity scenario",
        ]
        rationale = "Financing scenarios need board/financing memos and treasury policy context."
    else:
        categories = []
        kinds = []
        queries = []
        rationale = "No uploaded-document retrieval required for this decision type."

    role_queries = queries[:2] if queries else []
    if role == "procurement" and dt == DecisionType.vendor_renewal:
        role_queries = queries
    elif role == "risk" and dt in {DecisionType.security_blocker, DecisionType.hiring_plan}:
        role_queries = queries
    elif role == "treasury" and dt == DecisionType.financing_scenario:
        role_queries = queries

    return {
        "document_queries": role_queries,
        "document_source_categories": categories,
        "document_kinds": kinds,
        "document_rationale": rationale,
    }


def build_retrieval_filter(
    role_plan: RoleEvidencePlan,
    *,
    decision_type: DecisionType | str,
    entities: Optional[list[str]] = None,
    parse_job_id: Optional[str] = None,
    upload_batch_id: Optional[str] = None,
    max_freshness_days: int = 120,
    min_confidence: float = 0.5,
) -> DocumentRetrievalFilter:
    hints = document_plan_for_decision(decision_type, role=role_plan.role, entities=entities)
    categories = role_plan.document_source_categories or hints["document_source_categories"]
    kinds = role_plan.document_kinds or hints["document_kinds"]
    vendor = next(
        (
            entity
            for entity in (entities or [])
            if entity and not entity.startswith("$") and any(ch.isalpha() for ch in entity)
        ),
        None,
    )
    return DocumentRetrievalFilter(
        kinds=kinds,
        source_categories=categories,
        vendor=vendor,
        parse_job_id=parse_job_id,
        upload_batch_id=upload_batch_id,
        min_confidence=min_confidence,
        max_freshness_days=max_freshness_days,
    )
