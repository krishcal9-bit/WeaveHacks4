"""
Deterministic checks for role-specific cross-examination behavior.

No OpenAI or Redis calls: these tests verify the debate contract carries typed
challenge lanes so cross-exam does not collapse into generic rebuttals.
"""

from __future__ import annotations

from src.openai_council import (
    _PROMPT_TEMPLATES,
    ensure_role_specific_exchanges,
    role_challenge_profile,
)
from src.structured_models import ChallengeFinding, ChallengePanelReport, Exchange


def _complex_positions() -> list[dict]:
    return [
        {
            "agent": "treasury",
            "role": "Treasury",
            "stance": "conditional",
            "headline": "Cash timing needs protection",
            "cited_metrics": ["10.2 months runway", "$410K monthly burn", "$5M bridge close delay"],
        },
        {
            "agent": "fpna",
            "role": "FP&A",
            "stance": "support",
            "headline": "Forecast case is plausible",
            "cited_metrics": ["$1.4M weighted pipeline ARR", "38% stage probability", "11 month CAC payback"],
        },
        {
            "agent": "risk",
            "role": "Risk & Audit",
            "stance": "conditional",
            "headline": "Controls need evidence",
            "cited_metrics": ["AUD-21 high severity", "3 approval steps", "2 reconciliation gaps"],
        },
        {
            "agent": "procurement",
            "role": "Procurement",
            "stance": "conditional",
            "headline": "Terms need leverage",
            "cited_metrics": ["45-day renewal notice", "$70K switching cost", "14% benchmark gap"],
        },
    ]


def _challenge_report() -> dict:
    return {
        "summary": "Complex decision requires lane-specific challenge coverage.",
        "overall_grounding": 74,
        "unresolved_gaps": ["FP&A: forecast probability is not reconciled to late-cash timing."],
        "findings": [
            {
                "role": role,
                **role_challenge_profile(role),
                "cited_enough_numbers": True,
                "grounding_score": 75,
                "strongest_number": "role metric",
                "missing_evidence": [],
                "challenge": f"{role_challenge_profile(role)['challenge_label']}: test the role-specific weakness.",
            }
            for role in ["treasury", "fpna", "risk", "procurement"]
        ],
    }


def test_cross_exam_prompt_requires_distinct_role_challenge_lanes() -> None:
    prompt = _PROMPT_TEMPLATES["debate"].lower()

    for required in [
        "treasury challenges cash timing",
        "fp&a challenges forecast assumptions",
        "risk & audit challenges controls",
        "procurement challenges vendor terms",
        "cfo asks one synthesis question",
        "reliability must not speak",
        "cash_timing",
        "forecast_assumptions",
        "controls_policy",
        "vendor_terms",
        "synthesis_question",
    ]:
        assert required in prompt


def test_exchange_schema_carries_visible_challenge_metadata() -> None:
    required = set(Exchange.model_json_schema()["required"])
    assert {"from_role", "to_role", "challenge_type", "challenge_label", "challenge_lens", "point"} <= required

    exchange = Exchange(
        from_role="treasury",
        to_role="fpna",
        challenge_type="cash_timing",
        challenge_label="Cash timing",
        challenge_lens="cash runway, late receipts, and payment terms",
        point="If cash arrives 30 days late, how does the forecast still preserve runway?",
    )

    assert exchange.challenge_type == "cash_timing"
    assert exchange.challenge_label == "Cash timing"


def test_challenge_panel_findings_carry_role_specific_lanes() -> None:
    findings = [
        ChallengeFinding(
            role=role,
            **role_challenge_profile(role),
            cited_enough_numbers=True,
            grounding_score=80,
            strongest_number="role metric",
            missing_evidence=[],
            challenge=f"{role_challenge_profile(role)['challenge_label']}: test this lane.",
        )
        for role in ["treasury", "fpna", "risk", "procurement"]
    ]
    report = ChallengePanelReport(
        summary="Each role has a lane-specific follow-up.",
        overall_grounding=80,
        findings=findings,
        unresolved_gaps=[],
    )

    labels = {finding.challenge_label for finding in report.findings}
    assert {"Cash timing", "Forecast assumptions", "Controls / policy", "Vendor terms"} <= labels


def test_complex_decision_gets_at_least_three_role_specific_challenge_types() -> None:
    exchanges = ensure_role_specific_exchanges(
        [
            {
                "from_role": "Treasury",
                "to_role": "FP&A",
                "point": "What happens if the customer cash arrives a month late?",
            }
        ],
        positions=_complex_positions(),
        challenge_report=_challenge_report(),
    )

    types = {exchange["challenge_type"] for exchange in exchanges}
    challengers = {exchange["from_role"] for exchange in exchanges}

    assert len(types) >= 3
    assert {"cash_timing", "forecast_assumptions", "controls_policy", "vendor_terms"} <= types
    assert "synthesis_question" in types
    assert {"treasury", "fpna", "risk", "procurement", "cfo"} <= challengers
    assert "reliability" not in challengers
    assert all(exchange["challenge_label"] for exchange in exchanges)
    assert all(exchange["challenge_lens"] for exchange in exchanges)


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} role-specific cross-exam checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
