from __future__ import annotations

from src.data.seed import COMPANY, VENDORS
from src.integrations import connectors as C
from src.integrations.crm_pipeline_quality import summarize_pipeline_quality
from src.integrations.headcount_quality import summarize_headcount_quality
from src.integrations.invoice_messiness import summarize_invoice_messiness
from src.integrations.ledger_normalization import summarize_ledger_normalization
from src.integrations.models import SourceType
from src.integrations.reconcile import run_workflows


def _parse_demo_fixtures() -> dict[str, list]:
    parsed: dict[str, list] = {}
    for connector_id, spec in C.CONNECTORS.items():
        path = C.fixture_path(spec)
        raw = path.read_bytes()
        fmt = C.detect_format(path)
        records, issues, duplicates = C.parse_records(spec, raw, fmt)
        parsed[connector_id] = records
        if connector_id == SourceType.INVOICES.value:
            assert duplicates == 1
            assert any(issue.field == "amount" for issue in issues)
        else:
            assert not issues, f"{connector_id}: {issues}"
    return parsed


def test_messy_demo_fixtures_parse_with_normalization() -> None:
    parsed = _parse_demo_fixtures()

    invoices = parsed["invoices"]
    ledger = parsed["ledger"]
    crm = parsed["crm_opportunities"]
    headcount = parsed["headcount_plan"]
    security = parsed["security_evidence"]
    vendors = parsed["vendor_export"]

    ledger_by_id = {row.txn_id: row for row in ledger}
    assert len(ledger) == 17
    assert ledger_by_id["CARD-7711"].raw_description == "Datadog card charge"
    assert "CARD 4242 DATADOG INC" in (ledger_by_id["CARD-7711"].bank_description or "")
    assert ledger_by_id["CARD-7711"].inferred_vendor_id == "datadog"
    assert ledger_by_id["CARD-7711"].normalized_category == "software"
    assert ledger_by_id["CARD-7711"].payment_channel == "card"
    assert ledger_by_id["CARD-7711"].card_last4 == "4242"
    assert ledger_by_id["BANK-7001"].transaction_type == "payroll"
    assert ledger_by_id["BANK-7010"].normalized_category == "bank_fees"
    assert ledger_by_id["BANK-7011"].normalized_category == "intercompany_transfer"
    assert ledger_by_id["CARD-7716"].split_group_id == "GONG-MAY"
    assert ledger_by_id["CARD-7718"].normalized_category == "uncategorized"
    assert ledger_by_id["CARD-7718"].inferred_vendor_id is None
    assert ledger_by_id["CARD-7718"].raw_vendor_name == "Unknown Vendor SaaS Tool"
    dumped_ledger = ledger_by_id["CARD-7718"].model_dump(mode="json")
    assert dumped_ledger["raw_description"] == "Uncategorized SaaS charge"
    assert dumped_ledger["normalized_category"] == "uncategorized"

    normalization = summarize_ledger_normalization([row.model_dump(mode="json") for row in ledger])
    assert normalization["records"] == 17
    assert normalization["inferred_vendor_count"] >= 7
    assert normalization["unknown_vendor_count"] >= 2
    assert normalization["refund_count"] == 2
    assert normalization["fee_count"] == 2
    assert normalization["payroll_count"] == 1
    assert normalization["transfer_count"] == 2
    assert normalization["split_count"] == 2
    assert "raw_description" in normalization["raw_fields_persisted"]
    assert "inferred_vendor_id" in normalization["normalized_fields_persisted"]

    assert len(invoices) == 16  # duplicate invoice kept for reconciliation; one malformed row rejected
    assert [i.invoice_id for i in invoices].count("INV-1005") == 2
    assert any(i.amount < 0 for i in invoices), "credit memo should parse parenthesized negative money"
    assert any(i.vendor_name == "Amazon Web Svcs" and i.issue_date.isoformat() == "2026-04-30" for i in invoices)
    invoice_by_id = {i.invoice_id: i for i in invoices}
    assert invoice_by_id["INV-1012"].status == "disputed"
    assert invoice_by_id["INV-1012"].due_date is None
    assert "inactive users" in (invoice_by_id["INV-1012"].line_description or "")
    assert invoice_by_id["INV-1013"].currency == "EUR"
    assert invoice_by_id["INV-1013"].amount_usd == 1199
    assert invoice_by_id["INV-1015"].currency == "GBP"
    assert invoice_by_id["INV-1015"].payment_status == "partial"
    invoice_messiness = summarize_invoice_messiness([row.model_dump(mode="json") for row in invoices])
    assert invoice_messiness["partial_payment_count"] == 3
    assert invoice_messiness["overdue_count"] == 1
    assert invoice_messiness["disputed_count"] == 1
    assert invoice_messiness["missing_due_date_count"] == 2
    assert invoice_messiness["multi_currency"] is True
    assert invoice_messiness["currencies"] == ["EUR", "GBP", "USD"]
    assert invoice_messiness["line_description_count"] == 16
    assert invoice_messiness["open_balance_total"] > 120_000

    vendor_by_id = {v.vendor_id: v for v in vendors}
    assert vendor_by_id["datadog"].billing_frequency == "annual"
    assert "DataDog Inc" in vendor_by_id["datadog"].contract_aliases
    assert vendor_by_id["datadog"].tiered_pricing[1]["unit"] == "host_month"
    assert vendor_by_id["datadog"].termination_penalty == 30_000
    assert vendor_by_id["acme-analytics"].board_approved is False
    assert vendor_by_id["acme-analytics"].data_processing_addendum is False
    assert vendor_by_id["salesforce"].owner_history[-1]["owner"] == "RevOps"

    opp_by_id = {o.opportunity_id: o for o in crm}
    assert len(crm) == 12
    assert opp_by_id["OPP-201"].probability == 0.70
    assert opp_by_id["OPP-202"].probability == 0.85
    assert opp_by_id["OPP-202"].weighted_arr == 740_000
    assert opp_by_id["OPP-203"].probability is None
    assert opp_by_id["OPP-205"].probability is None
    assert opp_by_id["OPP-206"].close_date.isoformat() == "2026-11-10"
    assert opp_by_id["OPP-208"].opportunity_type == "renewal"
    assert opp_by_id["OPP-209"].account == "Cardinal Fulfilment"
    assert opp_by_id["OPP-211"].account == "Summit Supply Co"
    pipeline_quality = summarize_pipeline_quality([row.model_dump(mode="json") for row in crm])
    assert pipeline_quality["slipped_close_date_count"] == 6
    assert pipeline_quality["stage_aging_count"] >= 8
    assert pipeline_quality["stale_opportunity_count"] >= 5
    assert pipeline_quality["owner_change_count"] >= 8
    assert pipeline_quality["probability_override_count"] >= 9
    assert pipeline_quality["weighted_arr_mismatch_count"] >= 1
    assert pipeline_quality["missing_probability_count"] == 2
    assert pipeline_quality["duplicate_account_count"] == 3
    assert pipeline_quality["renewal_arr_at_risk"] == 830_000
    assert pipeline_quality["total_unweighted_arr"] > pipeline_quality["total_weighted_arr"]

    roles_by_id = {row.role_id: row for row in headcount}
    assert len(headcount) == 9
    assert roles_by_id["HC-101"].mapped_team == "Engineering"
    assert roles_by_id["HC-101"].current_start_date.isoformat() == "2026-09-15"
    assert roles_by_id["HC-101"].recruiting_slippage_days == 45
    assert roles_by_id["HC-104"].approval_status == "partial"
    assert roles_by_id["HC-104"].approved_headcount == 2
    assert roles_by_id["HC-106"].employment_type == "contractor"
    assert roles_by_id["HC-106"].approval_status == "unapproved"
    assert roles_by_id["HC-107"].role_type == "backfill"
    assert roles_by_id["HC-109"].backfill_for == "Implementation PM"
    headcount_quality = summarize_headcount_quality([row.model_dump(mode="json") for row in headcount])
    assert headcount_quality["total_headcount"] == 17
    assert headcount_quality["total_loaded_monthly_cost"] == 318_000
    assert headcount_quality["next_90_day_loaded_cost"] == 174_500
    assert headcount_quality["recruiting_slip_count"] == 8
    assert headcount_quality["contractor_count"] == 3
    assert headcount_quality["backfill_count"] == 2
    assert headcount_quality["partial_approval_count"] == 3
    assert headcount_quality["unapproved_count"] == 2
    assert headcount_quality["department_mapping_drift_count"] == 6
    assert headcount_quality["approval_risk_loaded_cost"] == 106_500

    stale = {s.control_id: s for s in security}
    assert stale["CC6.1"].evidence_date.isoformat() == "2026-01-15"
    assert stale["CC7.2"].blocked_arr == 310_000


