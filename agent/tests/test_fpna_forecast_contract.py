"""
Deterministic checks for FP&A's forecast and unit-economics lane.

No OpenAI or Redis calls: this verifies that FP&A is prompted, planned, and
fixture-tested around forecastability rather than compliance, procurement, or
generic affordability language.
"""

from __future__ import annotations

from src.openai_council import (
    ROLE_DIRECTIVES,
    _PROMPT_TEMPLATES,
    _fpna_context_summary,
    enforce_role_specific_evidence_plan,
    fpna_evidence_preferences,
)
from src.structured_models import DecisionPlan, DecisionType, Position, RoleEvidencePlan


FORECAST_TERMS = {
    "forecast quality",
    "arr movement",
    "pipeline probability",
    "pipeline quality",
    "slipped close dates",
    "stage aging",
    "stale opportunities",
    "probability overrides",
    "weighted/unweighted",
    "roi",
    "cac/payback",
    "gross margin",
    "sensitivity ranges",
    "scenario math",
    "hiring-plan quality",
    "recruiting slippage",
    "start-date capacity timing",
    "plan-vs-actual hiring drift",
    "plan-vs-actual",
    "forecastable",
}


def _generic_plan() -> DecisionPlan:
    return DecisionPlan(
        decision_type=DecisionType.capital_allocation,
        title="Enterprise growth investment",
        summary="Decide whether to fund a growth investment tied to enterprise ARR.",
        entities=["Enterprise ARR", "$420K"],
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
                rationale="Liquidity review.",
            ),
            RoleEvidencePlan(
                role="fpna",
                tools=["get_company_financials"],
                policy_queries=["spend approval"],
                focus_slices=["vendors"],
                prior_decisions=[],
                rationale="Generic affordability review.",
            ),
        ],
        decision_specific_focus=["growth investment"],
    )


def _fpna_position() -> Position:
    return Position(
        role_specific_lens=(
            "FP&A forecastability lens: ARR movement, pipeline probability, unit economics, "
            "scenario sensitivity, and plan-vs-actual deltas only."
        ),
        stance="conditional",
        headline="Forecast the case first",
        argument=(
            "Support only if the $4.22M weighted pipeline ARR is haircut for 6 slipped close dates, "
            "8 aged-stage opportunities, 5 stale opportunities, and 9 probability overrides before CAC payback "
            "is accepted below 9 months; otherwise the business case is not forecastable."
        ),
        key_points=[
            "Condition approval on pipeline quality and calibrated conversion, not affordability or total ARR alone.",
            "Use ARR bridge and gross-margin sensitivity before accepting the ROI case.",
        ],
        cited_metrics=[
            "$7.58M unweighted pipeline ARR",
            "$4.22M weighted pipeline ARR",
            "6 slipped close dates",
            "8 aged-stage opportunities",
            "5 stale opportunities",
            "9 probability overrides",
            "$830K renewal ARR at risk",
            "56% weighted/unweighted ARR ratio",
            "$318K/mo fully loaded hiring plan",
            "8 slipped recruiting starts",
            "3 partial headcount approvals",
            "78% gross margin",
            "$18.5K CAC",
            "6.5 month CAC payback",
            "ARR +$420K base / +$180K downside",
        ],
        evidence_used=[
            "forecast_assumptions",
            "pipeline_by_stage",
            "pipeline_quality",
            "slipped_close_dates",
            "stage_aging",
            "stale_opportunities",
            "probability_overrides",
            "renewal_vs_new_business",
            "weighted_unweighted_arr_gap",
            "customer_cohorts",
            "headcount_plan_quality",
            "recruiting_slippage",
            "hiring_start_timing",
            "fully_loaded_role_cost",
            "plan_vs_actual_hiring_drift",
            "arr_movements",
            "customer_contracts",
            "scenario_math",
            "plan_vs_actual_deltas",
        ],
        forecast_assumptions=[
            "Base case accepts $4.22M weighted pipeline ARR only after haircutting slipped close dates, aged stages, stale activity, and probability overrides.",
            "Separate $830K renewal ARR at risk from new-business and expansion growth before treating pipeline as upside.",
            "Move slipped or partially approved hiring rows out of base-case capacity until start dates and approval status are reconciled.",
            "ROI case assumes 78% gross margin and CAC payback below 9 months.",
        ],
        scenario_sensitivities=[
            "If stale/aged opportunities convert 10 points below override probability, weighted ARR lands at least $400K below plan.",
            "If gross margin compresses from 78% to 72%, payback extends beyond the 9-month gate.",
        ],
        plan_vs_actual_deltas=[
            "Prior FP&A decision predicted 7-month payback; actual landed at 6.5 months.",
        ],
        control_findings=[],
        missing_evidence_requests=[],
        approval_or_policy_blockers=[],
        negotiation_levers=[],
    )


