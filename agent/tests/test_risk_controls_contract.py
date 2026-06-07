"""
Deterministic checks for Risk & Audit's controls-adversary lane.

No OpenAI or Redis calls: this verifies Risk is prompted, planned, and
fixture-tested around controls, approvals, evidence, provenance, and dissent
instead of summarizing the decision.
"""

from __future__ import annotations

from src.openai_council import (
    ROLE_DIRECTIVES,
    _PROMPT_TEMPLATES,
    enforce_role_specific_evidence_plan,
    risk_evidence_preferences,
)
from src.structured_models import DecisionPlan, DecisionType, Position, RoleEvidencePlan


CONTROLS_TERMS = {
    "policy violations",
    "audit trail gaps",
    "downside scenarios",
    "approval gaps",
    "data-quality concerns",
    "fraud/error risk",
    "compliance blockers",
    "source-provenance",
    "hidden obligations",
    "challenge optimistic forecasts",
    "missing evidence",
    "board approval id",
    "owner attestation",
    "sla/security clause",
    "contract-vs-invoice mismatches",
    "renewal urgency",
    "partially approved roles",
    "unplanned headcount",
    "contractor approvals",
    "department mapping drift",
    "gov-runway-floor",
    "gov-board-notify",
    "gov-data-security",
    "bp-6",
}


def _generic_plan() -> DecisionPlan:
    return DecisionPlan(
        decision_type=DecisionType.security_blocker,
        title="Enterprise data access expansion",
        summary="Decide whether to approve a customer-data expansion tied to enterprise ARR.",
        entities=["customer data", "$310K blocked ARR"],
        required_facts=[],
        assumptions=[],
        follow_up_questions=[],
        role_plans=[
            RoleEvidencePlan(
                role="treasury",
                tools=["get_company_financials"],
                policy_queries=[],
                focus_slices=["cash_forecast"],
                prior_decisions=[],
                rationale="Liquidity supports the decision.",
            ),
            RoleEvidencePlan(
                role="fpna",
                tools=["get_company_financials"],
                policy_queries=[],
                focus_slices=["pipeline_by_stage"],
                prior_decisions=[],
                rationale="Forecast supports the decision.",
            ),
            RoleEvidencePlan(
                role="risk",
                tools=["get_company_financials"],
                policy_queries=["general downside"],
                focus_slices=["pipeline_by_stage"],
                prior_decisions=[],
                rationale="Generic risk review.",
            ),
            RoleEvidencePlan(
                role="procurement",
                tools=["list_vendors"],
                policy_queries=[],
                focus_slices=["vendors"],
                prior_decisions=[],
                rationale="Commercial terms support the decision.",
            ),
        ],
        decision_specific_focus=["enterprise expansion"],
    )


def _risk_position() -> Position:
    return Position(
        role_specific_lens=(
            "Risk & Audit controls-adversary lens: policy violations, approval route, audit trail, "
            "security evidence, reconciliation, and source provenance only."
        ),
        stance="conditional",
        headline="Do not clear controls yet",
        argument=(
            "Condition approval until AUD-21 and the open SOC 2 evidence gap are closed; the 8-12 point "
            "forecast overstatement and missing approval trail can invalidate the $310K enterprise upside."
        ),
        key_points=[
            "Challenge the optimistic forecast with audit and reconciliation evidence before support.",
            "Require approval route, source provenance, and security sign-off before customer-data expansion.",
        ],
        cited_metrics=[
            "AUD-21 high severity",
            "gov-board-notify / BP-1 board notification required",
            "gov-data-security / BP-6 security review required",
            "8-12 point forecast overstatement",
            "$310K security-blocked ARR risk",
            "3 owner attestations missing",
            "0 human approvals recorded",
            "2 high reconciliation discrepancies",
            "3 partial headcount approvals",
            "2 unplanned contractor seats",
        ],
        evidence_used=[
            "board_constraints",
            "governance_rules",
            "approval_matrix",
            "security_incidents",
            "audit_findings",
            "reconciliation_discrepancies",
            "source_provenance",
            "data_quality",
            "headcount_approval_status",
            "partial_headcount_approvals",
            "unplanned_headcount",
            "contractor_approvals",
            "department_mapping_drift",
        ],
        forecast_assumptions=[],
        scenario_sensitivities=[],
        plan_vs_actual_deltas=[],
        control_findings=[
            "AUD-21 says revenue forecast overstates technical-validation conversion by 8-12 points.",
            "gov-data-security / BP-6: Open SOC 2 evidence gap blocks enterprise data access tied to $310K ARR risk.",
            "Three contracts lack owner attestation before renewal.",
            "Acme Analytics lacks board approval id, DPA, and clean SLA/security clauses.",
            "Datadog has a contract-vs-invoice mismatch and auto-renew notice deadline risk.",
            "Hiring plan has partial approvals, unplanned contractor seats, and department mapping drift.",
        ],
        missing_evidence_requests=[
            "Signed approval route with human approver names and timestamps.",
            "Source provenance for CRM forecast, security evidence, and reconciliation imports.",
            "Security sign-off or board exception for customer-data access.",
            "Approval IDs for partially approved and unplanned headcount rows.",
        ],
        approval_or_policy_blockers=[
            "gov-board-notify / BP-1 requires board notification before signature.",
            "gov-security-revenue requires controls evidence before broad growth spend.",
            "gov-forecast-calibration hidden obligations include SOC 2 evidence deadline and forecast-calibration checkpoint.",
            "gov-headcount headcount policy blocks unplanned contractors unless approved and tied to signed revenue, security compliance, or runway-positive automation.",
        ],
        negotiation_levers=[],
    )


