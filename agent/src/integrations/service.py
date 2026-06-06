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
    Origin,
    ReconciliationReport,
    SecurityEvidence,
    SourceFormat,
    SourceType,
    VendorRecord,
)
from src.integrations.reconcile import run_workflows

COMPANY_KEY = f"{R.NS}:company:northwind"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redact_path(path: Optional[str]) -> Optional[str]:
    return redact_secrets(path) if path else path


def _existing_good(connector_id: str) -> Optional[dict[str, Any]]:
    """Return a prior provenance doc only if it represents real imported data."""
    doc = store.load_provenance(connector_id)
    if doc and doc.get("status") in ("imported", "partial", "skipped_unchanged") and (doc.get("accepted_count") or 0) > 0:
        return doc
    return None


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
        base.imported_at = _now()
        store.save_provenance(base)
        return ImportResult(provenance=base, records=store.load_dataset(spec.connector_id))

    # 3) Parse + validate.
    try:
        records, issues, duplicates = C.parse_records(spec, raw, fmt)
    except (ValueError, TypeError) as exc:
        return _fail(ImportStatus.ERROR, [redact_secrets(exc)])

    payload = [r.model_dump(mode="json") for r in records]
    base.imported_at = _now()
    base.record_count = len(records) + len(issues)
    base.accepted_count = len(records)
    base.rejected_count = len(issues)
    base.duplicate_count = duplicates
    base.validation_errors = issues
    if not records and not issues:
        base.status = ImportStatus.EMPTY
        base.blockers = ["Source parsed but contained zero records."]
    elif issues:
        base.status = ImportStatus.PARTIAL
        base.blockers = [f"{len(issues)} row(s) rejected during validation; see validation_errors."]
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
    """Import one browser-uploaded CSV/JSON file through the normal connector path."""
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
        base.imported_at = _now()
        store.save_provenance(base)
        return ImportResult(provenance=base, records=store.load_dataset(spec.connector_id))

    try:
        records, issues, duplicates = C.parse_records(spec, raw, fmt)
    except (ValueError, TypeError) as exc:
        base.blockers = [redact_secrets(exc)]
        store.save_provenance(base)
        return ImportResult(provenance=base)

    payload = [r.model_dump(mode="json") for r in records]
    base.imported_at = _now()
    base.record_count = len(records) + len(issues)
    base.accepted_count = len(records)
    base.rejected_count = len(issues)
    base.duplicate_count = duplicates
    base.validation_errors = issues
    if not records and not issues:
        base.status = ImportStatus.EMPTY
        base.blockers = ["Source parsed but contained zero records."]
    elif issues:
        base.status = ImportStatus.PARTIAL
        base.blockers = [f"{len(issues)} row(s) rejected during validation; see validation_errors."]
    else:
        base.status = ImportStatus.IMPORTED

    store.save_source(base, payload)
    return ImportResult(provenance=base, records=payload)


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
# Reconciliation
# --------------------------------------------------------------------------- #
def run_reconciliation() -> ReconciliationReport:
    company = R.get_json(COMPANY_KEY) or {}
    vendors_seed = R.search_vendors("*", 50) if company else []

    invoices = _load_typed(SourceType.INVOICES, Invoice)
    vendor_export = _load_typed(SourceType.VENDOR_EXPORT, VendorRecord)
    opportunities = _load_typed(SourceType.CRM_OPPORTUNITIES, CrmOpportunity)
    headcount = _load_typed(SourceType.HEADCOUNT_PLAN, HeadcountPlanRow)
    security = _load_typed(SourceType.SECURITY_EVIDENCE, SecurityEvidence)
    board_policies = _load_typed(SourceType.BOARD_POLICY, BoardPolicyDoc)

    summaries, discrepancies = run_workflows(
        invoices=invoices,
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
        blockers.append("Company system of record (atlas:company:northwind) is missing; run the seed.")
    for summary in summaries:
        blockers.extend(summary.blockers)

    actionable = sum(v for k, v in counts.items() if k != "info")
    status = "blocked" if not company else ("discrepancies" if actionable else "ok")

    sources_considered = [
        s["source_type"] for s in source_inventory() if s.get("status") in ("imported", "partial", "skipped_unchanged")
    ]

    generated = _now()
    report = ReconciliationReport(
        run_id=generated.strftime("recon-%Y%m%dT%H%M%SZ"),
        generated_at=generated,
        status=status,
        workflows=summaries,
        discrepancies=discrepancies,
        counts_by_severity=counts,
        confidence=import_confidence(),
        sources_considered=sources_considered,
        blockers=blockers,
    )
    store.save_reconciliation(report)
    for cid in sources_considered:
        store.mark_reconciled(cid)
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
                "source_timestamp": (prov or {}).get("source_timestamp"),
                "imported_at": (prov or {}).get("imported_at"),
                "checksum_sha256": (prov or {}).get("checksum_sha256"),
                "reconciliation_status": (prov or {}).get("reconciliation_status", "pending"),
                "blockers": (prov or {}).get("blockers", []),
            }
        )
    return out


def source_inventory() -> list[dict[str, Any]]:
    """Provenance for every persisted source, with file paths redacted."""
    out = []
    for doc in store.list_sources():
        doc = dict(doc)
        doc["source_path"] = _redact_path(doc.get("source_path"))
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


def import_confidence() -> ImportConfidence:
    inventory = source_inventory()
    total = len(C.CONNECTORS)
    imported = [d for d in inventory if d.get("status") in ("imported", "partial", "skipped_unchanged")]
    accepted = sum(int(d.get("accepted_count") or 0) for d in imported)
    rejected = sum(int(d.get("rejected_count") or 0) for d in imported)

    coverage = (len(imported) / total) if total else 0.0
    pass_rate = (accepted / (accepted + rejected)) if (accepted + rejected) else (1.0 if imported else 0.0)

    freshness_days: Optional[float] = None
    timestamps = [d.get("source_timestamp") for d in imported if d.get("source_timestamp")]
    if timestamps:
        newest = max(datetime.fromisoformat(str(t)) for t in timestamps)
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        freshness_days = round((_now() - newest).total_seconds() / 86400, 1)
    freshness_factor = 1.0 if freshness_days is None else max(0.0, 1.0 - (freshness_days / 180.0))

    components = {
        "coverage": round(coverage, 3),
        "validation_pass_rate": round(pass_rate, 3),
        "freshness_factor": round(freshness_factor, 3),
    }
    score = round(100 * (0.5 * coverage + 0.4 * pass_rate + 0.1 * freshness_factor)) if imported else 0
    detail = (
        f"{len(imported)}/{total} connectors imported · {pass_rate:.0%} rows valid"
        + (f" · newest source {freshness_days}d old" if freshness_days is not None else "")
    ) if imported else "No operations sources imported yet."

    return ImportConfidence(
        score=score,
        coverage=round(coverage, 3),
        validation_pass_rate=round(pass_rate, 3),
        freshness_days=freshness_days,
        sources_imported=len(imported),
        sources_total=total,
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
