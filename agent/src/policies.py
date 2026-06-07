"""
Policy engine for Atlas governance.

This module defines Acme Corp's structured board/finance policy rules and a
*deterministic* control engine that checks a recommendation against them. The
checks are intentionally not an LLM call — audit-grade controls must be grounded
in the company's real numbers and reproducible, never hallucinated. Each finding
quotes the observed value against the policy limit and points back to the policy
that produced it.

`DEFAULT_POLICY_RULES` is the canonical rule corpus; `data/seed.py` persists it to
RedisJSON and the RediSearch policy index. `load_policy_rules()` reads from Redis at
runtime and falls back to the canonical corpus if Redis hasn't been seeded yet, so
the engine is always able to reason.
"""

from __future__ import annotations

import re
from typing import Any

from src import redis_layer as R
from src.governance_models import (
    ControlViolation,
    DataSensitivity,
    PolicyRule,
    Severity,
)

# --------------------------------------------------------------------------- #
# Canonical board / finance policy rules (Acme Corp / Northwind)
# --------------------------------------------------------------------------- #
DEFAULT_POLICY_RULES: list[PolicyRule] = [
    PolicyRule(
        id="gov-runway-floor",
        control_id="CTRL-RUNWAY-FLOOR",
        title="Minimum runway floor",
        category="runway",
        severity=Severity.CRITICAL,
        text=(
            "Maintain at least 9 months of cash runway at all times. Any decision that would "
            "reduce runway below 9 months requires a signed financing term sheet or an explicit, "
            "board-approved runway exception with a financing plan."
        ),
        runway_floor_months=9.0,
        requires_board_approval=True,
        approval_route=["CFO", "Board"],
        notice_period_days=0,
        exception_process=(
            "Proceed only with a signed financing term sheet or a board-approved runway exception "
            "that names the financing owner, closing date, and contingency spend cuts."
        ),
        audit_requirements=[
            "Archive before/after runway model with source cash forecast.",
            "Log board exception or financing term sheet in the governance audit stream.",
        ],
        obligations=[
            {
                "kind": "runway_recheck",
                "owner_role": "Treasury",
                "due_in_days": 30,
                "evidence_required": ["Updated cash forecast", "Financing close status"],
            },
        ],
        evidence_required=[
            "Signed financing term sheet or board-approved runway exception with financing plan",
            "Before/after runway forecast",
        ],
        remediation="Reduce commitment, phase the spend, or secure a board-approved financing plan before proceeding.",
    ),
    PolicyRule(
        id="gov-spend-cfo",
        control_id="CTRL-SPEND-CFO",
        title="CFO spend-approval threshold",
        category="vendor_spend",
        severity=Severity.MEDIUM,
        text="Any single financial commitment over $50,000 per year requires CFO approval before signing.",
        amount_threshold=50_000.0,
        approval_route=["Department Head", "Controller", "CFO"],
        notice_period_days=0,
        exception_process="Document a CFO-signed delegated-authority exception before signature.",
        audit_requirements=[
            "Retain CFO approval memo with amount, term, owner, and vendor counterparty.",
            "Record approval timestamp and approver identity in the audit trail.",
        ],
        obligations=[
            {
                "kind": "approval_audit",
                "owner_role": "Controller",
                "due_in_days": 7,
                "evidence_required": ["CFO approval memo", "Signed contract or PO"],
            },
        ],
        evidence_required=["CFO approval memo", "Signed contract or purchase order"],
        remediation="Route to the Office of the CFO for sign-off.",
    ),
    PolicyRule(
        id="gov-board-notify",
        control_id="CTRL-BOARD-NOTIFY",
        title="Board notification requirement",
        category="board_notification",
        severity=Severity.MEDIUM,
        text=(
            "Any single vendor commitment above $150,000 annualized, any decision that materially "
            "moves runway, and any commitment touching regulated/customer data requires board "
            "notification before signing."
        ),
        amount_threshold=150_000.0,
        requires_board_notification=True,
        approval_route=["CFO", "Board"],
        notice_period_days=7,
        exception_process="CFO must log an urgent-signing exception and notify the board within 2 business days.",
        data_sensitivity=["customer_data", "regulated"],
        audit_requirements=[
            "Retain board notification memo and delivery timestamp.",
            "Attach runway-impact and data-sensitivity appendix when applicable.",
        ],
        obligations=[
            {
                "kind": "board_notification",
                "owner_role": "Office of the CFO",
                "due_in_days": 5,
                "evidence_required": ["Board notification memo", "Delivery receipt"],
            },
        ],
        evidence_required=["Board notification memo", "Runway impact summary if material"],
        remediation="Prepare and send a board notification memo before signing.",
    ),
    PolicyRule(
        id="gov-headcount",
        control_id="CTRL-HEADCOUNT",
        title="Headcount & burn discipline",
        category="headcount",
        severity=Severity.HIGH,
        text=(
            "Net-new headcount must keep monthly net-burn growth under 8% unless the role is directly "
            "tied to committed revenue, security compliance, or runway-positive automation."
        ),
        burn_growth_cap=0.08,
        applies_to=["Engineering", "Sales", "Customer Success", "Marketing", "G&A"],
        approval_route=["Department Head", "People Ops", "CFO"],
        notice_period_days=14,
        exception_process=(
            "CFO may approve a staged-start exception if the hiring owner documents signed revenue, "
            "security compliance, or runway-positive automation linkage."
        ),
        audit_requirements=[
            "Retain role approval status, start date, fully loaded cost, and department mapping.",
            "Reconcile planned/open/filled roles against the operating plan monthly.",
        ],
        obligations=[
            {
                "kind": "headcount_reconciliation",
                "owner_role": "People Ops",
                "due_in_days": 30,
                "evidence_required": ["Approved headcount row", "Start-date and cost reconciliation"],
            },
        ],
        evidence_required=[
            "Mapping of new headcount to committed revenue, compliance, or automation",
            "Approved headcount plan row with start date and fully loaded cost",
        ],
        remediation="Tie the hire to signed revenue/compliance, or stagger start dates to stay under the burn-growth cap.",
    ),
    PolicyRule(
        id="gov-gross-margin",
        control_id="CTRL-GROSS-MARGIN",
        title="Gross-margin floor",
        category="gross_margin",
        severity=Severity.HIGH,
        text=(
            "Decisions that add cost of goods sold (infrastructure, data, compute, hosting) must not "
            "push blended gross margin below 70%."
        ),
        margin_floor=0.70,
        applies_to=["Engineering", "Infrastructure", "Data"],
        approval_route=["FP&A", "CFO"],
        notice_period_days=0,
        exception_process="CFO must approve a temporary margin exception with a pricing, usage, or efficiency offset plan.",
        audit_requirements=[
            "Archive unit-economics model with revenue, COGS, usage, and discount assumptions.",
            "Re-test gross margin after the first full billing cycle.",
        ],
        obligations=[
            {
                "kind": "gross_margin_retest",
                "owner_role": "FP&A",
                "due_in_days": 45,
                "evidence_required": ["Actual COGS report", "Updated gross-margin model"],
            },
        ],
        evidence_required=["Updated unit-economics model showing gross margin at or above 70%"],
        remediation="Offset with committed-use discounts, pricing, or efficiency before adding COGS.",
    ),
    PolicyRule(
        id="gov-security-revenue",
        control_id="CTRL-SECURITY-REVENUE",
        title="Security-blocked revenue priority",
        category="security_revenue",
        severity=Severity.HIGH,
        text=(
            "When runway is under 12 months, controls that unblock signed or late-stage enterprise "
            "revenue (e.g., SOC 2 evidence) take priority over broad growth spend."
        ),
        runway_priority_below_months=12.0,
        applies_to=["Sales", "Marketing"],
        approval_route=["Risk & Audit", "CFO"],
        notice_period_days=0,
        exception_process="CFO and Risk & Audit must document why broad growth spend outranks the open revenue blocker.",
        data_sensitivity=["customer_data"],
        audit_requirements=[
            "Trace security gap to affected ARR and opportunity stage.",
            "Retain Risk & Audit sign-off before funding broad growth spend below 12 months runway.",
        ],
        obligations=[
            {
                "kind": "security_revenue_checkpoint",
                "owner_role": "Risk & Audit",
                "due_in_days": 14,
                "evidence_required": ["Security gap remediation status", "Blocked ARR report"],
            },
        ],
        evidence_required=["Confirmation that open enterprise-blocking security gaps are funded first"],
        remediation="Fund the security/compliance work that unblocks enterprise revenue before broad growth spend.",
    ),
    PolicyRule(
        id="gov-data-security",
        control_id="CTRL-DATA-SECURITY",
        title="Data-sensitivity vendor review",
        category="data_governance",
        severity=Severity.MEDIUM,
        text=(
            "Any vendor or decision that processes customer or regulated data requires a security "
            "review and a signed data processing agreement before go-live."
        ),
        requires_security_review=True,
        approval_route=["Security Review", "Legal"],
        notice_period_days=0,
        exception_process="No go-live exception is allowed for regulated data without Legal and Security Review sign-off.",
        data_sensitivity=["customer_data", "regulated"],
        audit_requirements=[
            "Store security review, DPA, data-flow owner, and access grant evidence.",
            "Reconcile vendor security evidence freshness before customer-data access.",
        ],
        obligations=[
            {
                "kind": "data_access_review",
                "owner_role": "Security Review",
                "due_in_days": 0,
                "evidence_required": ["Security review sign-off", "Signed DPA"],
            },
        ],
        evidence_required=["Security review sign-off", "Signed data processing agreement (DPA)"],
        remediation="Complete the security review and execute a DPA before granting data access.",
    ),
    PolicyRule(
        id="gov-forecast-calibration",
        control_id="CTRL-FORECAST-CALIBRATION",
        title="Post-decision forecast calibration",
        category="forecast_governance",
        severity=Severity.LOW,
        text=(
            "Every material council decision must compare predicted cash, ARR, margin, and control outcomes "
            "against actuals within 60 days and feed calibration back into the decision-outcomes log."
        ),
        approval_route=["FP&A"],
        notice_period_days=60,
        exception_process="CFO must approve skipping calibration and record why actuals cannot be measured.",
        audit_requirements=[
            "Retain predicted-vs-actual scorecard with source provenance.",
            "Attach replay directive when calibration misses exceed tolerance.",
        ],
        obligations=[
            {
                "kind": "forecast_calibration",
                "owner_role": "FP&A",
                "due_in_days": 60,
                "evidence_required": ["Predicted vs. actual outcome with calibration score"],
            },
        ],
        evidence_required=["Predicted vs. actual outcome with calibration score"],
        remediation="Create a forecast-calibration checkpoint and feed misses into replay cases.",
    ),
]

