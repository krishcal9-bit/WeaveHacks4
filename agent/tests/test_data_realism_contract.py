from __future__ import annotations

import csv
import io
import json
from typing import Any

from src import redis_layer as R
from src.data.seed import seed
from src.integrations import service as OPS
from src.tools import list_operations_sources


def _csv_bytes(fieldnames: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _import_messy_uploads() -> None:
    assert R.ping(), "Redis must be running for data-realism contract coverage"
    seed(verbose=False)
    OPS.reset_demo_state()

    ledger = _csv_bytes(
        [
            "reference",
            "transaction_date",
            "account_name",
            "description",
            "statement_description",
            "debit",
            "credit",
            "currency",
            "source_category",
            "payee",
            "department",
        ],
        [
            {
                "reference": "TXN-9001",
                "transaction_date": "2026-06-03",
                "account_name": "Corporate Card",
                "description": "Datadog card charge",
                "statement_description": "CARD 4242 DATADOG INC 866-329-4466 NY",
                "debit": "21500",
                "currency": "USD",
                "payee": "Data Dog Inc",
                "department": "Eng",
            },
            {
                "reference": "TXN-9001",
                "transaction_date": "2026-06-03",
                "account_name": "Corporate Card",
                "description": "Duplicate Datadog export row",
                "statement_description": "CARD 4242 DDOG INC NY",
                "debit": "21000",
                "currency": "USD",
                "payee": "Data Dog Inc",
                "department": "Engineering",
            },
            {
                "reference": "TXN-BAD",
                "transaction_date": "not-a-date",
                "account_name": "Corporate Card",
                "description": "Broken cash row",
                "statement_description": "CARD 4242 UNKNOWN VENDOR",
                "debit": "not money",
                "currency": "USD",
                "payee": "Unknown Vendor",
                "department": "Eng",
            },
            {
                "reference": "TXN-9002",
                "transaction_date": "2026-06-04",
                "account_name": "Corporate Card",
                "description": "Uncategorized SaaS charge",
                "statement_description": "CARD 4242 UNKNOWN_VENDOR*SAAS TOOL 800-555-0199",
                "debit": "2200",
                "currency": "USD",
                "payee": "Unknown Vendor SaaS Tool",
                "department": "Engineering",
            },
        ],
    )
    invoices = _csv_bytes(
        [
            "invoice_id",
            "vendor_id",
            "vendor_name",
            "issue_date",
            "due_date",
            "amount",
            "currency",
            "status",
            "po_number",
            "period",
            "line_description",
            "payment_status",
            "paid_amount",
            "balance_due",
            "terms",
            "contract_reference",
            "source_system",
            "exchange_rate",
            "amount_usd",
        ],
        [
            {
                "invoice_id": "INV-DUP",
                "vendor_id": "datadog",
                "vendor_name": "Datadog",
                "issue_date": "2026-06-01",
                "due_date": "2026-06-06",
                "amount": "21500",
                "currency": "USD",
                "status": "open",
                "period": "2026-06",
                "line_description": "Usage overage above committed host tier",
                "payment_status": "open",
                "paid_amount": "0",
                "balance_due": "21500",
                "terms": "Due on receipt",
                "contract_reference": "DD-2026-MSA",
                "source_system": "NetSuite",
                "exchange_rate": "1",
                "amount_usd": "21500",
            },
            {
                "invoice_id": "INV-DUP",
                "vendor_id": "datadog",
                "vendor_name": "DataDog Inc",
                "issue_date": "2026-06-01",
                "due_date": "2026-06-06",
                "amount": "21480",
                "currency": "USD",
                "status": "open",
                "period": "2026-06",
                "line_description": "Corrected vendor portal copy with a different total",
                "payment_status": "open",
                "paid_amount": "0",
                "balance_due": "21480",
                "terms": "Due on receipt",
                "contract_reference": "DD-2026-MSA",
                "source_system": "Vendor portal",
                "exchange_rate": "1",
                "amount_usd": "21480",
            },
            {
                "invoice_id": "INV-BAD",
                "vendor_id": "aws",
                "vendor_name": "AWS",
                "issue_date": "2026-06-01",
                "due_date": "2026-06-30",
                "amount": "not available",
                "currency": "USD",
                "status": "open",
                "line_description": "OCR failed amount row",
                "payment_status": "open",
                "paid_amount": "0",
                "terms": "Net 30",
                "source_system": "OCR export",
                "exchange_rate": "1",
            },
            {
                "invoice_id": "INV-NODUE",
                "vendor_id": "acme-analytics",
                "vendor_name": "Acme Analytics",
                "issue_date": "2026-06-01",
                "amount": "120000",
                "currency": "USD",
                "status": "open",
                "period": "2026-06",
                "line_description": "Annual prepay deposit without due date in AP",
                "payment_status": "open",
                "paid_amount": "0",
                "balance_due": "120000",
                "terms": "Net 15",
                "contract_reference": "ACME-ANL",
                "source_system": "Procurement inbox",
                "exchange_rate": "1",
                "amount_usd": "120000",
            },
            {
                "invoice_id": "INV-FX",
                "vendor_id": "figma",
                "vendor_name": "Figma EMEA",
                "issue_date": "2026-06-01",
                "due_date": "2026-07-01",
                "amount": "1100",
                "currency": "EUR",
                "status": "open",
                "line_description": "Design licenses billed by EMEA reseller",
                "payment_status": "open",
                "paid_amount": "0",
                "balance_due": "1199",
                "terms": "Net 30",
                "contract_reference": "FIGMA-2026",
                "source_system": "Email PDF",
                "exchange_rate": "1.09",
                "amount_usd": "1199",
            },
        ],
    )
    vendors = json.dumps(
        {
            "records": [
                {
                    "vendor_id": "datadog",
                    "name": "Data Dog, Inc.",
                    "category": "observability",
                    "annual_cost": 180000,
                    "monthly_cost": 15000,
                    "renewal_date": "2026-07-25",
                    "status": "up_for_renewal",
                    "owner": "Platform Ops",
                    "billing_frequency": "annual",
                    "contract_aliases": ["Datadog", "DataDog Inc", "Data Dog", "DDOG"],
                    "auto_renew": True,
                    "notice_window_days": 45,
                    "termination_notice_days": 45,
                    "board_approved": True,
                    "tiered_pricing": [{"tier": "committed hosts", "annual_price": 180000}],
                    "security_clause": "SOC 2 current; customer PII prohibited.",
                    "data_processing_addendum": True,
                },
                {
                    "vendor_id": "acme-analytics",
                    "name": "Acme Analytics Platform",
                    "category": "data",
                    "annual_cost": "$250,000",
                    "monthly_cost": "20833",
                    "renewal_date": "2027-06-01",
                    "status": "active",
                    "owner": "Data Ops",
                    "billing_frequency": "annual",
                    "contract_aliases": ["Acme Analytics", "ACME-ANL"],
                    "auto_renew": True,
                    "notice_window_days": 90,
                    "termination_notice_days": 90,
                    "board_approved": False,
                    "security_clause": "DPA unsigned; subprocessors list supplied by email only.",
                    "data_processing_addendum": False,
                },
            ]
        }
    ).encode("utf-8")

    OPS.import_uploaded_file("ledger", source_name="messy-ledger.csv", raw=ledger)
    OPS.import_uploaded_file("invoices", source_name="messy-invoices.csv", raw=invoices)
    OPS.import_uploaded_file("vendor_export", source_name="messy-vendors.json", raw=vendors)


def test_messy_uploads_reject_deduplicate_normalize_and_persist_provenance() -> None:
    _import_messy_uploads()

    ledger = OPS.get_source("ledger")
    assert ledger is not None
    ledger_prov = ledger["provenance"]
    assert ledger_prov["source_name"] == "messy-ledger.csv"
    assert ledger_prov["checksum_sha256"]
    assert ledger_prov["source_timestamp"]
    assert ledger_prov["status"] == "partial"
    assert ledger_prov["accepted_count"] == 2
    assert ledger_prov["duplicate_count"] == 1
    assert ledger_prov["rejected_count"] >= 2

    ledger_issues = ledger_prov["validation_errors"]
    assert any(issue["field"] == "date" and "row" in issue["location"] for issue in ledger_issues)
    assert any("duplicate record key" in issue["message"] for issue in ledger_issues)
    assert any("validation_errors" in blocker for blocker in ledger_prov["blockers"])
    assert any("duplicate record key" in blocker for blocker in ledger_prov["blockers"])

    ledger_records = ledger["sample"]
    assert [row["txn_id"] for row in ledger_records].count("TXN-9001") == 1
    datadog = next(row for row in ledger_records if row["txn_id"] == "TXN-9001")
    assert datadog["raw_description"] == "Datadog card charge"
    assert "DATADOG INC" in datadog["bank_description"]
    assert datadog["inferred_vendor_id"] == "datadog"
    assert datadog["normalized_category"] == "software"
    assert datadog["amount"] == -21500.0

    uncategorized = next(row for row in ledger_records if row["txn_id"] == "TXN-9002")
    assert uncategorized["raw_vendor_name"] == "Unknown Vendor SaaS Tool"
    assert uncategorized["normalized_category"] == "uncategorized"
    assert uncategorized["normalization_confidence"] < datadog["normalization_confidence"]
    assert "raw bank/card descriptor preserved" in uncategorized["normalization_notes"]

    invoices = OPS.get_source("invoices")
    assert invoices is not None
    invoice_prov = invoices["provenance"]
    assert invoice_prov["source_name"] == "messy-invoices.csv"
    assert invoice_prov["status"] == "partial"
    assert invoice_prov["accepted_count"] == 4
    assert invoice_prov["duplicate_count"] == 1
    assert invoice_prov["rejected_count"] == 1
    assert any(
        issue["field"] == "amount"
        and "expected a monetary amount" in issue["message"].lower()
        and "not available" in issue["message"]
        for issue in invoice_prov["validation_errors"]
    )
    assert [row["invoice_id"] for row in invoices["sample"]].count("INV-DUP") == 2
    assert invoice_prov["messiness_summary"]["duplicate_invoice_ids"] == ["INV-DUP"]
    assert invoice_prov["messiness_summary"]["missing_due_date_count"] == 1
    assert invoice_prov["messiness_summary"]["multi_currency"] is True


def test_messy_uploads_flag_discrepancies_and_inform_council_evidence() -> None:
    _import_messy_uploads()
    report = OPS.run_reconciliation()

    kinds = {disc.kind.value for disc in report.discrepancies}
    assert {
        "duplicate_invoice",
        "missing_due_date",
        "non_usd_invoice",
        "contract_invoice_mismatch",
        "missing_board_approval",
        "ledger_vendor_mismatch",
        "ledger_uncategorized_spend",
    }.issubset(kinds)
    assert report.confidence.validation_failure_count >= 3
    assert report.confidence.duplicate_count == 2
    assert report.confidence.reconciliation_discrepancy_count >= 7
    assert any("validation failure" in reason for reason in report.confidence.confidence_reasons)
    assert any("duplicate record key" in reason for reason in report.confidence.confidence_reasons)

    inventory = OPS.source_inventory()
    ledger_source = next(source for source in inventory if source["source_type"] == "ledger")
    invoice_source = next(source for source in inventory if source["source_type"] == "invoices")
    assert ledger_source["reconciliation_status"] == "needs_review"
    assert invoice_source["reconciliation_status"] == "needs_review"
    assert "reconciliation needs review" in ledger_source["confidence_reasons"]
    assert "reconciliation needs review" in invoice_source["confidence_reasons"]

    snapshot = OPS.operations_context_snapshot()
    assert snapshot is not None
    assert snapshot["confidence"]["validation_failure_count"] >= 3
    assert snapshot["confidence"]["duplicate_count"] == 2
    assert snapshot["reconciliation"]["open_discrepancies"] >= 7
    assert any(item["kind"] == "duplicate_invoice" for item in snapshot["reconciliation"]["top_discrepancies"])
    ledger_evidence = next(source for source in snapshot["sources"] if source["source_type"] == "ledger")
    invoice_evidence = next(source for source in snapshot["sources"] if source["source_type"] == "invoices")
    assert ledger_evidence["normalization_summary"]["uncategorized_count"] == 1
    assert "raw_description" in ledger_evidence["normalization_summary"]["raw_fields_persisted"]
    assert invoice_evidence["messiness_summary"]["duplicate_invoice_ids"] == ["INV-DUP"]
    assert invoice_evidence["messiness_summary"]["missing_due_date_count"] == 1

    council_tool_payload = json.loads(list_operations_sources.invoke({}))
    tool_sources = {source["source_type"]: source for source in council_tool_payload["imported_sources"]}
    assert tool_sources["ledger"]["confidence_score"] < 100
    assert tool_sources["invoices"]["confidence_reasons"]
    assert tool_sources["ledger"]["normalization_summary"]["records"] == 2
    assert council_tool_payload["confidence"]["reconciliation_discrepancy_count"] >= 7
