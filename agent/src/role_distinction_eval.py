"""
Role-distinction eval harness for the Atlas finance council.

This guard is deliberately deterministic: it can run in CI without OpenAI, and
it can also score a completed live council run from the real transcript. The
goal is not to judge business correctness; it detects persona collapse by
checking whether each role uses its own vocabulary, evidence categories, stance
logic, and failure modes.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any

import weave
from pydantic import BaseModel, Field

from src import redis_layer as R
from src.env import redact_secrets
from src.weave_eval import EVAL_NS, _new_id, _now, publish_to_weave, weave_links

ROLE_DISTINCTION_PREFIX = f"{EVAL_NS}:role_distinction:"
ROLE_DISTINCTION_INDEX = f"{EVAL_NS}:role_distinction_index"
LATEST_ROLE_DISTINCTION = f"{EVAL_NS}:role_distinction:latest"
ROLE_DISTINCTION_STREAM = "role_distinction_evals"
DEFAULT_ARTIFACT = Path(__file__).resolve().parents[1] / "artifacts" / "role_distinction" / "role-distinction-latest.json"

COUNCIL_ROLES: tuple[str, ...] = ("cfo", "treasury", "fpna", "risk", "procurement", "reliability")
ANALYST_ROLES: tuple[str, ...] = ("treasury", "fpna", "risk", "procurement")
PASSING_SCORE = 78
ROLE_PASSING_SCORE = 72

STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "into", "only", "role", "case",
    "decision", "support", "conditional", "oppose", "must", "before", "after", "against",
}

ROLE_EXPECTATIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "cfo": {
        "vocabulary": ("ruling", "tradeoff", "condition", "dissent", "confidence", "influence", "runway impact"),
        "evidence": ("analyst_influence", "dissent", "conditions", "runway_impact_basis", "governance"),
        "stance_logic": ("board-ready", "resolve", "weigh", "condition", "rule", "confidence"),
        "failure_modes": ("unresolved assumption", "dissent", "low confidence", "condition breach", "runway impact"),
    },
    "treasury": {
        "vocabulary": ("cash", "runway", "liquidity", "payment terms", "working capital", "late cash", "financing delay"),
        "evidence": ("cash_forecast", "ledger", "invoices", "payment_terms", "vendor_renewal_dates", "financing_scenarios"),
        "stance_logic": ("if cash arrives late", "cash buffer", "burn sensitivity", "monthly outflow", "runway floor"),
        "failure_modes": ("late cash", "annual prepay", "bridge delay", "minimum cash", "working capital"),
    },
    "fpna": {
        "vocabulary": ("forecast", "arr", "pipeline probability", "roi", "cac/payback", "gross margin", "sensitivity"),
        "evidence": ("forecast_assumptions", "pipeline_by_stage", "arr_movements", "customer_contracts", "scenario_math", "plan_vs_actual_deltas"),
        "stance_logic": ("forecastable", "conversion", "base case", "downside", "payback", "variance"),
        "failure_modes": ("conversion miss", "margin compression", "payback slip", "forecast range", "plan variance"),
    },
    "risk": {
        "vocabulary": ("policy", "approval", "audit trail", "control", "provenance", "compliance", "hidden obligation"),
        "evidence": ("board_policies", "governance_rules", "approval_route", "audit_findings", "reconciliation_discrepancies", "source_provenance", "security_evidence"),
        "stance_logic": ("blocker", "missing evidence", "exception", "attestation", "source quality", "condition support"),
        "failure_modes": ("missing approval", "audit gap", "policy exception", "provenance weakness", "fraud risk"),
    },
    "procurement": {
        "vocabulary": ("supplier", "contract", "renewal", "auto-renewal", "benchmark", "switching cost", "sla", "discount"),
        "evidence": ("vendor_exports", "invoices", "contract_metadata", "procurement_notes", "price_benchmarks", "termination_clauses", "slas", "prior_renewal_outcomes"),
        "stance_logic": ("negotiate", "leverage", "counter", "termination", "consolidation", "commercial ask"),
        "failure_modes": ("auto-renewal", "notice window", "switching cost", "sla miss", "benchmark gap"),
    },
    "reliability": {
        "vocabulary": ("scorecard", "evidence grounding", "calibration", "trace quality", "replay", "prompt directive", "weakness"),
        "evidence": ("agent_scorecards", "trace_metadata", "replay_cases", "prompt_improvement_directives", "grounding_gaps", "known_weaknesses"),
        "stance_logic": ("evaluator", "not re-decide", "score", "audit", "penalize", "replay"),
        "failure_modes": ("normal stance", "trace gap", "generic debate", "missing replay", "prompt drift"),
    },
}


class RoleDistinctionInput(BaseModel):
    role: str
    stance: str = ""
    headline: str = ""
    argument: str = ""
    key_points: list[str] = Field(default_factory=list)
    cited_metrics: list[str] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    forecast_assumptions: list[str] = Field(default_factory=list)
    scenario_sensitivities: list[str] = Field(default_factory=list)
    plan_vs_actual_deltas: list[str] = Field(default_factory=list)
    control_findings: list[str] = Field(default_factory=list)
    missing_evidence_requests: list[str] = Field(default_factory=list)
    approval_or_policy_blockers: list[str] = Field(default_factory=list)
    negotiation_levers: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)


class RepresentativeDecisionCase(BaseModel):
    id: str
    decision: str
    decision_type: str
    role_outputs: list[RoleDistinctionInput]


class RoleDistinctionScore(BaseModel):
    role: str
    score: int = Field(ge=0, le=100)
    vocabulary_score: int = Field(ge=0, le=100)
    evidence_score: int = Field(ge=0, le=100)
    stance_logic_score: int = Field(ge=0, le=100)
    failure_mode_score: int = Field(ge=0, le=100)
    expected_vocabulary_hits: list[str] = Field(default_factory=list)
    evidence_category_hits: list[str] = Field(default_factory=list)
    stance_logic_hits: list[str] = Field(default_factory=list)
    failure_mode_hits: list[str] = Field(default_factory=list)
    contamination_hits: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class PairwiseRoleSimilarity(BaseModel):
    role_a: str
    role_b: str
    vocabulary_jaccard: float
    evidence_jaccard: float
    collapse_risk: bool


class RoleDistinctionCaseResult(BaseModel):
    id: str
    decision: str
    decision_type: str
    overall_score: int = Field(ge=0, le=100)
    passed: bool
    role_scores: list[RoleDistinctionScore]
    pairwise_similarity: list[PairwiseRoleSimilarity]
    collapse_flags: list[str] = Field(default_factory=list)


class RoleDistinctionReport(BaseModel):
    id: str
    created_at: str
    source: str
    overall_score: int = Field(ge=0, le=100)
    passed: bool
    case_count: int
    role_average_scores: dict[str, int]
    cases: list[RoleDistinctionCaseResult]
    artifact_path: str | None = None
    redis: dict[str, Any] = Field(default_factory=dict)
    weave: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, int] = Field(default_factory=lambda: {"overall": PASSING_SCORE, "role": ROLE_PASSING_SCORE})


def _norm(value: str) -> str:
    return (value or "").lower().replace("&", "and")


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9_/-]{2,}", _norm(text))
        if token not in STOPWORDS
    }


def _text(role_output: RoleDistinctionInput) -> str:
    parts: list[str] = [
        role_output.stance,
        role_output.headline,
        role_output.argument,
        *role_output.key_points,
        *role_output.cited_metrics,
        *role_output.evidence_used,
        *role_output.forecast_assumptions,
        *role_output.scenario_sensitivities,
        *role_output.plan_vs_actual_deltas,
        *role_output.control_findings,
        *role_output.missing_evidence_requests,
        *role_output.approval_or_policy_blockers,
        *role_output.negotiation_levers,
        *role_output.failure_modes,
    ]
    return " ".join(str(part) for part in parts if part)


def _hits(needles: tuple[str, ...], haystack: str) -> list[str]:
    normalized = _norm(haystack)
    return [needle for needle in needles if _norm(needle) in normalized]


def _score_hits(hits: list[str], expected: tuple[str, ...], *, min_hits: int) -> int:
    if not expected:
        return 100
    denominator = min(len(expected), min_hits)
    return min(100, round(100 * len(set(hits)) / max(1, denominator)))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    return round(len(left & right) / max(1, len(left | right)), 3)


def _role_output(role: str, case: str) -> RoleDistinctionInput:
    """Representative, non-model role output used by the deterministic harness."""
    suffix = {
        "vendor_renewal": "Datadog renewal",
        "growth_investment": "enterprise growth spend",
        "security_blocker": "security blocker",
    }.get(case, case)
    fixtures: dict[str, RoleDistinctionInput] = {
        "cfo": RoleDistinctionInput(
            role="cfo",
            stance="conditional",
            headline=f"Conditional ruling on {suffix}",
            argument="CFO ruling weighs analyst influence, resolves dissent, and converts unresolved assumptions into board-ready conditions tied to runway impact.",
            key_points=["Condition approval on the controlling dissent and confidence threshold.", "Runway impact basis must stay explicit."],
            evidence_used=["analyst_influence", "dissent", "conditions", "runway_impact_basis", "governance"],
            failure_modes=["unresolved assumption", "dissent", "low confidence"],
        ),
        "treasury": RoleDistinctionInput(
            role="treasury",
            stance="conditional",
            headline="Protect liquidity timing",
            argument="Treasury supports only if cash runway, payment terms, working capital timing, and late cash receipts preserve the cash buffer.",
            key_points=["If cash arrives late, monthly outflow cannot breach the runway floor.", "No annual prepay without financing delay sensitivity."],
            cited_metrics=["10.2 months runway", "$410K monthly net burn", "45-day payment terms"],
            evidence_used=["cash_forecast", "ledger", "invoices", "payment_terms", "vendor_renewal_dates", "financing_scenarios"],
            failure_modes=["late cash", "annual prepay", "bridge delay"],
        ),
        "fpna": RoleDistinctionInput(
            role="fpna",
            stance="conditional",
            headline="Forecastability gates the case",
            argument="FP&A supports only if ARR movement, pipeline probability, ROI, CAC/payback, gross margin, and sensitivity ranges make the case forecastable.",
            key_points=["Conversion and margin assumptions must hold in the downside.", "Plan-vs-actual variance decides whether the base case is credible."],
            cited_metrics=["$1.7M weighted pipeline ARR", "42% conversion", "6.5 month CAC/payback", "78% gross margin"],
            evidence_used=["forecast_assumptions", "pipeline_by_stage", "arr_movements", "customer_contracts", "scenario_math", "plan_vs_actual_deltas"],
            forecast_assumptions=["Base case depends on 42% conversion and forecast range discipline."],
            scenario_sensitivities=["Conversion miss and margin compression push payback beyond target."],
            failure_modes=["conversion miss", "margin compression", "payback slip"],
        ),
        "risk": RoleDistinctionInput(
            role="risk",
            stance="conditional",
            headline="Controls evidence first",
            argument="Risk & Audit conditions support on policy compliance, approval route, audit trail, source provenance, and hidden obligation evidence.",
            key_points=["Missing approval or provenance weakness blocks support.", "Reconciliation and security evidence must close before commitment."],
            evidence_used=["board_policies", "governance_rules", "approval_route", "audit_findings", "reconciliation_discrepancies", "source_provenance", "security_evidence"],
            control_findings=["Policy exception and audit trail gap require attestation."],
            missing_evidence_requests=["Source quality and approval route evidence still missing."],
            failure_modes=["missing approval", "audit gap", "policy exception", "provenance weakness"],
        ),
        "procurement": RoleDistinctionInput(
            role="procurement",
            stance="conditional",
            headline="Use supplier leverage",
            argument="Procurement supports only after using supplier leverage, renewal timing, price benchmark, switching cost, SLA, termination, and discount terms.",
            key_points=["Counter with benchmark gap and consolidation leverage.", "Auto-renewal notice window controls negotiation urgency."],
            cited_metrics=["45-day notice window", "$70K switching cost", "14% benchmark gap", "22% volume discount"],
            evidence_used=["vendor_exports", "invoices", "contract_metadata", "procurement_notes", "price_benchmarks", "termination_clauses", "slas", "prior_renewal_outcomes"],
            negotiation_levers=["Commercial ask: discount, SLA credit, termination right, and renewal cap."],
            failure_modes=["auto-renewal", "notice window", "switching cost", "sla miss", "benchmark gap"],
        ),
        "reliability": RoleDistinctionInput(
            role="reliability",
            stance="scorecard",
            headline="Evaluator scorecard only",
            argument="Reliability audits evidence grounding, calibration, policy compliance, debate value, trace quality, weaknesses, replay cases, and prompt directives without re-deciding.",
            key_points=["Scorecard flags generic debate and missing replay cases.", "Prompt directive targets the weakest trace-backed behavior."],
            evidence_used=["agent_scorecards", "trace_metadata", "replay_cases", "prompt_improvement_directives", "grounding_gaps", "known_weaknesses"],
            failure_modes=["normal stance", "trace gap", "generic debate", "missing replay", "prompt drift"],
        ),
    }
    return fixtures[role]


def representative_decision_cases() -> list[RepresentativeDecisionCase]:
    decisions = [
        ("vendor-renewal-datadog", "Renew Datadog at $180K ARR-equivalent annual spend with a 45-day renewal notice.", "vendor_renewal"),
        ("growth-investment-enterprise", "Fund an enterprise growth push tied to $1.7M weighted pipeline ARR.", "growth_investment"),
        ("security-blocker-expansion", "Approve a customer expansion while a security evidence blocker remains open.", "security_blocker"),
    ]
    return [
        RepresentativeDecisionCase(
            id=case_id,
            decision=decision,
            decision_type=decision_type,
            role_outputs=[_role_output(role, decision_type) for role in COUNCIL_ROLES],
        )
        for case_id, decision, decision_type in decisions
    ]


@weave.op(name="eval.role_distinction.score_role")
def score_role_output(role_output: RoleDistinctionInput) -> RoleDistinctionScore:
    role = role_output.role
    expected = ROLE_EXPECTATIONS.get(role, ROLE_EXPECTATIONS["cfo"])
    text = _text(role_output)
    vocabulary_hits = _hits(expected["vocabulary"], text)
    evidence_hits = _hits(expected["evidence"], " ".join(role_output.evidence_used + [text]))
    stance_hits = _hits(expected["stance_logic"], text)
    failure_hits = _hits(expected["failure_modes"], text)

    other_vocab = []
    for other_role, other_expected in ROLE_EXPECTATIONS.items():
        if other_role == role:
            continue
        other_vocab.extend(_hits(other_expected["vocabulary"], text))
    contamination_penalty = min(35, len(set(other_vocab)) * 7)

    vocabulary_score = max(0, _score_hits(vocabulary_hits, expected["vocabulary"], min_hits=3) - contamination_penalty)
    evidence_score = _score_hits(evidence_hits, expected["evidence"], min_hits=3)
    stance_score = _score_hits(stance_hits, expected["stance_logic"], min_hits=2)
    failure_score = _score_hits(failure_hits, expected["failure_modes"], min_hits=2)
    overall = round(vocabulary_score * 0.30 + evidence_score * 0.30 + stance_score * 0.25 + failure_score * 0.15)

    missing = []
    if vocabulary_score < ROLE_PASSING_SCORE:
        missing.append("role vocabulary")
    if evidence_score < ROLE_PASSING_SCORE:
        missing.append("evidence category")
    if stance_score < ROLE_PASSING_SCORE:
        missing.append("stance logic")
    if failure_score < ROLE_PASSING_SCORE:
        missing.append("failure mode")

    return RoleDistinctionScore(
        role=role,
        score=max(0, min(100, overall)),
        vocabulary_score=vocabulary_score,
        evidence_score=evidence_score,
        stance_logic_score=stance_score,
        failure_mode_score=failure_score,
        expected_vocabulary_hits=vocabulary_hits,
        evidence_category_hits=evidence_hits,
        stance_logic_hits=stance_hits,
        failure_mode_hits=failure_hits,
        contamination_hits=sorted(set(other_vocab)),
        missing=missing,
    )


@weave.op(name="eval.role_distinction.score_case")
def score_case(case: RepresentativeDecisionCase) -> RoleDistinctionCaseResult:
    role_scores = [score_role_output(output) for output in case.role_outputs]
    by_role = {output.role: output for output in case.role_outputs}
    pairwise: list[PairwiseRoleSimilarity] = []
    collapse_flags: list[str] = []

    for i, role_a in enumerate(COUNCIL_ROLES):
        for role_b in COUNCIL_ROLES[i + 1:]:
            left = by_role.get(role_a)
            right = by_role.get(role_b)
            if not left or not right:
                continue
            vocab_jaccard = _jaccard(_tokens(_text(left)), _tokens(_text(right)))
            evidence_jaccard = _jaccard(set(left.evidence_used), set(right.evidence_used))
            collapse_risk = vocab_jaccard >= 0.42 or evidence_jaccard >= 0.35
            if collapse_risk:
                collapse_flags.append(f"{role_a}/{role_b} overlap: vocab={vocab_jaccard}, evidence={evidence_jaccard}")
            pairwise.append(
                PairwiseRoleSimilarity(
                    role_a=role_a,
                    role_b=role_b,
                    vocabulary_jaccard=vocab_jaccard,
                    evidence_jaccard=evidence_jaccard,
                    collapse_risk=collapse_risk,
                )
            )

    for role_score in role_scores:
        if role_score.score < ROLE_PASSING_SCORE:
            collapse_flags.append(f"{role_score.role} below role threshold: {role_score.score} ({', '.join(role_score.missing)})")

    raw_average = round(statistics.mean(score.score for score in role_scores)) if role_scores else 0
    penalty = min(25, len(collapse_flags) * 3)
    overall = max(0, raw_average - penalty)
    return RoleDistinctionCaseResult(
        id=case.id,
        decision=case.decision,
        decision_type=case.decision_type,
        overall_score=overall,
        passed=overall >= PASSING_SCORE and not collapse_flags,
        role_scores=role_scores,
        pairwise_similarity=pairwise,
        collapse_flags=collapse_flags,
    )


@weave.op(name="eval.role_distinction.run")
def run_role_distinction_eval(
    cases: list[RepresentativeDecisionCase] | None = None,
    *,
    source: str = "representative",
) -> RoleDistinctionReport:
    eval_cases = cases or representative_decision_cases()
    results = [score_case(case) for case in eval_cases]
    overall = round(statistics.mean(case.overall_score for case in results)) if results else 0
    role_average_scores: dict[str, int] = {}
    for role in COUNCIL_ROLES:
        scores = [score.score for case in results for score in case.role_scores if score.role == role]
        role_average_scores[role] = round(statistics.mean(scores)) if scores else 0
    return RoleDistinctionReport(
        id=_new_id("role-distinction"),
        created_at=_now(),
        source=source,
        overall_score=overall,
        passed=overall >= PASSING_SCORE and all(case.passed for case in results),
        case_count=len(results),
        role_average_scores=role_average_scores,
        cases=results,
        weave=weave_links(),
    )


def _ensure_artifact_path(path: str | Path | None) -> Path:
    out = Path(path) if path else DEFAULT_ARTIFACT
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def persist_role_distinction_report(
    report: RoleDistinctionReport,
    *,
    artifact_path: str | Path | None = DEFAULT_ARTIFACT,
    redis: bool = True,
    publish: bool = True,
) -> dict[str, Any]:
    data = report.model_dump(mode="json")
    result: dict[str, Any] = {"report_id": report.id}

    if artifact_path:
        out = _ensure_artifact_path(artifact_path)
        result["artifact_path"] = str(out)
        data["artifact_path"] = str(out)
        out.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    if redis:
        try:
            R.set_json(f"{ROLE_DISTINCTION_PREFIX}{report.id}", data)
            R.set_json(LATEST_ROLE_DISTINCTION, data)
            R.client().rpush(ROLE_DISTINCTION_INDEX, report.id)
            R.client().ltrim(ROLE_DISTINCTION_INDEX, -100, -1)
            result["event_id"] = R.append_event(ROLE_DISTINCTION_STREAM, data)
        except Exception as exc:
            result["redis_error"] = redact_secrets(exc)

    if publish:
        result["weave"] = publish_to_weave(data, name=f"atlas-role-distinction-{report.id}")

    report.artifact_path = data.get("artifact_path")
    report.redis = {k: v for k, v in result.items() if k in ("event_id", "redis_error")}
    report.weave = result.get("weave") or report.weave
    return result


def latest_role_distinction_report() -> dict | None:
    return R.get_json(LATEST_ROLE_DISTINCTION)


def list_role_distinction_reports(limit: int = 25) -> list[dict]:
    try:
        return R.read_events(ROLE_DISTINCTION_STREAM, count=limit)
    except Exception:
        return []


def _position_to_input(position: dict[str, Any]) -> RoleDistinctionInput:
    role = str(position.get("agent") or position.get("role") or "").lower().replace("&", "and")
    aliases = {
        "office of the cfo": "cfo",
        "cfo": "cfo",
        "treasury": "treasury",
        "fp&a": "fpna",
        "fpanda": "fpna",
        "fpa": "fpna",
        "fpna": "fpna",
        "risk and audit": "risk",
        "risk": "risk",
        "procurement": "procurement",
        "reliability": "reliability",
        "reliability auditor": "reliability",
    }
    role = aliases.get(role, role)
    return RoleDistinctionInput(
        role=role,
        stance=str(position.get("stance") or position.get("decision") or ""),
        headline=str(position.get("headline") or position.get("ruling") or ""),
        argument=str(position.get("argument") or position.get("rationale") or position.get("summary") or ""),
        key_points=[str(v) for v in (position.get("key_points") or position.get("conditions") or [])],
        cited_metrics=[str(v) for v in (position.get("cited_metrics") or [])],
        evidence_used=[str(v) for v in (position.get("evidence_used") or [])],
        forecast_assumptions=[str(v) for v in (position.get("forecast_assumptions") or [])],
        scenario_sensitivities=[str(v) for v in (position.get("scenario_sensitivities") or [])],
        plan_vs_actual_deltas=[str(v) for v in (position.get("plan_vs_actual_deltas") or [])],
        control_findings=[str(v) for v in (position.get("control_findings") or [])],
        missing_evidence_requests=[str(v) for v in (position.get("missing_evidence_requests") or [])],
        approval_or_policy_blockers=[str(v) for v in (position.get("approval_or_policy_blockers") or [])],
        negotiation_levers=[str(v) for v in (position.get("negotiation_levers") or [])],
        failure_modes=[str(v) for v in (position.get("failure_modes") or position.get("known_weaknesses") or [])],
    )


def case_from_live_run(
    *,
    decision: str,
    decision_type: str = "live",
    positions: list[dict[str, Any]] | None = None,
    recommendation: dict[str, Any] | None = None,
    reliability_scores: list[dict[str, Any]] | None = None,
    learning_report: dict[str, Any] | None = None,
) -> RepresentativeDecisionCase:
    outputs = [_position_to_input(pos) for pos in (positions or [])]
    if recommendation:
        cfo = dict(recommendation)
        cfo["agent"] = "cfo"
        cfo.setdefault("evidence_used", ["analyst_influence", "dissent", "conditions", "runway_impact_basis", "governance"])
        cfo.setdefault("failure_modes", recommendation.get("assumptions_converted_to_conditions") or recommendation.get("conditions") or [])
        outputs.append(_position_to_input(cfo))
    if reliability_scores or learning_report:
        rel = {
            "agent": "reliability",
            "stance": "scorecard",
            "headline": "Reliability scorecard",
            "argument": (learning_report or {}).get("summary", ""),
            "key_points": (learning_report or {}).get("prompt_improvement_directives", []),
            "evidence_used": ["agent_scorecards", "trace_metadata", "replay_cases", "prompt_improvement_directives", "grounding_gaps", "known_weaknesses"],
            "known_weaknesses": [
                weakness
                for score in (reliability_scores or [])
                for weakness in (score.get("known_weaknesses") or [])
            ],
        }
        outputs.append(_position_to_input(rel))
    deduped: dict[str, RoleDistinctionInput] = {}
    for output in outputs:
        if output.role in COUNCIL_ROLES:
            deduped[output.role] = output
    for role in COUNCIL_ROLES:
        deduped.setdefault(role, RoleDistinctionInput(role=role, headline="Missing role output"))
    return RepresentativeDecisionCase(
        id=_new_id("live-role-case"),
        decision=decision,
        decision_type=decision_type,
        role_outputs=[deduped[role] for role in COUNCIL_ROLES],
    )


def capture_role_distinction_eval(
    *,
    decision: str,
    positions: list[dict[str, Any]],
    recommendation: dict[str, Any],
    reliability_scores: list[dict[str, Any]] | None = None,
    learning_report: dict[str, Any] | None = None,
    decision_type: str = "live",
    source: str = "live",
    artifact_path: str | Path | None = None,
    persist: bool = True,
    publish: bool = True,
) -> dict[str, Any]:
    case = case_from_live_run(
        decision=decision,
        decision_type=decision_type,
        positions=positions,
        recommendation=recommendation,
        reliability_scores=reliability_scores,
        learning_report=learning_report,
    )
    report = run_role_distinction_eval([case], source=source)
    persisted = persist_role_distinction_report(
        report,
        artifact_path=artifact_path,
        redis=persist,
        publish=publish,
    )
    return {"report": report.model_dump(mode="json"), **persisted}
