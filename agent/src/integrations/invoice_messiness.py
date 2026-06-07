"""Parser-derived quality signals for accounts-payable invoice exports."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _balance(row: dict[str, Any]) -> float:
    explicit = row.get("balance_due")
    if explicit not in (None, ""):
        return _float(explicit)
    amount = _float(row.get("amount_usd") if _norm(row.get("currency")) != "usd" and row.get("amount_usd") else row.get("amount"))
    paid = _float(row.get("paid_amount"))
    if amount <= 0:
        return amount
    return max(0.0, amount - paid)


def summarize_invoice_messiness(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return compact, deterministic quality signals for invoice provenance."""
    if not records:
        return {}

    status_counts: Counter[str] = Counter()
    currencies: set[str] = set()
    names_by_vendor: dict[str, set[str]] = defaultdict(set)
    duplicate_ids: Counter[str] = Counter()
    partial_count = 0
    overdue_count = 0
    disputed_count = 0
    missing_due_date_count = 0
    line_description_count = 0
    non_usd_count = 0
    open_balance_total = 0.0
    missing_po_large_count = 0

    for row in records:
        invoice_id = str(row.get("invoice_id") or "").strip()
        if invoice_id:
            duplicate_ids[invoice_id] += 1
        status = _norm(row.get("payment_status") or row.get("status")) or "unknown"
        status_counts[status] += 1
        currency = str(row.get("currency") or "USD").strip().upper()
        currencies.add(currency)
        vendor_id = str(row.get("vendor_id") or "").strip()
        vendor_name = str(row.get("vendor_name") or "").strip()
        if vendor_id and vendor_name:
            names_by_vendor[vendor_id].add(vendor_name)
        balance = _balance(row)
        open_balance_total += max(0.0, balance)
        paid = _float(row.get("paid_amount"))
        amount = _float(row.get("amount_usd") if currency != "USD" and row.get("amount_usd") else row.get("amount"))
        if status in {"partial", "partially_paid", "partial_payment"} or (paid > 0 and balance > 0 and amount > 0):
            partial_count += 1
        if status in {"overdue", "late", "past_due"}:
            overdue_count += 1
        if status in {"disputed", "in_dispute"} or row.get("dispute_status") or row.get("dispute_reason"):
            disputed_count += 1
        if not row.get("due_date") and amount > 0 and status not in {"paid", "void", "credit"}:
            missing_due_date_count += 1
        if row.get("line_description") or row.get("line_items"):
            line_description_count += 1
        if currency and currency != "USD":
            non_usd_count += 1
        if amount >= 5_000 and not row.get("po_number") and status not in {"paid", "void", "credit"}:
            missing_po_large_count += 1

    duplicate_vendor_name_count = sum(1 for names in names_by_vendor.values() if len(names) > 1)
    duplicate_invoice_ids = sorted([invoice_id for invoice_id, count in duplicate_ids.items() if count > 1])
    issue_count = (
        partial_count
        + overdue_count
        + disputed_count
        + missing_due_date_count
        + duplicate_vendor_name_count
        + non_usd_count
        + missing_po_large_count
        + len(duplicate_invoice_ids)
    )

    return {
        "records": len(records),
        "issue_count": issue_count,
        "partial_payment_count": partial_count,
        "overdue_count": overdue_count,
        "disputed_count": disputed_count,
        "missing_due_date_count": missing_due_date_count,
        "duplicate_vendor_name_count": duplicate_vendor_name_count,
        "duplicate_invoice_ids": duplicate_invoice_ids,
        "non_usd_count": non_usd_count,
        "currencies": sorted(currencies),
        "multi_currency": len(currencies) > 1,
        "line_description_count": line_description_count,
        "missing_po_large_count": missing_po_large_count,
        "open_balance_total": round(open_balance_total, 2),
        "status_counts": dict(sorted(status_counts.items())),
        "recommended_actions": [
            "Confirm remaining balances and cash timing for partial or overdue invoices.",
            "Hold disputed invoices until owner, contract, and AP evidence agree.",
            "Backfill due dates, payment terms, and FX conversion before relying on runway.",
        ],
        "fields_persisted": [
            "line_description",
            "paid_amount",
            "balance_due",
            "payment_date",
            "dispute_status",
            "dispute_reason",
            "exchange_rate",
            "amount_usd",
        ],
    }