# Engine fallbacks (used only if a rule's structured threshold is missing).
FALLBACK_RUNWAY_FLOOR = 9.0
FALLBACK_CFO_THRESHOLD = 50_000.0
FALLBACK_BOARD_THRESHOLD = 150_000.0
FALLBACK_BURN_GROWTH_CAP = 0.08
FALLBACK_MARGIN_FLOOR = 0.70
FALLBACK_SECURITY_RUNWAY = 12.0


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_policy_rules() -> list[PolicyRule]:
    """Read structured policy rules from Redis; fall back to the canonical corpus
    if the governance namespace has not been seeded yet (or Redis is unreachable),
    so the control engine can always reason."""
    rules: list[PolicyRule] = []
    try:
        for doc in R.search_govpolicies("*", limit=100):
            try:
                rules.append(PolicyRule.model_validate(doc))
            except Exception:
                continue
    except Exception:
        rules = []
    return rules or list(DEFAULT_POLICY_RULES)


def rules_by_control(rules: list[PolicyRule] | None = None) -> dict[str, PolicyRule]:
    rules = rules if rules is not None else load_policy_rules()
    return {r.control_id: r for r in rules}


def get_rule(control_id: str, rules: list[PolicyRule] | None = None) -> PolicyRule | None:
    return rules_by_control(rules).get(control_id)


