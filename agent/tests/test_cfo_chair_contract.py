"""
Smoke checks for the CFO-chair council contract.

These tests are intentionally deterministic: no OpenAI call and no Redis read.
They verify that the OpenAI-native structured output contract distinguishes the
CFO chair from analyst seats and that a final recommendation payload can carry
the required board-ruling artifacts plus tool-computed runway impact.
"""

from __future__ import annotations

from src.openai_council import ROLE_DIRECTIVES, _PROMPT_TEMPLATES
from src.structured_models import Position, Recommendation


def _sample_recommendation() -> Recommendation:
    return Recommendation(
        decision="CONDITIONAL",
        ruling="CONDITIONAL approve only if procurement locks the renewal cap and Risk clears the audit blocker.",
        confidence=78,
        rationale=(
            "The weighted council supports moving forward, but the unresolved audit dependency and renewal "
            "notice window keep this below high confidence. The ruling preserves pipeline upside while capping "
            "cash exposure."
        ),
        tradeoffs=[
            "Accept modest runway compression to protect enterprise conversion timing.",
            "Trade vendor continuity against switching leverage and audit exposure.",
        ],
        analyst_influence=[
            {"role": "treasury", "influence_weight": 30, "effect_on_ruling": "Set the runway guardrail."},
            {"role": "fpna", "influence_weight": 25, "effect_on_ruling": "Supported the revenue timing case."},
            {"role": "risk", "influence_weight": 25, "effect_on_ruling": "Converted audit uncertainty into a condition."},
            {"role": "procurement", "influence_weight": 20, "effect_on_ruling": "Bound approval to renewal terms."},
        ],
        dissent="Risk opposed approval until the audit blocker is cleared; the CFO accepted that dissent as a condition.",
        key_risks=["Enterprise deal slips before the control gap closes."],
        conditions=[
            "Risk must clear the audit blocker before signature.",
            "Procurement must cap incremental spend at $40K per month.",
        ],
        policy_citations=["gov-board-notify", "gov-data-security", "pol-runway"],
        assumptions_converted_to_conditions=[
            "Unverified close date becomes a condition tied to enterprise pipeline confirmation.",
        ],
        runway_impact_basis="$40K incremental monthly spend, $0 one-time cost, $20K added monthly revenue.",
        estimated_monthly_cost=40_000.0,
        estimated_one_time_cost=0.0,
        estimated_added_monthly_revenue=20_000.0,
    )


def test_cfo_prompt_is_board_chair_not_functional_analyst() -> None:
    cfo_prompt = _PROMPT_TEMPLATES["cfo"].lower()

    for required in [
        "chair",
        "do not sound like treasury",
        "frame the tradeoffs",
        "influence weights",
        "explicit conditions",
        "resolve dissent",
        "runway_impact_basis",
    ]:
        assert required in cfo_prompt


def test_recommendation_contract_contains_chair_fields() -> None:
    required = set(Recommendation.model_json_schema()["required"])
    expected = {
        "decision",
        "ruling",
        "confidence",
        "conditions",
        "dissent",
        "tradeoffs",
        "analyst_influence",
        "policy_citations",
        "assumptions_converted_to_conditions",
        "runway_impact_basis",
    }

    assert expected <= required

    rec = _sample_recommendation()
    assert rec.ruling
    assert rec.confidence == 78
    assert rec.conditions
    assert {"gov-board-notify", "gov-data-security"} <= set(rec.policy_citations)
    assert rec.dissent
    assert rec.analyst_influence


def test_final_cfo_payload_contains_quantified_runway_impact() -> None:
    rec = _sample_recommendation().model_dump()
    final_payload = {
        **rec,
        "impact": {
            "current_runway_months": 10.2,
            "scenario_runway_months": 9.7,
            "delta_months": -0.5,
        },
        "runway_impact_summary": "Runway: 10.2 months -> 9.7 months (-0.5 months)",
    }

    assert final_payload["ruling"]
    assert isinstance(final_payload["confidence"], int)
    assert final_payload["conditions"]
    assert "gov-board-notify" in final_payload["policy_citations"]
    assert final_payload["dissent"]
    assert final_payload["impact"]["delta_months"] == -0.5
    assert "10.2" in final_payload["runway_impact_summary"]
    assert "9.7" in final_payload["runway_impact_summary"]


def test_analyst_turns_remain_role_specific() -> None:
    position_schema = Position.model_json_schema()
    assert "role_specific_lens" in position_schema["required"]
    for chair_only in ["ruling", "dissent", "analyst_influence", "runway_impact_basis"]:
        assert chair_only not in position_schema["properties"]

    for role, directive in ROLE_DIRECTIVES.items():
        pos = Position(
            role_specific_lens=f"{role} lens only: {directive}",
            stance="conditional",
            headline=f"{role} has a bounded view",
            argument="This seat cites its own evidence and leaves the final ruling to the CFO.",
            key_points=["Stay in lane.", "Cite the role's figures."],
            cited_metrics=["$40K monthly spend", "10.2 mo runway"],
            evidence_used=[directive],
            forecast_assumptions=[],
            scenario_sensitivities=[],
            plan_vs_actual_deltas=[],
            control_findings=[],
            missing_evidence_requests=[],
            approval_or_policy_blockers=[],
            negotiation_levers=[],
        )
        assert role in pos.role_specific_lens
        assert "final ruling" in pos.argument.lower()


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} CFO chair contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