def test_messy_demo_fixtures_drive_reconciliation_findings() -> None:
    parsed = _parse_demo_fixtures()

    summaries, discrepancies = run_workflows(
        ledger=parsed["ledger"],
        invoices=parsed["invoices"],
        vendor_export=parsed["vendor_export"],
        opportunities=parsed["crm_opportunities"],
        headcount=parsed["headcount_plan"],
        security=parsed["security_evidence"],
        board_policies=parsed["board_policy"],
        company=COMPANY,
        vendors_seed=VENDORS,
    )

    kinds = {d.kind.value for d in discrepancies}
    expected = {
        "duplicate_invoice",
        "late_invoice",
        "partial_payment",
        "disputed_invoice",
        "missing_due_date",
        "non_usd_invoice",
        "missing_po_number",
        "ledger_vendor_mismatch",
        "ledger_accrual_or_credit",
        "ledger_uncategorized_spend",
        "crm_probability_quality",
        "department_name_drift",
        "unplanned_headcount",
        "stale_security_evidence",
        "security_revenue_blocker",
        "board_constraint_violation",
        "contract_invoice_mismatch",
        "renewal_urgency",
        "missing_board_approval",
        "sla_security_clause_gap",
        "owner_attestation_gap",
    }
    assert expected.issubset(kinds)
    assert all(summary.status == "discrepancies" for summary in summaries)
    assert any("INV-1005" in d.title for d in discrepancies if d.kind.value == "duplicate_invoice")
    assert any("partially paid" in d.title.lower() for d in discrepancies if d.kind.value == "partial_payment")
    assert any("disputed" in d.title.lower() for d in discrepancies if d.kind.value == "disputed_invoice")
    assert any("missing a due date" in d.title.lower() for d in discrepancies if d.kind.value == "missing_due_date")
    assert any("denominated in eur" in d.title.lower() for d in discrepancies if d.kind.value == "non_usd_invoice")
    assert any(d.recommended_action for d in discrepancies if d.kind.value in {"partial_payment", "disputed_invoice", "missing_due_date", "non_usd_invoice"})
    assert any("Data Dog" in d.title for d in discrepancies if d.kind.value == "contract_overspend")
    assert any("invoice cadence" in d.title.lower() for d in discrepancies if d.kind.value == "contract_invoice_mismatch")
    assert any("auto-renew notice" in d.title.lower() for d in discrepancies if d.kind.value == "renewal_urgency")
    assert any("Eng" in d.title for d in discrepancies if d.kind.value == "department_name_drift")
    assert any("Prototype Lab Contractor" in d.title for d in discrepancies if d.kind.value == "unplanned_headcount")
    assert any("Demand Gen Manager" in d.title for d in discrepancies if d.kind.value == "unplanned_headcount")
    assert any("approval gap" in d.title.lower() and "Account Executive" in d.title for d in discrepancies if d.kind.value == "headcount_drift")
    assert any("Hiring start slipped" in d.title and "Platform Backfill" in d.title for d in discrepancies if d.kind.value == "headcount_drift")
    assert any("fully loaded" in d.detail.lower() for d in discrepancies if d.kind.value == "headcount_drift")
    headcount_summary = next(summary for summary in summaries if summary.workflow == "headcount_to_plan")
    assert "partial approvals" in headcount_summary.detail
    assert "loaded cost starts within 90 days" in headcount_summary.detail
    crm_quality = [d for d in discrepancies if d.kind.value == "crm_probability_quality"]
    assert any("pipeline quality" in d.title.lower() for d in crm_quality)
    assert any("slipped" in d.detail.lower() for d in crm_quality)
    assert any("probability override" in d.detail.lower() for d in crm_quality)
    assert any("weighted arr" in d.detail.lower() for d in crm_quality)
