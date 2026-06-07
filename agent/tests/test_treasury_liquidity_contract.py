"""
Deterministic checks for Treasury's liquidity-mechanics lane.

No OpenAI or Redis calls: this verifies that Treasury is prompted, planned, and
fixture-tested around cash timing rather than FP&A or Procurement language.
"""

from __future__ import annotations

from src.openai_council import (
    ROLE_DIRECTIVES,
    _PROMPT_TEMPLATES,
    enforce_treasury_liquidity_plan,
    treasury_evidence_preferences,
)
from src.structured_models import DecisionPlan, DecisionType, Position, RoleEvidencePlan


LIQUIDITY_TERMS = {
    "cash",
    "runway",
    "liquidity",
    "payment terms",
    "working capital",
    "renewal",
    "financing",
    "late",
    "hiring start timing",
    "fully loaded hiring cash impact",
    "contractor cash timing",
}


def _generic_plan() -> DecisionPlan:
    return DecisionPlan(
        decision_type=DecisionType.vendor_renewal,
        title="Datadog renewal",
        summary="Decide whether to renew Datadog.",
        entities=["Datadog"],
        required_facts=[],
        assumptions=[],
        follow_up_questions=[],
        role_plans=[
            RoleEvidencePlan(
                role="treasury",
                tools=["get_company_financials"],
                policy_queries=["runway guardrail"],
                focus_slices=["pipeline_by_stage"],
                prior_decisions=[],
                rationale="Generic liquidity review.",
            ),
            RoleEvidencePlan(
                role="fpna",
                tools=["get_company_financials"],
                policy_queries=[],
                focus_slices=["pipeline_by_stage"],
                prior_decisions=[],
                rationale="Forecast review.",
            ),
        ],
        decision_specific_focus=["renewal cost"],
    )


def test_treasury_prompt_is_liquidity_mechanics_not_fpna_or_procurement() -> None:
    directive = ROLE_DIRECTIVES["treasury"].lower()
    analyst_prompt = _PROMPT_TEMPLATES["treasury"].format(
        label="Treasury",
        company="Northwind Robotics",
        stage="Series A",
        mandate="cash runway",
        role_directive=ROLE_DIRECTIVES["treasury"],
        decision_type="vendor_renewal",
        focus="cash timing",
    ).lower()

    for term in LIQUIDITY_TERMS:
        assert term in directive
    assert "what happens if cash arrives late" in directive
    assert "do not talk like fp&a" in directive
    assert "do not talk like procurement" in directive
    assert "cash runway" in analyst_prompt
    assert "payment terms" in analyst_prompt


def test_treasury_evidence_plan_prefers_liquidity_sources() -> None:
    plan = enforce_treasury_liquidity_plan(_generic_plan())
    treasury_plan = next(role_plan for role_plan in plan.role_plans if role_plan.role == "treasury")
    prefs = treasury_evidence_preferences()

    for tool in [
        "compute_runway",
        "list_invoices",
        "list_operations_sources",
        "search_scenarios",
        "run_plan_sensitivity",
    ]:
        assert tool in treasury_plan.tools
        assert tool in prefs["tools"]

    for slice_name in [
        "cash_forecast",
        "cash_history",
        "ledger",
        "invoices",
        "payment_terms",
        "vendor_renewal_dates",
        "financing_scenarios",
        "headcount_start_dates",
        "fully_loaded_hiring_cash",
        "contractor_cash_timing",
    ]:
        assert slice_name in treasury_plan.focus_slices
        assert slice_name in prefs["focus_slices"]

    assert treasury_plan.focus_slices.index("cash_forecast") < treasury_plan.focus_slices.index("pipeline_by_stage")


def test_treasury_position_fixture_cites_liquidity_specific_metrics() -> None:
    position = Position(
        role_specific_lens=(
            "Treasury liquidity lens: cash runway, invoice timing, renewal payment schedule, "
            "and financing-close delay only."
        ),
        stance="conditional",
        headline="Protect the cash buffer",
        argument=(
            "Support only if the Datadog payment stays monthly and the $5M bridge is not delayed; "
            "a 30-day cash slip compresses runway before the renewal notice window closes."
        ),
        key_points=[
            "Keep renewal cash outflow monthly, not annual prepay.",
            "Recheck runway if customer invoices arrive 30 days late.",
        ],
        cited_metrics=[
            "$4.8M cash on hand",
            "$410K monthly net burn",
            "10.2 months runway",
            "$21.5K Datadog invoice due 2026-06-30",
            "45-day renewal notice",
            "$5M bridge close delay",
            "$174.5K/mo fully loaded hiring starts within 90 days",
        ],
        evidence_used=[
            "cash_forecast",
            "ledger",
            "invoices",
            "payment_terms",
            "vendor_renewal_dates",
            "financing_scenarios",
            "headcount_start_dates",
            "fully_loaded_hiring_cash",
        ],
        forecast_assumptions=[],
        scenario_sensitivities=[],
        plan_vs_actual_deltas=[],
        control_findings=[],
        missing_evidence_requests=[],
        approval_or_policy_blockers=[],
        negotiation_levers=[],
    )

    joined_metrics = " ".join(position.cited_metrics).lower()
    for required in ["cash", "burn", "runway", "invoice", "renewal", "bridge", "hiring"]:
        assert required in joined_metrics
    assert "roi" not in joined_metrics
    assert "switching cost" not in joined_metrics
    assert "payment_terms" in position.evidence_used
    assert "financing_scenarios" in position.evidence_used
    assert "fully_loaded_hiring_cash" in position.evidence_used


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} Treasury liquidity contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
