"""
Typed contracts for Atlas finance-operations connectors.

These Pydantic models define the *shape* of every external operations feed Atlas
can ingest — finance ledgers, invoices, vendor/procurement exports, CRM
opportunity exports, headcount plans, security evidence, and board policy docs —
plus the provenance, import-result, confidence, and reconciliation envelopes that
are persisted to Redis.

Strict live-only contract: nothing here fabricates records. A connector with no
configured source reports ``ImportStatus.NOT_CONFIGURED`` with explicit blockers;
parsers reject malformed rows with field-level validation errors instead of
guessing values. Money/date fields are coerced through small, well-defined
parsers (not broad ad hoc string parsing).
"""

from __future__ import annotations

import enum
import json
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.integrations.ledger_normalization import prepare_ledger_row

# Bump when the persisted record/provenance/reconciliation shapes change so that
# stale Redis documents can be detected and re-imported deliberately.
SCHEMA_VERSION = "1.7"


# --------------------------------------------------------------------------- #
# Enumerations — the connector taxonomy
# --------------------------------------------------------------------------- #
class SourceType(str, enum.Enum):
    LEDGER = "ledger"
    INVOICES = "invoices"
    VENDOR_EXPORT = "vendor_export"
    CRM_OPPORTUNITIES = "crm_opportunities"
    HEADCOUNT_PLAN = "headcount_plan"
    SECURITY_EVIDENCE = "security_evidence"
    BOARD_POLICY = "board_policy"


class SourceFormat(str, enum.Enum):
    CSV = "csv"
    JSON = "json"
    XLSX = "xlsx"
    XLS = "xls"


class ImportStatus(str, enum.Enum):
    NOT_CONFIGURED = "not_configured"   # no env var / no source path set
    MISSING_FILE = "missing_file"       # configured but the file is absent
    IMPORTED = "imported"               # all rows validated and persisted
    PARTIAL = "partial"                 # persisted, but some rows were rejected
    EMPTY = "empty"                     # file parsed but contained zero records
    SKIPPED_UNCHANGED = "skipped_unchanged"  # idempotent: checksum matched prior import
    ERROR = "error"                     # parse/IO failure (no data persisted)


class Origin(str, enum.Enum):
    EXTERNAL_FILE = "external-file"          # user-provided live operations export
    ACME_DEMO_FIXTURE = "acme-demo-fixture"  # bundled, opt-in demo operating data
    LIVE_API = "live-api"                    # reserved; never claimed unless verified


# --------------------------------------------------------------------------- #
# Field coercion helpers (focused parsers, not broad string munging)
# --------------------------------------------------------------------------- #
def coerce_money(value: Any) -> Optional[float]:
    """Parse a monetary cell into a float.

    Accepts plain numbers and common accounting text like ``"$28,000.00"`` or
    ``"(1,200)"`` (parenthesised negative). Empty cells become ``None``; anything
    that is not a number raises a clear ``ValueError`` so the row is rejected
    rather than silently mis-imported.
    """
    if value is None or isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if text == "":
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()").replace(",", "").replace("$", "").replace("USD", "").strip()
    if cleaned == "":
        return None
    try:
        amount = float(cleaned)
    except ValueError as exc:  # noqa: TRY003 - explicit, row-level error message
        raise ValueError(f"expected a monetary amount, got {value!r}") from exc
    return -amount if negative else amount


def coerce_date(value: Any) -> Optional[date]:
    """Parse common finance-export date formats into ``date``.

    Real exports rarely agree on one date shape: CSVs from AP, CRM, HRIS, and
    compliance tools tend to mix ISO, US-style, slash-delimited, and month-name
    formats. We accept a short explicit vocabulary and still reject values that
    are genuinely ambiguous or malformed.
    """
    if value is None:
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if text == "":
        return None
    normalized = text.replace(".", "/")
    patterns = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%b %d %Y",
        "%B %d %Y",
        "%Y-%m",
    )
    for pattern in patterns:
        try:
            parsed = datetime.strptime(normalized, pattern)
            return parsed.date().replace(day=1) if pattern == "%Y-%m" else parsed.date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"expected a supported date, got {value!r}") from exc


