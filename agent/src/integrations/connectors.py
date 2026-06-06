"""
Connector registry + structured file parsers for finance-operations feeds.

Each connector is configured by a single environment variable that points at a
CSV or JSON export. If the variable is unset the connector is **not configured**
(it never invents data); if it is set but the file is missing, that is surfaced
as an explicit blocker. Parsing is delegated to the typed models in
:mod:`src.integrations.models` via the stdlib ``csv``/``json`` parsers — there is
no ad hoc line-splitting.

Future API-backed connectors (Stripe, NetSuite, Salesforce, …) can be added by
extending :data:`CONNECTORS`; until one is genuinely wired and verified it must
keep ``Origin.LIVE_API`` out of its reported status.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from src.integrations.models import (
    RECORD_MODELS,
    Origin,
    SourceFormat,
    SourceType,
    ValidationIssue,
    _SourceRecord,
)


@dataclass(frozen=True)
class ConnectorSpec:
    connector_id: str
    source_type: SourceType
    env_var: str
    fixture_filename: str
    description: str

    @property
    def record_model(self) -> type[_SourceRecord]:
        return RECORD_MODELS[self.source_type]


# The connector taxonomy. connector_id == source_type value for a 1:1 mapping.
CONNECTORS: dict[str, ConnectorSpec] = {
    "ledger": ConnectorSpec(
        "ledger", SourceType.LEDGER, "ATLAS_LEDGER_FILE", "ledger.csv",
        "General ledger / cash transactions (CSV or JSON).",
    ),
    "invoices": ConnectorSpec(
        "invoices", SourceType.INVOICES, "ATLAS_INVOICES_FILE", "invoices.csv",
        "Accounts-payable vendor invoices (CSV or JSON).",
    ),
    "vendor_export": ConnectorSpec(
        "vendor_export", SourceType.VENDOR_EXPORT, "ATLAS_VENDOR_EXPORT_FILE", "vendor_export.json",
        "Vendor / procurement contract export (CSV or JSON).",
    ),
    "crm_opportunities": ConnectorSpec(
        "crm_opportunities", SourceType.CRM_OPPORTUNITIES, "ATLAS_CRM_FILE", "crm_opportunities.csv",
        "CRM pipeline opportunity export (CSV or JSON).",
    ),
    "headcount_plan": ConnectorSpec(
        "headcount_plan", SourceType.HEADCOUNT_PLAN, "ATLAS_HEADCOUNT_FILE", "headcount_plan.csv",
        "Actual / updated headcount plan from HRIS or planning sheet (CSV or JSON).",
    ),
    "security_evidence": ConnectorSpec(
        "security_evidence", SourceType.SECURITY_EVIDENCE, "ATLAS_SECURITY_FILE", "security_evidence.json",
        "Security / compliance control evidence (CSV or JSON).",
    ),
    "board_policy": ConnectorSpec(
        "board_policy", SourceType.BOARD_POLICY, "ATLAS_BOARD_POLICY_FILE", "board_policies.json",
        "Board policy / constraint documents with optional machine-checkable rules (CSV or JSON).",
    ),
}


def fixtures_dir() -> Path:
    """Directory holding the opt-in Acme demo operating-data fixtures."""
    return Path(__file__).resolve().parents[1] / "data" / "fixtures"


def configured_path(spec: ConnectorSpec) -> Optional[str]:
    """The source path from the connector's env var, or None when unset."""
    value = os.getenv(spec.env_var, "").strip()
    return value or None


def fixture_path(spec: ConnectorSpec) -> Path:
    return fixtures_dir() / spec.fixture_filename


def resolve_origin(path: Path) -> Origin:
    """Classify a file path as a bundled demo fixture vs. an external export."""
    try:
        path.resolve().relative_to(fixtures_dir().resolve())
        return Origin.ACME_DEMO_FIXTURE
    except (ValueError, OSError):
        return Origin.EXTERNAL_FILE


def detect_format(path: Path, override: Optional[SourceFormat] = None) -> SourceFormat:
    if override is not None:
        return override
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return SourceFormat.CSV
    if suffix in (".json", ".jsonl"):
        return SourceFormat.JSON
    raise ValueError(f"unsupported source format for {path.name!r}; use .csv or .json")


def checksum_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_timestamp(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _rows_from_csv(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def _rows_from_json(raw: bytes, source_type: SourceType) -> list[dict[str, Any]]:
    data = json.loads(raw.decode("utf-8-sig"))
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        # Accept {"records": [...]} or {"<source_type>": [...]} envelopes.
        rows = data.get("records")
        if rows is None:
            rows = data.get(source_type.value)
        if rows is None:
            raise ValueError(
                f"JSON object must contain a 'records' or '{source_type.value}' array"
            )
    else:
        raise ValueError("JSON source must be an array or an object with a 'records' array")
    if not isinstance(rows, list):
        raise ValueError("expected a JSON array of records")
    return [row for row in rows if isinstance(row, dict)]


def parse_records(
    spec: ConnectorSpec,
    raw: bytes,
    fmt: SourceFormat,
) -> tuple[list[_SourceRecord], list[ValidationIssue], int]:
    """Parse + validate raw bytes into typed records.

    Returns ``(records, issues, duplicate_count)``. Rows that fail validation are
    reported as :class:`ValidationIssue` (with row/field context) and dropped;
    duplicate ``record_key`` values keep the first occurrence and are counted.
    """
    if fmt is SourceFormat.CSV:
        rows = _rows_from_csv(raw)
    else:
        rows = _rows_from_json(raw, spec.source_type)

    model = spec.record_model
    records: list[_SourceRecord] = []
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    duplicates = 0

    for index, row in enumerate(rows):
        location = f"row {index + 2}" if fmt is SourceFormat.CSV else f"record {index + 1}"
        try:
            record = model.model_validate(row)
        except ValidationError as exc:
            for err in exc.errors():
                field = ".".join(str(part) for part in err.get("loc", ())) or None
                issues.append(ValidationIssue(location=location, field=field, message=err.get("msg", "invalid")))
            continue
        except (ValueError, TypeError) as exc:
            issues.append(ValidationIssue(location=location, message=str(exc)))
            continue
        key = record.record_key()
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        records.append(record)

    return records, issues, duplicates
