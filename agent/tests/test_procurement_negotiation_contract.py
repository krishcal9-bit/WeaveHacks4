"""
Deterministic checks for Procurement's vendor/commercial negotiation lane.

No OpenAI or Redis calls: this verifies Procurement is prompted, planned, and
fixture-tested around supplier leverage, contract terms, and negotiation levers
instead of generic finance language.
"""

from __future__ import annotations

from src.openai_council import (
    ROLE_DIRECTIVES,
    _PROMPT_TEMPLATES,
    enforce_role_specific_evidence_plan,
    procurement_evidence_preferences,
)
from src.structured_models import DecisionPlan, DecisionType, Position, RoleEvidencePlan


NEGOTIATION_TERMS = {
    "supplier leverage",
    "contract terms",
    "auto-renewal",
    "renewal dates",
    "price benchmarks",
    "consolidation",
    "switching cost",
    "slas",
    "termination clauses",
    "volume discounts",
    "contract aliases",
    "billing cadence",
    "tiered pricing",
    "termination penalties",
    "owner changes",
    "negotiation strategy",
}


def _generic_plan() -> DecisionPlan:
    return DecisionPlan(
        decision_type=DecisionType.vendor_renewal,
        title="Datadog renewal",
        summary="Decide whether to renew Datadog.",
        entities=["Datadog", "$180K"],
        required_facts=[],
        assumptions=[],
        follow_up_questions=[],
        role_plans=[
            RoleEvidencePlan(
                role="procurement",
                tools=["get_company_financials"],
                policy_queries=["generic spend review"],
                focus_slices=["cash_forecast"],
                prior_decisions=[],
                rationale="Generic finance review.",
            ),
        ],
        decision_specific_focus=["renewal cost"],
    )


def _procurement_position() -> Position:
    return Position(
        role_specific_lens=(
            "Procurement commercial-negotiation lens: supplier leverage, renewal terms, "
            "price benchmarks, switching cost, SLAs, and negotiation strategy only."
        ),
        stance="conditional",
        headline="Negotiate before renewal",
        argument=(
            "Support renewal only after using the 45-day notice window and $70K switching cost BATNA "
            "to cap Datadog at $162K ARR, add SLA credits, and remove auto-renewal."
        ),
        key_points=[
            "Use renewal timing and consolidation threat as supplier leverage.",
            "Make the ask explicit: cap price, remove auto-renewal, and add SLA credits.",
        ],
        cited_metrics=[
            "$180K annual contract",
            "$162K renewal cap",
            "45-day termination notice",
            "$70K switching cost",
            "14% price benchmark gap",
            "3 observability vendors eligible for consolidation",
        ],
        evidence_used=[
            "vendors",
            "vendor_exports",
            "invoices",
            "purchase_orders",
            "contract_metadata",
            "procurement_notes",
            "vendor_clauses",
            "prior_renewal_outcomes",
        ],
        forecast_assumptions=[],
        scenario_sensitivities=[],
        plan_vs_actual_deltas=[],
        control_findings=[],
        missing_evidence_requests=[],
        approval_or_policy_blockers=[],
        negotiation_levers=[
            "45-day termination notice creates urgency before auto-renewal.",
            "$70K switching cost sets the walk-away threshold and BATNA.",
            "14% price benchmark gap supports a $162K renewal cap.",
            "Consolidate observability vendors and request SLA credits for production incidents.",
            "Use contract aliases and annual-billing/monthly-invoice mismatch to force invoice cleanup before signing.",
            "Use the $30K termination penalty and Platform Ops owner change to set the escalation path.",
        ],
    )


def test_procurement_prompt_is_vendor_negotiator_not_generic_finance() -> None:
    directive = ROLE_DIRECTIVES["procurement"].lower()
    classifier = _PROMPT_TEMPLATES["classifier"].lower()
    analyst_prompt = _PROMPT_TEMPLATES["procurement"].format(
        label="Procurement",
        company="Northwind Robotics",
        stage="Series A",
        mandate="vendor negotiation",
        role_directive=ROLE_DIRECTIVES["procurement"],
        decision_type="vendor_renewal",
        focus="renewal terms",
    ).lower()

    for term in NEGOTIATION_TERMS:
        assert term in directive
    assert "never sound like generic finance" in directive
    assert "do not lead with runway" in directive
    assert "procurement's evidence plan must prefer vendor exports" in classifier
    assert "prior renewal outcomes" in classifier
    assert "negotiation_levers" in analyst_prompt


def test_procurement_evidence_plan_prefers_commercial_sources() -> None:
    plan = enforce_role_specific_evidence_plan(_generic_plan())
    procurement_plan = next(role_plan for role_plan in plan.role_plans if role_plan.role == "procurement")
    prefs = procurement_evidence_preferences()

    for tool in [
        "list_vendors",
        "list_invoices",
        "list_purchase_orders",
        "search_finance_knowledge",
        "list_operations_sources",
        "get_reconciliation_summary",
        "list_open_discrepancies",
    ]:
        assert tool in procurement_plan.tools
        assert tool in prefs["tools"]

    for slice_name in [
        "vendors",
        "vendor_exports",
        "invoices",
        "purchase_orders",
        "contract_metadata",
        "contract_aliases",
        "procurement_notes",
        "prior_renewal_outcomes",
        "vendor_clauses",
        "price_benchmarks",
        "volume_discounts",
        "tiered_pricing",
        "billing_frequency",
        "billing_terms",
        "switching_costs",
        "slas",
        "sla_credits",
        "termination_clauses",
        "termination_penalties",
        "notice_windows",
        "owner_changes",
    ]:
        assert slice_name in procurement_plan.focus_slices
        assert slice_name in prefs["focus_slices"]

    assert procurement_plan.focus_slices.index("vendors") < procurement_plan.focus_slices.index("cash_forecast")


def test_procurement_position_fixture_surfaces_negotiation_levers() -> None:
    position = _procurement_position()
    joined_metrics = " ".join(position.cited_metrics).lower()
    joined_output = " ".join([position.argument, *position.negotiation_levers]).lower()

    for required in ["annual contract", "termination notice", "switching cost", "price benchmark", "consolidation"]:
        assert required in joined_metrics
    assert position.negotiation_levers
    assert "auto-renewal" in joined_output
    assert "sla credits" in joined_output
    assert "contract aliases" in joined_output
    assert "monthly-invoice mismatch" in joined_output
    assert "termination penalty" in joined_output
    assert "owner change" in joined_output
    assert "batna" in joined_output
    assert "runway" not in joined_output
    assert "cac" not in joined_output
    assert "audit" not in joined_output


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} Procurement negotiation contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
