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
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bump when the persisted record/provenance/reconciliation shapes change so that
# stale Redis documents can be detected and re-imported deliberately.
SCHEMA_VERSION = "1.0"


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


def _blank_to_none(value: Any) -> Any:
    """CSV columns arrive as ``""`` for empties; treat those as missing."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


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
    account: str
    description: str = ""
    amount: float
    currency: str = "USD"
    category: Optional[str] = None
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None

    _money = field_validator("amount", mode="before")(coerce_money)

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

    _money = field_validator("amount", mode="before")(coerce_money)

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
    auto_renew: Optional[bool] = None
    board_approved: Optional[bool] = None
    notes: Optional[str] = None

    _annual = field_validator("annual_cost", mode="before")(coerce_money)
    _monthly = field_validator("monthly_cost", mode="before")(coerce_money)

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

    _arr = field_validator("arr", mode="before")(coerce_money)
    _weighted = field_validator("weighted_arr", mode="before")(coerce_money)

    @field_validator("probability", mode="before")
    @classmethod
    def _normalise_probability(cls, value: Any) -> Any:
        """Accept ``0.6`` or ``"60%"``; store as a 0..1 fraction."""
        if value is None or isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if text == "":
            return None
        if text.endswith("%"):
            return float(text[:-1]) / 100.0
        return float(text)

    def weighted(self) -> float:
        """Weighted ARR — explicit if provided, else arr × probability."""
        if self.weighted_arr is not None:
            return self.weighted_arr
        if self.probability is not None:
            return round(self.arr * self.probability, 2)
        return self.arr

    def record_key(self) -> str:
        return self.opportunity_id


class HeadcountPlanRow(_SourceRecord):
    """An actual/updated headcount plan row from the HRIS or planning sheet."""

    team: str
    role: Optional[str] = None
    headcount: int = Field(ge=0)
    monthly_cost: float
    start_month: Optional[str] = None
    status: str = "planned"  # planned | open | filled
    funding_basis: Optional[str] = None

    _monthly = field_validator("monthly_cost", mode="before")(coerce_money)

    @field_validator("headcount", mode="before")
    @classmethod
    def _coerce_headcount(cls, value: Any) -> Any:
        if value is None or value == "":
            return 0
        return int(float(str(value)))

    def record_key(self) -> str:
        return f"{self.team}:{self.role or 'all'}"


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

    _threshold = field_validator("threshold", mode="before")(coerce_money)

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

    def configured(self) -> bool:
        return self.status not in (ImportStatus.NOT_CONFIGURED,)

    def has_data(self) -> bool:
        return self.status in (ImportStatus.IMPORTED, ImportStatus.PARTIAL, ImportStatus.SKIPPED_UNCHANGED) and self.accepted_count > 0


class ImportResult(BaseModel):
    """Result envelope returned by the importer for one connector run."""

    provenance: ImportProvenance
    records: list[dict[str, Any]] = Field(default_factory=list)


class ImportConfidence(BaseModel):
    """Transparent 0..100 confidence in the imported operations picture."""

    score: int = Field(ge=0, le=100)
    coverage: float = Field(ge=0, le=1)            # configured+imported / total connectors
    validation_pass_rate: float = Field(ge=0, le=1)
    freshness_days: Optional[float] = None         # age of the newest source
    sources_imported: int = 0
    sources_total: int = 0
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
    CONTRACT_OVERSPEND = "contract_overspend"
    CONTRACT_UNDERSPEND = "contract_underspend"
    CRM_FORECAST_VARIANCE = "crm_forecast_variance"
    HEADCOUNT_DRIFT = "headcount_drift"
    UNPLANNED_HEADCOUNT = "unplanned_headcount"
    POLICY_VIOLATION = "policy_violation"
    BOARD_CONSTRAINT_VIOLATION = "board_constraint_violation"
    SECURITY_REVENUE_BLOCKER = "security_revenue_blocker"
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