def _simulated_council_decision() -> dict:
    plan = enforce_role_specific_evidence_plan(_generic_plan())
    fpna_position = _fpna_position()
    return {
        "decision": "CONDITIONAL",
        "reason": "FP&A conditioned the ruling on forecastability gates before the CFO weighs runway.",
        "forecastability_gate": {
            "required": True,
            "conditions": [*fpna_position.forecast_assumptions, *fpna_position.scenario_sensitivities],
        },
        "positions": [fpna_position.model_dump()],
        "decision_plan": plan.model_dump(),
    }


def test_fpna_prompt_is_forecast_and_unit_economics_not_compliance_or_procurement() -> None:
    directive = ROLE_DIRECTIVES["fpna"].lower()
    classifier = _PROMPT_TEMPLATES["classifier"].lower()
    analyst_prompt = _PROMPT_TEMPLATES["fpna"].format(
        label="FP&A",
        company="Northwind Robotics",
        stage="Series A",
        mandate="forecast quality",
        role_directive=ROLE_DIRECTIVES["fpna"],
        decision_type="capital_allocation",
        focus="forecastability",
    ).lower()

    for term in FORECAST_TERMS:
        assert term in directive
    assert "business case is forecastable" in classifier
    assert "do not talk like procurement" in directive
    assert "do not talk like risk/audit" in directive
    assert "forecast_assumptions" in analyst_prompt
    assert "scenario_sensitivities" in analyst_prompt
    assert "plan_vs_actual_deltas" in analyst_prompt


def test_fpna_evidence_plan_prefers_forecast_sources() -> None:
    plan = enforce_role_specific_evidence_plan(_generic_plan())
    fpna_plan = next(role_plan for role_plan in plan.role_plans if role_plan.role == "fpna")
    prefs = fpna_evidence_preferences()

    for tool in [
        "build_strategic_plan",
        "run_plan_sensitivity",
        "run_plan_stress_test",
        "list_arr_movements",
        "list_customer_contracts",
        "list_operations_sources",
        "get_reconciliation_summary",
        "list_open_discrepancies",
        "search_scenarios",
    ]:
        assert tool in fpna_plan.tools
        assert tool in prefs["tools"]

    for slice_name in [
        "forecast_assumptions",
        "pipeline_by_stage",
        "pipeline_quality",
        "slipped_close_dates",
        "stage_aging",
        "stale_opportunities",
        "probability_overrides",
        "duplicate_accounts",
        "renewal_vs_new_business",
        "weighted_unweighted_arr_gap",
            "customer_cohorts",
            "headcount_plan_quality",
            "recruiting_slippage",
            "hiring_start_timing",
            "fully_loaded_role_cost",
            "plan_vs_actual_hiring_drift",
            "arr_movements",
        "customer_contracts",
        "scenario_math",
        "plan_vs_actual_deltas",
        "decision_outcomes",
    ]:
        assert slice_name in fpna_plan.focus_slices
        assert slice_name in prefs["focus_slices"]

    assert fpna_plan.focus_slices.index("forecast_assumptions") < fpna_plan.focus_slices.index("vendors")


