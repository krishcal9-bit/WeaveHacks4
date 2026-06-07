"""
Deterministic checks for Reliability Auditor as an evaluator, not a participant.

No OpenAI or Redis calls: these tests lock the prompt/schema/self-improvement
contract so Reliability cannot quietly become a sixth approve/reject voice.
"""

from __future__ import annotations

from pydantic import ValidationError

from src.openai_council import _PROMPT_TEMPLATES, _PROMPT_VERSION_IDS
from src.self_improvement import prompt_directive_from_reliability_score
from src.structured_models import ReliabilityReport, ReliabilityScore


def _sample_score(agent_id: str = "treasury") -> ReliabilityScore:
    return ReliabilityScore(
        agent_id=agent_id,
        evidence_grounding=72,
        forecast_calibration=64,
        policy_compliance=81,
        debate_value=68,
        outcome_accuracy=70,
        confidence_calibration=66,
        trace_quality=92,
        reliability=72,
        rationale="Auditor scorecard cites missing cash-timing evidence and trace-backed policy coverage.",
        known_weaknesses=["Late-cash scenario was not stress-tested."],
        prompt_adjustment="Ask for invoice timing and cash-receipt delay evidence before scoring liquidity claims.",
        replay_cases=[
            "Replay with a 30-day customer cash delay and verify Treasury flags liquidity timing.",
            "Replay with missing invoice terms and require evidence-grounding penalty.",
        ],
        prompt_improvement_directive=(
            "Before taking any liquidity stance, cite cash forecast, invoice terms, renewal dates, "
            "and the effect of late cash receipts."
        ),
        promotion_gate="Promote only if replay improves evidence grounding and trace quality without policy regression.",
    )


def test_reliability_prompt_is_evaluator_not_participant() -> None:
    prompt = _PROMPT_TEMPLATES["reliability"].lower()

    for required in [
        "an evaluator, not a participant",
        "must not re-decide",
        "post-decision scorecard only",
        "normal_decision_prohibited",
        "audit_scope",
        "known_weaknesses",
        "replay_cases",
        "prompt_improvement_directive",
        "self-improvement loop",
        "trace quality",
        "policy_compliance",
        "debate_value",
    ]:
        assert required in prompt

    for forbidden_stance in ["approve/reject/conditional/defer", "produce a ruling"]:
        assert forbidden_stance in prompt

    assert _PROMPT_VERSION_IDS["reliability"] == "reliability.v3-evaluator-scorecard"


def test_reliability_schema_is_scorecard_not_decision_contract() -> None:
    report_schema = ReliabilityReport.model_json_schema()
    score_schema = ReliabilityScore.model_json_schema()

    assert {
        "audit_scope",
        "normal_decision_prohibited",
        "summary",
        "scores",
        "eval_dataset",
        "replay_plan",
        "prompt_improvement_directives",
        "promotion_gate",
    } <= set(report_schema["required"])

    assert {
        "evidence_grounding",
        "forecast_calibration",
        "policy_compliance",
        "debate_value",
        "outcome_accuracy",
        "confidence_calibration",
        "trace_quality",
        "known_weaknesses",
        "replay_cases",
        "prompt_improvement_directive",
    } <= set(score_schema["required"])

    for forbidden in ["stance", "decision", "ruling", "conditions"]:
        assert forbidden not in report_schema["properties"]
        assert forbidden not in score_schema["properties"]


def test_reliability_rejects_normal_approve_reject_stance_payload() -> None:
    try:
        ReliabilityReport(
            audit_scope="Evaluator scorecard only; Reliability is not ruling on the case.",
            normal_decision_prohibited=True,
            summary="Council scorecard is complete.",
            scores=[_sample_score()],
            eval_dataset="atlas-demo-replay",
            replay_plan=["Replay missing invoice terms."],
            prompt_improvement_directives=["Require cash timing evidence before liquidity conclusions."],
            promotion_gate="No prompt promotion without replay gain.",
            stance="APPROVE",
        )
    except ValidationError as exc:
        assert "stance" in str(exc)
    else:
        raise AssertionError("ReliabilityReport accepted a normal approve/reject stance.")


def test_reliability_scorecard_contains_replay_and_prompt_directive() -> None:
    report = ReliabilityReport(
        audit_scope="Evaluator scorecard only; Reliability does not approve, reject, condition, or defer.",
        normal_decision_prohibited=True,
        summary="Auditor found one low-confidence Treasury replay gap.",
        scores=[_sample_score()],
        eval_dataset="atlas-demo-replay",
        replay_plan=["Replay all agents with source-provenance gaps and compare trace-quality deltas."],
        prompt_improvement_directives=["Make each role cite its own weakest evidence slice before stance."],
        promotion_gate="Promote only after replay lifts grounding, calibration, and trace quality.",
    )

    assert report.normal_decision_prohibited is True
    assert "scorecard" in report.audit_scope.lower()
    assert report.scores[0].replay_cases
    assert report.scores[0].prompt_improvement_directive
    assert "APPROVE" not in report.model_dump_json()
    assert "REJECT" not in report.model_dump_json()


def test_self_improvement_prefers_auditor_prompt_directive() -> None:
    score = _sample_score().model_dump()
    assert prompt_directive_from_reliability_score(score) == score["prompt_improvement_directive"]

    score["prompt_improvement_directive"] = ""
    assert prompt_directive_from_reliability_score(score) == score["prompt_adjustment"]


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"  ok {test.__name__}")
    print(f"\n{len(ALL_TESTS)}/{len(ALL_TESTS)} Reliability evaluator contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
