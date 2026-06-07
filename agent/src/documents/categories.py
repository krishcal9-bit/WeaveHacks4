"""Heuristics for mapping uploads to source categories."""

from __future__ import annotations

import re
from typing import Optional

from src.documents.models import DocumentSourceCategory

_CONNECTOR_DEFAULTS: dict[str, DocumentSourceCategory] = {
    "ledger": DocumentSourceCategory.LEDGER_EXPORT,
    "invoices": DocumentSourceCategory.INVOICE,
    "vendor_export": DocumentSourceCategory.VENDOR_CONTRACT,
    "crm_opportunities": DocumentSourceCategory.CRM_EXPORT,
    "headcount_plan": DocumentSourceCategory.HEADCOUNT_SHEET,
    "security_evidence": DocumentSourceCategory.SECURITY_EVIDENCE,
    "board_policy": DocumentSourceCategory.POLICY_DOC,
}


def infer_source_category(
    filename: str,
    detected_kind: str,
    *,
    connector_id: Optional[str] = None,
    vendor: Optional[str] = None,
) -> DocumentSourceCategory:
    name = (filename or "").lower()
    if connector_id and connector_id in _CONNECTOR_DEFAULTS:
        base = _CONNECTOR_DEFAULTS[connector_id]
    else:
        base = DocumentSourceCategory.MISC_NOTE

    if any(token in name for token in ("contract", "msa", "sow", "renewal", "vendor")):
        return DocumentSourceCategory.VENDOR_CONTRACT
    if any(token in name for token in ("invoice", "bill", "ap_", "ar_")):
        return DocumentSourceCategory.INVOICE
    if any(token in name for token in ("procurement", "negotiation", "rfp", "quote", "po_")):
        return DocumentSourceCategory.PROCUREMENT_NOTE
    if any(token in name for token in ("headcount", "hiring", "req_", "open_roles", "hc_")):
        return DocumentSourceCategory.HEADCOUNT_SHEET
    if any(token in name for token in ("board", "approval", "memo", "resolution")):
        if any(token in name for token in ("financ", "bridge", "term sheet", "raise", "debt", "equity")):
            return DocumentSourceCategory.FINANCING_MEMO
        return DocumentSourceCategory.BOARD_APPROVAL
    if any(token in name for token in ("security", "soc2", "audit", "pen test", "pentest", "vuln")):
        return DocumentSourceCategory.SECURITY_EVIDENCE
    if any(token in name for token in ("ledger", "gl_", "journal", "bank")):
        return DocumentSourceCategory.LEDGER_EXPORT
    if any(token in name for token in ("policy", "governance", "compliance")):
        return DocumentSourceCategory.POLICY_DOC
    if any(token in name for token in ("pipeline", "crm", "opportunity", "deal")):
        return DocumentSourceCategory.CRM_EXPORT
    if any(token in name for token in ("financ", "bridge", "term sheet", "capital", "runway")):
        return DocumentSourceCategory.FINANCING_MEMO

    if vendor and re.search(r"contract|renewal", name):
        return DocumentSourceCategory.VENDOR_CONTRACT
    return base


def infer_vendor(filename: str, text_excerpt: str = "") -> Optional[str]:
    combined = f"{filename}\n{text_excerpt[:500]}".lower()
    for pattern in (
        r"\b(datadog|snowflake|aws|azure|google cloud|gcp|salesforce|hubspot|okta|zoom|slack)\b",
        r"vendor[:\s]+([a-z0-9][a-z0-9 _.-]{2,40})",
    ):
        match = re.search(pattern, combined, flags=re.IGNORECASE)
        if match:
            return match.group(1 if match.lastindex else 0).strip().title()
    return None
