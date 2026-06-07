"""
Deterministic checks for the role-distinction eval harness.

No OpenAI or Redis calls: these tests verify that the scorer catches persona
collapse and emits a Redis/JSON-friendly artifact.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from src.role_distinction_eval import (
    COUNCIL_ROLES,
    PASSING_SCORE,
    RepresentativeDecisionCase,
    RoleDistinctionInput,
    capture_role_distinction_eval,
    persist_role_distinction_report,
    representative_decision_cases,
    run_role_distinction_eval,
)


def test_representative_role_distinction_harness_passes() -> None:
    report = run_role_distinction_eval(source="test")

    assert report.passed is True
    assert report.case_count >= 3
    assert report.overall_score >= PASSING_SCORE
    assert set(report.role_average_scores) == set(COUNCIL_ROLES)
    assert all(case.passed for case in report.cases)
    assert not any(case.collapse_flags for case in report.cases)


def test_collapsed_generic_outputs_fail_the_guard() -> None:
    generic = (
        "Support because the decision is affordable and strategically useful. "
        "Review the numbers and proceed if leadership agrees."
    )
    case = RepresentativeDecisionCase(
        id="collapsed-generic",
        decision="Renew a vendor while considering cash, forecast, risk, and terms.",
        decision_type="collapse_test",
        role_outputs=[
            RoleDistinctionInput(
                role=role,
                stance="support",
                headline="Generic support",
                argument=generic,
                key_points=["Check the numbers", "Proceed if aligned"],
                evidence_used=["financials"],
            )
            for role in COUNCIL_ROLES
        ],
    )

    report = run_role_distinction_eval([case], source="collapse-test")
    result = report.cases[0]

    assert report.passed is False
    assert result.overall_score < PASSING_SCORE
    assert result.collapse_flags
    assert any("below role threshold" in flag or "overlap" in flag for flag in result.collapse_flags)


def test_role_distinction_artifact_is_json_and_redis_friendly() -> None:
    report = run_role_distinction_eval(representative_decision_cases()[:1], source="artifact-test")

    with TemporaryDirectory() as tmp:
        artifact = Path(tmp) / "role-distinction.json"
        meta = persist_role_distinction_report(report, artifact_path=artifact, redis=False, publish=False)
        payload = json.loads(artifact.read_text(encoding="utf-8"))

    json.dumps(payload)
    assert meta["artifact_path"].endswith("role-distinction.json")
    assert payload["id"] == report.id
    assert payload["cases"][0]["role_scores"]
    assert payload["thresholds"]["overall"] == PASSING_SCORE


def test_live_capture_scores_all_roles_from_council_state() -> None:
    positions = [
        output.model_dump()
        for output in representative_decision_cases()[0].role_outputs
        if output.role in {"treasury", "fpna", "risk", "procurement"}
    ]
    recommendation = {
        "decision": "CONDITIONAL",
        "ruling": "CFO ruling resolves dissent into explicit conditions.",
        "rationale": "Weigh analyst influence, dissent, conditions, confidence, and runway impact basis.",
        "conditions": ["Close unresolved assumption before spend."],
        "assumptions_converted_to_conditions": ["Runway impact basis must be refreshed."],
        "evidence_used": ["analyst_influence", "dissent", "conditions", "runway_impact_basis", "governance"],
    }
    reliability_scores = [
        {
            "agent_id": role,
            "reliability": 84,
            "known_weaknesses": ["trace quality gap", "missing replay"],
        }
        for role in COUNCIL_ROLES
    ]

    with TemporaryDirectory() as tmp:
        meta = capture_role_distinction_eval(
            decision="Live-style decision",
            positions=positions,
            recommendation=recommendation,
            reliability_scores=reliability_scores,
            learning_report={"summary": "Evaluator scorecard audits evidence grounding and prompt directives."},
            source="test-live",
            artifact_path=Path(tmp) / "live-role-distinction.json",
            persist=False,
            publish=False,
        )

    report = meta["report"]
    roles = {score["role"] for score in report["cases"][0]["role_scores"]}
    assert roles == set(COUNCIL_ROLES)
    assert report["case_count"] == 1
    assert report["overall_score"] >= 70


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} role-distinction eval checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
