"""
Typed governance models for Atlas — the contract for turning a council
recommendation into a *governed* decision with policy controls, an approval
route, an immutable audit trail, exceptions, obligations, and post-decision
monitoring.

These models are the schema for everything persisted under the governance
namespace in Redis (RedisJSON records + an append-only audit Stream). They are
deliberately conservative about *who decides*: `ActorType` distinguishes
``system``/``agent``/``service`` from ``human``, and nothing in the codebase
auto-creates a ``human`` decision. The system records the council's *recommendation*
and routes it to human approvers as ``pending_approval`` — it never pretends a
person signed off (see ``approvals.record_decision``).

Pydantic v2. All timestamps are real UTC ISO-8601 strings captured at creation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Small helpers (real time + stable ids — never mocked)
# --------------------------------------------------------------------------- #
def utc_now_iso() -> str:
    """Real UTC timestamp; audit evidence must reflect when things happened."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class ApprovalStatus(str, Enum):
    """Lifecycle of an approval request. `approved`/`conditionally_approved` may be
    reached by *system* auto-clear when policy requires no human sign-off, but any
    request that routes to a human approver stays `pending_approval` until that
    person actually decides via the API — the system never sets it on their behalf."""

    DRAFT = "draft"
    PENDING = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONDITIONAL = "conditionally_approved"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActorType(str, Enum):
    """Provenance of an action. The integrity rule of the whole module: a `human`
    decision is only ever recorded from an explicit, operator-supplied API call —
    no graph node, tool, or seed creates one."""

    SYSTEM = "system"
    AGENT = "agent"
    SERVICE = "service"
    HUMAN = "human"