def _simulated_council_decision() -> dict:
    plan = enforce_role_specific_evidence_plan(_generic_plan())
    risk_position = _risk_position()
    supportive_positions = [
        {"agent": "treasury", "stance": "support", "headline": "Liquidity can absorb it"},
        {"agent": "fpna", "stance": "support", "headline": "Forecast supports upside"},
        {"agent": "procurement", "stance": "support", "headline": "Terms are acceptable"},
    ]
    return {
        "decision": "CONDITIONAL",
        "positions": [*supportive_positions, {"agent": "risk", **risk_position.model_dump()}],
        "risk_dissent": risk_position.model_dump(),
        "decision_plan": plan.model_dump(),
    }


def test_risk_prompt_is_controls_adversary_not_decision_summary() -> None:
    directive = ROLE_DIRECTIVES["risk"].lower()
    classifier = _PROMPT_TEMPLATES["classifier"].lower()
    analyst_prompt = _PROMPT_TEMPLATES["risk"].format(
        label="Risk & Audit",
        company="Northwind Robotics",
        stage="Series A",
        mandate="controls",
        role_directive=ROLE_DIRECTIVES["risk"],
        decision_type="security_blocker",
        focus="controls evidence",
    ).lower()

    for term in CONTROLS_TERMS:
        assert term in directive
    assert "not a decision summarizer" in directive
    assert "oppose or condition" in directive
    assert "governance rules" in classifier
    assert "reconciliation discrepancies" in classifier
    assert "control_findings" in analyst_prompt
    assert "missing_evidence_requests" in analyst_prompt
    assert "approval_or_policy_blockers" in analyst_prompt


def test_risk_evidence_plan_prefers_controls_sources() -> None:
    plan = enforce_role_specific_evidence_plan(_generic_plan())
    risk_plan = next(role_plan for role_plan in plan.role_plans if role_plan.role == "risk")
    prefs = risk_evidence_preferences()

    for tool in [
        "check_controls",
        "required_approvals",
        "missing_evidence",
        "obligations_if_approved",
        "list_operations_sources",
        "get_reconciliation_summary",
        "list_open_discrepancies",
        "get_operations_data_confidence",
        "search_finance_knowledge",
    ]:
        assert tool in risk_plan.tools
        assert tool in prefs["tools"]

    for slice_name in [
        "board_constraints",
        "governance_rules",
        "approval_matrix",
        "security_incidents",
        "audit_findings",
        "reconciliation_discrepancies",
        "operations_sources",
            "source_provenance",
            "data_quality",
            "forecast_assumptions",
            "missing_board_approvals",
            "owner_attestation_gaps",
            "sla_security_clauses",
            "dpa_status",
            "contract_invoice_mismatches",
            "renewal_urgency",
            "headcount_approval_status",
            "partial_headcount_approvals",
            "unplanned_headcount",
            "contractor_approvals",
            "department_mapping_drift",
        ]:
        assert slice_name in risk_plan.focus_slices
        assert slice_name in prefs["focus_slices"]

    assert risk_plan.focus_slices.index("board_constraints") < risk_plan.focus_slices.index("pipeline_by_stage")


def test_risk_position_fixture_cites_controls_metrics_and_missing_evidence() -> None:
    position = _risk_position()
    joined_metrics = " ".join(position.cited_metrics).lower()
    joined_controls = " ".join(
        [
            position.role_specific_lens,
            position.argument,
            *position.control_findings,
            *position.missing_evidence_requests,
            *position.approval_or_policy_blockers,
        ]
    ).lower()

    for required in ["aud-21", "8-12", "$310k", "approvals", "reconciliation", "headcount", "contractor", "gov-data-security", "bp-1"]:
        assert required in joined_metrics
    assert position.stance in {"oppose", "conditional"}
    assert position.control_findings
    assert position.missing_evidence_requests
    assert position.approval_or_policy_blockers
    assert "approval route" in joined_controls
    assert "source provenance" in joined_controls
    assert "security sign-off" in joined_controls
    assert "board approval id" in joined_controls
    assert "sla/security clauses" in joined_controls
    assert "contract-vs-invoice mismatch" in joined_controls
    assert "auto-renew notice deadline" in joined_controls
    assert "unplanned contractor" in joined_controls
    assert "headcount policy" in joined_controls
    for policy_id in ["gov-board-notify", "gov-data-security", "gov-security-revenue", "gov-forecast-calibration", "gov-headcount", "bp-6"]:
        assert policy_id in joined_controls
    assert "summarize the decision" not in joined_controls


def test_risk_conditions_decision_even_when_other_agents_support() -> None:
    decision = _simulated_council_decision()
    positions = {item["agent"]: item for item in decision["positions"]}
    risk_plan = next(role_plan for role_plan in decision["decision_plan"]["role_plans"] if role_plan["role"] == "risk")

    assert positions["treasury"]["stance"] == "support"
    assert positions["fpna"]["stance"] == "support"
    assert positions["procurement"]["stance"] == "support"
    assert positions["risk"]["stance"] == "conditional"
    assert decision["decision"] == "CONDITIONAL"
    assert positions["risk"]["control_findings"]
    assert positions["risk"]["missing_evidence_requests"]
    assert "check_controls" in risk_plan["tools"]
    assert "required_approvals" in risk_plan["tools"]


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} Risk controls contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
