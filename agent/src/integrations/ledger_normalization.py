"""Conservative normalization for bank-style ledger exports.

The helpers in this module annotate ledger rows; they do not rewrite source
facts. Source fields such as description/vendor/category remain intact, while
``normalized_*`` and ``inferred_*`` fields explain the best-effort read of noisy
bank/card descriptors.
"""

from __future__ import annotations

import re
from typing import Any, Optional


_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "txn_id": ("txn_id", "transaction_id", "transaction id", "id", "reference", "ref_number", "bank_reference"),
    "date": ("date", "posted_date", "posting_date", "transaction_date", "settled_date"),
    "account": ("account", "account_name", "bank_account", "ledger_account"),
    "description": ("description", "bank_description", "statement_description", "memo", "name"),
    "amount": ("amount", "signed_amount", "net_amount"),
    "category": ("category", "source_category", "bank_category", "gl_category"),
    "vendor_id": ("vendor_id", "vendor id", "supplier_id"),
    "vendor_name": ("vendor_name", "vendor", "payee", "merchant_name", "counterparty"),
}

_CATEGORY_ALIASES = {
    "": "uncategorized",
    "not categorized": "uncategorized",
    "uncategorised": "uncategorized",
    "software_credit": "refund",
    "cash receipt": "revenue",
    "bank fee": "bank_fees",
    "wire fee": "bank_fees",
    "intercompany": "intercompany_transfer",
}

_KNOWN_VENDOR_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    (r"\b(DATA ?DOG|DATADOG|DDOG)\b", "datadog", "Datadog", "software"),
    (r"\b(AWS|AMAZON WEB S(?:ERVICE)?S?|AMZN AWS)\b", "aws", "Amazon Web Services", "infrastructure"),
    (r"\bSNOWFLAKE\b", "snowflake", "Snowflake", "data"),
    (r"\b(SALES\s*FORCE|SALESFORCE|SFDC|SFCI)\b", "salesforce", "Salesforce", "crm"),
    (r"\bGONG(?:\.IO)?\b", "gong", "Gong", "sales"),
    (r"\bGITHUB\b", "github", "GitHub Enterprise", "engineering"),
    (r"\bFIGMA|FIGJAM\b", "figma", "Figma", "design"),
    (r"\bRIPPLING|RPLNG\b", "rippling", "Rippling", "payroll"),
)

_NOISY_DESCRIPTOR_TOKENS = {
    "card",
    "visa",
    "debit",
    "ach",
    "pos",
    "sq",
    "tst",
    "payment",
    "pmt",
    "co",
    "id",
    "ref",
    "online",
    "remote",
    "deposit",
    "purchase",
    "recurring",
    "withdrawal",
    "credit",
    "debit",
}