def test_fpna_position_fixture_cites_forecast_specific_metrics() -> None:
    position = _fpna_position()
    joined_metrics = " ".join(position.cited_metrics).lower()
    joined_output = " ".join(
        [
            position.role_specific_lens,
            position.argument,
            *position.key_points,
            *position.forecast_assumptions,
            *position.scenario_sensitivities,
            *position.plan_vs_actual_deltas,
        ]
    ).lower()

    for required in [
        "unweighted pipeline arr",
        "weighted pipeline arr",
        "slipped close dates",
        "aged-stage opportunities",
        "stale opportunities",
        "probability overrides",
        "weighted/unweighted arr ratio",
        "renewal arr at risk",
        "fully loaded hiring plan",
        "slipped recruiting starts",
        "partial headcount approvals",
        "gross margin",
        "cac",
        "payback",
        "downside",
    ]:
        assert required in joined_metrics
    assert position.forecast_assumptions
    assert position.scenario_sensitivities
    assert position.plan_vs_actual_deltas
    assert "forecastable" in joined_output
    assert "total arr alone" in joined_output
    assert "compliance" not in joined_output
    assert "switching cost" not in joined_output
    assert "renewal notice" not in joined_output


def test_simulated_council_decision_uses_fpna_forecastability_gate() -> None:
    decision = _simulated_council_decision()
    fpna_plan = next(role_plan for role_plan in decision["decision_plan"]["role_plans"] if role_plan["role"] == "fpna")
    fpna_position = decision["positions"][0]

    assert decision["decision"] == "CONDITIONAL"
    assert decision["forecastability_gate"]["required"] is True
    assert "forecastability" in decision["reason"].lower()
    assert "forecast_assumptions" in fpna_plan["focus_slices"]
    assert "pipeline_by_stage" in fpna_plan["focus_slices"]
    assert fpna_position["forecast_assumptions"]
    assert fpna_position["scenario_sensitivities"]
    assert fpna_position["plan_vs_actual_deltas"]


def test_fpna_context_summary_exposes_pipeline_quality_from_crm_sources() -> None:
    quality = {
        "quality_issue_count": 42,
        "total_unweighted_arr": 7_580_000,
        "total_weighted_arr": 4_223_500,
        "weighted_to_unweighted_ratio": 0.5572,
        "slipped_close_date_count": 6,
        "stage_aging_count": 8,
        "stale_opportunity_count": 5,
        "probability_override_count": 9,
        "renewal_arr_at_risk": 830_000,
    }
    headcount_quality = {
        "issue_count": 24,
        "total_loaded_monthly_cost": 318_000,
        "recruiting_slip_count": 8,
        "partial_approval_count": 3,
        "unapproved_count": 2,
        "next_90_day_loaded_cost": 174_500,
    }
    summary = _fpna_context_summary(
        {
            "financials": {
                "pipeline_by_stage": [
                    {"stage": "Proposal", "arr": 1_000_000, "weighted_arr": 450_000},
                    {"stage": "Negotiation", "arr": 500_000, "weighted_arr": 350_000},
                ],
            },
            "operations": {
                "sources": [
                    {"source_type": "vendor_export", "pipeline_quality_summary": {"quality_issue_count": 0}},
                    {"source_type": "crm_opportunities", "pipeline_quality_summary": quality},
                    {"source_type": "headcount_plan", "headcount_quality_summary": headcount_quality},
                ]
            },
        }
    )

    assert summary["pipeline_quality"] == quality
    assert summary["headcount_plan_quality"] == headcount_quality
    assert summary["pipeline_arr"] == 1_500_000
    assert summary["weighted_pipeline_arr"] == 800_000
    assert summary["implied_pipeline_conversion"] == 0.5333
    questions = " ".join(summary["forecastability_questions"]).lower()
    assert "slipped" in questions
    assert "override-heavy" in questions
    assert "renewal protection" in questions
    assert "partially approved" in questions
    assert "downside hiring capacity" in questions


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} FP&A forecast contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
