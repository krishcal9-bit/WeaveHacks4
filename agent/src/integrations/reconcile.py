"""
Reconciliation workflows: compare imported operations data against the seeded
company system of record and emit explainable, deterministic discrepancies.

Each workflow is a pure function over typed records (no Redis I/O, no model
calls) so it is easy to reason about and test. A workflow whose required source
has not been imported returns an ``insufficient_data`` summary with explicit
blockers and an informational ``MISSING_SOURCE`` discrepancy — it never assumes a
clean pass on absent data.

Workflows
---------
1. invoices → vendors            (unmatched / shadow spend)
2. contract terms → spend        (annualised over/underspend vs contract value)
3. ledger quality → invoices      (credits, accruals, unmatched vendor aliases)
4. CRM pipeline → forecast        (weighted ARR vs the company forecast assumption)
5. headcount → hiring plan        (count / cost drift, department naming drift)
6. policy & board constraints     (vendor commitment notification, renewal review)
7. security → revenue priority    (controls blocking signed/late-stage revenue)
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from src.integrations.models import (
    BoardPolicyDoc,
    CrmOpportunity,
    Discrepancy,
    DiscrepancyKind,
    DiscrepancySeverity,
    HeadcountPlanRow,
    Invoice,
    LedgerEntry,
    SecurityEvidence,
    SourceType,
    VendorRecord,
    WorkflowSummary,
)
from src.integrations.crm_pipeline_quality import STAGE_AGING_DAYS, STALE_ACTIVITY_DAYS, summarize_pipeline_quality
from src.integrations.headcount_quality import summarize_headcount_quality

# Tolerances (fraction) before a variance is flagged. Tuned so small noise does
# not raise discrepancies but material drift does.
CONTRACT_SPEND_TOLERANCE = 0.10
CRM_FORECAST_TOLERANCE = 0.10
HEADCOUNT_COST_TOLERANCE = 0.05

# Default policy thresholds, used when a board_policy connector does not supply a
# machine-checkable rule. These mirror the seeded Acme board constraints/policies.
DEFAULT_VENDOR_BOARD_NOTIFICATION = 150_000.0   # annualised single-vendor commitment
DEFAULT_VENDOR_REVIEW_VALUE = 100_000.0         # contract value requiring review
DEFAULT_VENDOR_REVIEW_DAYS = 60                  # days before renewal to review
DEFAULT_MIN_RUNWAY_MONTHS = 9.0
MISSING_PO_THRESHOLD = 5_000.0
SECURITY_EVIDENCE_STALE_DAYS = 90


def _disc_id(kind: DiscrepancyKind, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]
    return f"{kind.value}-{digest}"


def _norm(name: Optional[str]) -> str:
    return " ".join((name or "").lower().split())


_LEGAL_SUFFIXES = {
    "inc",
    "inc.",
    "llc",
    "ltd",
    "ltd.",
    "corp",
    "corp.",
    "corporation",
    "co",
    "co.",
    "company",
}


def _canonical_vendor_name(name: Optional[str]) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (name or "").lower())
    words = [w for w in text.split() if w not in _LEGAL_SUFFIXES]
    compact = " ".join(words)
    aliases = {
        "amazon web svcs": "amazon web services",
        "amazon aws": "amazon web services",
        "aws": "amazon web services",
        "data dog": "datadog",
        "datadog": "datadog",
        "sfci sales cloud": "salesforce",
        "sales force": "salesforce",
    }
    return aliases.get(compact, compact)


def _contract_name_index(contracts: dict[str, dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for vid, contract in contracts.items():
        names = [contract.get("name"), vid, *(contract.get("contract_aliases") or [])]
        for name in names:
            if not name:
                continue
            index[_norm(str(name))] = vid
            index[_canonical_vendor_name(str(name))] = vid
    return index


def _match_contract(
    *,
    vendor_id: Optional[str],
    vendor_name: Optional[str],
    contracts: dict[str, dict[str, Any]],
    name_index: dict[str, str],
) -> Optional[str]:
    if vendor_id and vendor_id in contracts:
        return vendor_id
    if vendor_id:
        alias = name_index.get(_canonical_vendor_name(vendor_id)) or name_index.get(_norm(vendor_id))
        if alias:
            return alias
    return name_index.get(_norm(vendor_name)) or name_index.get(_canonical_vendor_name(vendor_name))


def _invoice_status(inv: Invoice) -> str:
    return _norm(inv.payment_status or inv.status)


def _invoice_amount_usd(inv: Invoice) -> float:
    amount = float(inv.amount or 0.0)
    currency = (inv.currency or "USD").upper()
    if currency == "USD":
        return amount
    if inv.amount_usd is not None:
        return float(inv.amount_usd)
    if inv.exchange_rate:
        return amount * float(inv.exchange_rate)
    return amount


def _invoice_balance(inv: Invoice) -> float:
    if inv.balance_due is not None:
        return float(inv.balance_due)
    amount = _invoice_amount_usd(inv)
    if amount <= 0:
        return amount
    paid = float(inv.paid_amount or 0.0)
    return max(0.0, amount - paid)


def _is_invoice_open(inv: Invoice) -> bool:
    return _invoice_status(inv) not in {"paid", "void", "credit", "cancelled", "canceled"}


def _is_partial_payment(inv: Invoice) -> bool:
    status = _invoice_status(inv)
    paid = float(inv.paid_amount or 0.0)
    balance = _invoice_balance(inv)
    amount = _invoice_amount_usd(inv)
    return status in {"partial", "partially_paid", "partial_payment"} or (paid > 0 and balance > 0 and amount > 0)


def _is_disputed_invoice(inv: Invoice) -> bool:
    status = _invoice_status(inv)
    return status in {"disputed", "in_dispute"} or bool(inv.dispute_status or inv.dispute_reason)


def _money_label(amount: float, currency: str = "USD") -> str:
    return f"{amount:,.0f} {currency}"


def _canonical_team(name: Optional[str]) -> str:
    text = _norm(name).replace("&", "and")
    aliases = {
        "eng": "engineering",
        "engineering platform": "engineering",
        "engineering / platform": "engineering",
        "engineering and platform": "engineering",
        "cust success": "customer success",
        "customer success cs": "customer success",
        "cs": "customer success",
        "sales ops": "sales",
        "revenue sales": "sales",
        "growth": "marketing",
        "growth marketing": "marketing",
    }
    return aliases.get(text, text)


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1", "on"}:
        return True
    if text in {"false", "no", "n", "0", "off"}:
        return False
    return None


def _billing_frequency(contract: dict[str, Any]) -> str:
    raw = str(contract.get("billing_frequency") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"annually", "yearly", "annual_prepay"}:
        return "annual"
    if raw in {"monthly_in_arrears", "month_to_month"}:
        return "monthly"
    return raw


def _notice_days(contract: dict[str, Any], fallback: int) -> int:
    value = contract.get("notice_window_days") or contract.get("termination_notice_days") or fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _missing(workflow: str, source_type: SourceType, *also: str) -> tuple[WorkflowSummary, list[Discrepancy]]:
    needed = ", ".join([source_type.value, *also])
    blocker = f"{workflow}: requires imported source(s): {needed}"
    disc = Discrepancy(
        id=_disc_id(DiscrepancyKind.MISSING_SOURCE, workflow),
        kind=DiscrepancyKind.MISSING_SOURCE,
        severity=DiscrepancySeverity.INFO,
        title=f"{workflow} not run — source not configured",
        detail=f"No data imported for: {needed}. Configure the connector to enable this check.",
        sources=[source_type.value, *also],
        recommended_action=f"Import {needed} (set the connector env var or load the demo fixture).",
        confidence=100,
    )
    return WorkflowSummary(workflow=workflow, status="insufficient_data", blockers=[blocker], detail=blocker), [disc]


# --------------------------------------------------------------------------- #
# Contract baseline: seeded vendors overlaid with any imported vendor export
# --------------------------------------------------------------------------- #
def _build_contracts(
    vendors_seed: list[dict[str, Any]],
    vendor_export: list[VendorRecord],
) -> dict[str, dict[str, Any]]:
    """Authoritative contract terms keyed by vendor_id, with a name index."""
    contracts: dict[str, dict[str, Any]] = {}
    contract_fields = (
        "monthly_cost",
        "renewal_date",
        "status",
        "owner",
        "termination_notice_days",
        "notice_window_days",
        "auto_renew",
        "board_approved",
        "board_approval_id",
        "billing_frequency",
        "billing_terms",
        "contract_aliases",
        "tiered_pricing",
        "owner_history",
        "termination_penalty",
        "sla_uptime_pct",
        "sla_credits",
        "security_clause",
        "data_processing_addendum",
        "switching_cost",
        "data_sensitivity",
        "notes",
    )
    seeded_ids = {(v.get("id") or _norm(v.get("name"))) for v in vendors_seed}
    for v in vendors_seed:
        vid = v.get("id") or _norm(v.get("name"))
        doc = {
            "vendor_id": vid,
            "name": v.get("name"),
            "annual_cost": float(v.get("annual_cost") or 0),
            "source": "seed",
            "board_approved": v.get("board_approved", True),  # seeded vendors are existing, board-known commitments
        }
        for field in contract_fields:
            if field in v:
                doc[field] = v.get(field)
        doc["renewal_date"] = _as_date(doc.get("renewal_date"))
        contracts[vid] = doc
    for rec in vendor_export:
        doc = {
            "vendor_id": rec.vendor_id,
            "name": rec.name,
            "annual_cost": float(rec.annual_cost or 0),
            "renewal_date": rec.renewal_date,
            "status": rec.status,
            "source": "vendor_export",
            "board_approved": rec.board_approved,
            "is_new": rec.vendor_id not in seeded_ids,
        }
        for field in contract_fields:
            value = getattr(rec, field, None)
            if value not in (None, [], ""):
                doc[field] = value
        contracts[rec.vendor_id] = doc
    return contracts


# --------------------------------------------------------------------------- #
# 1) Invoices → vendors
# --------------------------------------------------------------------------- #
def reconcile_invoices_to_vendors(
    invoices: list[Invoice],
    contracts: dict[str, dict[str, Any]],
    as_of: date,
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "invoices_to_vendors"
    if not invoices:
        return _missing(workflow, SourceType.INVOICES)
    name_index = _contract_name_index(contracts)
    discrepancies: list[Discrepancy] = []
    seen_invoices: dict[str, Invoice] = {}
    matched = 0
    partials = 0
    disputes = 0
    missing_due_dates = 0
    fx_rows = 0
    for inv in invoices:
        amount_usd = _invoice_amount_usd(inv)
        balance = _invoice_balance(inv)
        status = _invoice_status(inv)
        if inv.invoice_id in seen_invoices:
            first = seen_invoices[inv.invoice_id]
            duplicate_changed = first.vendor_name != inv.vendor_name or round(_invoice_amount_usd(first), 2) != round(amount_usd, 2)
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.DUPLICATE_INVOICE, inv.invoice_id, inv.vendor_name, str(amount_usd)),
                    kind=DiscrepancyKind.DUPLICATE_INVOICE,
                    severity=DiscrepancySeverity.HIGH if duplicate_changed else DiscrepancySeverity.MEDIUM,
                    title=f"Duplicate invoice id {inv.invoice_id}",
                    detail=(
                        f"Invoice id {inv.invoice_id} appears more than once "
                        f"({first.vendor_name} {_money_label(_invoice_amount_usd(first))}; "
                        f"{inv.vendor_name} {_money_label(amount_usd)})."
                    ),
                    sources=[SourceType.INVOICES.value],
                    observed={
                        "invoice_id": inv.invoice_id,
                        "vendors": [first.vendor_name, inv.vendor_name],
                        "amounts_usd": [round(_invoice_amount_usd(first), 2), round(amount_usd, 2)],
                    },
                    recommended_action="Hold payment until AP confirms whether this is a duplicate, corrected invoice, or vendor-portal replacement.",
                    confidence=92,
                    references={"invoice_id": inv.invoice_id},
                )
            )
        else:
            seen_invoices[inv.invoice_id] = inv

        vid = _match_contract(
            vendor_id=inv.vendor_id,
            vendor_name=inv.vendor_name,
            contracts=contracts,
            name_index=name_index,
        )
        if vid:
            matched += 1
        else:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.UNMATCHED_INVOICE, inv.invoice_id),
                    kind=DiscrepancyKind.UNMATCHED_INVOICE,
                    severity=DiscrepancySeverity.MEDIUM,
                    title=f"Invoice {inv.invoice_id} has no vendor on file",
                    detail=(
                        f"Invoice from '{inv.vendor_name}' for {_money_label(amount_usd)} "
                        f"does not match any contracted vendor — possible unapproved/shadow spend."
                    ),
                    sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                    observed={"invoice_id": inv.invoice_id, "vendor_name": inv.vendor_name, "amount_usd": round(amount_usd, 2)},
                    recommended_action="Confirm the vendor exists in procurement and the spend is authorised.",
                    confidence=90,
                    references={"invoice_id": inv.invoice_id},
                )
            )

        if _is_partial_payment(inv):
            partials += 1
            severity = DiscrepancySeverity.HIGH if balance >= 25_000 else DiscrepancySeverity.MEDIUM if balance >= 5_000 else DiscrepancySeverity.LOW
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.PARTIAL_PAYMENT, inv.invoice_id),
                    kind=DiscrepancyKind.PARTIAL_PAYMENT,
                    severity=severity,
                    title=f"Invoice {inv.invoice_id} is partially paid",
                    detail=(
                        f"{inv.vendor_name} shows paid {_money_label(float(inv.paid_amount or 0.0))} "
                        f"with {_money_label(balance)} still outstanding."
                    ),
                    sources=[SourceType.INVOICES.value],
                    observed={
                        "invoice_id": inv.invoice_id,
                        "paid_amount": inv.paid_amount,
                        "balance_due": round(balance, 2),
                        "payment_date": inv.payment_date.isoformat() if inv.payment_date else None,
                    },
                    recommended_action="Treasury should model the remaining cash outflow date and AP should confirm whether the vendor expects immediate catch-up payment.",
                    confidence=88,
                    references={"invoice_id": inv.invoice_id},
                )
            )

        if _is_disputed_invoice(inv):
            disputes += 1
            severity = DiscrepancySeverity.HIGH if balance >= 5_000 else DiscrepancySeverity.MEDIUM
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.DISPUTED_INVOICE, inv.invoice_id),
                    kind=DiscrepancyKind.DISPUTED_INVOICE,
                    severity=severity,
                    title=f"Invoice {inv.invoice_id} is disputed",
                    detail=(
                        f"{inv.vendor_name} invoice dispute: {inv.dispute_reason or inv.dispute_status or inv.status}. "
                        f"Balance at issue is {_money_label(balance)}."
                    ),
                    sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                    observed={
                        "invoice_id": inv.invoice_id,
                        "dispute_status": inv.dispute_status,
                        "dispute_reason": inv.dispute_reason,
                        "balance_due": round(balance, 2),
                    },
                    recommended_action="Hold payment, request owner evidence, and have Procurement tie the disputed line to contract terms before approval.",
                    confidence=90,
                    references={"invoice_id": inv.invoice_id},
                )
            )

        if not inv.due_date and _is_invoice_open(inv) and amount_usd > 0:
            missing_due_dates += 1
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.MISSING_DUE_DATE, inv.invoice_id),
                    kind=DiscrepancyKind.MISSING_DUE_DATE,
                    severity=DiscrepancySeverity.MEDIUM if amount_usd >= MISSING_PO_THRESHOLD else DiscrepancySeverity.LOW,
                    title=f"Invoice {inv.invoice_id} is missing a due date",
                    detail=(
                        f"{inv.vendor_name} invoice for {_money_label(amount_usd)} has terms "
                        f"'{inv.terms or 'not supplied'}' but no due date in the AP export."
                    ),
                    sources=[SourceType.INVOICES.value],
                    observed={"invoice_id": inv.invoice_id, "terms": inv.terms, "amount_usd": round(amount_usd, 2)},
                    recommended_action="Backfill due date/payment terms before Treasury relies on cash runway or payment sequencing.",
                    confidence=86,
                    references={"invoice_id": inv.invoice_id},
                )
            )

        if (inv.currency or "USD").upper() != "USD":
            fx_rows += 1
            has_fx = inv.amount_usd is not None and inv.exchange_rate is not None
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.NON_USD_INVOICE, inv.invoice_id),
                    kind=DiscrepancyKind.NON_USD_INVOICE,
                    severity=DiscrepancySeverity.LOW if has_fx else DiscrepancySeverity.MEDIUM,
                    title=f"Invoice {inv.invoice_id} is denominated in {inv.currency}",
                    detail=(
                        f"{inv.vendor_name} invoice is {inv.amount:,.0f} {inv.currency}; "
                        f"USD equivalent is {_money_label(amount_usd)} using rate {inv.exchange_rate or 'missing'}."
                    ),
                    sources=[SourceType.INVOICES.value],
                    expected="USD runway model input",
                    observed={"currency": inv.currency, "amount": inv.amount, "exchange_rate": inv.exchange_rate, "amount_usd": round(amount_usd, 2)},
                    recommended_action="Confirm FX rate and settlement date before locking the cash forecast.",
                    confidence=80 if has_fx else 70,
                    references={"invoice_id": inv.invoice_id},
                )
            )

        overdue_status = status in {"open", "unpaid", "pending", "overdue", "late", "past_due", "partial", "partially_paid", "disputed", "in_dispute"}
        if inv.due_date and inv.due_date < as_of and overdue_status and balance > 0:
            days = (as_of - inv.due_date).days
            severity = DiscrepancySeverity.CRITICAL if days >= 30 and balance >= 25_000 else DiscrepancySeverity.HIGH if days >= 14 or balance >= 25_000 else DiscrepancySeverity.MEDIUM
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.LATE_INVOICE, inv.invoice_id),
                    kind=DiscrepancyKind.LATE_INVOICE,
                    severity=severity,
                    title=f"Invoice {inv.invoice_id} is {days} day(s) late",
                    detail=f"{inv.vendor_name} invoice due {inv.due_date.isoformat()} remains {inv.status} with {_money_label(balance)} outstanding.",
                    sources=[SourceType.INVOICES.value],
                    observed={"invoice_id": inv.invoice_id, "due_date": inv.due_date.isoformat(), "status": inv.status, "balance_due": round(balance, 2)},
                    recommended_action="Confirm immediate payment timing, vendor service risk, and whether late cash receipt would force payment deferral.",
                    confidence=86,
                    references={"invoice_id": inv.invoice_id},
                )
            )

        if amount_usd >= MISSING_PO_THRESHOLD and _is_invoice_open(inv) and not inv.po_number:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.MISSING_PO_NUMBER, inv.invoice_id),
                    kind=DiscrepancyKind.MISSING_PO_NUMBER,
                    severity=DiscrepancySeverity.HIGH if amount_usd >= 20_000 else DiscrepancySeverity.MEDIUM,
                    title=f"Invoice {inv.invoice_id} is missing a PO number",
                    detail=f"{inv.vendor_name} invoice for {_money_label(amount_usd)} lacks a purchase order reference.",
                    sources=[SourceType.INVOICES.value],
                    observed={"invoice_id": inv.invoice_id, "amount_usd": round(amount_usd, 2), "po_number": inv.po_number},
                    recommended_action="Tie the invoice to an approved PO or hold approval routing.",
                    confidence=84,
                    references={"invoice_id": inv.invoice_id},
                )
            )
    status = "discrepancies" if discrepancies else "ok"
    detail = (
        f"{matched}/{len(invoices)} invoices matched to a contracted vendor; "
        f"{partials} partial, {disputes} disputed, {missing_due_dates} missing due date, {fx_rows} non-USD."
    )
    return WorkflowSummary(workflow=workflow, status=status, checked=len(invoices), discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# 2) Contract terms → spend
# --------------------------------------------------------------------------- #
def reconcile_contract_terms_to_spend(
    invoices: list[Invoice],
    contracts: dict[str, dict[str, Any]],
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "contract_terms_to_spend"
    if not invoices:
        return _missing(workflow, SourceType.INVOICES)
    name_index = _contract_name_index(contracts)

    spend: dict[str, float] = {}
    months: dict[str, set[str]] = {}
    invoice_rows: dict[str, list[Invoice]] = {}
    for inv in invoices:
        vid = _match_contract(
            vendor_id=inv.vendor_id,
            vendor_name=inv.vendor_name,
            contracts=contracts,
            name_index=name_index,
        )
        if not vid:
            continue  # unmatched invoices are handled by workflow 1
        spend[vid] = spend.get(vid, 0.0) + _invoice_amount_usd(inv)
        invoice_rows.setdefault(vid, []).append(inv)
        period = inv.period or (inv.issue_date.strftime("%Y-%m") if inv.issue_date else None)
        if period:
            months.setdefault(vid, set()).add(period)

    discrepancies: list[Discrepancy] = []
    checked = 0
    for vid, total in spend.items():
        contract = contracts.get(vid) or {}
        annual_cost = float(contract.get("annual_cost") or 0)
        if annual_cost <= 0:
            continue
        checked += 1
        observed_months = len(months.get(vid)) or 1
        annualised = total / observed_months * 12
        ratio = annualised / annual_cost
        monthly_cost = float(contract.get("monthly_cost") or annual_cost / 12)
        billing = _billing_frequency(contract)
        vendor_invoices = invoice_rows.get(vid, [])
        positive_invoice_amounts = [_invoice_amount_usd(inv) for inv in vendor_invoices if _invoice_amount_usd(inv) > 0]
        if billing == "annual" and observed_months > 1:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.CONTRACT_INVOICE_MISMATCH, vid, "billing-frequency"),
                    kind=DiscrepancyKind.CONTRACT_INVOICE_MISMATCH,
                    severity=DiscrepancySeverity.MEDIUM,
                    title=f"{contract.get('name', vid)} invoice cadence conflicts with annual billing terms",
                    detail=(
                        f"Contract metadata says annual billing ({contract.get('billing_terms') or 'no terms text'}), "
                        f"but AP shows invoices across {observed_months} period(s). Confirm whether these are accruals, "
                        "overage true-ups, or duplicate monthly invoices before relying on renewal economics."
                    ),
                    sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                    expected="annual billing cadence",
                    observed=f"{observed_months} invoice period(s)",
                    recommended_action="Tie each invoice to the contract billing schedule or update the contract metadata.",
                    confidence=82,
                    references={"vendor_id": vid, "billing_frequency": billing},
                )
            )
        if billing == "monthly" and positive_invoice_amounts:
            largest_invoice = max(positive_invoice_amounts)
            if monthly_cost > 0 and largest_invoice > monthly_cost * (1 + CONTRACT_SPEND_TOLERANCE):
                discrepancies.append(
                    Discrepancy(
                        id=_disc_id(DiscrepancyKind.CONTRACT_INVOICE_MISMATCH, vid, "monthly-amount"),
                        kind=DiscrepancyKind.CONTRACT_INVOICE_MISMATCH,
                        severity=DiscrepancySeverity.MEDIUM,
                        title=f"{contract.get('name', vid)} invoice exceeds monthly contract rate",
                        detail=(
                            f"Largest invoice {largest_invoice:,.0f} vs expected monthly contract rate {monthly_cost:,.0f}. "
                            "Check line descriptions, usage overages, price tiers, or invoice coding before approving payment."
                        ),
                        sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                        expected=round(monthly_cost, 2),
                        observed=round(largest_invoice, 2),
                        delta=round(largest_invoice - monthly_cost, 2),
                        recommended_action="Reconcile invoice amount to contract tier or require procurement sign-off on overage.",
                        confidence=84,
                        references={"vendor_id": vid, "billing_frequency": billing},
                    )
                )
        if contract.get("tiered_pricing") and monthly_cost > 0 and positive_invoice_amounts:
            over_tier = [amount for amount in positive_invoice_amounts if amount > monthly_cost * (1 + CONTRACT_SPEND_TOLERANCE)]
            if over_tier:
                largest_invoice = max(over_tier)
                discrepancies.append(
                    Discrepancy(
                        id=_disc_id(DiscrepancyKind.CONTRACT_INVOICE_MISMATCH, vid, "tiered-pricing"),
                        kind=DiscrepancyKind.CONTRACT_INVOICE_MISMATCH,
                        severity=DiscrepancySeverity.HIGH if largest_invoice > monthly_cost * 1.25 else DiscrepancySeverity.MEDIUM,
                        title=f"{contract.get('name', vid)} invoices appear above contracted tier",
                        detail=(
                            f"Invoice amount {largest_invoice:,.0f} exceeds the implied monthly commitment "
                            f"{monthly_cost:,.0f}; contract has {len(contract.get('tiered_pricing') or [])} pricing tier(s)."
                        ),
                        sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                        expected=round(monthly_cost, 2),
                        observed=round(largest_invoice, 2),
                        delta=round(largest_invoice - monthly_cost, 2),
                        recommended_action="Map usage to the contract tier table and use overage as a renewal negotiation lever.",
                        confidence=83,
                        references={"vendor_id": vid},
                    )
                )
        if ratio > 1 + CONTRACT_SPEND_TOLERANCE:
            severity = DiscrepancySeverity.HIGH if ratio > 1.25 else DiscrepancySeverity.MEDIUM
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.CONTRACT_OVERSPEND, vid),
                    kind=DiscrepancyKind.CONTRACT_OVERSPEND,
                    severity=severity,
                    title=f"{contract.get('name', vid)} spend is running {ratio - 1:.0%} over contract",
                    detail=(
                        f"Annualised invoice run-rate {annualised:,.0f} vs contracted {annual_cost:,.0f} "
                        f"(observed over {observed_months} month(s))."
                    ),
                    sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                    expected=annual_cost,
                    observed=round(annualised, 2),
                    delta=round(annualised - annual_cost, 2),
                    recommended_action="Review usage tier / renegotiate; confirm budget impact on burn.",
                    confidence=80,
                    references={"vendor_id": vid},
                )
            )
        elif ratio < 1 - CONTRACT_SPEND_TOLERANCE:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.CONTRACT_UNDERSPEND, vid),
                    kind=DiscrepancyKind.CONTRACT_UNDERSPEND,
                    severity=DiscrepancySeverity.LOW,
                    title=f"{contract.get('name', vid)} spend is {1 - ratio:.0%} under contract",
                    detail=(
                        f"Annualised invoice run-rate {annualised:,.0f} vs contracted {annual_cost:,.0f} — "
                        f"possible unused commitment or savings opportunity."
                    ),
                    sources=[SourceType.INVOICES.value, SourceType.VENDOR_EXPORT.value],
                    expected=annual_cost,
                    observed=round(annualised, 2),
                    delta=round(annualised - annual_cost, 2),
                    recommended_action="Right-size the commitment at renewal or reallocate budget.",
                    confidence=70,
                    references={"vendor_id": vid},
                )
            )
    status = "discrepancies" if discrepancies else "ok"
    detail = f"Compared annualised spend to contract value for {checked} vendor(s)."
    return WorkflowSummary(workflow=workflow, status=status, checked=checked, discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# 2b) Ledger quality and vendor tie-out
# --------------------------------------------------------------------------- #
def reconcile_ledger_quality(
    ledger: list[LedgerEntry],
    contracts: dict[str, dict[str, Any]],
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "ledger_quality"
    if not ledger:
        return _missing(workflow, SourceType.LEDGER)

    name_index = _contract_name_index(contracts)
    discrepancies: list[Discrepancy] = []
    vendor_rows = 0
    for entry in ledger:
        category = _norm(entry.category or entry.normalized_category or entry.inferred_category)
        description = _norm(entry.normalized_description or entry.description)
        vendor_id = entry.vendor_id or entry.inferred_vendor_id
        vendor_name = entry.vendor_name or entry.normalized_vendor_name or entry.inferred_vendor_name
        has_vendor = bool(vendor_id or vendor_name)
        if has_vendor:
            vendor_rows += 1
            vid = _match_contract(
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                contracts=contracts,
                name_index=name_index,
            )
            if not vid:
                discrepancies.append(
                    Discrepancy(
                        id=_disc_id(DiscrepancyKind.LEDGER_VENDOR_MISMATCH, entry.txn_id),
                        kind=DiscrepancyKind.LEDGER_VENDOR_MISMATCH,
                        severity=DiscrepancySeverity.MEDIUM,
                        title=f"Ledger transaction {entry.txn_id} has no matched vendor",
                        detail=(
                            f"Ledger row '{entry.raw_description or entry.description}' for {entry.amount:,.0f} {entry.currency} "
                            f"references '{vendor_name or vendor_id}', but procurement has no matching contract."
                        ),
                        sources=[SourceType.LEDGER.value, SourceType.VENDOR_EXPORT.value],
                        observed={
                            "txn_id": entry.txn_id,
                            "raw_description": entry.raw_description or entry.description,
                            "vendor_name": vendor_name,
                            "amount": entry.amount,
                            "normalization_confidence": entry.normalization_confidence,
                        },
                        recommended_action="Map the vendor alias, attach a contract, or classify the spend as unapproved/shadow.",
                        confidence=82,
                        references={"txn_id": entry.txn_id},
                    )
                )

        is_uncategorized_spend = (entry.amount or 0) < 0 and category in {"", "uncategorized", "not_categorized"} and abs(entry.amount or 0) >= 1_000
        if is_uncategorized_spend:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.LEDGER_UNCATEGORIZED_SPEND, entry.txn_id),
                    kind=DiscrepancyKind.LEDGER_UNCATEGORIZED_SPEND,
                    severity=DiscrepancySeverity.MEDIUM,
                    title=f"Ledger spend needs category review: {entry.txn_id}",
                    detail=(
                        f"Bank/card row '{entry.raw_description or entry.description}' for {abs(entry.amount or 0):,.0f} "
                        "has no reliable category after normalization."
                    ),
                    sources=[SourceType.LEDGER.value],
                    observed={
                        "txn_id": entry.txn_id,
                        "raw_description": entry.raw_description or entry.description,
                        "normalized_description": entry.normalized_description,
                        "normalized_category": entry.normalized_category,
                        "inferred_vendor_name": entry.inferred_vendor_name,
                    },
                    recommended_action="Assign a reviewed GL category and vendor mapping before relying on burn analysis.",
                    confidence=80,
                    references={"txn_id": entry.txn_id},
                )
            )

        is_credit = (entry.amount or 0) > 0 and category not in {"revenue", "cash_receipt", "intercompany_transfer"}
        is_accrual = "accrual" in category or "accrual" in description
        if is_credit or is_accrual:
            label = "credit" if is_credit else "accrual"
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.LEDGER_ACCRUAL_OR_CREDIT, entry.txn_id, label),
                    kind=DiscrepancyKind.LEDGER_ACCRUAL_OR_CREDIT,
                    severity=DiscrepancySeverity.LOW if is_credit else DiscrepancySeverity.MEDIUM,
                    title=f"Ledger {label} needs tie-out: {entry.txn_id}",
                    detail=(
                        f"Ledger row '{entry.raw_description or entry.description}' is classified as {category or 'uncategorized'} "
                        f"with amount {entry.amount:,.0f}. Credits and accruals should be tied to invoices, "
                        "cash timing, and forecast assumptions before the council relies on burn."
                    ),
                    sources=[SourceType.LEDGER.value, SourceType.INVOICES.value],
                    observed={
                        "txn_id": entry.txn_id,
                        "amount": entry.amount,
                        "category": entry.category,
                        "normalized_category": entry.normalized_category,
                        "transaction_type": entry.transaction_type,
                    },
                    recommended_action="Tie the ledger adjustment to an invoice, credit memo, or accrual schedule.",
                    confidence=78,
                    references={"txn_id": entry.txn_id},
                )
            )

    status = "discrepancies" if discrepancies else "ok"
    detail = f"Checked {len(ledger)} ledger row(s), including {vendor_rows} vendor-coded transaction(s)."
    return WorkflowSummary(workflow=workflow, status=status, checked=len(ledger), discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# 3) CRM pipeline → forecast
# --------------------------------------------------------------------------- #
def reconcile_crm_to_forecast(
    opportunities: list[CrmOpportunity],
    company: dict[str, Any],
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "crm_to_forecast"
    if not opportunities:
        return _missing(workflow, SourceType.CRM_OPPORTUNITIES)
    pipeline = company.get("pipeline_by_stage") or []
    if not pipeline:
        blocker = f"{workflow}: company.pipeline_by_stage is missing from the system of record."
        return WorkflowSummary(workflow=workflow, status="insufficient_data", blockers=[blocker], detail=blocker), []

    crm_weighted = sum(o.weighted() for o in opportunities)
    forecast_weighted = sum(float(s.get("weighted_arr") or 0) for s in pipeline)
    quality = summarize_pipeline_quality([o.model_dump(mode="json") for o in opportunities])
    as_of = _as_date(company.get("updated")) or date(2026, 6, 15)
    account_names: dict[str, set[str]] = {}
    for opp in opportunities:
        account_key = opp.account_id or opp.parent_account or opp.account
        if not account_key or not opp.account:
            continue
        account_names.setdefault(account_key, set()).add(opp.account)
    discrepancies: list[Discrepancy] = []
    for opp in opportunities:
        quality_notes: list[str] = []
        if opp.probability is None and opp.weighted_arr is None:
            quality_notes.append("missing probability and weighted ARR")
        elif opp.probability is None:
            quality_notes.append("missing probability; using supplied weighted ARR only")
        elif opp.probability < 0 or opp.probability > 1:
            quality_notes.append(f"probability outside 0..1 after normalization ({opp.probability})")
        if opp.close_date is None:
            quality_notes.append("missing close date")
        prior_close = opp.prior_close_date or opp.original_close_date
        if opp.close_date and prior_close and opp.close_date > prior_close:
            quality_notes.append(f"close date slipped from {prior_close.isoformat()} to {opp.close_date.isoformat()}")
        if (opp.days_in_stage or 0) > STAGE_AGING_DAYS:
            quality_notes.append(f"{opp.days_in_stage} days in {opp.stage}")
        elif opp.stage_entered_date and (as_of - opp.stage_entered_date).days > STAGE_AGING_DAYS:
            quality_notes.append(f"{(as_of - opp.stage_entered_date).days} days in {opp.stage}")
        if opp.last_activity_date and (as_of - opp.last_activity_date).days > STALE_ACTIVITY_DAYS:
            quality_notes.append(f"stale activity since {opp.last_activity_date.isoformat()}")
        if opp.previous_owner and opp.owner and opp.previous_owner != opp.owner:
            quality_notes.append(f"owner changed from {opp.previous_owner} to {opp.owner}")
        if opp.probability_override is not None or opp.probability_override_reason:
            quality_notes.append("probability override requires calibration")
        if opp.probability is not None and opp.weighted_arr is not None:
            expected_weighted = opp.arr * opp.probability
            if abs(opp.weighted_arr - expected_weighted) > max(5_000.0, opp.arr * 0.05):
                quality_notes.append(f"weighted ARR {opp.weighted_arr:,.0f} does not match probability-implied {expected_weighted:,.0f}")
        account_key = opp.account_id or opp.parent_account or opp.account
        if account_key and len(account_names.get(account_key, set())) > 1:
            quality_notes.append(f"duplicate account aliases: {', '.join(sorted(account_names[account_key]))}")
        if quality_notes:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.CRM_PROBABILITY_QUALITY, opp.opportunity_id),
                    kind=DiscrepancyKind.CRM_PROBABILITY_QUALITY,
                    severity=DiscrepancySeverity.MEDIUM if len(quality_notes) >= 3 or opp.arr >= 750_000 else DiscrepancySeverity.LOW,
                    title=f"CRM pipeline quality needs review: {opp.name}",
                    detail=f"Opportunity {opp.opportunity_id} has imperfect forecast inputs: {', '.join(quality_notes)}.",
                    sources=[SourceType.CRM_OPPORTUNITIES.value],
                    observed={
                        "opportunity_id": opp.opportunity_id,
                        "account": opp.account,
                        "account_id": opp.account_id,
                        "stage": opp.stage,
                        "opportunity_type": opp.opportunity_type,
                        "arr": opp.arr,
                        "probability": opp.probability,
                        "probability_override": opp.probability_override,
                        "system_probability": opp.system_probability,
                        "weighted_arr": opp.weighted_arr,
                        "close_date": opp.close_date.isoformat() if opp.close_date else None,
                        "prior_close_date": prior_close.isoformat() if prior_close else None,
                        "days_in_stage": opp.days_in_stage,
                        "last_activity_date": opp.last_activity_date.isoformat() if opp.last_activity_date else None,
                    },
                    recommended_action="FP&A should re-age the opportunity, reconcile overrides to stage history, and separate renewal protection from growth ARR before accepting the forecast.",
                    confidence=80,
                    references={"opportunity_id": opp.opportunity_id},
                )
            )
    if forecast_weighted > 0:
        variance = (crm_weighted - forecast_weighted) / forecast_weighted
        if abs(variance) > CRM_FORECAST_TOLERANCE:
            severity = DiscrepancySeverity.MEDIUM if abs(variance) > 0.20 else DiscrepancySeverity.LOW
            direction = "above" if variance > 0 else "below"
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.CRM_FORECAST_VARIANCE, "weighted"),
                    kind=DiscrepancyKind.CRM_FORECAST_VARIANCE,
                    severity=severity,
                    title=f"CRM weighted pipeline is {abs(variance):.0%} {direction} the forecast assumption",
                    detail=(
                        f"Live CRM weighted ARR {crm_weighted:,.0f} vs forecast assumption {forecast_weighted:,.0f} "
                        f"across {len(opportunities)} opportunities."
                    ),
                    sources=[SourceType.CRM_OPPORTUNITIES.value, "company.pipeline_by_stage"],
                    expected=round(forecast_weighted, 2),
                    observed=round(crm_weighted, 2),
                    delta=round(crm_weighted - forecast_weighted, 2),
                    recommended_action="Reconcile forecast assumptions with live pipeline; check stage conversion calibration.",
                    confidence=75,
                )
            )
    status = "discrepancies" if discrepancies else "ok"
    detail = (
        f"CRM weighted ARR {crm_weighted:,.0f} vs forecast {forecast_weighted:,.0f}; "
        f"quality issues: {quality.get('quality_issue_count', 0)}, "
        f"{quality.get('slipped_close_date_count', 0)} slipped, "
        f"{quality.get('stage_aging_count', 0)} aged, "
        f"{quality.get('stale_opportunity_count', 0)} stale."
    )
    return WorkflowSummary(workflow=workflow, status=status, checked=len(opportunities), discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# 4) Headcount → hiring plan
# --------------------------------------------------------------------------- #
def reconcile_headcount_to_plan(
    headcount: list[HeadcountPlanRow],
    company: dict[str, Any],
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "headcount_to_plan"
    if not headcount:
        return _missing(workflow, SourceType.HEADCOUNT_PLAN)
    plan = company.get("hiring_plan") or []
    if not plan:
        blocker = f"{workflow}: company.hiring_plan is missing from the system of record."
        return WorkflowSummary(workflow=workflow, status="insufficient_data", blockers=[blocker], detail=blocker), []

    as_of = _as_date(company.get("updated")) or date(2026, 6, 15)
    quality = summarize_headcount_quality([row.model_dump(mode="json") for row in headcount])
    plan_by_team = {
        _canonical_team(p.get("team")): {
            "label": p.get("team"),
            "roles": int(p.get("roles") or 0),
            "monthly_cost": float(p.get("monthly_cost") or 0),
            "start_month": p.get("start_month"),
            "dependency": p.get("dependency"),
        }
        for p in plan
    }

    def _row_team_key(row: HeadcountPlanRow) -> str:
        return _canonical_team(row.mapped_team or row.team)

    def _row_start(row: HeadcountPlanRow) -> Optional[date]:
        return row.actual_start_date or row.current_start_date or _as_date(row.start_month) or row.planned_start_date

    def _row_planned_start(row: HeadcountPlanRow, planned: Optional[dict[str, Any]]) -> Optional[date]:
        return row.planned_start_date or _as_date((planned or {}).get("start_month"))

    # Aggregate imported rows by team.
    actual: dict[str, dict[str, Any]] = {}
    for row in headcount:
        team_key = _row_team_key(row)
        bucket = actual.setdefault(
            team_key,
            {
                "headcount": 0.0,
                "monthly_cost": 0.0,
                "base_monthly_cost": 0.0,
                "label": row.mapped_team or row.team,
                "source_labels": set(),
                "drift": 0.0,
            },
        )
        bucket["headcount"] += row.headcount
        bucket["monthly_cost"] += row.loaded_monthly_cost()
        bucket["base_monthly_cost"] += row.monthly_cost or 0.0
        bucket["source_labels"].add(row.team)
        if row.mapped_team and _norm(row.team) != _norm(row.mapped_team):
            bucket["drift"] = 1.0
        elif _norm(row.team) != team_key:
            bucket["drift"] = 1.0

    discrepancies: list[Discrepancy] = []
    for row in headcount:
        team_key = _row_team_key(row)
        planned = plan_by_team.get(team_key)
        start = _row_start(row)
        planned_start = _row_planned_start(row, planned)
        loaded_cost = row.loaded_monthly_cost()
        approval_status = _norm(row.approval_status)
        approved_headcount = row.approved_headcount if row.approved_headcount is not None else (row.headcount if approval_status == "approved" else 0)
        role_label = row.role or row.role_id or row.team
        observed = {
            "role_id": row.role_id,
            "team": row.team,
            "mapped_team": row.mapped_team,
            "role": row.role,
            "headcount": row.headcount,
            "status": row.status,
            "employment_type": row.employment_type,
            "role_type": row.role_type,
            "monthly_cost": row.monthly_cost,
            "fully_loaded_monthly_cost": row.fully_loaded_monthly_cost,
            "cash_monthly_cost": loaded_cost,
            "planned_start_date": planned_start.isoformat() if planned_start else None,
            "current_or_actual_start_date": start.isoformat() if start else None,
            "recruiting_slippage_days": row.recruiting_slippage_days,
            "approval_status": row.approval_status,
            "approved_headcount": row.approved_headcount,
            "approval_id": row.approval_id,
            "backfill_for": row.backfill_for,
        }
        if planned is None:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.UNPLANNED_HEADCOUNT, row.role_id or row.record_key()),
                    kind=DiscrepancyKind.UNPLANNED_HEADCOUNT,
                    severity=DiscrepancySeverity.HIGH,
                    title=f"Unplanned headcount: {role_label}",
                    detail=(
                        f"{row.headcount} {row.employment_type} role(s) for '{row.team}' "
                        f"carry {loaded_cost:,.0f}/mo fully loaded cash impact but do not map to the funded hiring plan. "
                        f"Approval status is {row.approval_status}; start timing is {start.isoformat() if start else 'unknown'}."
                    ),
                    sources=[SourceType.HEADCOUNT_PLAN.value, "company.hiring_plan"],
                    observed=observed,
                    recommended_action="Freeze or route for approval unless the role maps to signed revenue, security compliance, or runway-positive automation.",
                    confidence=88,
                    references={"role_id": row.role_id or row.record_key()},
                )
            )
        if approval_status != "approved" or (approved_headcount < row.headcount):
            unapproved_roles = max(0, row.headcount - approved_headcount)
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.HEADCOUNT_DRIFT, row.role_id or row.record_key(), "approval"),
                    kind=DiscrepancyKind.HEADCOUNT_DRIFT,
                    severity=DiscrepancySeverity.HIGH if approval_status in {"unapproved", "pending"} else DiscrepancySeverity.MEDIUM,
                    title=f"Headcount approval gap: {role_label}",
                    detail=(
                        f"{approved_headcount} of {row.headcount} role(s) are approved; {unapproved_roles} remain unresolved. "
                        f"Loaded monthly cash exposure is {loaded_cost:,.0f}/mo and approval id is {row.approval_id or 'missing'}."
                    ),
                    sources=[SourceType.HEADCOUNT_PLAN.value, "company.hiring_plan"],
                    expected="approved roles with approval_id before hiring commitment",
                    observed=observed,
                    recommended_action="Risk should require approval provenance and FP&A/Treasury should exclude unresolved roles from base-case hiring cash.",
                    confidence=86,
                    references={"role_id": row.role_id or row.record_key()},
                )
            )
        slip_days = row.recruiting_slippage_days or 0
        if slip_days >= 30:
            timing_delta = None
            if planned_start and start:
                timing_delta = (start - planned_start).days
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.HEADCOUNT_DRIFT, row.role_id or row.record_key(), "start"),
                    kind=DiscrepancyKind.HEADCOUNT_DRIFT,
                    severity=DiscrepancySeverity.MEDIUM,
                    title=f"Hiring start slipped: {role_label}",
                    detail=(
                        f"Recruiting slippage is {slip_days} day(s); planned start {planned_start.isoformat() if planned_start else 'unknown'} "
                        f"vs current/actual start {start.isoformat() if start else 'unknown'}. "
                        "FP&A should update plan-vs-actual capacity timing and Treasury should move the cash impact."
                    ),
                    sources=[SourceType.HEADCOUNT_PLAN.value, "company.hiring_plan"],
                    expected=planned_start.isoformat() if planned_start else None,
                    observed=observed,
                    delta=timing_delta,
                    recommended_action="Reforecast role start timing before relying on capacity, ARR support, or runway impact.",
                    confidence=82,
                    references={"role_id": row.role_id or row.record_key()},
                )
            )
        if start and start <= as_of and row.status in {"planned", "open"}:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.HEADCOUNT_DRIFT, row.role_id or row.record_key(), "past-start"),
                    kind=DiscrepancyKind.HEADCOUNT_DRIFT,
                    severity=DiscrepancySeverity.MEDIUM,
                    title=f"Headcount start date is stale: {role_label}",
                    detail=(
                        f"Role status is {row.status}, but the current start date {start.isoformat()} is on or before the as-of date "
                        f"{as_of.isoformat()}. Capacity and cash timing need HRIS confirmation."
                    ),
                    sources=[SourceType.HEADCOUNT_PLAN.value],
                    observed=observed,
                    recommended_action="Confirm whether the role is still open, filled, or canceled before the council uses it.",
                    confidence=76,
                    references={"role_id": row.role_id or row.record_key()},
                )
            )

    for team_key, agg in actual.items():
        planned = plan_by_team.get(team_key)
        if planned is None:
            continue
        if agg.get("drift"):
            source_labels = ", ".join(sorted(str(label) for label in agg.get("source_labels", set())))
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.DEPARTMENT_NAME_DRIFT, team_key, str(agg["label"])),
                    kind=DiscrepancyKind.DEPARTMENT_NAME_DRIFT,
                    severity=DiscrepancySeverity.LOW,
                    title=f"Department naming drift: {agg['label']}",
                    detail=f"Imported team label(s) '{source_labels}' were reconciled to planned team '{planned.get('label') or team_key}'.",
                    sources=[SourceType.HEADCOUNT_PLAN.value, "company.hiring_plan"],
                    expected=planned.get("label") or team_key,
                    observed={"mapped_team": agg["label"], "source_labels": sorted(str(label) for label in agg.get("source_labels", set()))},
                    recommended_action="Normalize the HRIS/planning department name so headcount joins stay auditable.",
                    confidence=74,
                    references={"team": agg["label"]},
                )
            )
        if agg["headcount"] > planned["roles"]:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.HEADCOUNT_DRIFT, team_key, "count"),
                    kind=DiscrepancyKind.HEADCOUNT_DRIFT,
                    severity=DiscrepancySeverity.MEDIUM,
                    title=f"{agg['label']} headcount exceeds plan",
                    detail=f"{int(agg['headcount'])} role(s) vs planned {planned['roles']} after mapping aliases and backfills.",
                    sources=[SourceType.HEADCOUNT_PLAN.value, "company.hiring_plan"],
                    expected=planned["roles"],
                    observed=int(agg["headcount"]),
                    delta=agg["headcount"] - planned["roles"],
                    recommended_action="Confirm incremental roles are funded, approved, and inside the plan-vs-actual hiring gate.",
                    confidence=85,
                )
            )
        if planned["monthly_cost"] > 0 and agg["monthly_cost"] > planned["monthly_cost"] * (1 + HEADCOUNT_COST_TOLERANCE):
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.HEADCOUNT_DRIFT, team_key, "cost"),
                    kind=DiscrepancyKind.HEADCOUNT_DRIFT,
                    severity=DiscrepancySeverity.MEDIUM,
                    title=f"{agg['label']} monthly cost exceeds plan",
                    detail=f"{agg['monthly_cost']:,.0f}/mo fully loaded vs planned {planned['monthly_cost']:,.0f}/mo.",
                    sources=[SourceType.HEADCOUNT_PLAN.value, "company.hiring_plan"],
                    expected=planned["monthly_cost"],
                    observed=agg["monthly_cost"],
                    delta=round(agg["monthly_cost"] - planned["monthly_cost"], 2),
                    recommended_action="Reconcile compensation assumptions with the funded plan and net-burn guardrail.",
                    confidence=80,
                )
            )
    status = "discrepancies" if discrepancies else "ok"
    detail = (
        f"Compared {len(headcount)} role row(s) / {len(actual)} team(s) to the funded plan; "
        f"headcount quality issues: {quality.get('issue_count', 0)}, "
        f"{quality.get('recruiting_slip_count', 0)} slipped, "
        f"{quality.get('partial_approval_count', 0)} partial approvals, "
        f"{quality.get('unapproved_count', 0)} unapproved/pending, "
        f"{quality.get('next_90_day_loaded_cost', 0):,.0f}/mo loaded cost starts within 90 days."
    )
    return WorkflowSummary(workflow=workflow, status=status, checked=len(headcount), discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# Policy threshold resolution (board_policy connector → defaults)
# --------------------------------------------------------------------------- #
def _resolve_threshold(board_policies: list[BoardPolicyDoc], rule: str, default: float) -> float:
    for doc in board_policies:
        if doc.rule == rule and doc.threshold is not None:
            return float(doc.threshold)
    return default


# --------------------------------------------------------------------------- #
# 5) Policy & board constraints (vendor commitment notification, renewal review)
# --------------------------------------------------------------------------- #
def reconcile_policy_and_board(
    contracts: dict[str, dict[str, Any]],
    board_policies: list[BoardPolicyDoc],
    as_of: date,
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "policy_and_board_constraints"
    notify_threshold = _resolve_threshold(board_policies, "vendor_commitment_board_notification", DEFAULT_VENDOR_BOARD_NOTIFICATION)
    review_value = _resolve_threshold(board_policies, "vendor_competitive_review_value", DEFAULT_VENDOR_REVIEW_VALUE)
    review_days = int(_resolve_threshold(board_policies, "vendor_competitive_review_days", DEFAULT_VENDOR_REVIEW_DAYS))

    discrepancies: list[Discrepancy] = []
    checked = 0
    for vid, c in contracts.items():
        checked += 1
        annual_cost = float(c.get("annual_cost") or 0)
        # Board notification: only flag *new* commitments (from the live vendor export)
        # that breach the threshold without recorded board approval — seeded vendors are
        # existing, board-known commitments and are not re-flagged.
        if c.get("source") == "vendor_export" and c.get("is_new") and annual_cost >= notify_threshold and c.get("board_approved") is not True:
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.BOARD_CONSTRAINT_VIOLATION, vid, "notify"),
                    kind=DiscrepancyKind.BOARD_CONSTRAINT_VIOLATION,
                    severity=DiscrepancySeverity.HIGH,
                    title=f"New {c.get('name', vid)} commitment needs board notification",
                    detail=(
                        f"New vendor commitment of {annual_cost:,.0f}/yr is at/above the "
                        f"{notify_threshold:,.0f} board-notification threshold and is not marked board-approved."
                    ),
                    sources=[SourceType.VENDOR_EXPORT.value, SourceType.BOARD_POLICY.value],
                    expected=f"<= {notify_threshold:,.0f}/yr or board-approved",
                    observed=annual_cost,
                    delta=round(annual_cost - notify_threshold, 2),
                    recommended_action="Notify the board before signing; record approval in the vendor record.",
                    confidence=90,
                    references={"vendor_id": vid},
                )
            )
        if c.get("source") == "vendor_export" and annual_cost >= notify_threshold and (
            c.get("board_approved") is not True or not c.get("board_approval_id")
        ):
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.MISSING_BOARD_APPROVAL, vid, "approval-id"),
                    kind=DiscrepancyKind.MISSING_BOARD_APPROVAL,
                    severity=DiscrepancySeverity.HIGH,
                    title=f"{c.get('name', vid)} lacks board approval evidence",
                    detail=(
                        f"Contract value {annual_cost:,.0f}/yr is at/above the {notify_threshold:,.0f} "
                        "board-notification threshold, but the vendor export has no board approval id."
                    ),
                    sources=[SourceType.VENDOR_EXPORT.value, SourceType.BOARD_POLICY.value],
                    expected="board_approved=true with board_approval_id",
                    observed={"board_approved": c.get("board_approved"), "board_approval_id": c.get("board_approval_id")},
                    recommended_action="Attach the board packet or block signature until approval provenance exists.",
                    confidence=88,
                    references={"vendor_id": vid},
                )
            )

        if annual_cost >= review_value:
            clause_gaps: list[str] = []
            if c.get("data_processing_addendum") is not True:
                clause_gaps.append("DPA not signed or missing")
            if not c.get("security_clause"):
                clause_gaps.append("security clause missing")
            if not c.get("sla_uptime_pct"):
                clause_gaps.append("SLA uptime not documented")
            if clause_gaps:
                discrepancies.append(
                    Discrepancy(
                        id=_disc_id(DiscrepancyKind.SLA_SECURITY_CLAUSE_GAP, vid),
                        kind=DiscrepancyKind.SLA_SECURITY_CLAUSE_GAP,
                        severity=DiscrepancySeverity.HIGH if c.get("data_processing_addendum") is not True else DiscrepancySeverity.MEDIUM,
                        title=f"{c.get('name', vid)} has SLA/security clause gaps",
                        detail=(
                            f"High-value contract has unresolved clause evidence: {', '.join(clause_gaps)}. "
                            f"Security clause: {c.get('security_clause') or 'missing'}."
                        ),
                        sources=[SourceType.VENDOR_EXPORT.value, SourceType.BOARD_POLICY.value],
                        expected="DPA, SLA, and security clauses documented",
                        observed={"gaps": clause_gaps, "sla_uptime_pct": c.get("sla_uptime_pct")},
                        recommended_action="Require DPA/security review and SLA remedies before approval or renewal.",
                        confidence=82,
                        references={"vendor_id": vid},
                    )
                )

            owner_history = c.get("owner_history") or []
            notes = _norm(c.get("notes"))
            if len(owner_history) > 1 and "attestation" not in notes:
                discrepancies.append(
                    Discrepancy(
                        id=_disc_id(DiscrepancyKind.OWNER_ATTESTATION_GAP, vid),
                        kind=DiscrepancyKind.OWNER_ATTESTATION_GAP,
                        severity=DiscrepancySeverity.MEDIUM,
                        title=f"{c.get('name', vid)} owner change lacks attestation",
                        detail=(
                            f"Contract has {len(owner_history)} owner-history entries but no owner attestation in notes; "
                            "risk cannot verify who accepts renewal, SLA, and termination obligations."
                        ),
                        sources=[SourceType.VENDOR_EXPORT.value],
                        expected="current owner attestation after owner change",
                        observed={"owner": c.get("owner"), "owner_history": owner_history},
                        recommended_action="Record current owner attestation before renewal or signature.",
                        confidence=78,
                        references={"vendor_id": vid},
                    )
                )

        # Competitive review window: high-value contracts within N days of renewal.
        renewal = c.get("renewal_date")
        renewal = renewal if isinstance(renewal, date) else _as_date(renewal)
        if annual_cost > review_value and renewal is not None:
            days = (renewal - as_of).days
            notice_days = _notice_days(c, review_days)
            notice_deadline = renewal - timedelta(days=notice_days)
            days_to_notice = (notice_deadline - as_of).days
            if _as_bool(c.get("auto_renew")) is True and days >= 0 and days_to_notice <= 14:
                missed = days_to_notice < 0
                discrepancies.append(
                    Discrepancy(
                        id=_disc_id(DiscrepancyKind.RENEWAL_URGENCY, vid, "auto-renew-notice"),
                        kind=DiscrepancyKind.RENEWAL_URGENCY,
                        severity=DiscrepancySeverity.CRITICAL if missed else DiscrepancySeverity.HIGH,
                        title=(
                            f"{c.get('name', vid)} auto-renew notice deadline "
                            f"{'has passed' if missed else f'is in {days_to_notice} day(s)'}"
                        ),
                        detail=(
                            f"Auto-renew contract renews on {renewal.isoformat()} with a {notice_days}-day notice window; "
                            f"notice deadline is {notice_deadline.isoformat()}. Termination penalty is "
                            f"{float(c.get('termination_penalty') or 0):,.0f}."
                        ),
                        sources=[SourceType.VENDOR_EXPORT.value, SourceType.BOARD_POLICY.value],
                        expected=f"notice before {notice_deadline.isoformat()}",
                        observed=f"{days_to_notice} day(s) to notice deadline",
                        recommended_action="Escalate renewal decision now; negotiate, give notice, or record why auto-renew is acceptable.",
                        confidence=90,
                        references={"vendor_id": vid, "renewal_date": renewal.isoformat(), "notice_deadline": notice_deadline.isoformat()},
                    )
                )
            if 0 <= days <= review_days:
                discrepancies.append(
                    Discrepancy(
                        id=_disc_id(DiscrepancyKind.POLICY_VIOLATION, vid, "review"),
                        kind=DiscrepancyKind.POLICY_VIOLATION,
                        severity=DiscrepancySeverity.MEDIUM,
                        title=f"{c.get('name', vid)} renewal needs competitive review",
                        detail=(
                            f"Contract at {annual_cost:,.0f}/yr renews in {days} day(s) ({renewal.isoformat()}); "
                            f"policy requires competitive review at least {review_days} days before renewal."
                        ),
                        sources=[SourceType.VENDOR_EXPORT.value, SourceType.BOARD_POLICY.value],
                        expected=f"review >= {review_days} days before renewal",
                        observed=f"{days} days to renewal",
                        recommended_action="Open a competitive review / renegotiation before the renewal date.",
                        confidence=80,
                        references={"vendor_id": vid, "renewal_date": renewal.isoformat()},
                    )
                )
    status = "discrepancies" if discrepancies else "ok"
    detail = f"Checked {checked} contract(s) against board approvals, renewal notices, and SLA/security clauses."
    return WorkflowSummary(workflow=workflow, status=status, checked=checked, discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# 6) Security → revenue priority
# --------------------------------------------------------------------------- #
def reconcile_security_revenue(
    evidence: list[SecurityEvidence],
    as_of: date,
) -> tuple[WorkflowSummary, list[Discrepancy]]:
    workflow = "security_revenue_priority"
    if not evidence:
        return _missing(workflow, SourceType.SECURITY_EVIDENCE)
    open_states = {"gap", "not_started", "in_progress"}
    discrepancies: list[Discrepancy] = []
    for ev in evidence:
        if ev.evidence_date is None or (as_of - ev.evidence_date).days > SECURITY_EVIDENCE_STALE_DAYS:
            age = None if ev.evidence_date is None else (as_of - ev.evidence_date).days
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.STALE_SECURITY_EVIDENCE, ev.control_id),
                    kind=DiscrepancyKind.STALE_SECURITY_EVIDENCE,
                    severity=DiscrepancySeverity.MEDIUM if ev.blocks_revenue else DiscrepancySeverity.LOW,
                    title=f"Security evidence is stale: {ev.control_id}",
                    detail=(
                        f"{ev.framework} control '{ev.title}' has "
                        f"{'no evidence date' if age is None else f'evidence {age} days old'}."
                    ),
                    sources=[SourceType.SECURITY_EVIDENCE.value],
                    observed={
                        "control_id": ev.control_id,
                        "evidence_date": ev.evidence_date.isoformat() if ev.evidence_date else None,
                        "blocks_revenue": ev.blocks_revenue,
                    },
                    recommended_action="Refresh source provenance and control evidence before clearing policy blockers.",
                    confidence=82,
                    references={"control_id": ev.control_id},
                )
            )
        if ev.blocks_revenue and ev.status.lower() in open_states:
            blocked = ev.blocked_arr or 0
            severity = DiscrepancySeverity.CRITICAL if blocked >= 250_000 else DiscrepancySeverity.HIGH
            discrepancies.append(
                Discrepancy(
                    id=_disc_id(DiscrepancyKind.SECURITY_REVENUE_BLOCKER, ev.control_id),
                    kind=DiscrepancyKind.SECURITY_REVENUE_BLOCKER,
                    severity=severity,
                    title=f"Security control {ev.control_id} is blocking revenue",
                    detail=(
                        f"{ev.framework} control '{ev.title}' is '{ev.status}' and blocks "
                        f"{blocked:,.0f} of revenue. Policy prioritises unblocking controls when runway is tight."
                    ),
                    sources=[SourceType.SECURITY_EVIDENCE.value, "company.board_constraints"],
                    observed={"control_id": ev.control_id, "status": ev.status, "blocked_arr": blocked},
                    recommended_action="Prioritise remediation evidence to unblock signed/late-stage enterprise revenue.",
                    confidence=85,
                    references={"control_id": ev.control_id},
                )
            )
    status = "discrepancies" if discrepancies else "ok"
    detail = f"Checked {len(evidence)} control(s) for revenue-blocking gaps."
    return WorkflowSummary(workflow=workflow, status=status, checked=len(evidence), discrepancy_count=len(discrepancies), detail=detail), discrepancies


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_workflows(
    *,
    invoices: list[Invoice],
    ledger: list[LedgerEntry],
    vendor_export: list[VendorRecord],
    opportunities: list[CrmOpportunity],
    headcount: list[HeadcountPlanRow],
    security: list[SecurityEvidence],
    board_policies: list[BoardPolicyDoc],
    company: dict[str, Any],
    vendors_seed: list[dict[str, Any]],
    as_of: Optional[date] = None,
) -> tuple[list[WorkflowSummary], list[Discrepancy]]:
    """Run every workflow, returning per-workflow summaries and all discrepancies."""
    as_of = as_of or _as_date(company.get("updated")) or datetime.now(timezone.utc).date()
    contracts = _build_contracts(vendors_seed, vendor_export)

    summaries: list[WorkflowSummary] = []
    discrepancies: list[Discrepancy] = []
    for summary, discs in (
        reconcile_invoices_to_vendors(invoices, contracts, as_of),
        reconcile_contract_terms_to_spend(invoices, contracts),
        reconcile_ledger_quality(ledger, contracts),
        reconcile_crm_to_forecast(opportunities, company),
        reconcile_headcount_to_plan(headcount, company),
        reconcile_policy_and_board(contracts, board_policies, as_of),
        reconcile_security_revenue(security, as_of),
    ):
        summaries.append(summary)
        discrepancies.extend(discs)
    return summaries, discrepancies
