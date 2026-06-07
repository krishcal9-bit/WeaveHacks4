"""
Orchestration for finance-operations ingestion + reconciliation.

This is the single public surface the API, CLI, and LangChain tools call. It ties
together :mod:`connectors` (parse), :mod:`store` (persist with provenance), and
:mod:`reconcile` (compare against the seeded system of record), and computes a
transparent confidence score over the imported picture.

Live-only contract: importing requires Redis (to persist) but *not* the LLM/Weave
stack — connectors are independent of model availability. Nothing is fabricated:
unconfigured connectors persist a ``not_configured`` provenance with blockers, and
reconciliation reports missing sources rather than assuming a clean pass.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src import redis_layer as R
from src.env import redact_secrets
from src.integrations import connectors as C
from src.integrations import store
from src.integrations.models import (
    BoardPolicyDoc,
    CrmOpportunity,
    HeadcountPlanRow,
    ImportConfidence,
    ImportProvenance,
    ImportResult,
    ImportStatus,
    Invoice,
    LedgerEntry,
    Origin,
    ReconciliationReport,
    SecurityEvidence,
    SourceFormat,
    SourceConfidence,
    SourceType,
    VendorRecord,
)
from src.integrations.crm_pipeline_quality import summarize_pipeline_quality
from src.integrations.headcount_quality import summarize_headcount_quality
from src.integrations.invoice_messiness import summarize_invoice_messiness
from src.integrations.ledger_normalization import summarize_ledger_normalization
from src.integrations.reconcile import run_workflows

COMPANY_KEY = f"{R.NS}:company:northwind"
IMPORTED_STATUSES = {"imported", "partial", "skipped_unchanged"}
SOURCE_STALE_DAYS = 45

REQUIRED_FACTS_BY_SOURCE: dict[str, list[str]] = {
    SourceType.LEDGER.value: ["cash movement timing", "bank-style transaction descriptions", "normalized vendor/category fields"],
    SourceType.INVOICES.value: ["invoice due dates", "payment status/open balance", "PO or approval linkage"],
    SourceType.VENDOR_EXPORT.value: ["contract renewal date", "billing terms", "auto-renewal/termination clauses"],
    SourceType.CRM_OPPORTUNITIES.value: ["probability and stage", "close date freshness", "weighted and unweighted ARR"],
    SourceType.HEADCOUNT_PLAN.value: ["approval status", "start date", "fully loaded monthly cost"],
    SourceType.SECURITY_EVIDENCE.value: ["control status", "evidence date", "blocked ARR or obligation linkage"],
    SourceType.BOARD_POLICY.value: ["policy ID", "approval route", "required evidence"],
}

SEVERITY_WEIGHTS = {"info": 0.0, "low": 1.0, "medium": 2.5, "high": 5.0, "critical": 8.0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redact_path(path: Optional[str]) -> Optional[str]:
    return redact_secrets(path) if path else path


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _source_age_days(doc: dict[str, Any]) -> Optional[float]:
    dt = _parse_datetime(doc.get("source_timestamp") or doc.get("imported_at"))
    if not dt:
        return None
    return max(0.0, round((_now() - dt).total_seconds() / 86400, 1))


def _freshness_factor(age_days: Optional[float]) -> float:
    if age_days is None:
        return 0.72
    if age_days <= 7:
        return 1.0
    if age_days <= SOURCE_STALE_DAYS:
        return max(0.78, 1.0 - ((age_days - 7) / SOURCE_STALE_DAYS) * 0.22)
    return max(0.0, 0.78 - ((age_days - SOURCE_STALE_DAYS) / 180.0) * 0.78)


def _source_confidence(connector_id: str, doc: dict[str, Any] | None = None) -> SourceConfidence:
    spec = C.CONNECTORS[connector_id]
    data = dict(doc or {})
    status = str(data.get("status") or ImportStatus.NOT_CONFIGURED.value)
    accepted = int(data.get("accepted_count") or 0)
    rejected = int(data.get("rejected_count") or 0)
    duplicates = int(data.get("duplicate_count") or 0)
    reconciliation_status = str(data.get("reconciliation_status") or "pending")
    age_days = _source_age_days(data)
    required_missing = [] if status in IMPORTED_STATUSES and accepted > 0 else list(REQUIRED_FACTS_BY_SOURCE.get(spec.source_type.value, []))
    reasons: list[str] = []

    if required_missing:
        reasons.append("required facts missing: " + ", ".join(required_missing[:2]))
    if status == "partial":
        reasons.append("partial import")
    elif status not in IMPORTED_STATUSES:
        reasons.append(status.replace("_", " "))
    if rejected:
        reasons.append(f"{rejected} validation failure{'s' if rejected != 1 else ''}")
    if duplicates:
        reasons.append(f"{duplicates} duplicate key{'s' if duplicates != 1 else ''}")
    if age_days is None and status in IMPORTED_STATUSES:
        reasons.append("source age unknown")
    elif age_days is not None and age_days > SOURCE_STALE_DAYS:
        reasons.append(f"source {age_days:g}d old")
    if reconciliation_status == "needs_review":
        reasons.append("reconciliation needs review")
    for blocker in (data.get("blockers") or [])[:2]:
        if isinstance(blocker, str) and blocker not in reasons:
            reasons.append(blocker)

    if status not in IMPORTED_STATUSES or accepted <= 0:
        score = 0
    else:
        total_rows = max(accepted + rejected, 1)
        validation_penalty = min(34.0, (rejected / total_rows) * 70.0)
        duplicate_penalty = min(22.0, (duplicates / max(accepted, 1)) * 90.0)
        freshness_penalty = (1.0 - _freshness_factor(age_days)) * 28.0
        reconciliation_penalty = 14.0 if reconciliation_status == "needs_review" else 0.0
        blocker_penalty = min(16.0, len(data.get("blockers") or []) * 4.0)
        partial_penalty = 6.0 if status == "partial" else 0.0
        score = round(max(0.0, 100.0 - validation_penalty - duplicate_penalty - freshness_penalty - reconciliation_penalty - blocker_penalty - partial_penalty))

    return SourceConfidence(
        connector_id=connector_id,
        source_type=spec.source_type,
        score=int(score),
        status=status,
        freshness_days=age_days,
        accepted_count=accepted,
        rejected_count=rejected,
        duplicate_count=duplicates,
        reconciliation_status=reconciliation_status,
        required_facts_missing=required_missing,
        reasons=reasons[:8],
    )


def _existing_good(connector_id: str) -> Optional[dict[str, Any]]:
    """Return a prior provenance doc only if it represents real imported data."""
    doc = store.load_provenance(connector_id)
    if doc and doc.get("status") in ("imported", "partial", "skipped_unchanged") and (doc.get("accepted_count") or 0) > 0:
        return doc
    return None


def _attach_source_summaries(provenance: ImportProvenance, payload: list[dict[str, Any]]) -> None:
    """Persist parser-derived source quality metadata on source provenance."""
    if provenance.source_type is SourceType.LEDGER:
        provenance.normalization_summary = summarize_ledger_normalization(payload)
    if provenance.source_type is SourceType.INVOICES:
        provenance.messiness_summary = summarize_invoice_messiness(payload)
    if provenance.source_type is SourceType.CRM_OPPORTUNITIES:
        provenance.pipeline_quality_summary = summarize_pipeline_quality(payload)
    if provenance.source_type is SourceType.HEADCOUNT_PLAN:
        provenance.headcount_quality_summary = summarize_headcount_quality(payload)


def _attach_parse_metadata(provenance: ImportProvenance, metadata: C.ParseMetadata, source_name: Optional[str]) -> None:
    if metadata.workbook_sheet:
        provenance.workbook_name = metadata.workbook_name or source_name
        provenance.workbook_sheet = metadata.workbook_sheet
        provenance.workbook_sheets = list(metadata.workbook_sheets)
        provenance.header_row_number = metadata.header_row_number
        provenance.hidden_column_count = metadata.hidden_column_count
        provenance.extra_column_count = metadata.extra_column_count


def _copy_prior_parse_metadata(provenance: ImportProvenance, prior: dict[str, Any]) -> None:
    provenance.workbook_name = prior.get("workbook_name")
    provenance.workbook_sheet = prior.get("workbook_sheet")
    provenance.workbook_sheets = prior.get("workbook_sheets") or []
    provenance.header_row_number = prior.get("header_row_number")
    provenance.hidden_column_count = int(prior.get("hidden_column_count") or 0)
    provenance.extra_column_count = int(prior.get("extra_column_count") or 0)


# --------------------------------------------------------------------------- #
# Import pipeline
# --------------------------------------------------------------------------- #
def import_connector(
    spec: C.ConnectorSpec,
    *,
    demo: bool = False,
    explicit_path: Optional[str] = None,
    fmt_override: Optional[SourceFormat] = None,
) -> ImportResult:
    """Import one connector.

    A successful parse (or an unchanged-checksum re-run) persists provenance +
    dataset. A failed/unconfigured attempt persists a status only when no prior
    successful import exists — it never clobbers previously imported data, so the
    last-known-good dataset survives an accidental no-source ``import``.
    """
    # 1) Resolve the source path: explicit > demo fixture > env var.
    if explicit_path:
        path_str: Optional[str] = explicit_path
    elif demo:
        fixture = C.fixture_path(spec)
        path_str = str(fixture) if fixture.exists() else None
    else:
        path_str = C.configured_path(spec)

    base = ImportProvenance(
        connector_id=spec.connector_id,
        source_type=spec.source_type,
        origin=Origin.EXTERNAL_FILE,
        status=ImportStatus.NOT_CONFIGURED,
        env_var=spec.env_var,
    )

    def _fail(status: ImportStatus, blockers: list[str]) -> ImportResult:
        """Report a non-success outcome without destroying a prior good import."""
        prior = _existing_good(spec.connector_id)
        if prior is not None:
            prov = ImportProvenance.model_validate(prior)
            prov.blockers = blockers  # surface the current attempt (not persisted)
            return ImportResult(provenance=prov, records=store.load_dataset(spec.connector_id))
        base.status = status
        base.blockers = blockers
        store.save_provenance(base)
        return ImportResult(provenance=base)

    if not path_str:
        return _fail(
            ImportStatus.NOT_CONFIGURED,
            [
                f"Not configured. Set {spec.env_var} to a CSV/JSON export "
                f"(or run with --demo to load the bundled Acme fixture)."
            ],
        )

    path = Path(path_str)
    base.origin = C.resolve_origin(path)
    base.source_path = str(path)
    base.source_name = path.name

    if not path.exists():
        return _fail(ImportStatus.MISSING_FILE, [f"Configured path does not exist: {_redact_path(str(path))}"])

    # 2) Read + checksum (idempotency).
    try:
        raw = path.read_bytes()
        fmt = C.detect_format(path, fmt_override)
    except (OSError, ValueError) as exc:
        return _fail(ImportStatus.ERROR, [redact_secrets(exc)])

    checksum = C.checksum_bytes(raw)
    base.checksum_sha256 = checksum
    base.source_format = fmt
    base.source_timestamp = C.file_timestamp(path)

    if store.existing_checksum(spec.connector_id) == checksum:
        prior = store.load_provenance(spec.connector_id) or {}
        base.status = ImportStatus.SKIPPED_UNCHANGED
        base.record_count = int(prior.get("record_count") or 0)
        base.accepted_count = int(prior.get("accepted_count") or 0)
        base.rejected_count = int(prior.get("rejected_count") or 0)
        base.duplicate_count = int(prior.get("duplicate_count") or 0)
        _copy_prior_parse_metadata(base, prior)
        base.normalization_summary = prior.get("normalization_summary") or {}
        base.messiness_summary = prior.get("messiness_summary") or {}
        base.pipeline_quality_summary = prior.get("pipeline_quality_summary") or {}
        base.headcount_quality_summary = prior.get("headcount_quality_summary") or {}
        base.imported_at = _now()
        store.save_provenance(base)
        return ImportResult(provenance=base, records=store.load_dataset(spec.connector_id))

    # 3) Parse + validate.
    try:
        records, issues, duplicates, metadata = C.parse_records_with_metadata(spec, raw, fmt)
    except (ValueError, TypeError) as exc:
        return _fail(ImportStatus.ERROR, [redact_secrets(exc)])

    payload = [r.model_dump(mode="json") for r in records]
    _attach_parse_metadata(base, metadata, path.name)
    _attach_source_summaries(base, payload)
    base.imported_at = _now()
    base.record_count = len(records) + len(issues)
    base.accepted_count = len(records)
    base.rejected_count = len(issues)
    base.duplicate_count = duplicates
    base.validation_errors = issues
    if not records and not issues:
        base.status = ImportStatus.EMPTY
        base.blockers = ["Source parsed but contained zero records."]
    elif issues or duplicates:
        base.status = ImportStatus.PARTIAL
        base.blockers = []
        if issues:
            base.blockers.append(f"{len(issues)} row(s) rejected during validation; see validation_errors.")
        if duplicates:
            base.blockers.append(f"{duplicates} duplicate record key(s) detected; reconciliation will review them.")
    else:
        base.status = ImportStatus.IMPORTED

    store.save_source(base, payload)
    return ImportResult(provenance=base, records=payload)


def import_uploaded_file(
    connector_id: str,
    *,
    source_name: str,
    raw: bytes,
    fmt_override: Optional[SourceFormat] = None,
) -> ImportResult:
    """Import one browser-uploaded CSV/JSON file through the normal connector path.

    The company system of record is (re)derived from the uploaded datasets by the
    caller (API / CLI) via ``apply_company_derivation`` after import."""
    if connector_id not in C.CONNECTORS:
        raise ValueError(f"unknown connector: {connector_id}; valid: {', '.join(C.CONNECTORS)}")

    spec = C.CONNECTORS[connector_id]
    safe_name = Path(source_name or spec.fixture_filename).name
    base = ImportProvenance(
        connector_id=spec.connector_id,
        source_type=spec.source_type,
        origin=Origin.EXTERNAL_FILE,
        status=ImportStatus.ERROR,
        env_var=spec.env_var,
        source_name=safe_name,
        source_timestamp=_now(),
    )

    try:
        fmt = C.detect_format(Path(safe_name), fmt_override)
    except ValueError as exc:
        base.blockers = [redact_secrets(exc)]
        store.save_provenance(base)
        return ImportResult(provenance=base)

    checksum = C.checksum_bytes(raw)
    base.checksum_sha256 = checksum
    base.source_format = fmt

    if store.existing_checksum(spec.connector_id) == checksum:
        prior = store.load_provenance(spec.connector_id) or {}
        base.status = ImportStatus.SKIPPED_UNCHANGED
        base.record_count = int(prior.get("record_count") or 0)
        base.accepted_count = int(prior.get("accepted_count") or 0)
        base.rejected_count = int(prior.get("rejected_count") or 0)
        base.duplicate_count = int(prior.get("duplicate_count") or 0)
        _copy_prior_parse_metadata(base, prior)
        base.normalization_summary = prior.get("normalization_summary") or {}
        base.messiness_summary = prior.get("messiness_summary") or {}
        base.pipeline_quality_summary = prior.get("pipeline_quality_summary") or {}
        base.headcount_quality_summary = prior.get("headcount_quality_summary") or {}
        base.imported_at = _now()
        store.save_provenance(base)
        return ImportResult(provenance=base, records=store.load_dataset(spec.connector_id))

    try:
        records, issues, duplicates, metadata = C.parse_records_with_metadata(spec, raw, fmt)
    except (ValueError, TypeError) as exc:
        base.blockers = [redact_secrets(exc)]
        store.save_provenance(base)
        return ImportResult(provenance=base)

    payload = [r.model_dump(mode="json") for r in records]
    _attach_parse_metadata(base, metadata, safe_name)
    _attach_source_summaries(base, payload)
    base.imported_at = _now()
    base.record_count = len(records) + len(issues)
    base.accepted_count = len(records)
    base.rejected_count = len(issues)
    base.duplicate_count = duplicates
    base.validation_errors = issues
    if not records and not issues:
        base.status = ImportStatus.EMPTY
        base.blockers = ["Source parsed but contained zero records."]
    elif issues or duplicates:
        base.status = ImportStatus.PARTIAL
        base.blockers = []
        if issues:
            base.blockers.append(f"{len(issues)} row(s) rejected during validation; see validation_errors.")
        if duplicates:
            base.blockers.append(f"{duplicates} duplicate record key(s) detected; reconciliation will review them.")
    else:
        base.status = ImportStatus.IMPORTED

    store.save_source(base, payload)
    return ImportResult(provenance=base, records=payload)


def import_workbook(
    *,
    source_name: str,
    raw: bytes,
) -> list[ImportResult]:
    """Import one Excel workbook, routing matched worksheets to every connector."""
    safe_name = Path(source_name or "operations-workbook.xlsx").name
    fmt = C.detect_format(Path(safe_name))
    if fmt not in C.EXCEL_FORMATS:
        raise ValueError("Workbook import requires a .xlsx or .xls file.")
    return [
        import_uploaded_file(
            connector_id,
            source_name=safe_name,
            raw=raw,
            fmt_override=fmt,
        )
        for connector_id in C.CONNECTORS
    ]


def run_import(
    connector_ids: Optional[list[str]] = None,
    *,
    demo: bool = False,
    explicit_path: Optional[str] = None,
    fmt_override: Optional[SourceFormat] = None,
) -> list[ImportResult]:
    """Import the requested connectors (default: all)."""
    ids = connector_ids or list(C.CONNECTORS.keys())
    unknown = [cid for cid in ids if cid not in C.CONNECTORS]
    if unknown:
        raise ValueError(f"unknown connector(s): {', '.join(unknown)}; valid: {', '.join(C.CONNECTORS)}")
    if explicit_path and len(ids) != 1:
        raise ValueError("explicit_path requires exactly one connector id")
    return [
        import_connector(C.CONNECTORS[cid], demo=demo, explicit_path=explicit_path, fmt_override=fmt_override)
        for cid in ids
    ]


# --------------------------------------------------------------------------- #
# Typed dataset loading (re-validate stored payloads for type-safe reconcile)
# --------------------------------------------------------------------------- #
def _load_typed(source_type: SourceType, model) -> list:
    out = []
    for row in store.load_dataset(source_type.value):
        try:
            out.append(model.model_validate(row))
        except Exception:
            continue  # stored rows were validated at import; skip any stragglers
    return out


# --------------------------------------------------------------------------- #
# Company derivation (upload-driven system of record)
# --------------------------------------------------------------------------- #
def apply_company_derivation() -> Optional[dict[str, Any]]:
    """Derive the company financials record from uploaded datasets and persist it.

    Returns the derived record (and writes it to ``COMPANY_KEY``) when the
    uploaded ledger carries enough signal to model a company; returns ``None``
    and leaves any existing record untouched otherwise. This is what lets the
    council debate the operator's own numbers once a real dataset is uploaded,
    instead of a seeded baseline.
    """
    from src.integrations.derive_company import derive_company_record

    record = derive_company_record()
    if not record:
        return None
    R.set_json(COMPANY_KEY, record)
    _apply_uploaded_vendors()
    return record


def _apply_uploaded_vendors() -> int:
    """Make the operator's uploaded vendor register the procurement system of record.

    Upload-first: when a vendor_export has been imported, those vendors replace any
    seeded scaffolding vendors at ``atlas:vendor:*`` so the council's ``list_vendors``
    tool and the dashboard show the operator's real contracts. No-op when nothing
    has been uploaded (the seeded scaffolding stays in place for the empty state).
    """
    vendors = _load_typed(SourceType.VENDOR_EXPORT, VendorRecord)
    if not vendors:
        return 0
    R.delete_keys_matching(f"{R.VENDOR_PREFIX}*")
    for vendor in vendors:
        payload = vendor.model_dump(mode="json")
        payload.setdefault("id", vendor.vendor_id)
        R.set_json(f"{R.VENDOR_PREFIX}{vendor.vendor_id}", payload)
    try:
        R.ensure_vendor_index()
    except Exception:
        pass
    return len(vendors)


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #
def run_reconciliation() -> ReconciliationReport:
    company = R.get_json(COMPANY_KEY) or {}
    vendors_seed = R.search_vendors("*", 50) if company else []

    ledger = _load_typed(SourceType.LEDGER, LedgerEntry)
    invoices = _load_typed(SourceType.INVOICES, Invoice)
    vendor_export = _load_typed(SourceType.VENDOR_EXPORT, VendorRecord)
    opportunities = _load_typed(SourceType.CRM_OPPORTUNITIES, CrmOpportunity)
    headcount = _load_typed(SourceType.HEADCOUNT_PLAN, HeadcountPlanRow)
    security = _load_typed(SourceType.SECURITY_EVIDENCE, SecurityEvidence)
    board_policies = _load_typed(SourceType.BOARD_POLICY, BoardPolicyDoc)

    summaries, discrepancies = run_workflows(
        invoices=invoices,
        ledger=ledger,
        vendor_export=vendor_export,
        opportunities=opportunities,
        headcount=headcount,
        security=security,
        board_policies=board_policies,
        company=company,
        vendors_seed=vendors_seed,
    )

    counts: dict[str, int] = {}
    for disc in discrepancies:
        counts[disc.severity.value] = counts.get(disc.severity.value, 0) + 1

    blockers: list[str] = []
    if not company:
        blockers.append(
            "Company system of record is missing; upload your company files (ledger, headcount, CRM, "
            "vendors, security, board policy) on the Data tab so the financials can be derived."
        )
    for summary in summaries:
        blockers.extend(summary.blockers)

    actionable = sum(v for k, v in counts.items() if k != "info")
    status = "blocked" if not company else ("discrepancies" if actionable else "ok")

    sources_considered = [
        s["source_type"] for s in source_inventory() if s.get("status") in ("imported", "partial", "skipped_unchanged")
    ]
    review_sources = {
        str(source)
        for disc in discrepancies
        if disc.severity.value != "info"
        for source in disc.sources
        if isinstance(source, str)
    }
    for cid in sources_considered:
        source_type = C.CONNECTORS.get(cid).source_type.value if cid in C.CONNECTORS else cid
        store.mark_reconciled(cid, "needs_review" if source_type in review_sources or cid in review_sources else "reconciled")

    generated = _now()
    report = ReconciliationReport(
        run_id=generated.strftime("recon-%Y%m%dT%H%M%SZ"),
        generated_at=generated,
        status=status,
        workflows=summaries,
        discrepancies=discrepancies,
        counts_by_severity=counts,
        confidence=import_confidence(discrepancies=[d.model_dump(mode="json") for d in discrepancies], workflows=[w.model_dump(mode="json") for w in summaries]),
        sources_considered=sources_considered,
        blockers=blockers,
    )
    store.save_reconciliation(report)
    return report


# --------------------------------------------------------------------------- #
# Inventories, status, confidence (read-only, redacted)
# --------------------------------------------------------------------------- #
def connector_statuses() -> list[dict[str, Any]]:
    """Per-connector configuration + import state (never fabricates data)."""
    provenance_by_id = {doc.get("connector_id"): doc for doc in store.list_sources()}
    out: list[dict[str, Any]] = []
    for cid, spec in C.CONNECTORS.items():
        configured_path = C.configured_path(spec)
        fixture = C.fixture_path(spec)
        prov = provenance_by_id.get(cid)
        source_conf = _source_confidence(cid, prov).model_dump(mode="json")
        out.append(
            {
                "connector_id": cid,
                "source_type": spec.source_type.value,
                "description": spec.description,
                "env_var": spec.env_var,
                "configured": bool(configured_path),
                "configured_path": _redact_path(configured_path),
                "demo_fixture_available": fixture.exists(),
                "transport": "file",
                "status": (prov or {}).get("status", ImportStatus.NOT_CONFIGURED.value),
                "origin": (prov or {}).get("origin"),
                "record_count": (prov or {}).get("accepted_count", 0),
                "source_name": (prov or {}).get("source_name"),
                "source_format": (prov or {}).get("source_format"),
                "workbook_name": (prov or {}).get("workbook_name"),
                "workbook_sheet": (prov or {}).get("workbook_sheet"),
                "workbook_sheets": (prov or {}).get("workbook_sheets", []),
                "header_row_number": (prov or {}).get("header_row_number"),
                "hidden_column_count": (prov or {}).get("hidden_column_count", 0),
                "extra_column_count": (prov or {}).get("extra_column_count", 0),
                "source_timestamp": (prov or {}).get("source_timestamp"),
                "imported_at": (prov or {}).get("imported_at"),
                "checksum_sha256": (prov or {}).get("checksum_sha256"),
                "reconciliation_status": (prov or {}).get("reconciliation_status", "pending"),
                "blockers": (prov or {}).get("blockers", []),
                "accepted_count": (prov or {}).get("accepted_count", 0),
                "rejected_count": (prov or {}).get("rejected_count", 0),
                "duplicate_count": (prov or {}).get("duplicate_count", 0),
                "validation_errors": (prov or {}).get("validation_errors", []),
                "normalization_summary": (prov or {}).get("normalization_summary", {}),
                "messiness_summary": (prov or {}).get("messiness_summary", {}),
                "pipeline_quality_summary": (prov or {}).get("pipeline_quality_summary", {}),
                "headcount_quality_summary": (prov or {}).get("headcount_quality_summary", {}),
                "confidence_score": source_conf["score"],
                "confidence_reasons": source_conf["reasons"],
                "freshness_days": source_conf["freshness_days"],
                "required_facts_missing": source_conf["required_facts_missing"],
            }
        )
    return out


def source_inventory() -> list[dict[str, Any]]:
    """Provenance for every persisted source, with file paths redacted."""
    out = []
    for doc in store.list_sources():
        doc = dict(doc)
        doc["source_path"] = _redact_path(doc.get("source_path"))
        connector_id = str(doc.get("connector_id") or "")
        if connector_id in C.CONNECTORS:
            source_conf = _source_confidence(connector_id, doc).model_dump(mode="json")
            doc["confidence_score"] = source_conf["score"]
            doc["confidence_reasons"] = source_conf["reasons"]
            doc["freshness_days"] = source_conf["freshness_days"]
            doc["required_facts_missing"] = source_conf["required_facts_missing"]
        out.append(doc)
    return out


def get_source(connector_id: str, *, sample: int = 10) -> Optional[dict[str, Any]]:
    prov = store.load_provenance(connector_id)
    if not prov:
        return None
    prov = dict(prov)
    prov["source_path"] = _redact_path(prov.get("source_path"))
    records = store.load_dataset(connector_id)
    return {"provenance": prov, "record_count": len(records), "sample": records[: max(0, sample)]}


def import_confidence(
    *,
    discrepancies: Optional[list[dict[str, Any]]] = None,
    workflows: Optional[list[dict[str, Any]]] = None,
) -> ImportConfidence:
    persisted_sources = {doc.get("connector_id"): doc for doc in store.list_sources()}
    total = len(C.CONNECTORS)
    source_scores = [_source_confidence(cid, persisted_sources.get(cid)) for cid in C.CONNECTORS]
    imported_scores = [s for s in source_scores if s.status in IMPORTED_STATUSES and s.accepted_count > 0]
    accepted = sum(s.accepted_count for s in imported_scores)
    rejected = sum(s.rejected_count for s in imported_scores)
    duplicates = sum(s.duplicate_count for s in imported_scores)

    coverage = (len(imported_scores) / total) if total else 0.0
    pass_rate = (accepted / (accepted + rejected)) if (accepted + rejected) else (1.0 if imported_scores else 0.0)
    duplicate_rate = duplicates / max(accepted, 1) if imported_scores else 1.0
    duplicate_factor = max(0.0, 1.0 - min(1.0, duplicate_rate * 8.0))

    freshness_days: Optional[float] = None
    average_source_age_days: Optional[float] = None
    oldest_source_age_days: Optional[float] = None
    ages = [s.freshness_days for s in imported_scores if s.freshness_days is not None]
    if ages:
        freshness_days = min(ages)
        average_source_age_days = round(sum(ages) / len(ages), 1)
        oldest_source_age_days = max(ages)
    freshness_values = [_freshness_factor(s.freshness_days) for s in imported_scores]
    freshness_factor = (sum(freshness_values) / len(freshness_values)) if freshness_values else 0.0
    stale_count = sum(1 for s in imported_scores if s.freshness_days is not None and s.freshness_days > SOURCE_STALE_DAYS)

    report = store.load_reconciliation()
    if discrepancies is None and report:
        discrepancies = report.get("discrepancies") or []
    if workflows is None and report:
        workflows = report.get("workflows") or []
    actionable_discrepancies = [d for d in (discrepancies or []) if str(d.get("severity") or "") != "info"]
    reconciliation_penalty = min(40.0, sum(SEVERITY_WEIGHTS.get(str(d.get("severity") or "").lower(), 2.0) for d in actionable_discrepancies))
    reconciliation_factor = max(0.0, 1.0 - reconciliation_penalty / 40.0)
    required_missing = [fact for s in source_scores for fact in s.required_facts_missing]
    required_fact_factor = 1.0 - (len(required_missing) / max(sum(len(v) for v in REQUIRED_FACTS_BY_SOURCE.values()), 1))
    required_fact_factor = max(0.0, min(1.0, required_fact_factor))

    components = {
        "coverage": round(coverage, 3),
        "validation_pass_rate": round(pass_rate, 3),
        "duplicate_factor": round(duplicate_factor, 3),
        "freshness_factor": round(freshness_factor, 3),
        "reconciliation_factor": round(reconciliation_factor, 3),
        "required_fact_factor": round(required_fact_factor, 3),
    }
    score = (
        round(100 * (0.24 * coverage + 0.22 * pass_rate + 0.12 * duplicate_factor + 0.16 * freshness_factor + 0.16 * reconciliation_factor + 0.10 * required_fact_factor))
        if imported_scores
        else 0
    )
    score = max(0, min(100, score))

    reasons: list[str] = []
    if len(imported_scores) < total:
        reasons.append(f"{total - len(imported_scores)} source{'s' if total - len(imported_scores) != 1 else ''} missing required facts")
    if rejected:
        reasons.append(f"{rejected} validation failure{'s' if rejected != 1 else ''}")
    if duplicates:
        reasons.append(f"{duplicates} duplicate record key{'s' if duplicates != 1 else ''}")
    if stale_count:
        reasons.append(f"{stale_count} stale source{'s' if stale_count != 1 else ''}")
    if actionable_discrepancies:
        noun = "discrepancy" if len(actionable_discrepancies) == 1 else "discrepancies"
        reasons.append(f"{len(actionable_discrepancies)} reconciliation {noun}")
    if required_missing:
        reasons.append(f"{len(required_missing)} required fact{'s' if len(required_missing) != 1 else ''} missing")

    detail = (
        f"{len(imported_scores)}/{total} connectors imported · {pass_rate:.0%} rows valid · "
        f"{duplicates} duplicate{'s' if duplicates != 1 else ''} · "
        f"{len(actionable_discrepancies)} reconciliation issue{'s' if len(actionable_discrepancies) != 1 else ''}"
        + (f" · newest source {freshness_days:g}d old" if freshness_days is not None else "")
    ) if imported_scores else "No operations sources imported yet; required facts are missing."

    return ImportConfidence(
        score=score,
        coverage=round(coverage, 3),
        validation_pass_rate=round(pass_rate, 3),
        freshness_days=freshness_days,
        average_source_age_days=average_source_age_days,
        oldest_source_age_days=oldest_source_age_days,
        sources_imported=len(imported_scores),
        sources_total=total,
        validation_failure_count=rejected,
        duplicate_count=duplicates,
        stale_source_count=stale_count,
        reconciliation_discrepancy_count=len(actionable_discrepancies),
        required_missing_count=len(required_missing),
        required_facts_missing=required_missing[:24],
        confidence_reasons=reasons[:12],
        source_confidence=source_scores,
        detail=detail,
        components=components,
    )


def reconciliation_summary() -> Optional[dict[str, Any]]:
    return store.load_reconciliation()


def reset_demo_state() -> dict[str, Any]:
    """Clear bounded uploaded-demo state without touching the seeded system of record."""
    connector_ids = list(C.CONNECTORS.keys())
    deleted = {}
    deleted.update(store.clear_sources(connector_ids))
    deleted.update(store.clear_reconciliation())
    return {
        "status": "reset",
        "deleted": deleted,
        "connectors": connector_statuses(),
        "confidence": import_confidence().model_dump(mode="json"),
    }


def list_discrepancies(severity: Optional[str] = None, kind: Optional[str] = None) -> list[dict[str, Any]]:
    report = store.load_reconciliation()
    if not report:
        return []
    discs = report.get("discrepancies") or []
    if severity:
        discs = [d for d in discs if d.get("severity") == severity.lower()]
    if kind:
        discs = [d for d in discs if d.get("kind") == kind.lower()]
    return discs


def get_discrepancy(discrepancy_id: str) -> Optional[dict[str, Any]]:
    report = store.load_reconciliation()
    if not report:
        return None
    for disc in report.get("discrepancies") or []:
        if disc.get("id") == discrepancy_id:
            return disc
    return None


# --------------------------------------------------------------------------- #
# Compact snapshot for the council intake context (only when data exists)
# --------------------------------------------------------------------------- #
def operations_context_snapshot() -> Optional[dict[str, Any]]:
    """A small, honest operations summary for the debate context.

    Returns ``None`` when no connector has imported data, so the core demo is
    unchanged unless real operations data has been ingested.
    """
    inventory = source_inventory()
    imported = [d for d in inventory if d.get("status") in ("imported", "partial", "skipped_unchanged") and (d.get("accepted_count") or 0) > 0]
    if not imported:
        return None

    report = store.load_reconciliation()
    snapshot: dict[str, Any] = {
        "sources": [
            {
                "source_type": d.get("source_type"),
                "origin": d.get("origin"),
                "records": d.get("accepted_count"),
                "status": d.get("status"),
                "source_timestamp": d.get("source_timestamp"),
                "workbook_name": d.get("workbook_name"),
                "workbook_sheet": d.get("workbook_sheet"),
                "workbook_sheets": d.get("workbook_sheets") or [],
                "header_row_number": d.get("header_row_number"),
                "hidden_column_count": d.get("hidden_column_count") or 0,
                "extra_column_count": d.get("extra_column_count") or 0,
                "freshness_days": d.get("freshness_days"),
                "confidence_score": d.get("confidence_score"),
                "confidence_reasons": d.get("confidence_reasons") or [],
                "required_facts_missing": d.get("required_facts_missing") or [],
                "normalization_summary": d.get("normalization_summary") or {},
                "messiness_summary": d.get("messiness_summary") or {},
                "pipeline_quality_summary": d.get("pipeline_quality_summary") or {},
                "headcount_quality_summary": d.get("headcount_quality_summary") or {},
            }
            for d in imported
        ],
        "confidence": import_confidence().model_dump(mode="json"),
    }
    if report:
        actionable = [d for d in (report.get("discrepancies") or []) if d.get("severity") != "info"]
        ranked = sorted(actionable, key=lambda d: _SEVERITY_ORDER.get(d.get("severity"), 0), reverse=True)
        snapshot["reconciliation"] = {
            "status": report.get("status"),
            "generated_at": report.get("generated_at"),
            "counts_by_severity": report.get("counts_by_severity"),
            "open_discrepancies": len(actionable),
            "top_discrepancies": [
                {
                    "id": d.get("id"),
                    "kind": d.get("kind"),
                    "severity": d.get("severity"),
                    "title": d.get("title"),
                    "recommended_action": d.get("recommended_action"),
                }
                for d in ranked[:6]
            ],
            "blockers": report.get("blockers", []),
        }
    return snapshot


_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
