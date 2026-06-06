"""
Atlas Redis schema — the single source of truth for the financial operating
database's key map, index specifications, and document models.

This module is intentionally **pure schema**: it imports no Redis client, so it
can be consumed by ``redis_layer`` (which builds indexes from these specs),
``scenario_engine``, ``api``, ``health``, ``data.seed``, and the preflight
scripts without import cycles.

Everything Atlas writes lives under the ``atlas:`` namespace. The full key /
index / stream / channel map is enumerated here and surfaced verbatim through
``/api/redis-map`` so the demo can show Redis as the system of record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Namespace + schema version
# --------------------------------------------------------------------------- #
NS = "atlas"

# Bumping SCHEMA_VERSION makes ``redis_layer.run_migrations`` drop + rebuild the
# search/vector indexes on next seed so field changes take effect idempotently.
SCHEMA_VERSION = 3

EMBED_DIM = 1536

# The canonical demo company id (kept as "northwind" for backwards-compat keys).
COMPANY_ID = "northwind"


# --------------------------------------------------------------------------- #
# Key builders (the human-readable Redis key map)
# --------------------------------------------------------------------------- #
def k(*parts: str | int) -> str:
    """Join parts into a namespaced ``atlas:`` key."""
    return ":".join([NS, *[str(p) for p in parts]])


# JSON document prefixes (RedisJSON system of record).
COMPANY_PREFIX = f"{NS}:company:"
VENDOR_PREFIX = f"{NS}:vendor:"
DEPARTMENT_PREFIX = f"{NS}:department:"
INVOICE_PREFIX = f"{NS}:invoice:"
PO_PREFIX = f"{NS}:po:"
CONTRACT_PREFIX = f"{NS}:contract:"
ARR_PREFIX = f"{NS}:arr:"
SCENARIO_PREFIX = f"{NS}:scenario:"
# NOTE: approvals/governance (atlas:approval:*, atlas:govpolicy:*, atlas:obligation:*)
# and connector ingestion (atlas:source:*, atlas:dataset:*) are owned by sibling
# workstreams in redis_layer.py / integrations/. This module deliberately does
# not define or seed those namespaces to avoid collisions.

# HASH prefix for the vector RAG corpus (policies, decisions, vendor clauses,
# audit findings) — one corpus, filtered by the ``kind`` tag.
KNOWLEDGE_PREFIX = f"{NS}:knowledge:"

# Search / vector index names.
VENDOR_INDEX = f"{NS}:idx:vendors"
DEPARTMENT_INDEX = f"{NS}:idx:departments"
INVOICE_INDEX = f"{NS}:idx:invoices"
PO_INDEX = f"{NS}:idx:purchase_orders"
CONTRACT_INDEX = f"{NS}:idx:contracts"
SCENARIO_INDEX = f"{NS}:idx:scenarios"
KNOWLEDGE_INDEX = f"{NS}:idx:knowledge"

# Streams (append-only logs for dashboards + replay). The legacy "decisions" and
# "evals" streams already exist; "scenarios" is new for scenario mutations.
STREAM_DECISIONS = "decisions"
STREAM_SCENARIOS = "scenarios"
STREAM_EVALS = "evals"
ALL_STREAMS = (STREAM_DECISIONS, STREAM_SCENARIOS, STREAM_EVALS)

# Pub/Sub channel.
DASHBOARD_CHANNEL = "dashboard"

# Meta keys (financial-OS-scoped so the schema version is independent of sibling
# workstreams' own versioning).
SCHEMA_VERSION_KEY = f"{NS}:meta:financial_schema_version"
SEED_MANIFEST_KEY = f"{NS}:meta:financial_seed"

# Cache prefix.
CACHE_PREFIX = f"{NS}:cache:"

# Singleton document keys.
COMPANY_KEY = f"{COMPANY_PREFIX}{COMPANY_ID}"
RELIABILITY_LATEST_KEY = f"{NS}:reliability:latest"


def vendor_key(doc_id: str) -> str:
    return f"{VENDOR_PREFIX}{doc_id}"


def department_key(doc_id: str) -> str:
    return f"{DEPARTMENT_PREFIX}{doc_id}"


def invoice_key(doc_id: str) -> str:
    return f"{INVOICE_PREFIX}{doc_id}"


def po_key(doc_id: str) -> str:
    return f"{PO_PREFIX}{doc_id}"


def contract_key(doc_id: str) -> str:
    return f"{CONTRACT_PREFIX}{doc_id}"


def arr_key(month: str) -> str:
    return f"{ARR_PREFIX}{month}"


def scenario_key(doc_id: str) -> str:
    return f"{SCENARIO_PREFIX}{doc_id}"


def knowledge_key(doc_id: str) -> str:
    return f"{KNOWLEDGE_PREFIX}{doc_id}"


# --------------------------------------------------------------------------- #
# Index specifications (declarative; redis_layer builds redis-py fields)
# --------------------------------------------------------------------------- #
FieldType = Literal["text", "tag", "numeric", "vector"]


@dataclass(frozen=True)
class FieldSpec:
    """One indexed field. ``path`` is a JSON path for JSON indexes or a hash
    field name for HASH indexes; ``name`` is the search alias used in queries."""

    path: str
    name: str
    type: FieldType
    sortable: bool = False
    options: dict[str, Any] | None = None  # vector params, etc.


@dataclass(frozen=True)
class IndexSpec:
    name: str
    prefix: str
    on: Literal["JSON", "HASH"]
    fields: tuple[FieldSpec, ...]
    description: str = ""

    @property
    def is_vector(self) -> bool:
        return any(f.type == "vector" for f in self.fields)


def _vector_field() -> FieldSpec:
    return FieldSpec(
        path="embedding",
        name="embedding",
        type="vector",
        options={
            "ALGO": "HNSW",
            "TYPE": "FLOAT32",
            "DIM": EMBED_DIM,
            "DISTANCE_METRIC": "COSINE",
        },
    )


VENDOR_INDEX_SPEC = IndexSpec(
    name=VENDOR_INDEX,
    prefix=VENDOR_PREFIX,
    on="JSON",
    description="Vendor & SaaS contracts (cost, renewal, clauses).",
    fields=(
        FieldSpec("$.name", "name", "text"),
        FieldSpec("$.category", "category", "tag"),
        FieldSpec("$.annual_cost", "annual_cost", "numeric", sortable=True),
        FieldSpec("$.monthly_cost", "monthly_cost", "numeric", sortable=True),
        FieldSpec("$.status", "status", "tag"),
        FieldSpec("$.renewal_date", "renewal_date", "text"),
        FieldSpec("$.owner", "owner", "tag"),
        FieldSpec("$.termination_notice_days", "termination_notice_days", "numeric"),
        FieldSpec("$.switching_cost", "switching_cost", "numeric", sortable=True),
        FieldSpec("$.data_sensitivity", "data_sensitivity", "tag"),
        FieldSpec("$.auto_renew", "auto_renew", "tag"),
        FieldSpec("$.notes", "notes", "text"),
    ),
)

DEPARTMENT_INDEX_SPEC = IndexSpec(
    name=DEPARTMENT_INDEX,
    prefix=DEPARTMENT_PREFIX,
    on="JSON",
    description="Departments, owners, budgets, and YTD spend.",
    fields=(
        FieldSpec("$.name", "name", "text"),
        FieldSpec("$.head", "head", "text"),
        FieldSpec("$.cost_center", "cost_center", "tag"),
        FieldSpec("$.category", "category", "tag"),
        FieldSpec("$.headcount", "headcount", "numeric", sortable=True),
        FieldSpec("$.monthly_budget", "monthly_budget", "numeric", sortable=True),
        FieldSpec("$.ytd_spend", "ytd_spend", "numeric", sortable=True),
        FieldSpec("$.ytd_budget", "ytd_budget", "numeric", sortable=True),
    ),
)

INVOICE_INDEX_SPEC = IndexSpec(
    name=INVOICE_INDEX,
    prefix=INVOICE_PREFIX,
    on="JSON",
    description="Accounts-receivable invoices by customer, status, ageing.",
    fields=(
        FieldSpec("$.customer", "customer", "text"),
        FieldSpec("$.segment", "segment", "tag"),
        FieldSpec("$.amount", "amount", "numeric", sortable=True),
        FieldSpec("$.status", "status", "tag"),
        FieldSpec("$.issued", "issued", "text"),
        FieldSpec("$.due", "due", "text"),
        FieldSpec("$.days_overdue", "days_overdue", "numeric", sortable=True),
    ),
)

PO_INDEX_SPEC = IndexSpec(
    name=PO_INDEX,
    prefix=PO_PREFIX,
    on="JSON",
    description="Purchase orders against vendors, with approval status.",
    fields=(
        FieldSpec("$.vendor_id", "vendor_id", "tag"),
        FieldSpec("$.description", "description", "text"),
        FieldSpec("$.amount", "amount", "numeric", sortable=True),
        FieldSpec("$.status", "status", "tag"),
        FieldSpec("$.department", "department", "tag"),
        FieldSpec("$.approval_status", "approval_status", "tag"),
    ),
)

CONTRACT_INDEX_SPEC = IndexSpec(
    name=CONTRACT_INDEX,
    prefix=CONTRACT_PREFIX,
    on="JSON",
    description="Customer contracts (ARR, term, renewal) by segment.",
    fields=(
        FieldSpec("$.customer", "customer", "text"),
        FieldSpec("$.segment", "segment", "tag"),
        FieldSpec("$.arr", "arr", "numeric", sortable=True),
        FieldSpec("$.status", "status", "tag"),
        FieldSpec("$.start_date", "start_date", "text"),
        FieldSpec("$.end_date", "end_date", "text"),
        FieldSpec("$.term_months", "term_months", "numeric"),
        FieldSpec("$.auto_renew", "auto_renew", "tag"),
        FieldSpec("$.owner", "owner", "tag"),
    ),
)

SCENARIO_INDEX_SPEC = IndexSpec(
    name=SCENARIO_INDEX,
    prefix=SCENARIO_PREFIX,
    on="JSON",
    description="Forked what-if scenario branches with computed metrics.",
    fields=(
        FieldSpec("$.name", "name", "text"),
        FieldSpec("$.base", "base", "tag"),
        FieldSpec("$.status", "status", "tag"),
        FieldSpec("$.created_at", "created_at", "text", sortable=True),
        FieldSpec("$.summary", "summary", "text"),
        FieldSpec("$.tags", "tags", "tag"),
        FieldSpec("$.projected.runway_months", "runway_months", "numeric", sortable=True),
        FieldSpec("$.projected.burn_multiple", "burn_multiple", "numeric", sortable=True),
        FieldSpec("$.projected.gross_margin", "gross_margin", "numeric", sortable=True),
        FieldSpec("$.projected.cac_payback_months", "cac_payback_months", "numeric", sortable=True),
        FieldSpec("$.projected.monthly_net_burn", "monthly_net_burn", "numeric", sortable=True),
        FieldSpec("$.violation_count", "violation_count", "numeric", sortable=True),
    ),
)

KNOWLEDGE_INDEX_SPEC = IndexSpec(
    name=KNOWLEDGE_INDEX,
    prefix=KNOWLEDGE_PREFIX,
    on="HASH",
    description="Vector RAG corpus: policies, decisions, vendor clauses, audit findings.",
    fields=(
        FieldSpec("text", "text", "text"),
        FieldSpec("title", "title", "text"),
        FieldSpec("kind", "kind", "tag"),
        FieldSpec("source_id", "source_id", "tag"),
        FieldSpec("category", "category", "tag"),
        FieldSpec("severity", "severity", "tag"),
        FieldSpec("effective_date", "effective_date", "text"),
        FieldSpec("tags", "tags", "tag"),
        _vector_field(),
    ),
)

# JSON document indexes rebuilt by migrations; the vector index is listed last.
JSON_INDEX_SPECS: tuple[IndexSpec, ...] = (
    VENDOR_INDEX_SPEC,
    DEPARTMENT_INDEX_SPEC,
    INVOICE_INDEX_SPEC,
    PO_INDEX_SPEC,
    CONTRACT_INDEX_SPEC,
    SCENARIO_INDEX_SPEC,
)
ALL_INDEX_SPECS: tuple[IndexSpec, ...] = (*JSON_INDEX_SPECS, KNOWLEDGE_INDEX_SPEC)

INDEX_BY_NAME: dict[str, IndexSpec] = {spec.name: spec for spec in ALL_INDEX_SPECS}

# Collections that are expected to be non-empty after a seed (used by health /
# preflight to validate seeded counts). Maps a label → key glob pattern.
SEEDED_COLLECTIONS: dict[str, str] = {
    "company": f"{COMPANY_PREFIX}*",
    "vendors": f"{VENDOR_PREFIX}*",
    "departments": f"{DEPARTMENT_PREFIX}*",
    "invoices": f"{INVOICE_PREFIX}*",
    "purchase_orders": f"{PO_PREFIX}*",
    "contracts": f"{CONTRACT_PREFIX}*",
    "arr_movements": f"{ARR_PREFIX}*",
    "knowledge": f"{KNOWLEDGE_PREFIX}*",
}


# --------------------------------------------------------------------------- #
# Document models (typed helpers for JSON documents)
# --------------------------------------------------------------------------- #
class Department(BaseModel):
    id: str
    name: str
    head: str
    cost_center: str
    category: str = "opex"
    headcount: int = 0
    monthly_budget: float = 0.0
    ytd_budget: float = 0.0
    ytd_spend: float = 0.0
    notes: str = ""


class Invoice(BaseModel):
    id: str
    customer: str
    segment: str = ""
    contract_id: str | None = None
    amount: float = 0.0
    issued: str = ""
    due: str = ""
    status: Literal["paid", "outstanding", "overdue"] = "outstanding"
    days_overdue: int = 0


class PurchaseOrder(BaseModel):
    id: str
    vendor_id: str
    description: str = ""
    amount: float = 0.0
    department: str = ""
    status: Literal["draft", "open", "approved", "received", "cancelled"] = "open"
    approval_status: Literal["not_required", "pending", "approved", "rejected"] = "pending"
    approval_id: str | None = None
    created: str = ""


class VendorClause(BaseModel):
    auto_renew: bool = False
    price_increase_cap_pct: float | None = None
    termination_notice_days: int | None = None
    liability_cap: float | None = None
    data_processing_addendum: bool = False
    sla_uptime_pct: float | None = None
    renewal_uplift_pct: float | None = None


class CustomerContract(BaseModel):
    id: str
    customer: str
    segment: str = ""
    arr: float = 0.0
    start_date: str = ""
    end_date: str = ""
    term_months: int = 12
    auto_renew: bool = True
    status: Literal["active", "renewing", "at_risk", "churned"] = "active"
    owner: str = ""
    expansion_arr: float = 0.0


class ArrMovement(BaseModel):
    month: str
    new_arr: float = 0.0
    expansion_arr: float = 0.0
    contraction_arr: float = 0.0
    churned_arr: float = 0.0
    net_new_arr: float = 0.0
    ending_arr: float = 0.0


class SecurityControlBlocker(BaseModel):
    id: str
    control: str
    framework: str = "SOC 2"
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    blocked_arr: float = 0.0
    status: str = "open"
    owner: str = ""
    due: str = ""


class KnowledgeDoc(BaseModel):
    id: str
    kind: Literal["policy", "decision", "vendor_clause", "audit_finding"]
    title: str
    text: str
    source_id: str = ""
    category: str = ""
    severity: str = ""
    effective_date: str = ""
    tags: list[str] = Field(default_factory=list)


class BoardPolicy(BaseModel):
    """Machine-readable board constraints used by the scenario engine to flag
    violations deterministically (the natural-language list still feeds the LLM)."""

    min_runway_months: float = 9.0
    cfo_approval_annual: float = 50_000.0
    board_notify_annual: float = 150_000.0
    max_quarterly_netburn_growth: float = 0.08
    min_cash_buffer: float = 1_500_000.0
    max_burn_multiple: float = 2.0
    min_gross_margin: float = 0.70


# --------------------------------------------------------------------------- #
# Scenario engine models
# --------------------------------------------------------------------------- #
ScenarioChangeType = Literal[
    "hire",
    "vendor_renegotiation",
    "revenue_slip",
    "financing",
    "churn_shock",
    "compliance_blocker",
    "capex",
    "opex_change",
]


class ScenarioChange(BaseModel):
    """A single mutation applied to a forked company branch. Only the fields
    relevant to ``type`` need to be set; the engine reads them by type."""

    type: ScenarioChangeType
    label: str = ""
    # hire / opex_change
    team: str | None = None
    roles: int | None = None
    monthly_cost: float | None = None
    start_month: str | None = None
    # vendor_renegotiation
    vendor_id: str | None = None
    new_annual_cost: float | None = None
    # revenue_slip / churn_shock / compliance_blocker
    segment: str | None = None
    pct: float | None = None
    amount: float | None = None
    months: int | None = None
    control: str | None = None
    blocked_arr: float | None = None
    # financing
    financing_type: Literal["equity", "debt", "grant"] | None = None
    # capex
    one_time: float | None = None
    # generic added revenue (e.g. expansion from the change)
    added_monthly_revenue: float | None = None


class ConstraintViolation(BaseModel):
    code: str
    label: str
    threshold: float | str
    actual: float | str
    severity: Literal["low", "medium", "high"] = "medium"
    violated: bool = True
    detail: str = ""


class ScenarioMetrics(BaseModel):
    cash_on_hand: float = 0.0
    monthly_revenue: float = 0.0
    cogs_monthly: float = 0.0
    opex_monthly_total: float = 0.0
    monthly_gross_burn: float = 0.0
    monthly_net_burn: float = 0.0
    runway_months: float | None = None
    gross_margin: float = 0.0
    burn_multiple: float | None = None
    cac_payback_months: float | None = None
    mrr: float = 0.0
    arr: float = 0.0
    net_new_arr_annual: float = 0.0
    headcount: int = 0


class ScenarioProjectionPoint(BaseModel):
    month: str
    cash: float
    net_burn: float
    arr: float


class Scenario(BaseModel):
    id: str
    name: str
    base: str = COMPANY_ID
    status: Literal["draft", "evaluated", "archived"] = "evaluated"
    description: str = ""
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    changes: list[ScenarioChange] = Field(default_factory=list)
    baseline: ScenarioMetrics = Field(default_factory=ScenarioMetrics)
    projected: ScenarioMetrics = Field(default_factory=ScenarioMetrics)
    deltas: dict[str, float | None] = Field(default_factory=dict)
    violations: list[ConstraintViolation] = Field(default_factory=list)
    violation_count: int = 0
    projection: list[ScenarioProjectionPoint] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Redis map (for /api/redis-map + handoff documentation)
# --------------------------------------------------------------------------- #
def redis_key_map() -> dict[str, Any]:
    """A structured description of every Redis structure Atlas uses."""
    return {
        "namespace": NS,
        "schema_version": SCHEMA_VERSION,
        "json_documents": {
            "company": {"key": COMPANY_KEY, "type": "RedisJSON"},
            "vendors": {"prefix": VENDOR_PREFIX, "type": "RedisJSON"},
            "departments": {"prefix": DEPARTMENT_PREFIX, "type": "RedisJSON"},
            "invoices": {"prefix": INVOICE_PREFIX, "type": "RedisJSON"},
            "purchase_orders": {"prefix": PO_PREFIX, "type": "RedisJSON"},
            "contracts": {"prefix": CONTRACT_PREFIX, "type": "RedisJSON"},
            "arr_movements": {"prefix": ARR_PREFIX, "type": "RedisJSON"},
            "scenarios": {"prefix": SCENARIO_PREFIX, "type": "RedisJSON"},
            "reliability_latest": {"key": RELIABILITY_LATEST_KEY, "type": "RedisJSON"},
        },
        "search_indexes": {
            spec.name: {
                "on": spec.on,
                "prefix": spec.prefix,
                "fields": [f.name for f in spec.fields],
                "vector": spec.is_vector,
                "description": spec.description,
            }
            for spec in ALL_INDEX_SPECS
        },
        "knowledge_corpus": {"prefix": KNOWLEDGE_PREFIX, "type": "HASH + HNSW vector"},
        "streams": {name: f"{NS}:stream:{name}" for name in ALL_STREAMS},
        "pubsub": {"dashboard": f"{NS}:{DASHBOARD_CHANNEL}"},
        "cache": {"prefix": CACHE_PREFIX, "type": "string + TTL"},
        "meta": {"schema_version": SCHEMA_VERSION_KEY, "seed_manifest": SEED_MANIFEST_KEY},
    }
