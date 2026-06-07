"""Deterministic checks for realistic headcount/hiring evidence routing."""

from __future__ import annotations

from src.openai_council import _fpna_context_summary, _risk_context_summary, _treasury_context_summary


HEADCOUNT_QUALITY = {
    "records": 9,
    "total_headcount": 17,
    "issue_count": 24,
    "total_loaded_monthly_cost": 318_000,
    "next_90_day_loaded_cost": 174_500,
    "recruiting_slip_count": 8,
    "contractor_count": 3,
    "backfill_count": 2,
    "partial_approval_count": 3,
    "unapproved_count": 2,
    "department_mapping_drift_count": 6,
    "approval_risk_loaded_cost": 106_500,
}


def _context() -> dict:
    return {
        "financials": {
            "cash_on_hand": 4_200_000,
            "monthly_net_burn": 410_000,
            "runway_months": 10.2,
            "hiring_plan": [
                {"team": "Engineering", "roles": 5, "monthly_cost": 95_000, "start_month": "2026-08"},
            ],
            "pipeline_by_stage": [
                {"stage": "Contracting", "arr": 910_000, "weighted_arr": 774_000},
            ],
            "decision_outcomes": [
                {"owner": "FP&A", "predicted": "hiring starts in July", "actual": "8 starts slipped"},
            ],
        },
        "operations": {
            "sources": [
                {"source_type": "headcount_plan", "headcount_quality_summary": HEADCOUNT_QUALITY},
            ]
        },
    }


def test_hiring_quality_routes_to_each_role_with_distinct_lens() -> None:
    context = _context()

    treasury = _treasury_context_summary(context)
    fpna = _fpna_context_summary(context)
    risk = _risk_context_summary(context)

    assert treasury["hiring_cash_impact"] == HEADCOUNT_QUALITY
    assert fpna["headcount_plan_quality"] == HEADCOUNT_QUALITY
    assert risk["headcount_control_gaps"] == HEADCOUNT_QUALITY

    treasury_questions = " ".join(treasury["late_cash_questions"]).lower()
    fpna_questions = " ".join(fpna["forecastability_questions"]).lower()
    risk_questions = " ".join(risk["adversarial_questions"]).lower()

    assert "cash" in treasury_questions
    assert "fully loaded role costs" in treasury_questions or "fully loaded role cost" in treasury_questions
    assert "downside hiring capacity" in fpna_questions
    assert "partially approved" in fpna_questions
    assert "approval evidence" in risk_questions
    assert "unplanned headcount" in risk_questions


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} headcount hiring contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
