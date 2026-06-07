"""Executive-safe degraded states — never hide failures, never leak secrets."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from src.env import redact_secrets

try:
    from src.integrations.file_validation import UploadValidationCode, UploadValidationError
except Exception:  # pragma: no cover — offline import smoke
    UploadValidationCode = None  # type: ignore
    UploadValidationError = Exception  # type: ignore


class ExecutiveStateCode(str, Enum):
    SERVICE_OFFLINE = "service_offline"
    REDIS_UNAVAILABLE = "redis_unavailable"
    PARSE_FAILED = "parse_failed"
    SOURCE_STALE = "source_stale"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MODEL_REFUSED = "model_refused"
    RECONCILIATION_BLOCKED = "reconciliation_blocked"
    UNSUPPORTED_FILE = "unsupported_file"
    MISSING_FACT = "missing_fact"


_CATALOG: dict[ExecutiveStateCode, dict[str, str]] = {
    ExecutiveStateCode.SERVICE_OFFLINE: {
        "title": "Service offline",
        "message": "The Atlas agent service is not reachable.",
        "action": "Start the demo server with scripts/dev-live.sh, then refresh.",
    },
    ExecutiveStateCode.REDIS_UNAVAILABLE: {
        "title": "Redis unavailable",
        "message": "The live system of record is not reachable.",
        "action": "Start Redis Stack with scripts/start-redis-stack.sh, then refresh.",
    },
    ExecutiveStateCode.PARSE_FAILED: {
        "title": "Parse failed",
        "message": "The uploaded file could not be parsed into searchable evidence.",
        "action": "Check the file format and try a supported export again.",
    },
    ExecutiveStateCode.SOURCE_STALE: {
        "title": "Source stale",
        "message": "One or more imported sources are older than the freshness window.",
        "action": "Re-import the source or note the staleness in the council brief.",
    },
    ExecutiveStateCode.INSUFFICIENT_EVIDENCE: {
        "title": "Insufficient evidence",
        "message": "Not enough grounded evidence was retrieved for this role.",
        "action": "Upload supporting documents or load connector sources before re-running.",
    },
    ExecutiveStateCode.MODEL_REFUSED: {
        "title": "Model refused",
        "message": "The model declined to complete this structured council step.",
        "action": "Rephrase the decision or reduce sensitive content, then retry.",
    },
    ExecutiveStateCode.RECONCILIATION_BLOCKED: {
        "title": "Reconciliation blocked",
        "message": "Reconciliation found material discrepancies that block a clean read.",
        "action": "Review open discrepancies in the data room before accepting the case.",
    },
    ExecutiveStateCode.UNSUPPORTED_FILE: {
        "title": "Unsupported file",
        "message": "This file type is not accepted for import.",
        "action": "Export CSV, JSON, Excel, PDF, DOCX, or plain text and try again.",
    },
    ExecutiveStateCode.MISSING_FACT: {
        "title": "Missing fact",
        "message": "A required fact for this decision type is not present in live data.",
        "action": "Load the missing connector source or upload the supporting document.",
    },
}


def executive_state(
    code: ExecutiveStateCode,
    *,
    message: Optional[str] = None,
    action: Optional[str] = None,
    detail: Any = None,
    context: Optional[str] = None,
) -> dict[str, Any]:
    meta = _CATALOG[code]
    payload: dict[str, Any] = {
        "code": code.value,
        "title": meta["title"],
        "message": message or meta["message"],
        "action": action or meta["action"],
    }
    if context:
        payload["context"] = context
    if detail is not None:
        payload["detail_redacted"] = redact_secrets(detail)
    return payload


def _match_text(raw: str) -> Optional[ExecutiveStateCode]:
    text = raw.lower()
    if re.search(r"redis.*(unreachable|not reachable|connection|refused|timeout)|redis is not", text):
        return ExecutiveStateCode.REDIS_UNAVAILABLE
    if re.search(r"failed to fetch|network error|service unavailable|503|502|connection refused|econnrefused", text):
        return ExecutiveStateCode.SERVICE_OFFLINE
    if re.search(r"refusal|refused|content.?filter|safety", text):
        return ExecutiveStateCode.MODEL_REFUSED
    if re.search(r"reconcil.*block|blocked.*reconcil|discrepanc", text):
        return ExecutiveStateCode.RECONCILIATION_BLOCKED
    if re.search(r"stale|freshness|outdated source", text):
        return ExecutiveStateCode.SOURCE_STALE
    if re.search(r"unsupported|not accepted|allowed.?kinds", text):
        return ExecutiveStateCode.UNSUPPORTED_FILE
    if re.search(r"parse|extract|index.*fail|pipeline_error|empty.?file|corrupt", text):
        return ExecutiveStateCode.PARSE_FAILED
    if re.search(r"missing.*fact|required.*fact|not present", text):
        return ExecutiveStateCode.MISSING_FACT
    if re.search(r"insufficient|no evidence|no hits|empty bundle", text):
        return ExecutiveStateCode.INSUFFICIENT_EVIDENCE
    return None


def to_executive_error(
    raw: Any,
    *,
    context: Optional[str] = None,
    default: ExecutiveStateCode = ExecutiveStateCode.SERVICE_OFFLINE,
) -> dict[str, Any]:
    if isinstance(raw, UploadValidationError):
        code = (
            ExecutiveStateCode.UNSUPPORTED_FILE
            if raw.code in {UploadValidationCode.UNSUPPORTED_TYPE, UploadValidationCode.EXTENSION_MISMATCH}  # type: ignore[union-attr]
            else ExecutiveStateCode.PARSE_FAILED
        )
        return executive_state(code, message=raw.message, detail=raw.as_detail(), context=context)

    if isinstance(raw, dict) and raw.get("code") in {item.value for item in ExecutiveStateCode}:
        code = ExecutiveStateCode(str(raw["code"]))
        return executive_state(
            code,
            message=str(raw.get("message") or ""),
            action=str(raw.get("action") or "") or None,
            detail=raw.get("detail_redacted") or raw.get("detail"),
            context=context,
        )

    text = redact_secrets(raw)
    code = _match_text(text) or default
    return executive_state(code, detail=text, context=context)


def http_exception_detail(raw: Any, *, context: Optional[str] = None, status_hint: int | None = None) -> dict[str, Any]:
    detail = to_executive_error(raw, context=context)
    if status_hint is not None:
        detail["status"] = status_hint
    return detail
