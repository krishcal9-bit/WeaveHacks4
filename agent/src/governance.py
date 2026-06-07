"""
Governance orchestration for Atlas — the layer that turns a CFO recommendation
into a *governed* decision.

`govern_recommendation` is the single entrypoint the debate graph (and the REST
API) call. It:
  1. derives the routing inputs (committed amount, runway impact, department,
     data sensitivity, risk tier) deterministically from the recommendation +
     the company's real numbers;
  2. evaluates every policy control (`policies.evaluate_controls`);
  3. builds the approval route (`approvals.build_route`);
  4. determines the initial status and records the who/what/when/why — as a
     *council recommendation* and *system routing/auto-clear*, never a fake human
     approval;
  5. generates post-decision obligations and monitoring triggers (board notice,
     renewal windows, SOC 2 evidence, revenue milestones, forecast calibration);
  6. computes which evidence is required, present, and missing; and
  7. persists everything (RedisJSON record + immutable audit stream + pub/sub).

`preview_governance` runs steps 1–6 without persisting — it backs the read-only
agent tools ("what approvals are required", "which controls are violated", …).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src import approvals
from src import redis_layer as R
from src.policies import evaluate_controls, load_policy_rules, text_signals
from src.governance_models import (
    ActorType,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    ControlViolation,
    DataSensitivity,
    ExceptionRequest,
    MonitoringTrigger,
    Obligation,
    RiskTier,
    Severity,
)

COMPANY_KEY = f"{R.NS}:company:northwind"

STATUS_LABELS: dict[str, str] = {
    ApprovalStatus.DRAFT.value: "Draft — deferred for more information",
    ApprovalStatus.PENDING.value: "Pending approval",
    ApprovalStatus.APPROVED.value: "Approved",
    ApprovalStatus.REJECTED.value: "Rejected by council",
    ApprovalStatus.CONDITIONAL.value: "Conditionally approved",
    ApprovalStatus.EXPIRED.value: "Expired",
    ApprovalStatus.SUPERSEDED.value: "Superseded",
}


# --------------------------------------------------------------------------- #
# Context loading
# --------------------------------------------------------------------------- #
def load_context() -> dict[str, Any]:
    """Build the governance context (company financials + vendors) from Redis. Used
    by the API/tools paths; the debate graph already has this in state."""
    financials = R.get_json(COMPANY_KEY) or {}
    try:
        vendors = R.search_vendors("*", 50)
    except Exception:
        vendors = R.list_json(R.VENDOR_PREFIX, 50)
    return {"financials": financials, "vendors": vendors, "policies": []}


# --------------------------------------------------------------------------- #
# Input derivation (deterministic, grounded in the company's real data)
# --------------------------------------------------------------------------- #
_DEPT_KEYWORDS: list[tuple[str, str]] = [
    (r"\b(engineers?|engineering|backend|frontend|platform|infra|devops|sre|r&d)\b", "Engineering"),
    (r"\b(sales|account execs?|sdrs?|quota|reps?)\b", "Sales"),
    (r"\b(customer success|support|onboarding|csm|implementation)\b", "Customer Success"),
    (r"\b(marketing|brand|campaign|demand gen|ads?|advertis)\b", "Marketing"),
    (r"\b(security|compliance|soc\s?2|audit|pentest|dpa)\b", "Security"),
    (r"\b(data|warehouse|analytics|snowflake|bi)\b", "Data"),
    (r"\b(finance|fp&a|accounting|treasury|payroll|hris|hr)\b", "G&A"),
]
_VENDOR_CATEGORY_DEPT: dict[str, str] = {
    "infrastructure": "Engineering",
    "observability": "Engineering",
    "engineering": "Engineering",
    "data": "Data",
    "crm": "Sales",
    "sales": "Sales",
    "hr_payroll": "G&A",
    "design": "Engineering",
}
_CUSTOMER_DATA_RX = re.compile(r"\b(customer data|pii|personal data|production telemetry|revenue data|customer revenue)\b", re.I)
_REGULATED_RX = re.compile(r"\b(hipaa|gdpr|pci|regulated|phi|sox)\b", re.I)


def referenced_vendors(decision_text: str, context: dict[str, Any]) -> list[dict]:
    text = (decision_text or "").lower()
    hits: list[dict] = []
    for v in (context or {}).get("vendors", []) or []:
        name = str(v.get("name", "")).lower()
        vid = str(v.get("id", "")).lower()
        if (name and name in text) or (vid and len(vid) > 2 and vid in text):
            hits.append(v)
    return hits


def _infer_department(decision_text: str, vendors: list[dict]) -> str:
    text = decision_text or ""
    for pattern, dept in _DEPT_KEYWORDS:
        if re.search(pattern, text, re.I):
            return dept
    for v in vendors:
        dept = _VENDOR_CATEGORY_DEPT.get(str(v.get("category", "")).lower())
        if dept:
            return dept
    return "Cross-functional"


def _infer_sensitivity(decision_text: str, vendors: list[dict]) -> DataSensitivity:
    if _REGULATED_RX.search(decision_text or ""):
        return DataSensitivity.REGULATED
    if _CUSTOMER_DATA_RX.search(decision_text or ""):
        return DataSensitivity.CUSTOMER
    for v in vendors:
        sensitivity = str(v.get("data_sensitivity", "")).lower()
        if any(tok in sensitivity for tok in ("customer", "revenue", "production", "telemetry", "pii")):
            return DataSensitivity.CUSTOMER
    return DataSensitivity.INTERNAL


def derive_risk_tier(confidence: int, key_risks: list[str], violations: list[ControlViolation]) -> RiskTier:
    n = len(key_risks or [])
    blocking = any(v.blocking for v in violations)
    critical = any(v.severity == Severity.CRITICAL for v in violations)
    high_ct = sum(1 for v in violations if v.severity in (Severity.HIGH, Severity.CRITICAL))
    if blocking or critical or confidence < 50 or n >= 4 or high_ct >= 3:
        return RiskTier.HIGH
    if confidence < 65 or n >= 3 or high_ct >= 1:
        return RiskTier.ELEVATED
    if confidence < 80 or n >= 1:
        return RiskTier.MODERATE
    return RiskTier.LOW


def _estimates_from_recommendation(recommendation: dict[str, Any]) -> tuple[float, float, float]:
    """Pull the cost estimates back out of the recommendation's runway impact when
    they are not supplied explicitly."""
    impact = (recommendation or {}).get("impact") or {}
    scenario = impact.get("scenario") or {}
    return (
        float(scenario.get("extra_monthly_spend") or 0.0),
        float(scenario.get("one_time_cost") or 0.0),
        float(scenario.get("added_monthly_revenue") or 0.0),
    )


def derive_inputs(
    decision_text: str,
    recommendation: dict[str, Any],
    context: dict[str, Any],
    *,
    monthly_cost: float | None = None,
    one_time_cost: float | None = None,
    added_monthly_revenue: float | None = None,
    department: str | None = None,
    data_sensitivity: str | None = None,
) -> dict[str, Any]:
    impact = (recommendation or {}).get("impact") or {}
    est_monthly, est_one_time, est_rev = _estimates_from_recommendation(recommendation)
    monthly_cost = est_monthly if monthly_cost is None else monthly_cost
    one_time_cost = est_one_time if one_time_cost is None else one_time_cost
    added_monthly_revenue = est_rev if added_monthly_revenue is None else added_monthly_revenue

    vendors = referenced_vendors(decision_text, context)
    vendor_annual = max([float(v.get("annual_cost") or 0) for v in vendors], default=0.0)
    amount_annualized = max(one_time_cost + monthly_cost * 12.0, vendor_annual)

    financials = (context or {}).get("financials") or {}
    runway_before = impact.get("current_runway_months")
    if runway_before is None:
        runway_before = financials.get("runway_months")
    runway_after = impact.get("scenario_runway_months")
    runway_delta = impact.get("delta_months")
    # When the runway impact wasn't pre-computed (e.g. API/tool path), compute it
    # from the company's real cash record so the runway-floor control is accurate.
    if runway_after is None and (monthly_cost or one_time_cost or added_monthly_revenue):
        cash = float(financials.get("cash_on_hand") or 0.0) - float(one_time_cost or 0.0)
        net_burn = float(financials.get("monthly_net_burn") or 0.0) + float(monthly_cost or 0.0) - float(added_monthly_revenue or 0.0)
        if net_burn > 0 and cash > 0:
            runway_after = round(cash / net_burn, 1)
            if runway_before is not None:
                runway_delta = round(runway_after - float(runway_before), 1)
    if runway_after is None:
        runway_after = runway_before

    dept = department or _infer_department(decision_text, vendors)
    sensitivity = DataSensitivity(data_sensitivity) if data_sensitivity else _infer_sensitivity(decision_text, vendors)

    return {
        "monthly_cost": float(monthly_cost or 0.0),
        "one_time_cost": float(one_time_cost or 0.0),
        "added_monthly_revenue": float(added_monthly_revenue or 0.0),
        "amount_annualized": float(amount_annualized or 0.0),
        "department": dept,
        "data_sensitivity": sensitivity,
        "runway_before_months": runway_before,
        "runway_after_months": runway_after,
        "runway_delta_months": runway_delta,
        "referenced_vendors": vendors,
        "signals": text_signals(decision_text),
    }


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
_BASE_EVIDENCE_PRESENT = [
    "Council recommendation and rationale",
    "Quantified runway and burn impact (compute_runway)",
]


def _evidence_satisfied(item: str, context: dict[str, Any]) -> bool:
    """Only evidence the council run genuinely produces is treated as 'present'.
    Everything a control demands from a human (board memo, DPA, financing plan,
    security sign-off, …) is honestly reported as missing until supplied."""
    low = item.lower()
    if "runway" in low or "impact" in low:
        return True
    if "rationale" in low or "recommendation" in low:
        return True
    return False


def compute_evidence(violations: list[ControlViolation], context: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    required: list[str] = []
    for v in violations:
        for item in v.evidence_required:
            if item not in required:
                required.append(item)
    present = list(_BASE_EVIDENCE_PRESENT)
    for item in required:
        if _evidence_satisfied(item, context) and item not in present:
            present.append(item)
    missing = [item for item in required if not _evidence_satisfied(item, context)]
    return required, present, missing


# --------------------------------------------------------------------------- #
# Obligations + monitoring triggers
# --------------------------------------------------------------------------- #
def _today() -> date:
    return datetime.now(timezone.utc).date()


def _due(days: int) -> str:
    return (_today() + timedelta(days=days)).isoformat()


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(value)).date()
    except (ValueError, TypeError):
        return None


def generate_obligations(
    *,
    inputs: dict[str, Any],
    violations: list[ControlViolation],
    context: dict[str, Any],
    recommendation: dict[str, Any],
) -> tuple[list[Obligation], list[MonitoringTrigger]]:
    obligations: list[Obligation] = []
    monitoring: list[MonitoringTrigger] = []
    controls = {v.control_id for v in violations}
    financials = (context or {}).get("financials") or {}

    # Board notification (within 5 business days ≈ 7 calendar days).
    if any(v.requires_board for v in violations) or "CTRL-BOARD-NOTIFY" in controls:
        obligations.append(Obligation(
            title="Board notification",
            description="Send the board a notification memo before signing (commitment size / runway impact / sensitive-data scope).",
            kind="board_notification",
            owner_role="Office of the CFO",
            due_date=_due(7),
            source_policy="gov-board-notify",
            evidence_required=["Board notification memo"],
        ))

    # Renewal-notice windows for any referenced vendor.
    for v in inputs.get("referenced_vendors", []):
        renewal = _parse_date(v.get("renewal_date"))
        notice_days = int(v.get("termination_notice_days") or 0)
        if renewal and notice_days:
            notice_deadline = renewal - timedelta(days=notice_days)
            owner = v.get("owner") or "Procurement"
            obligations.append(Obligation(
                title=f"File renewal/termination notice — {v.get('name')}",
                description=f"{v.get('name')} renews {v.get('renewal_date')} with {notice_days}-day notice; decide and notify before the window closes.",
                kind="renewal_notice",
                owner_role=owner,
                due_date=notice_deadline.isoformat(),
                source_policy="gov-vendor-renewal",
                evidence_required=["Signed renewal or termination notice"],
            ))
            monitoring.append(MonitoringTrigger(
                kind="renewal_window",
                label=f"Renewal notice window opens — {v.get('name')}",
                trigger_date=(notice_deadline - timedelta(days=7)).isoformat(),
                condition=f"notice not filed by {notice_deadline.isoformat()}",
                metric="renewal_notice",
                target=v.get("renewal_date"),
            ))

    # SOC 2 / security evidence deadline — only when the decision itself touches
    # security or sensitive data (a company-wide open gap is not this decision's task).
    if {"CTRL-SECURITY-REVENUE", "CTRL-DATA-SECURITY"} & controls:
        security_source_policy = "gov-data-security" if "CTRL-DATA-SECURITY" in controls else "gov-security-revenue"
        soc_due = _security_due_date(financials) or _due(30)
        obligations.append(Obligation(
            title="Collect SOC 2 / security evidence",
            description="Close open enterprise-blocking security control gaps and collect SOC 2 evidence before granting data access or unblocking revenue.",
            kind="soc2_evidence",
            owner_role="Risk & Audit",
            due_date=soc_due,
            source_policy=security_source_policy,
            evidence_required=["SOC 2 evidence package", "Security review sign-off"],
        ))
        monitoring.append(MonitoringTrigger(
            kind="soc2_deadline",
            label="SOC 2 evidence deadline checkpoint",
            trigger_date=(_parse_date(soc_due) - timedelta(days=7)).isoformat() if _parse_date(soc_due) else _due(23),
            condition="SOC 2 evidence not on file",
            metric="soc2_evidence",
        ))

    # Revenue milestone (decision expected to generate revenue).
    added_rev = inputs.get("added_monthly_revenue") or 0.0
    if added_rev > 0:
        target = f"+${added_rev:,.0f}/mo MRR realized"
        obligations.append(Obligation(
            title="Validate incremental revenue milestone",
            description=f"Confirm the decision delivers {target} within 90 days; otherwise revisit the commitment.",
            kind="revenue_milestone",
            owner_role="FP&A",
            due_date=_due(90),
            source_policy="gov-spend-cfo",
            evidence_required=["Realized incremental MRR report"],
        ))
        monitoring.append(MonitoringTrigger(
            kind="revenue_milestone",
            label="Incremental revenue milestone check (+90d)",
            trigger_date=_due(90),
            condition=f"realized MRR < {target}",
            metric="incremental_mrr",
            target=target,
        ))

    # Forecast-calibration checkpoint (predicted vs actual) — always.
    obligations.append(Obligation(
        title="Forecast calibration checkpoint",
        description="Compare the predicted outcome to actuals and score calibration; feed the result back into the decision-outcomes log.",
        kind="forecast_calibration",
        owner_role="FP&A",
        due_date=_due(60),
        source_policy="gov-forecast-calibration",
        evidence_required=["Predicted vs. actual outcome with calibration score"],
    ))
    monitoring.append(MonitoringTrigger(
        kind="forecast_calibration",
        label="Forecast calibration checkpoint (+60d)",
        trigger_date=_due(60),
        condition="calibration_score < 80",
        metric="calibration_score",
        target=">= 80",
    ))

    # Runway re-check when the decision pressures runway.
    if "CTRL-RUNWAY-FLOOR" in controls:
        floor = next((r.runway_floor_months for r in load_policy_rules() if r.control_id == "CTRL-RUNWAY-FLOOR" and r.runway_floor_months), 9.0)
        monitoring.append(MonitoringTrigger(
            kind="runway_recheck",
            label="Runway floor re-check (+30d)",
            trigger_date=_due(30),
            condition=f"runway < {floor:g} months",
            metric="runway_months",
            target=f">= {floor:g}",
        ))

    # Control-remediation obligations for blocking / exception controls.
    for v in violations:
        if v.blocking or v.requires_exception:
            obligations.append(Obligation(
                title=f"Remediate control: {v.title}",
                description=v.remediation or v.message,
                kind="control_remediation",
                owner_role="Office of the CFO" if v.requires_board else "Risk & Audit",
                due_date=_due(14),
                source_policy=v.policy_id,
                evidence_required=v.evidence_required,
            ))

    # Always close with a follow-up review.
    obligations.append(Obligation(
        title="Post-decision follow-up review",
        description="Confirm conditions met, obligations on track, and no new control gaps opened.",
        kind="follow_up",
        owner_role="Office of the CFO",
        due_date=_due(30),
        source_policy="gov-follow-up",
        evidence_required=["Follow-up review notes"],
    ))
    monitoring.append(MonitoringTrigger(
        kind="follow_up",
        label="Post-decision follow-up (+30d)",
        trigger_date=_due(30),
        condition="conditions or obligations outstanding",
    ))

    return obligations, monitoring


def _security_due_date(financials: dict[str, Any]) -> str | None:
    candidates: list[date] = []
    for f in financials.get("audit_findings", []) or []:
        area = str(f.get("area", "")).lower()
        if "security" in area or "ai governance" in area or "soc" in area:
            parsed = _parse_date(f.get("due"))
            if parsed:
                candidates.append(parsed)
    return min(candidates).isoformat() if candidates else None


# --------------------------------------------------------------------------- #
# Status + initial decision records (never fabricates a human approval)
# --------------------------------------------------------------------------- #
def _determine_status_and_decisions(
    req: ApprovalRequest,
    *,
    cfo_decision: str,
    violations: list[ControlViolation],
) -> tuple[ApprovalStatus, list[ApprovalDecision], list[ExceptionRequest]]:
    cfo = (cfo_decision or "").strip().upper()
    decisions: list[ApprovalDecision] = []
    exceptions: list[ExceptionRequest] = []
    has_human_steps = req.human_approvals_pending()
    has_blocking = any(v.blocking for v in violations)
    rationale = (req.recommendation or {}).get("rationale") or req.why or "Council recommendation."

    if cfo == "REJECT":
        status = ApprovalStatus.REJECTED
        decisions.append(ApprovalDecision(
            request_id=req.id, actor="Atlas Council", actor_type=ActorType.AGENT,
            action="recommended", status_after=status, provenance="council",
            rationale=f"Council recommended REJECT. {rationale}",
        ))
        return status, decisions, exceptions

    if cfo == "DEFER":
        status = ApprovalStatus.DRAFT
        decisions.append(ApprovalDecision(
            request_id=req.id, actor="Atlas Council", actor_type=ActorType.AGENT,
            action="recommended", status_after=status, provenance="council",
            rationale=f"Council deferred pending more information. {rationale}",
        ))
        return status, decisions, exceptions

    # APPROVE / CONDITIONAL — the council *recommends*; humans approve.
    decisions.append(ApprovalDecision(
        request_id=req.id, actor="Atlas Council", actor_type=ActorType.AGENT,
        action="recommended", status_after=ApprovalStatus.PENDING, provenance="council",
        rationale=f"Council recommended {cfo or 'APPROVE'}. {rationale}",
        conditions=(req.recommendation or {}).get("conditions") or [],
    ))

    if has_human_steps or has_blocking:
        status = ApprovalStatus.PENDING
        roles = ", ".join(s.approver_role for s in req.route) or "designated approvers"
        decisions.append(ApprovalDecision(
            request_id=req.id, actor="atlas-governance", actor_type=ActorType.SYSTEM,
            action="routed", status_after=status, provenance="system",
            rationale=f"Routed to {len(req.route)} approver(s) [{roles}]; awaiting human sign-off. No approval is recorded yet.",
        ))
        for v in violations:
            if v.blocking or v.requires_exception:
                exc = ExceptionRequest(
                    request_id=req.id, policy_id=v.policy_id, control_id=v.control_id,
                    justification=f"Blocking control {v.control_id}: {v.message} — requires board-approved exception or remediation.",
                    requested_by="Atlas Council", requested_by_type=ActorType.AGENT,
                    compensating_controls=[v.remediation] if v.remediation else [],
                    expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
                )
                exceptions.append(exc)
                decisions.append(ApprovalDecision(
                    request_id=req.id, actor="Atlas Council", actor_type=ActorType.AGENT,
                    action="exception_requested", status_after=status, provenance="system",
                    rationale=f"Exception requested for {v.control_id} (pending board approval).",
                ))
        return status, decisions, exceptions

    # No human approval required by policy and nothing blocking → system auto-clear.
    status = ApprovalStatus.CONDITIONAL if cfo == "CONDITIONAL" else ApprovalStatus.APPROVED
    decisions.append(ApprovalDecision(
        request_id=req.id, actor="atlas-governance", actor_type=ActorType.SYSTEM,
        action="auto_cleared", status_after=status, provenance="system",
        rationale="No human approval required by policy (below approval thresholds, no controls engaged); system-cleared.",
        conditions=(req.recommendation or {}).get("conditions") or [] if status == ApprovalStatus.CONDITIONAL else [],
    ))
    return status, decisions, exceptions


# --------------------------------------------------------------------------- #
# The orchestration entrypoints
# --------------------------------------------------------------------------- #
def _build_request(
    decision_text: str,
    recommendation: dict[str, Any],
    context: dict[str, Any],
    *,
    monthly_cost: float | None,
    one_time_cost: float | None,
    added_monthly_revenue: float | None,
    department: str | None,
    data_sensitivity: str | None,
    created_by: str,
    created_by_type: ActorType,
    source: str,
) -> tuple[ApprovalRequest, dict[str, Any]]:
    inputs = derive_inputs(
        decision_text, recommendation, context,
        monthly_cost=monthly_cost, one_time_cost=one_time_cost,
        added_monthly_revenue=added_monthly_revenue,
        department=department, data_sensitivity=data_sensitivity,
    )
    violations = evaluate_controls(
        amount_annualized=inputs["amount_annualized"],
        monthly_cost=inputs["monthly_cost"],
        added_monthly_revenue=inputs["added_monthly_revenue"],
        runway_before_months=inputs["runway_before_months"],
        runway_after_months=inputs["runway_after_months"],
        department=inputs["department"],
        data_sensitivity=inputs["data_sensitivity"],
        decision_text=decision_text,
        context=context,
        signals=inputs["signals"],
    )
    confidence = int((recommendation or {}).get("confidence") or 0)
    key_risks = (recommendation or {}).get("key_risks") or []
    risk_tier = derive_risk_tier(confidence, key_risks, violations)
    route = approvals.build_route(
        amount_annualized=inputs["amount_annualized"],
        risk_tier=risk_tier,
        data_sensitivity=inputs["data_sensitivity"].value,
        violations=violations,
    )
    obligations, monitoring = generate_obligations(
        inputs=inputs, violations=violations, context=context, recommendation=recommendation,
    )
    evidence_required, evidence_present, evidence_missing = compute_evidence(violations, context)
    matrix = approvals.load_matrix()
    expiry_days = int(matrix.get("expiry_days") or 14)

    req = ApprovalRequest(
        title=(decision_text or "").strip()[:160] or "Untitled decision",
        decision_text=decision_text or "",
        recommendation=recommendation or {},
        amount_annualized=inputs["amount_annualized"],
        one_time_cost=inputs["one_time_cost"],
        monthly_cost=inputs["monthly_cost"],
        added_monthly_revenue=inputs["added_monthly_revenue"],
        department=inputs["department"],
        data_sensitivity=inputs["data_sensitivity"],
        risk_tier=risk_tier,
        runway_before_months=inputs["runway_before_months"],
        runway_after_months=inputs["runway_after_months"],
        runway_delta_months=inputs["runway_delta_months"],
        route=route,
        violations=violations,
        obligations=obligations,
        monitoring=monitoring,
        evidence_required=evidence_required,
        evidence_present=evidence_present,
        evidence_missing=evidence_missing,
        blocked=any(v.blocking for v in violations),
        created_by=created_by,
        created_by_type=created_by_type,
        why=((recommendation or {}).get("rationale") or "")[:400],
        source=source,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=expiry_days)).isoformat(),
    )
    status, decisions, exceptions = _determine_status_and_decisions(
        req, cfo_decision=(recommendation or {}).get("decision", ""), violations=violations,
    )
    req.status = status
    req.decisions = decisions
    req.exceptions = exceptions
    for obl in req.obligations:
        obl.request_id = req.id
    for mon in req.monitoring:
        mon.request_id = req.id
    return req, inputs


def govern_recommendation(
    decision_text: str,
    recommendation: dict[str, Any],
    context: dict[str, Any] | None = None,
    *,
    monthly_cost: float | None = None,
    one_time_cost: float | None = None,
    added_monthly_revenue: float | None = None,
    department: str | None = None,
    data_sensitivity: str | None = None,
    created_by: str = "Atlas Council",
    created_by_type: ActorType | str = ActorType.AGENT,
    source: str = "council_debate",
    persist: bool = True,
) -> ApprovalRequest:
    """Govern a recommendation end-to-end and (by default) persist it."""
    created_by_type = ActorType(created_by_type) if not isinstance(created_by_type, ActorType) else created_by_type
    context = context or load_context()
    req, _ = _build_request(
        decision_text, recommendation, context,
        monthly_cost=monthly_cost, one_time_cost=one_time_cost,
        added_monthly_revenue=added_monthly_revenue,
        department=department, data_sensitivity=data_sensitivity,
        created_by=created_by, created_by_type=created_by_type, source=source,
    )
    if not persist:
        return req

    superseded = approvals.supersede_prior_pending(req.title, req.id)
    audit_events = [AuditEvent(
        type="request_created",
        request_id=req.id,
        actor=created_by,
        actor_type=created_by_type,
        summary=f"Governed recommendation created: {req.status.value} · {len(req.route)} approver step(s) · {len(req.violations)} control(s) engaged.",
        payload={
            "status": req.status.value,
            "amount_annualized": req.amount_annualized,
            "department": req.department,
            "risk_tier": req.risk_tier.value,
            "data_sensitivity": req.data_sensitivity.value,
            "blocked": req.blocked,
            "superseded_prior": superseded,
        },
    )]
    for v in req.violations:
        audit_events.append(AuditEvent(
            type="control_flagged",
            request_id=req.id,
            actor="atlas-governance",
            actor_type=ActorType.SYSTEM,
            summary=f"[{v.severity.value}] {v.control_id}: {v.message}",
            payload={"control_id": v.control_id, "policy_id": v.policy_id, "blocking": v.blocking},
        ))
    approvals.save_request(req, audit_events=audit_events, publish_event="approval_created")
    return req


def preview_governance(
    decision_text: str,
    recommendation: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> ApprovalRequest:
    """Run the governance pipeline without persisting — backs the read-only tools."""
    context = context or load_context()
    return govern_recommendation(decision_text, recommendation or {}, context, persist=False, **kwargs)


# --------------------------------------------------------------------------- #
# Presentation helpers (state streaming + transcript turn)
# --------------------------------------------------------------------------- #
def status_label(req: ApprovalRequest) -> str:
    label = STATUS_LABELS.get(req.status.value, req.status.value)
    if req.status in (ApprovalStatus.APPROVED, ApprovalStatus.CONDITIONAL):
        if any(d.action == "auto_cleared" for d in req.decisions):
            return f"{label} (system-cleared — no human approval required)"
    return label


def governance_state(req: ApprovalRequest) -> dict[str, Any]:
    """Compact-but-complete payload for AG-UI state streaming + the REST API."""
    data = req.model_dump(mode="json")
    data["status_label"] = status_label(req)
    data["human_approvals_pending"] = req.human_approvals_pending()
    data["summary"] = governance_narrative(req)
    return data


def governance_narrative(req: ApprovalRequest) -> str:
    blocking = sum(1 for v in req.violations if v.blocking)
    roles = ", ".join(s.approver_role for s in req.route) or "none required"
    policy_refs = sorted({v.policy_id for v in req.violations if v.policy_id})
    parts = [
        f"{status_label(req)}.",
        f"{len(req.violations)} control(s) engaged ({blocking} blocking).",
        f"Approval route: {roles}.",
        f"{len(req.obligations)} obligation(s), {len(req.monitoring)} monitoring trigger(s).",
    ]
    if policy_refs:
        parts.append("Policy refs: " + ", ".join(policy_refs[:5]) + ".")
    if req.evidence_missing:
        parts.append("Evidence still missing: " + "; ".join(req.evidence_missing[:3]) + ".")
    if req.human_approvals_pending():
        parts.append("No human approval has been recorded — request is pending sign-off.")
    return " ".join(parts)


def governance_turn(req: ApprovalRequest) -> dict[str, Any]:
    """A deterministic transcript turn summarizing the governance outcome."""
    key_points = [f"{v.control_id} / {v.policy_id}: {v.title}" for v in req.violations[:3]]
    if req.route:
        key_points.append("Approvers: " + ", ".join(s.approver_role for s in req.route))
    if req.evidence_missing:
        key_points.append("Missing evidence: " + "; ".join(req.evidence_missing[:2]))
    return {
        "agent": "governance",
        "label": "Governance & Controls",
        "role": "Controls, Approvals & Audit",
        "monogram": "GV",
        "type": "governance",
        "headline": f"{status_label(req)} · {len(req.route)}-step route",
        "argument": governance_narrative(req),
        "key_points": key_points,
        "approval_id": req.id,
        "status": req.status.value,
    }