# --------------------------------------------------------------------------- #
# Text signals — deterministic keyword detection for control scoping
# --------------------------------------------------------------------------- #
_HEADCOUNT_RX = re.compile(r"\b(hir(e|ing)|headcount|fte|head count|new role|backfill|recruit|engineers?|reps?|salespeople)\b", re.I)
_GROWTH_RX = re.compile(r"\b(marketing|brand|campaign|ads?|advertis|growth experiment|demand gen|top.of.funnel|paid acquisition|sponsorship)\b", re.I)
_COGS_RX = re.compile(r"\b(infrastructure|infra|compute|hosting|cloud|aws|gpu|data warehouse|snowflake|datadog|observability|bandwidth|storage)\b", re.I)
_SECURITY_INVEST_RX = re.compile(r"\b(soc\s?2|security|compliance|audit|penetration|pentest|control gap|dpa|iso\s?27001|remediation|evidence)\b", re.I)


def text_signals(decision_text: str) -> dict[str, bool]:
    text = decision_text or ""
    return {
        "is_headcount": bool(_HEADCOUNT_RX.search(text)),
        "is_growth_spend": bool(_GROWTH_RX.search(text)),
        "is_cogs_like": bool(_COGS_RX.search(text)),
        "is_security_investment": bool(_SECURITY_INVEST_RX.search(text)),
    }


