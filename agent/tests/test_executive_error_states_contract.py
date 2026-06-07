from __future__ import annotations

import os

from src.env import redact_secrets
from src.errors import ExecutiveStateCode, executive_state, to_executive_error
from src.integrations.file_validation import UploadValidationCode, UploadValidationError


def test_executive_state_catalog_has_actionable_copy() -> None:
    payload = executive_state(ExecutiveStateCode.REDIS_UNAVAILABLE)
    assert payload["code"] == "redis_unavailable"
    assert payload["title"] == "Redis unavailable"
    assert payload["action"]
    assert "message" in payload


def test_upload_validation_maps_to_unsupported_file() -> None:
    exc = UploadValidationError(
        code=UploadValidationCode.UNSUPPORTED_TYPE,
        message="Only CSV exports are accepted for this connector.",
        detected_kind="pdf",
        allowed_kinds=("csv",),
    )
    payload = to_executive_error(exc, context="connector upload")
    assert payload["code"] == "unsupported_file"
    assert "CSV" in payload["message"] or "accepted" in payload["message"].lower()


def test_redis_failure_maps_to_redis_unavailable() -> None:
    payload = to_executive_error(ConnectionError("Redis connection refused on localhost:6379"))
    assert payload["code"] == "redis_unavailable"


def test_model_refusal_maps_cleanly() -> None:
    payload = to_executive_error("Model refusal: policy violation on structured output")
    assert payload["code"] == "model_refused"


def test_executive_errors_never_echo_secrets() -> None:
    secret = os.environ.get("OPENAI_API_KEY", "").strip()
    if not secret or len(secret) < 8:
        payload = to_executive_error("upstream failed with token [redacted]")
        assert "[redacted]" in redact_secrets(str(payload))
        return
    payload = to_executive_error(f"upstream failed with token {secret}")
    serialized = redact_secrets(str(payload))
    assert secret not in serialized
    assert "[redacted]" in serialized
