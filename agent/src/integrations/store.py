"""
Redis persistence for imported operations data + reconciliation results.

Every imported feed is stored as two RedisJSON documents under the ``atlas:``
namespace owned by :mod:`src.redis_layer`:

  • ``atlas:source:<connector_id>``  — provenance metadata (checksum, schema
    version, source timestamp, counts, reconciliation status, blockers)
  • ``atlas:dataset:<connector_id>`` — the validated record payload

Reconciliation reports are written to ``atlas:reconciliation:latest`` and appended
to the ``atlas:stream:reconciliation`` event log for an auditable history.

Writes only ever touch the specific keys for the connector/report in question —
there are no pattern deletes or destructive ``FLUSHDB`` operations here.
"""

from __future__ import annotations

from typing import Any, Optional

from src import redis_layer as R
from src.integrations.models import (
    ImportProvenance,
    ReconciliationReport,
)


def _source_key(connector_id: str) -> str:
    return f"{R.SOURCE_PREFIX}{connector_id}"


def _dataset_key(connector_id: str) -> str:
    return f"{R.DATASET_PREFIX}{connector_id}"


# --------------------------------------------------------------------------- #
# Sources (provenance) + datasets (payload)
# --------------------------------------------------------------------------- #
def save_source(provenance: ImportProvenance, records: list[dict[str, Any]]) -> None:
    """Persist one connector's provenance + validated records (idempotent overwrite)."""
    R.set_json(_source_key(provenance.connector_id), provenance.model_dump(mode="json"))
    R.set_json(_dataset_key(provenance.connector_id), {"records": records})


def save_provenance(provenance: ImportProvenance) -> None:
    """Persist provenance only (used for not-configured / missing-file statuses)."""
    R.set_json(_source_key(provenance.connector_id), provenance.model_dump(mode="json"))


def load_provenance(connector_id: str) -> Optional[dict[str, Any]]:
    return R.get_json(_source_key(connector_id))


def existing_checksum(connector_id: str) -> Optional[str]:
    """Checksum of the last successfully imported file, for idempotency checks."""
    doc = load_provenance(connector_id)
    if not doc:
        return None
    if doc.get("status") not in ("imported", "partial", "skipped_unchanged"):
        return None
    return doc.get("checksum_sha256")


def load_dataset(connector_id: str) -> list[dict[str, Any]]:
    doc = R.get_json(_dataset_key(connector_id))
    if not doc:
        return []
    records = doc.get("records") if isinstance(doc, dict) else None
    return records or []


def list_sources() -> list[dict[str, Any]]:
    """All persisted source-provenance docs, newest import first."""
    out: list[dict[str, Any]] = []
    for key in R.keys(f"{R.SOURCE_PREFIX}*"):
        doc = R.get_json(key)
        if doc:
            out.append(doc)
    out.sort(key=lambda d: d.get("imported_at") or "", reverse=True)
    return out


def mark_reconciled(connector_id: str) -> None:
    """Flip a source's reconciliation_status to 'reconciled' without rewriting payload."""
    doc = load_provenance(connector_id)
    if not doc:
        return
    doc["reconciliation_status"] = "reconciled"
    R.set_json(_source_key(connector_id), doc)


def clear_sources(connector_ids: list[str]) -> dict[str, int]:
    """Delete only connector provenance + payload docs for the provided ids."""
    deleted: dict[str, int] = {}
    for connector_id in connector_ids:
        source_key = _source_key(connector_id)
        dataset_key = _dataset_key(connector_id)
        deleted[source_key] = R.delete_key(source_key)
        deleted[dataset_key] = R.delete_key(dataset_key)
    return deleted


# --------------------------------------------------------------------------- #
# Reconciliation reports
# --------------------------------------------------------------------------- #
def save_reconciliation(report: ReconciliationReport) -> Optional[str]:
    """Persist the latest report and append a compact entry to the run log."""
    payload = report.model_dump(mode="json")
    R.set_json(R.RECON_LATEST, payload)
    try:
        return R.append_event(
            R.RECON_STREAM,
            {
                "run_id": report.run_id,
                "status": report.status,
                "discrepancies": len(report.discrepancies),
                "counts_by_severity": report.counts_by_severity,
                "sources_considered": report.sources_considered,
                "source": "reconciliation",
            },
        )
    except Exception:  # the run-log append is best-effort; latest doc is authoritative
        return None


def load_reconciliation() -> Optional[dict[str, Any]]:
    return R.get_json(R.RECON_LATEST)


def clear_reconciliation() -> dict[str, int]:
    """Delete the latest reconciliation singleton, preserving the audit stream."""
    return {R.RECON_LATEST: R.delete_key(R.RECON_LATEST)}