def _blank_to_none(value: Any) -> Any:
    """CSV columns arrive as ``""`` for empties; treat those as missing."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def coerce_percent(value: Any) -> Optional[float]:
    """Parse percent cells while preserving human-scale values like 99.9."""
    if value is None or isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if text == "":
        return None
    cleaned = text.replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(f"expected a percentage, got {value!r}") from exc


def coerce_string_list(value: Any) -> list[str]:
    """Accept JSON arrays or common delimiter-separated export cells."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError("expected a JSON array")
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in text.replace("|", ";").split(";") if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def coerce_dict_list(value: Any) -> list[dict[str, Any]]:
    """Accept list-of-dicts or a JSON-encoded list-of-dicts from exports."""
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("expected a JSON array")
        return [item for item in parsed if isinstance(item, dict)]
    raise ValueError("expected a list of objects")


class _SourceRecord(BaseModel):
    """Base for ingested records: ignore unknown columns, normalise blanks."""

    model_config = ConfigDict(extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _strip_blanks(cls, value: Any) -> Any:
        return _blank_to_none(value)

    def record_key(self) -> str:
        """Stable id used for de-duplication / drilldown within a dataset."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Ingested record models
# --------------------------------------------------------------------------- #
class LedgerEntry(_SourceRecord):
    """A single general-ledger / cash transaction line."""

    txn_id: str
    date: date
    account: str = "unclassified"
    description: str = ""
    amount: float
    currency: str = "USD"
    category: Optional[str] = None
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    department: Optional[str] = None
    source_system: Optional[str] = None
    external_account_id: Optional[str] = None
    posted_date: Optional[date] = None
    bank_description: Optional[str] = None
    statement_description: Optional[str] = None
    memo: Optional[str] = None
    merchant_descriptor: Optional[str] = None
    counterparty: Optional[str] = None
    payment_channel: Optional[str] = None
    card_last4: Optional[str] = None
    transaction_type: Optional[str] = None
    split_group_id: Optional[str] = None
    split_parent_id: Optional[str] = None
    raw_description: Optional[str] = None
    raw_vendor_name: Optional[str] = None
    raw_category: Optional[str] = None
    normalized_description: Optional[str] = None
    normalized_vendor_name: Optional[str] = None
    normalized_category: Optional[str] = None
    inferred_vendor_id: Optional[str] = None
    inferred_vendor_name: Optional[str] = None
    inferred_category: Optional[str] = None
    normalization_confidence: Optional[int] = Field(default=None, ge=0, le=100)
    normalization_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _prepare_bank_style_row(cls, value: Any) -> Any:
        return prepare_ledger_row(value)

    _money = field_validator("amount", mode="before")(coerce_money)
    _date = field_validator("date", "posted_date", mode="before")(coerce_date)
    _notes = field_validator("normalization_notes", mode="before")(coerce_string_list)

    def record_key(self) -> str:
        return self.txn_id


class Invoice(_SourceRecord):
    """A vendor invoice (accounts-payable line)."""

    invoice_id: str
    vendor_name: str
    vendor_id: Optional[str] = None
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    amount: float
    currency: str = "USD"
    status: str = "open"
    po_number: Optional[str] = None
    period: Optional[str] = None  # e.g. "2026-05" billing period
    line_description: Optional[str] = None
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    payment_status: Optional[str] = None
    paid_amount: Optional[float] = None
    balance_due: Optional[float] = None
    payment_date: Optional[date] = None
    payment_reference: Optional[str] = None
    dispute_status: Optional[str] = None
    dispute_reason: Optional[str] = None
    approved_by: Optional[str] = None
    terms: Optional[str] = None
    contract_reference: Optional[str] = None
    source_system: Optional[str] = None
    exchange_rate: Optional[float] = None
    amount_usd: Optional[float] = None

    _money = field_validator("amount", "paid_amount", "balance_due", "amount_usd", mode="before")(coerce_money)
    _dates = field_validator("issue_date", "due_date", "payment_date", mode="before")(coerce_date)
    _line_items = field_validator("line_items", mode="before")(coerce_dict_list)

    def record_key(self) -> str:
        return self.invoice_id


class VendorRecord(_SourceRecord):
    """A vendor/procurement-system export row describing a contract commitment."""

    vendor_id: str
    name: str
    category: Optional[str] = None
    annual_cost: float
    monthly_cost: Optional[float] = None
    renewal_date: Optional[date] = None
    status: str = "active"
    owner: Optional[str] = None
    termination_notice_days: Optional[int] = None
    notice_window_days: Optional[int] = None
    auto_renew: Optional[bool] = None
    board_approved: Optional[bool] = None
    board_approval_id: Optional[str] = None
    billing_frequency: Optional[str] = None
    billing_terms: Optional[str] = None
    contract_aliases: list[str] = Field(default_factory=list)
    tiered_pricing: list[dict[str, Any]] = Field(default_factory=list)
    owner_history: list[dict[str, Any]] = Field(default_factory=list)
    termination_penalty: Optional[float] = None
    sla_uptime_pct: Optional[float] = None
    sla_credits: Optional[str] = None
    security_clause: Optional[str] = None
    data_processing_addendum: Optional[bool] = None
    notes: Optional[str] = None

    _annual = field_validator("annual_cost", mode="before")(coerce_money)
    _monthly = field_validator("monthly_cost", mode="before")(coerce_money)
    _termination_penalty = field_validator("termination_penalty", mode="before")(coerce_money)
    _renewal = field_validator("renewal_date", mode="before")(coerce_date)
    _aliases = field_validator("contract_aliases", mode="before")(coerce_string_list)
    _dict_lists = field_validator("tiered_pricing", "owner_history", mode="before")(coerce_dict_list)
    _sla = field_validator("sla_uptime_pct", mode="before")(coerce_percent)

    @field_validator("billing_frequency", mode="before")
    @classmethod
    def _normalise_billing_frequency(cls, value: Any) -> Any:
        if value is None:
            return value
        text = str(value).strip().lower().replace("_", " ").replace("-", " ")
        aliases = {
            "annually": "annual",
            "yearly": "annual",
            "annual prepay": "annual",
            "monthly in arrears": "monthly",
            "month to month": "monthly",
        }
        return aliases.get(text, text.replace(" ", "_"))

    def record_key(self) -> str:
        return self.vendor_id


class CrmOpportunity(_SourceRecord):
    """A CRM pipeline opportunity export row."""

    opportunity_id: str
    name: str
    account: Optional[str] = None
    stage: str
    arr: float
    probability: Optional[float] = None
    weighted_arr: Optional[float] = None
    close_date: Optional[date] = None
    owner: Optional[str] = None
    opportunity_type: Optional[str] = None  # new_business | renewal | expansion
    prior_close_date: Optional[date] = None
    original_close_date: Optional[date] = None
    stage_entered_date: Optional[date] = None
    days_in_stage: Optional[int] = None
    previous_owner: Optional[str] = None
    owner_changed_at: Optional[date] = None
    probability_override: Optional[float] = None
    probability_override_reason: Optional[str] = None
    system_probability: Optional[float] = None
    forecast_category: Optional[str] = None
    next_step: Optional[str] = None
    last_activity_date: Optional[date] = None
    source_system: Optional[str] = None
    account_id: Optional[str] = None
    parent_account: Optional[str] = None
    is_renewal: Optional[bool] = None
    is_expansion: Optional[bool] = None
    renewal_arr_at_risk: Optional[float] = None

    _arr = field_validator("arr", mode="before")(coerce_money)
    _weighted = field_validator("weighted_arr", "renewal_arr_at_risk", mode="before")(coerce_money)
    _close = field_validator(
        "close_date",
        "prior_close_date",
        "original_close_date",
        "stage_entered_date",
        "owner_changed_at",
        "last_activity_date",
        mode="before",
    )(coerce_date)

    @field_validator("is_renewal", "is_expansion", mode="before")
    @classmethod
    def _coerce_bool(cls, value: Any) -> Any:
        if value is None or isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text == "":
            return None
        if text in {"true", "t", "yes", "y", "1"}:
            return True
        if text in {"false", "f", "no", "n", "0"}:
            return False
        raise ValueError(f"expected a boolean, got {value!r}")

    @field_validator("probability", "probability_override", "system_probability", mode="before")
    @classmethod
    def _normalise_probability(cls, value: Any) -> Any:
        """Accept ``0.6`` or ``"60%"``; store as a 0..1 fraction."""
        if value is None:
            return value
        if isinstance(value, (int, float)):
            return value / 100.0 if 1 < value <= 100 else value
        text = str(value).strip()
        if text == "":
            return None
        if text.lower() in {"tbd", "n/a", "na", "unknown", "manual"}:
            return None
        if text.endswith("%"):
            return float(text[:-1].strip()) / 100.0
        parsed = float(text)
        return parsed / 100.0 if 1 < parsed <= 100 else parsed

    def weighted(self) -> float:
        """Weighted ARR — explicit if provided, else arr × probability."""
        if self.weighted_arr is not None:
            return self.weighted_arr
        if self.probability is not None:
            return round(self.arr * self.probability, 2)
        return 0.0

    def record_key(self) -> str:
        return self.opportunity_id


class HeadcountPlanRow(_SourceRecord):
    """An actual/updated headcount plan row from the HRIS or planning sheet."""

    role_id: Optional[str] = None
    team: str
    mapped_team: Optional[str] = None
    role: Optional[str] = None
    headcount: int = Field(ge=0)
    monthly_cost: float
    fully_loaded_monthly_cost: Optional[float] = None
    start_month: Optional[str] = None
    planned_start_date: Optional[date] = None
    current_start_date: Optional[date] = None
    actual_start_date: Optional[date] = None
    status: str = "planned"  # planned | open | filled
    employment_type: str = "fte"  # fte | contractor
    role_type: str = "net_new"  # net_new | backfill | contractor
    backfill_for: Optional[str] = None
    recruiting_slippage_days: Optional[int] = None
    approval_status: str = "approved"  # approved | partial | pending | unapproved
    approved_headcount: Optional[int] = None
    approval_id: Optional[str] = None
    funding_basis: Optional[str] = None
    owner: Optional[str] = None
    notes: Optional[str] = None

    _monthly = field_validator("monthly_cost", "fully_loaded_monthly_cost", mode="before")(coerce_money)
    _dates = field_validator("planned_start_date", "current_start_date", "actual_start_date", mode="before")(coerce_date)

    @field_validator("headcount", "approved_headcount", "recruiting_slippage_days", mode="before")
    @classmethod
    def _coerce_int(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        return int(float(str(value)))

    @field_validator("status", "employment_type", "role_type", "approval_status", mode="before")
    @classmethod
    def _normalise_label(cls, value: Any) -> Any:
        if value is None:
            return value
        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "partially_approved": "partial",
            "partially_approved_roles": "partial",
            "not_approved": "unapproved",
            "temp": "contractor",
            "temporary": "contractor",
            "consultant": "contractor",
            "new": "net_new",
            "net_new_role": "net_new",
        }
        return aliases.get(text, text)

    def loaded_monthly_cost(self) -> float:
        """Fully loaded monthly cash impact, falling back to source monthly cost."""
        return float(self.fully_loaded_monthly_cost if self.fully_loaded_monthly_cost is not None else self.monthly_cost)

    def record_key(self) -> str:
        return self.role_id or f"{self.team}:{self.role or 'all'}:{self.start_month or self.current_start_date or ''}"


class SecurityEvidence(_SourceRecord):
    """A security/compliance control with its current evidence status."""

    control_id: str
    framework: str = "SOC 2"
    title: str
    status: str  # satisfied | gap | in_progress | not_started
    owner: Optional[str] = None
    evidence_date: Optional[date] = None
    blocks_revenue: bool = False
    blocked_arr: Optional[float] = None
    summary: Optional[str] = None

    _blocked = field_validator("blocked_arr", mode="before")(coerce_money)
    _evidence = field_validator("evidence_date", mode="before")(coerce_date)

    @field_validator("blocks_revenue", mode="before")
    @classmethod
    def _coerce_bool(cls, value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return bool(value)
        return str(value).strip().lower() in {"true", "1", "yes", "y"}

    def record_key(self) -> str:
        return self.control_id


class BoardPolicyDoc(_SourceRecord):
    """A board policy / constraint document with an optional machine-checkable rule."""

    policy_id: str
    title: str
    category: Optional[str] = None
    text: str
    rule: Optional[str] = None       # e.g. "vendor_commitment_board_notification"
    threshold: Optional[float] = None
    unit: Optional[str] = None       # e.g. "usd_per_year", "months"
    control_id: Optional[str] = None
    severity: Optional[str] = None
    approval_route: list[str] = Field(default_factory=list)
    notice_period_days: Optional[int] = None
    required_evidence: list[str] = Field(default_factory=list)
    exception_process: Optional[str] = None
    data_sensitivity: list[str] = Field(default_factory=list)
    audit_requirements: list[str] = Field(default_factory=list)
    obligations: list[dict[str, Any]] = Field(default_factory=list)
    owner_role: Optional[str] = None
    effective_date: Optional[date] = None

    _threshold = field_validator("threshold", mode="before")(coerce_money)
    _lists = field_validator(
        "approval_route",
        "required_evidence",
        "data_sensitivity",
        "audit_requirements",
        mode="before",
    )(coerce_string_list)
    _obligations = field_validator("obligations", mode="before")(coerce_dict_list)
    _effective = field_validator("effective_date", mode="before")(coerce_date)

    def record_key(self) -> str:
        return self.policy_id


# Maps a SourceType to its record model (single source of truth for parsing).
RECORD_MODELS: dict[SourceType, type[_SourceRecord]] = {
    SourceType.LEDGER: LedgerEntry,
    SourceType.INVOICES: Invoice,
    SourceType.VENDOR_EXPORT: VendorRecord,
    SourceType.CRM_OPPORTUNITIES: CrmOpportunity,
    SourceType.HEADCOUNT_PLAN: HeadcountPlanRow,
    SourceType.SECURITY_EVIDENCE: SecurityEvidence,
    SourceType.BOARD_POLICY: BoardPolicyDoc,
}


# --------------------------------------------------------------------------- #
# Provenance + import results
# --------------------------------------------------------------------------- #
class ValidationIssue(BaseModel):
    """A single rejected row, with enough context to fix the source."""

    location: str            # e.g. "row 12" or "record 3"
    field: Optional[str] = None
    message: str


class ImportProvenance(BaseModel):
    """Everything we know about *where* a dataset came from and *how fresh* it is."""

    connector_id: str
    source_type: SourceType
    origin: Origin
    status: ImportStatus
    env_var: str
    schema_version: str = SCHEMA_VERSION
    source_name: Optional[str] = None        # file basename (safe to display)
    source_path: Optional[str] = None        # full path (run through redact at edges)
    source_format: Optional[SourceFormat] = None
    workbook_name: Optional[str] = None
    workbook_sheet: Optional[str] = None
    workbook_sheets: list[str] = Field(default_factory=list)
    header_row_number: Optional[int] = None
    hidden_column_count: int = 0
    extra_column_count: int = 0
    source_timestamp: Optional[datetime] = None  # file mtime
    imported_at: Optional[datetime] = None
    checksum_sha256: Optional[str] = None
    record_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    reconciliation_status: str = "pending"   # pending | reconciled
    blockers: list[str] = Field(default_factory=list)
    validation_errors: list[ValidationIssue] = Field(default_factory=list)
    normalization_summary: dict[str, Any] = Field(default_factory=dict)
    messiness_summary: dict[str, Any] = Field(default_factory=dict)
    pipeline_quality_summary: dict[str, Any] = Field(default_factory=dict)
    headcount_quality_summary: dict[str, Any] = Field(default_factory=dict)

    def configured(self) -> bool:
        return self.status not in (ImportStatus.NOT_CONFIGURED,)

    def has_data(self) -> bool:
        return self.status in (ImportStatus.IMPORTED, ImportStatus.PARTIAL, ImportStatus.SKIPPED_UNCHANGED) and self.accepted_count > 0


class ImportResult(BaseModel):
    """Result envelope returned by the importer for one connector run."""

    provenance: ImportProvenance
    records: list[dict[str, Any]] = Field(default_factory=list)


class SourceConfidence(BaseModel):
    """Explainable confidence for one imported or expected source."""

    connector_id: str
    source_type: SourceType
    score: int = Field(ge=0, le=100)
    status: str = ""
    freshness_days: Optional[float] = None
    accepted_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    reconciliation_status: str = "pending"
    required_facts_missing: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ImportConfidence(BaseModel):
    """Transparent 0..100 confidence in the imported operations picture."""

    score: int = Field(ge=0, le=100)
    coverage: float = Field(ge=0, le=1)            # configured+imported / total connectors
    validation_pass_rate: float = Field(ge=0, le=1)
    freshness_days: Optional[float] = None         # age of the newest source
    average_source_age_days: Optional[float] = None
    oldest_source_age_days: Optional[float] = None
    sources_imported: int = 0
    sources_total: int = 0
    validation_failure_count: int = 0
    duplicate_count: int = 0
    stale_source_count: int = 0
    reconciliation_discrepancy_count: int = 0
    required_missing_count: int = 0
    required_facts_missing: list[str] = Field(default_factory=list)
    confidence_reasons: list[str] = Field(default_factory=list)
    source_confidence: list[SourceConfidence] = Field(default_factory=list)
    detail: str = ""
    components: dict[str, float] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #
class DiscrepancySeverity(str, enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DiscrepancyKind(str, enum.Enum):
    UNMATCHED_INVOICE = "unmatched_invoice"
    DUPLICATE_INVOICE = "duplicate_invoice"
    LATE_INVOICE = "late_invoice"
    PARTIAL_PAYMENT = "partial_payment"
    DISPUTED_INVOICE = "disputed_invoice"
    MISSING_DUE_DATE = "missing_due_date"
    NON_USD_INVOICE = "non_usd_invoice"
    MISSING_PO_NUMBER = "missing_po_number"
    CONTRACT_OVERSPEND = "contract_overspend"
    CONTRACT_UNDERSPEND = "contract_underspend"
    CONTRACT_INVOICE_MISMATCH = "contract_invoice_mismatch"
    RENEWAL_URGENCY = "renewal_urgency"
    MISSING_BOARD_APPROVAL = "missing_board_approval"
    SLA_SECURITY_CLAUSE_GAP = "sla_security_clause_gap"
    OWNER_ATTESTATION_GAP = "owner_attestation_gap"
    LEDGER_VENDOR_MISMATCH = "ledger_vendor_mismatch"
    LEDGER_ACCRUAL_OR_CREDIT = "ledger_accrual_or_credit"
    LEDGER_UNCATEGORIZED_SPEND = "ledger_uncategorized_spend"
    CRM_FORECAST_VARIANCE = "crm_forecast_variance"
    CRM_PROBABILITY_QUALITY = "crm_probability_quality"
    HEADCOUNT_DRIFT = "headcount_drift"
    UNPLANNED_HEADCOUNT = "unplanned_headcount"
    DEPARTMENT_NAME_DRIFT = "department_name_drift"
    POLICY_VIOLATION = "policy_violation"
    BOARD_CONSTRAINT_VIOLATION = "board_constraint_violation"
    SECURITY_REVENUE_BLOCKER = "security_revenue_blocker"
    STALE_SECURITY_EVIDENCE = "stale_security_evidence"
    MISSING_SOURCE = "missing_source"


class Discrepancy(BaseModel):
    """An explainable mismatch between two grounded sources of truth."""

    id: str
    kind: DiscrepancyKind
    severity: DiscrepancySeverity
    title: str
    detail: str
    sources: list[str] = Field(default_factory=list)   # source_types / keys involved
    expected: Optional[Any] = None
    observed: Optional[Any] = None
    delta: Optional[float] = None
    recommended_action: str = ""
    confidence: int = Field(ge=0, le=100, default=80)
    references: dict[str, Any] = Field(default_factory=dict)


class WorkflowSummary(BaseModel):
    """Per-workflow rollup so the API/agents can see coverage at a glance."""

    workflow: str
    status: str  # ok | discrepancies | insufficient_data
    checked: int = 0
    discrepancy_count: int = 0
    detail: str = ""
    blockers: list[str] = Field(default_factory=list)


class ReconciliationReport(BaseModel):
    """The full, persisted reconciliation run."""

    run_id: str
    generated_at: datetime
    schema_version: str = SCHEMA_VERSION
    status: str  # ok | discrepancies | blocked
    workflows: list[WorkflowSummary] = Field(default_factory=list)
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    counts_by_severity: dict[str, int] = Field(default_factory=dict)
    confidence: ImportConfidence
    sources_considered: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