def _first(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
        value = lowered.get(alias.lower())
        if value not in (None, ""):
            return value
    return None


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _as_money(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()").replace("$", "").replace(",", "").replace("USD", "").strip()
    if cleaned == "":
        return None
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return -amount if negative else amount


def _amount_from_debit_credit(row: dict[str, Any]) -> Optional[float]:
    amount = _as_money(_first(row, _FIELD_ALIASES["amount"]))
    if amount is not None:
        return amount
    debit = _as_money(_first(row, ("debit", "withdrawal", "outflow", "charge")))
    credit = _as_money(_first(row, ("credit", "deposit", "inflow")))
    if debit not in (None, 0):
        return -abs(float(debit))
    if credit not in (None, 0):
        return abs(float(credit))
    return None


def _card_last4(text: str, row: dict[str, Any]) -> Optional[str]:
    explicit = _first(row, ("card_last4", "card last4", "card_last_four"))
    if explicit not in (None, ""):
        digits = re.sub(r"\D+", "", str(explicit))
        return digits[-4:] if digits else None
    match = re.search(r"\b(?:CARD|VISA|MC|MASTERCARD|AMEX)[^\d]{0,8}(\d{4})\b", text, flags=re.I)
    return match.group(1) if match else None


def _transaction_type(text: str, amount: Optional[float], row: dict[str, Any]) -> str:
    raw = _first(row, ("transaction_type", "type", "txn_type"))
    if raw not in (None, ""):
        return str(raw).strip().lower().replace(" ", "_")
    upper = text.upper()
    if "INTERCOMPANY" in upper or "TRANSFER" in upper:
        return "transfer"
    if "PAYROLL" in upper:
        return "payroll"
    if "FEE" in upper:
        return "fee"
    if "REFUND" in upper or "REVERSAL" in upper or (amount or 0) > 0 and "CREDIT" in upper:
        return "refund"
    if "CARD" in upper or "VISA" in upper or "POS" in upper:
        return "card_charge"
    if "ACH" in upper:
        return "ach"
    if (amount or 0) > 0:
        return "cash_in"
    return "cash_out"


def _payment_channel(text: str, tx_type: str) -> Optional[str]:
    upper = text.upper()
    if tx_type == "card_charge" or "CARD" in upper or "VISA" in upper:
        return "card"
    if "ACH" in upper:
        return "ach"
    if "WIRE" in upper:
        return "wire"
    if "JOURNAL" in upper or "ACCRUAL" in upper:
        return "journal"
    if "TRANSFER" in upper:
        return "transfer"
    return None


def _normalize_vendor(value: Any) -> Optional[str]:
    text = _clean_text(value)
    if not text:
        return None
    cleaned = re.sub(r"[*#:\d]+", " ", text)
    cleaned = re.sub(r"\b(?:INC|LLC|LTD|CORP|CO)\b\.?", "", cleaned, flags=re.I)
    words = [
        word
        for word in re.sub(r"[^A-Za-z0-9]+", " ", cleaned).split()
        if word.lower() not in _NOISY_DESCRIPTOR_TOKENS and not re.fullmatch(r"\d{2,}", word)
    ]
    if not words:
        return None
    return " ".join(words[:5]).strip().title()


def _infer_known_vendor(text: str, raw_vendor: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    haystack = f"{raw_vendor or ''} {text}".upper()
    for pattern, vendor_id, vendor_name, category in _KNOWN_VENDOR_PATTERNS:
        if re.search(pattern, haystack, flags=re.I):
            return vendor_id, vendor_name, category, pattern
    return None, None, None, None


def _infer_category(text: str, amount: Optional[float], raw_category: Optional[str], vendor_category: Optional[str], tx_type: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    raw_key = _norm_key(raw_category)
    if raw_key:
        category = _CATEGORY_ALIASES.get(raw_key, raw_key.replace(" ", "_"))
        notes.append("category normalized from source category")
        return category, notes
    if vendor_category:
        notes.append("category inferred from recognized vendor descriptor")
        return vendor_category, notes
    upper = text.upper()
    if tx_type == "payroll" or "PAYROLL" in upper:
        return "payroll", ["category inferred from payroll descriptor"]
    if tx_type == "fee" or "FEE" in upper:
        return "bank_fees", ["category inferred from bank/card fee descriptor"]
    if tx_type == "transfer":
        return "intercompany_transfer", ["category inferred from transfer descriptor"]
    if "ACCRUAL" in upper:
        return "accrual", ["category inferred from accrual descriptor"]
    if "PARTS" in upper or "HARDWARE" in upper:
        return "hardware", ["category inferred from hardware descriptor"]
    if tx_type == "refund" and (amount or 0) > 0:
        return "refund", ["category inferred from refund/reversal descriptor"]
    if (amount or 0) > 0:
        return "revenue", ["category inferred from positive cash receipt"]
    return "uncategorized", ["category unresolved; left as uncategorized"]


def _confidence(*, has_source_vendor: bool, inferred_vendor_id: Optional[str], category: str, tx_type: str) -> int:
    score = 35
    if has_source_vendor:
        score += 30
    if inferred_vendor_id:
        score += 30
    if category and category != "uncategorized":
        score += 20
    if tx_type in {"fee", "transfer", "payroll", "refund"}:
        score += 10
    return max(0, min(95, score))


def prepare_ledger_row(raw: Any) -> Any:
    """Return a row with source aliases and normalized ledger annotations filled."""
    if not isinstance(raw, dict):
        return raw
    row = dict(raw)
    amount = _amount_from_debit_credit(row)
    for target, aliases in _FIELD_ALIASES.items():
        if row.get(target) in (None, ""):
            value = amount if target == "amount" else _first(row, aliases)
            if value not in (None, ""):
                row[target] = value
    if not row.get("account"):
        row["account"] = "unclassified"
    if not row.get("description"):
        row["description"] = "No description supplied"
    if amount is not None:
        row["amount"] = amount

    description = _clean_text(row.get("description"))
    bank_description = _clean_text(_first(row, ("bank_description", "statement_description", "memo", "description")))
    raw_vendor = _clean_text(_first(row, ("vendor_name", "vendor", "merchant_name", "payee", "counterparty"))) or None
    raw_category = _clean_text(_first(row, ("category", "source_category", "bank_category", "gl_category"))) or None
    text = " ".join(part for part in [description, bank_description, raw_vendor] if part)
    tx_type = _transaction_type(text, amount, row)
    inferred_vendor_id, inferred_vendor_name, vendor_category, pattern = _infer_known_vendor(text, raw_vendor)
    normalized_vendor_name = _normalize_vendor(raw_vendor or inferred_vendor_name)
    normalized_category, category_notes = _infer_category(text, amount, raw_category, vendor_category, tx_type)

    notes: list[str] = []
    if bank_description:
        notes.append("raw bank/card descriptor preserved")
    if pattern:
        notes.append(f"known vendor pattern matched: {inferred_vendor_id}")
    if normalized_vendor_name and not row.get("vendor_name"):
        notes.append("normalized likely vendor from merchant/counterparty text")
    notes.extend(category_notes)
    if tx_type in {"fee", "refund", "transfer", "payroll"}:
        notes.append(f"transaction type inferred as {tx_type}")
    if row.get("split_group_id") or row.get("split_parent_id"):
        notes.append("split transaction metadata preserved")

    row.setdefault("raw_description", description)
    row.setdefault("bank_description", bank_description or None)
    row.setdefault("raw_vendor_name", raw_vendor)
    row.setdefault("raw_category", raw_category)
    row.setdefault("normalized_description", re.sub(r"\s+", " ", re.sub(r"\b\d{4,}\b", " ", text)).strip())
    row.setdefault("normalized_vendor_name", normalized_vendor_name)
    row.setdefault("normalized_category", normalized_category)
    row.setdefault("inferred_vendor_id", inferred_vendor_id)
    row.setdefault("inferred_vendor_name", inferred_vendor_name or normalized_vendor_name)
    row.setdefault("inferred_category", normalized_category)
    row.setdefault("transaction_type", tx_type)
    row.setdefault("payment_channel", _payment_channel(text, tx_type))
    row.setdefault("merchant_descriptor", _clean_text(_first(row, ("merchant_descriptor", "merchant_name", "payee", "counterparty"))) or raw_vendor)
    row.setdefault("card_last4", _card_last4(text, row))
    row.setdefault(
        "normalization_confidence",
        _confidence(
            has_source_vendor=bool(raw_vendor),
            inferred_vendor_id=inferred_vendor_id,
            category=normalized_category,
            tx_type=tx_type,
        ),
    )
    row.setdefault("normalization_notes", notes)
    return row


def summarize_ledger_normalization(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact provenance summary for imported ledger normalization."""
    total = len(records)
    inferred_vendor = sum(1 for row in records if row.get("inferred_vendor_id") and not row.get("vendor_id"))
    unknown_vendor = sum(
        1
        for row in records
        if (row.get("amount") or 0) < 0 and not (row.get("vendor_id") or row.get("inferred_vendor_id")) and row.get("transaction_type") not in {"payroll", "transfer", "fee"}
    )
    uncategorized = sum(1 for row in records if row.get("normalized_category") in (None, "", "uncategorized"))
    return {
        "records": total,
        "inferred_vendor_count": inferred_vendor,
        "unknown_vendor_count": unknown_vendor,
        "uncategorized_count": uncategorized,
        "refund_count": sum(1 for row in records if row.get("transaction_type") == "refund"),
        "fee_count": sum(1 for row in records if row.get("transaction_type") == "fee"),
        "payroll_count": sum(1 for row in records if row.get("transaction_type") == "payroll"),
        "transfer_count": sum(1 for row in records if row.get("transaction_type") == "transfer"),
        "split_count": sum(
            1
            for row in records
            if (row.get("split_group_id") or row.get("split_parent_id")) and row.get("transaction_type") != "transfer"
        ),
        "raw_fields_persisted": [
            "raw_description",
            "raw_vendor_name",
            "raw_category",
            "bank_description",
        ],
        "normalized_fields_persisted": [
            "normalized_description",
            "normalized_vendor_name",
            "normalized_category",
            "inferred_vendor_id",
            "inferred_category",
            "transaction_type",
            "normalization_confidence",
        ],
    }