class RiskTier(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"


class DataSensitivity(str, Enum):
    NONE = "none"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    CUSTOMER = "customer_data"
    REGULATED = "regulated"


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
class PolicyRule(BaseModel):
    """A board / finance policy with both human-readable text and the structured
    thresholds the deterministic control engine evaluates against. Persisted as
    RedisJSON (`atlas:govpolicy:*`) and indexed with RediSearch for lookup."""

    id: str
    control_id: str = Field(description="stable control identifier, e.g. CTRL-RUNWAY-FLOOR")
    title: str
    category: str = Field(description="runway | vendor_spend | headcount | gross_margin | security_revenue | board_notification | data_governance")
    severity: Severity = Severity.MEDIUM
    text: str
    source: str = "board_policy"
    applies_to: list[str] = Field(default_factory=list, description="department/category tags this rule scopes to; empty = all")
    # Structured thresholds (any may be None — only the relevant ones are set)
    amount_threshold: Optional[float] = Field(default=None, description="annualized $ that triggers this control")
    runway_floor_months: Optional[float] = None
    margin_floor: Optional[float] = None
    burn_growth_cap: Optional[float] = None
    runway_priority_below_months: Optional[float] = None
    requires_board_notification: bool = False
    requires_board_approval: bool = False
    requires_security_review: bool = False
    approval_route: list[str] = Field(default_factory=list)
    notice_period_days: Optional[int] = None
    exception_process: str = ""
    data_sensitivity: list[str] = Field(default_factory=list)
    audit_requirements: list[str] = Field(default_factory=list)
    obligations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_required: list[str] = Field(default_factory=list)
    remediation: str = ""


# --------------------------------------------------------------------------- #
# Controls / violations
# --------------------------------------------------------------------------- #
class ControlViolation(BaseModel):
    """A specific, quantified way a recommendation conflicts with a policy. Produced
    by the deterministic engine in `policies.evaluate_controls` — grounded in the
    company's real numbers, never hallucinated."""

    control_id: str
    policy_id: str
    title: str
    category: str
    severity: Severity
    message: str = Field(description="what is wrong and why, quantified")
    observed: Optional[str] = None
    limit: Optional[str] = None
    blocking: bool = Field(default=False, description="if true, blocks system auto-clear and forces human/board approval")
    requires_exception: bool = Field(default=False, description="needs an explicit policy exception to proceed")
    requires_board: bool = False
    requires_security_review: bool = False
    remediation: str = ""
    evidence_required: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Approval route + decisions
# --------------------------------------------------------------------------- #
class ApprovalStep(BaseModel):
    """One required sign-off in the route. `approver_type` is the *expected* signer
    (almost always a human role); its `status` stays `pending_approval` until a real
    decision is recorded against it."""

    sequence: int
    approver_role: str = Field(description="e.g. Department Head, Controller, CFO, Board, Security Review")
    approver_type: ActorType = ActorType.HUMAN
    reason: str = Field(description="why this step exists — the policy/threshold that triggered it")
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: Optional[str] = None
    decided_by_type: Optional[ActorType] = None
    decided_at: Optional[str] = None
    note: Optional[str] = None
    policy_refs: list[str] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    """An immutable record of an action taken on a request: who / what / when / why.

    `actor_type` makes provenance explicit. `action` distinguishes a council
    *recommendation* and system *routing* (both non-human) from an actual human
    `approved`/`rejected`/`conditionally_approved`."""

    id: str = Field(default_factory=lambda: new_id("decn"))
    request_id: str
    actor: str = Field(description="who acted (system id, agent role, or human identity)")
    actor_type: ActorType = Field(description="provenance — never auto-set to human")
    action: str = Field(description="recommended | routed | auto_cleared | approved | rejected | conditionally_approved | exception_requested | superseded | expired | reopened")
    status_after: ApprovalStatus
    rationale: str = Field(description="why this action was taken")
    conditions: list[str] = Field(default_factory=list)
    at: str = Field(default_factory=utc_now_iso)
    provenance: str = Field(default="system", description="council | system | api | service")
    step_sequence: Optional[int] = None


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class ExceptionRequest(BaseModel):
    """A request to proceed despite a blocking control, with compensating controls.
    Created by the system/council as `pending_approval` — only a human approver can
    grant it (recorded as an ApprovalDecision against the exception's board step)."""

    id: str = Field(default_factory=lambda: new_id("exc"))
    request_id: str
    policy_id: str
    control_id: str
    justification: str
    requested_by: str = "Atlas Council"
    requested_by_type: ActorType = ActorType.AGENT
    status: ApprovalStatus = ApprovalStatus.PENDING
    compensating_controls: list[str] = Field(default_factory=list)
    expires_at: Optional[str] = None
    at: str = Field(default_factory=utc_now_iso)


# --------------------------------------------------------------------------- #
# Post-decision: obligations + monitoring
# --------------------------------------------------------------------------- #
class Obligation(BaseModel):
    """A commitment that must be fulfilled if/when the decision proceeds — a task
    with an owner, a due date, and required evidence. Persisted standalone
    (`atlas:obligation:*`) so the monitoring view can query upcoming/overdue items
    across all requests."""

    id: str = Field(default_factory=lambda: new_id("obl"))
    request_id: Optional[str] = None
    title: str
    description: str
    kind: str = Field(description="board_notification | renewal_notice | soc2_evidence | revenue_milestone | forecast_calibration | follow_up | control_remediation")
    owner_role: str
    due_date: Optional[str] = Field(default=None, description="ISO date (YYYY-MM-DD)")
    status: str = Field(default="open", description="open | met | overdue | waived")
    source_policy: Optional[str] = None
    evidence_required: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class MonitoringTrigger(BaseModel):
    """A scheduled checkpoint that should fire a re-evaluation or alert after a
    decision: renewal notice windows, SOC 2 evidence deadlines, revenue milestones,
    forecast-calibration checkpoints, runway re-checks, follow-up reviews."""

    id: str = Field(default_factory=lambda: new_id("mon"))
    request_id: Optional[str] = None
    kind: str = Field(description="renewal_window | soc2_deadline | revenue_milestone | forecast_calibration | runway_recheck | follow_up")
    label: str
    trigger_date: Optional[str] = Field(default=None, description="ISO date the check should run")
    condition: Optional[str] = Field(default=None, description="e.g. 'runway < 9 months'")
    metric: Optional[str] = None
    target: Optional[str] = None
    status: str = Field(default="scheduled", description="scheduled | fired | cleared")
    obligation_id: Optional[str] = None


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class AuditEvent(BaseModel):
    """An append-only audit-trail entry. Persisted to a Redis Stream
    (`atlas:stream:audit`) which is the immutable system of record for governance;
    the ApprovalRequest JSON holds current state, the stream holds history."""

    type: str = Field(description="e.g. request_created, routed, control_flagged, decision_recorded, exception_requested, status_changed, superseded, expired")
    request_id: Optional[str] = None
    actor: str
    actor_type: ActorType
    summary: str
    at: str = Field(default_factory=utc_now_iso)
    payload: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# The request — the central governed object
# --------------------------------------------------------------------------- #
class ApprovalRequest(BaseModel):
    """A council recommendation wrapped in governance: routing inputs, the controls
    it triggered, the approval route, recorded decisions, exceptions, obligations,
    monitoring triggers, and evidence status. Persisted as RedisJSON
    (`atlas:approval:*`); decisions are also appended to the audit Stream."""

    id: str = Field(default_factory=lambda: new_id("apr"))
    company_id: str = "northwind"
    title: str
    decision_text: str
    recommendation: dict[str, Any] = Field(default_factory=dict, description="snapshot of the CFO recommendation")
    status: ApprovalStatus = ApprovalStatus.DRAFT

    # Routing inputs (derived deterministically from the recommendation + context)
    amount_annualized: float = 0.0
    one_time_cost: float = 0.0
    monthly_cost: float = 0.0
    added_monthly_revenue: float = 0.0
    department: str = "Cross-functional"
    data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    risk_tier: RiskTier = RiskTier.MODERATE
    runway_before_months: Optional[float] = None
    runway_after_months: Optional[float] = None
    runway_delta_months: Optional[float] = None

    # Governance content
    route: list[ApprovalStep] = Field(default_factory=list)
    violations: list[ControlViolation] = Field(default_factory=list)
    decisions: list[ApprovalDecision] = Field(default_factory=list)
    exceptions: list[ExceptionRequest] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    monitoring: list[MonitoringTrigger] = Field(default_factory=list)
    evidence_required: list[str] = Field(default_factory=list)
    evidence_present: list[str] = Field(default_factory=list)
    evidence_missing: list[str] = Field(default_factory=list)
    blocked: bool = False

    # who / when / why
    created_by: str = "Atlas Council"
    created_by_type: ActorType = ActorType.AGENT
    why: str = Field(default="", description="one-line justification for the recommendation")
    source: str = "council_debate"
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    expires_at: Optional[str] = None
    superseded_by: Optional[str] = None

    def pending_steps(self) -> list[ApprovalStep]:
        return [s for s in self.route if s.status == ApprovalStatus.PENDING]

    def human_approvals_pending(self) -> bool:
        return any(s.approver_type == ActorType.HUMAN and s.status == ApprovalStatus.PENDING for s in self.route)