def has_open_security_gap(context: dict[str, Any]) -> bool:
    financials = (context or {}).get("financials") or {}
    for incident in financials.get("security_incidents", []) or []:
        status = str(incident.get("status", "")).lower()
        if "open" in status or "gap" in status:
            return True
    for finding in financials.get("audit_findings", []) or []:
        area = str(finding.get("area", "")).lower()
        severity = str(finding.get("severity", "")).lower()
        if severity == "high" and ("security" in area or "ai governance" in area or "soc" in area):
            return True
    return False


# --------------------------------------------------------------------------- #
# The control engine
# --------------------------------------------------------------------------- #
def _money(value: float) -> str:
    return f"${value:,.0f}"


def evaluate_controls(
    *,
    amount_annualized: float,
    monthly_cost: float,
    added_monthly_revenue: float,
    runway_before_months: float | None,
    runway_after_months: float | None,
    department: str,
    data_sensitivity: DataSensitivity | str,
    decision_text: str,
    context: dict[str, Any],
    signals: dict[str, bool] | None = None,
    financing_in_hand: bool = False,
) -> list[ControlViolation]:
    """Deterministically evaluate every governance control against a recommendation.

    Returns the controls that are *engaged* — either hard breaches (``blocking``)
    or thresholds crossed that require approval/notification. Controls that pass
    are simply omitted. Every finding is quantified (observed vs. limit) and traces
    back to the policy that produced it.
    """
    rules = load_policy_rules()
    by_control = rules_by_control(rules)
    sig = signals or text_signals(decision_text)
    sensitivity = data_sensitivity.value if isinstance(data_sensitivity, DataSensitivity) else str(data_sensitivity)
    financials = (context or {}).get("financials") or {}
    out: list[ControlViolation] = []

    def rule(control_id: str) -> PolicyRule | None:
        return by_control.get(control_id)

    # 1) Runway floor — hard breach is blocking and needs a board-approved exception.
    runway_rule = rule("CTRL-RUNWAY-FLOOR")
    floor = (runway_rule.runway_floor_months if runway_rule and runway_rule.runway_floor_months else FALLBACK_RUNWAY_FLOOR)
    if runway_after_months is not None and runway_after_months < floor and not financing_in_hand:
        out.append(ControlViolation(
            control_id="CTRL-RUNWAY-FLOOR",
            policy_id=runway_rule.id if runway_rule else "gov-runway-floor",
            title="Runway floor breach",
            category="runway",
            severity=Severity.CRITICAL,
            message=(
                f"Projected runway of {runway_after_months} months falls below the {floor:g}-month "
                f"board floor (from {runway_before_months} months) with no signed financing in hand."
            ),
            observed=f"{runway_after_months} months",
            limit=f">= {floor:g} months",
            blocking=True,
            requires_exception=True,
            requires_board=True,
            remediation=runway_rule.remediation if runway_rule else "Secure a board-approved financing plan.",
            evidence_required=(runway_rule.evidence_required if runway_rule else ["Board-approved runway exception"]),
        ))
    elif (
        runway_after_months is not None
        and runway_before_months is not None
        and (runway_before_months - runway_after_months) >= 1.0
        and runway_after_months < floor + 2.0
    ):
        out.append(ControlViolation(
            control_id="CTRL-RUNWAY-FLOOR",
            policy_id=runway_rule.id if runway_rule else "gov-runway-floor",
            title="Runway pressure",
            category="runway",
            severity=Severity.MEDIUM,
            message=(
                f"Decision cuts runway by {round(runway_before_months - runway_after_months, 1)} months to "
                f"{runway_after_months} months — within 2 months of the {floor:g}-month floor."
            ),
            observed=f"{runway_after_months} months",
            limit=f"comfortably above {floor:g} months",
            requires_board=True,
            remediation="Notify the board of the runway impact and confirm the financing path.",
        ))

    # 2) CFO spend threshold.
    cfo_rule = rule("CTRL-SPEND-CFO")
    cfo_threshold = (cfo_rule.amount_threshold if cfo_rule and cfo_rule.amount_threshold else FALLBACK_CFO_THRESHOLD)
    if amount_annualized > cfo_threshold:
        out.append(ControlViolation(
            control_id="CTRL-SPEND-CFO",
            policy_id=cfo_rule.id if cfo_rule else "gov-spend-cfo",
            title="CFO approval required",
            category="vendor_spend",
            severity=Severity.MEDIUM,
            message=(
                f"Committed spend of {_money(amount_annualized)}/yr exceeds the {_money(cfo_threshold)} "
                f"CFO approval threshold."
            ),
            observed=f"{_money(amount_annualized)}/yr",
            limit=f"<= {_money(cfo_threshold)}/yr without CFO sign-off",
            remediation="Route to the Office of the CFO for sign-off.",
            evidence_required=(cfo_rule.evidence_required if cfo_rule else ["CFO approval memo"]),
        ))

    # 3) Board notification — large commitment, runway move, or sensitive data.
    board_rule = rule("CTRL-BOARD-NOTIFY")
    board_threshold = (board_rule.amount_threshold if board_rule and board_rule.amount_threshold else FALLBACK_BOARD_THRESHOLD)
    board_reasons: list[str] = []
    if amount_annualized > board_threshold:
        board_reasons.append(f"commitment {_money(amount_annualized)}/yr exceeds {_money(board_threshold)}/yr")
    if sensitivity in (DataSensitivity.CUSTOMER.value, DataSensitivity.REGULATED.value):
        board_reasons.append(f"touches {sensitivity.replace('_', ' ')}")
    if board_reasons:
        out.append(ControlViolation(
            control_id="CTRL-BOARD-NOTIFY",
            policy_id=board_rule.id if board_rule else "gov-board-notify",
            title="Board notification required",
            category="board_notification",
            severity=Severity.MEDIUM,
            message="Board notification required: " + "; ".join(board_reasons) + ".",
            observed="; ".join(board_reasons),
            limit=f"board notice for commitments > {_money(board_threshold)}/yr or sensitive-data scope",
            requires_board=True,
            remediation="Prepare and send a board notification memo before signing.",
            evidence_required=(board_rule.evidence_required if board_rule else ["Board notification memo"]),
        ))

    # 4) Headcount & burn discipline.
    base_net_burn = float(financials.get("monthly_net_burn") or 0.0)
    if sig.get("is_headcount") and monthly_cost > 0 and base_net_burn > 0:
        hc_rule = rule("CTRL-HEADCOUNT")
        cap = (hc_rule.burn_growth_cap if hc_rule and hc_rule.burn_growth_cap else FALLBACK_BURN_GROWTH_CAP)
        burn_growth = monthly_cost / base_net_burn
        tied_to_revenue = added_monthly_revenue >= max(monthly_cost * 0.5, 1.0)
        if burn_growth > cap and not tied_to_revenue:
            out.append(ControlViolation(
                control_id="CTRL-HEADCOUNT",
                policy_id=hc_rule.id if hc_rule else "gov-headcount",
                title="Headcount burn-growth breach",
                category="headcount",
                severity=Severity.HIGH,
                message=(
                    f"Adds {_money(monthly_cost)}/mo = {burn_growth:.0%} net-burn growth on a "
                    f"{_money(base_net_burn)}/mo base, above the {cap:.0%} cap, and is not tied to committed revenue."
                ),
                observed=f"{burn_growth:.0%} net-burn growth",
                limit=f"<= {cap:.0%} unless tied to committed revenue",
                remediation=hc_rule.remediation if hc_rule else "Tie to committed revenue or stagger starts.",
                evidence_required=(hc_rule.evidence_required if hc_rule else ["Headcount-to-revenue mapping"]),
            ))

    # 5) Gross-margin floor (only when the spend is COGS-like, not headcount/OpEx).
    if (sig.get("is_cogs_like") or department in ("Infrastructure", "Data")) and not sig.get("is_headcount"):
        gm_rule = rule("CTRL-GROSS-MARGIN")
        margin_floor = (gm_rule.margin_floor if gm_rule and gm_rule.margin_floor else FALLBACK_MARGIN_FLOOR)
        revenue = float(financials.get("monthly_revenue") or 0.0)
        cogs = float(financials.get("cogs_monthly") or 0.0)
        if revenue > 0 and monthly_cost > 0:
            new_revenue = revenue + max(added_monthly_revenue, 0.0)
            projected_margin = (new_revenue - cogs - monthly_cost) / new_revenue
            if projected_margin < margin_floor:
                out.append(ControlViolation(
                    control_id="CTRL-GROSS-MARGIN",
                    policy_id=gm_rule.id if gm_rule else "gov-gross-margin",
                    title="Gross-margin floor breach",
                    category="gross_margin",
                    severity=Severity.HIGH,
                    message=(
                        f"Adding {_money(monthly_cost)}/mo of COGS-like spend projects blended gross margin at "
                        f"{projected_margin:.0%}, below the {margin_floor:.0%} floor "
                        f"(current {float(financials.get('gross_margin') or 0):.0%})."
                    ),
                    observed=f"{projected_margin:.0%} projected gross margin",
                    limit=f">= {margin_floor:.0%}",
                    remediation=gm_rule.remediation if gm_rule else "Offset with discounts, pricing, or efficiency.",
                    evidence_required=(gm_rule.evidence_required if gm_rule else ["Updated unit-economics model"]),
                ))

    # 6) Security-blocked revenue priority (broad growth spend while enterprise revenue is blocked).
    sr_rule = rule("CTRL-SECURITY-REVENUE")
    security_runway = (sr_rule.runway_priority_below_months if sr_rule and sr_rule.runway_priority_below_months else FALLBACK_SECURITY_RUNWAY)
    runway_now = runway_before_months if runway_before_months is not None else float(financials.get("runway_months") or 0.0)
    if (
        sig.get("is_growth_spend")
        and not sig.get("is_security_investment")
        and runway_now < security_runway
        and has_open_security_gap(context)
        and amount_annualized > 0
    ):
        out.append(ControlViolation(
            control_id="CTRL-SECURITY-REVENUE",
            policy_id=sr_rule.id if sr_rule else "gov-security-revenue",
            title="Security-blocked revenue takes priority",
            category="security_revenue",
            severity=Severity.HIGH,
            message=(
                f"Runway is {runway_now} months (< {security_runway:g}) and open enterprise-blocking security gaps exist; "
                f"broad growth spend of {_money(amount_annualized)}/yr is deprioritized behind unblocking signed revenue."
            ),
            observed=f"growth spend at {runway_now}-month runway with open security gaps",
            limit=f"prioritize security-blocked revenue below {security_runway:g}-month runway",
            requires_security_review=True,
            remediation=sr_rule.remediation if sr_rule else "Fund enterprise-unblocking security work first.",
            evidence_required=(sr_rule.evidence_required if sr_rule else ["Confirmation security gaps are funded first"]),
        ))

    # 7) Data-sensitivity vendor review.
    if sensitivity in (DataSensitivity.CUSTOMER.value, DataSensitivity.REGULATED.value):
        ds_rule = rule("CTRL-DATA-SECURITY")
        out.append(ControlViolation(
            control_id="CTRL-DATA-SECURITY",
            policy_id=ds_rule.id if ds_rule else "gov-data-security",
            title="Data-sensitivity review required",
            category="data_governance",
            severity=Severity.MEDIUM,
            message=(
                f"Decision scope is classified {sensitivity.replace('_', ' ')}; a security review and signed DPA "
                f"are required before go-live."
            ),
            observed=sensitivity.replace("_", " "),
            limit="security review + DPA on file",
            requires_security_review=True,
            remediation=ds_rule.remediation if ds_rule else "Complete security review and execute a DPA.",
            evidence_required=(ds_rule.evidence_required if ds_rule else ["Security review sign-off", "Signed DPA"]),
        ))

    return out
