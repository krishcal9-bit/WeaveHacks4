"""
Atlas — promotion gates for the W&B Weave evaluation operating system.

A **promotion candidate** is a proposed prompt/model change for one council agent
(e.g. ``treasury.v4-liquidity-stress`` replacing ``treasury.v3``). Before any
candidate can be promoted it must be **replayed** against a replay set and beat the
incumbent on explicit, enforced gates. Unproven candidates are blocked by default.

Everything here is **live**:

- Replay runs a real ``weave.Model`` (incumbent vs candidate prompt) over a real
  ``weave.Dataset`` of past decisions, scored by real ``weave.Scorer`` rubrics —
  optionally wrapped in a canonical ``weave.Evaluation`` (with a robust direct
  fallback that is equally live, never mocked).
- Gate decisions are persisted under ``atlas:evaluation:promotion:*``, appended to
  the ``atlas:stream:promotions`` Redis Stream, and published to Weave.
- All Weave links are redacted; no secret is printed.

Enforced gates (see :func:`summarize_gates`):
  hard, no-regression on reliability / policy / evidence / calibration,
  replay coverage, trace quality; plus a soft reliability-improvement bar that
  separates auto-approval from human review.
"""

from __future__ import annotations

import json
from statistics import mean
from typing import Any

import weave
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src import redis_layer as R
from src.env import redact_secrets
from src.replay_sets import (
    DEFAULT_SLUG,
    ensure_default_replay_set,
    get_replay_set,
)
from src.weave_eval import EVAL_NS, _new_id, _now, publish_to_weave, weave_links

# --------------------------------------------------------------------------- #
# Namespacing + enforced thresholds
# --------------------------------------------------------------------------- #
CANDIDATE_PREFIX = f"{EVAL_NS}:candidate:"
CANDIDATE_INDEX = f"{EVAL_NS}:candidate_index"   # Redis SET of candidate ids
PROMO_PREFIX = f"{EVAL_NS}:promotion:"
LATEST_PROMO = f"{EVAL_NS}:promotion:latest"

MIN_RELIABILITY_GAIN = 3.0   # auto-approval bar (points)
MIN_CASES = 2                # replay coverage floor
REGRESSION_TOLERANCE = 0.0   # no-regression epsilon
REPLAY_MAX_CASES = 3         # bound live model cost per replay

_HARD_NO_REGRESS = (
    ("reliability", "Reliability"),
    ("policy_compliance", "Policy compliance"),
    ("evidence_grounding", "Evidence grounding"),
    ("calibration", "Calibration"),
)


# --------------------------------------------------------------------------- #
# Stable Pydantic models
# --------------------------------------------------------------------------- #
class PromotionCandidate(BaseModel):
    id: str
    agent_id: str
    version_label: str
    incumbent_label: str
    prompt_hash: str = ""
    candidate_prompt_hash: str = ""
    prompt_adjustment: str = ""
    promotion_gate: str = ""
    reliability_dimensions: list[str] = Field(default_factory=list)
    gate_metric: str = ""
    replay_set: str | None = None
    status: str = Field(default="proposed", description="proposed | replaying | blocked | approved | needs_review")
    created_at: str = ""
    updated_at: str = ""
    last_gate_id: str | None = None


class GateResult(BaseModel):
    name: str
    label: str
    kind: str = Field(description="hard | soft")
    passed: bool
    incumbent: float | None = None
    candidate: float | None = None
    delta: float | None = None
    threshold: float = 0.0
    detail: str = ""


class GateDecision(BaseModel):
    id: str
    candidate_id: str
    candidate_label: str
    incumbent_label: str
    agent_id: str
    replay_set: str | None = None
    status: str = Field(description="blocked | approved | needs_review")
    decided_by: str = Field(default="auto", description="auto | human")
    gates: list[GateResult] = Field(default_factory=list)
    score_deltas: dict[str, float] = Field(default_factory=dict)
    incumbent_scores: dict[str, float] = Field(default_factory=dict)
    candidate_scores: dict[str, float] = Field(default_factory=dict)
    case_count: int = 0
    board_explanation: str = ""
    trace_quality_issues: list[dict[str, Any]] = Field(default_factory=list)
    weave: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


# --------------------------------------------------------------------------- #
# Live replay model + rubric scorers (real Weave objects)
# --------------------------------------------------------------------------- #
class CandidatePromptModel(weave.Model):
    """Runs one council agent's position call under a specific prompt version."""

    agent_id: str
    version_label: str
    prompt_adjustment: str = ""
    temperature: float = 0.4

    @weave.op
    async def predict(self, decision: str, context: dict | None = None, **kwargs) -> dict:
        # Lazy import keeps src.agent → promotion_gates acyclic at module load.
        from src.agent import ROSTER, Position, llm

        persona = ROSTER.get(self.agent_id, ROSTER["cfo"])
        company = ((context or {}).get("financials") or {}).get("name") or "Acme Corp"
        directive = (
            f"\n\nPROMPT VERSION DIRECTIVE ({self.version_label}): {self.prompt_adjustment}"
            if self.prompt_adjustment
            else ""
        )
        system = SystemMessage(
            content=(
                f"You are {persona['label']} at {company} (Series A), on its investment committee. "
                f"Your mandate is {persona['mandate']}. Evaluate the decision strictly from your "
                f"function's perspective, citing specific figures from the company context. Take a "
                f"clear stance (support / oppose / conditional) and defend it crisply, like a senior "
                f"finance executive. Never mention being an AI or a model." + directive
            )
        )
        human = HumanMessage(
            content=(
                f"DECISION UNDER REVIEW:\n{decision}\n\n"
                f"COMPANY CONTEXT:\n{json.dumps(context or {}, default=str)}\n\nGive your position."
            )
        )
        model = llm(self.temperature).with_structured_output(Position)
        position: Position = await model.ainvoke([system, human])
        return {
            "agent_id": self.agent_id,
            "version": self.version_label,
            "stance": position.stance,
            "headline": position.headline,
            "argument": position.argument,
            "key_points": position.key_points,
        }


def _output_text(output: dict | None) -> str:
    output = output or {}
    return " ".join(
        [str(output.get("headline", "")), str(output.get("argument", ""))]
        + [str(p) for p in (output.get("key_points") or [])]
    )


class _RubricScorer(weave.Scorer):
    dimension: str = "reliability"


class EvidenceGroundingScorer(_RubricScorer):
    @weave.op
    def score(self, output=None, **kwargs) -> dict:
        from src.weave_eval import _count_figures

        text = _output_text(output)
        figures = _count_figures(text)
        has_points = bool((output or {}).get("key_points"))
        score = min(100, figures * 18 + (15 if has_points else 0))
        return {"score": float(score), "figures": figures}


class PolicyComplianceScorer(_RubricScorer):
    @weave.op
    def score(self, output=None, **kwargs) -> dict:
        from src.weave_eval import _policy_hits

        text = _output_text(output)
        hits = _policy_hits(text)
        runway_aware = 1 if ("runway" in text.lower() or "burn" in text.lower()) else 0
        score = min(100, hits * 14 + runway_aware * 16)
        return {"score": float(score), "policy_terms": hits}


class CalibrationScorer(_RubricScorer):
    dimension: str = "calibration"

    @weave.op
    def score(self, output=None, expected_decision=None, **kwargs) -> dict:
        out = output or {}
        stance = str(out.get("stance") or "").lower()
        expected = str(expected_decision or "").upper()
        expected_stance = {
            "APPROVE": "support",
            "REJECT": "oppose",
            "CONDITIONAL": "conditional",
            "DEFER": "conditional",
        }.get(expected)
        aligned = 1 if (expected_stance and stance == expected_stance) else 0
        partial = 1 if (expected_stance and not aligned and stance == "conditional") else 0
        text = _output_text(out).lower()
        hedged = 1 if any(word in text for word in ("risk", "condition", "downside", "if ", "contingent")) else 0
        score = 45 * aligned + 20 * partial + 35 * hedged + (20 if stance else 0)
        return {"score": float(min(100, score)), "aligned": bool(aligned)}


def _build_scorers() -> list[_RubricScorer]:
    return [
        EvidenceGroundingScorer(name="evidence_grounding", dimension="evidence_grounding"),
        PolicyComplianceScorer(name="policy_compliance", dimension="policy_compliance"),
        CalibrationScorer(name="calibration", dimension="calibration"),
    ]


def _composite_reliability(scores: dict[str, float]) -> float:
    return round(
        0.4 * scores.get("evidence_grounding", 0)
        + 0.3 * scores.get("policy_compliance", 0)
        + 0.3 * scores.get("calibration", 0),
        1,
    )


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 1)


# --------------------------------------------------------------------------- #
# Candidate persistence
# --------------------------------------------------------------------------- #
def _candidate_id(agent_id: str, version_label: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", version_label.lower()).strip("-")
    return f"cand-{agent_id}-{slug}"


def save_candidate(candidate: PromotionCandidate) -> None:
    R.set_json(f"{CANDIDATE_PREFIX}{candidate.id}", candidate.model_dump())
    try:
        R.client().sadd(CANDIDATE_INDEX, candidate.id)
    except Exception:
        pass


def get_candidate(candidate_id: str) -> dict | None:
    return R.get_json(f"{CANDIDATE_PREFIX}{candidate_id}")


def list_candidates() -> list[dict]:
    try:
        ids = sorted(R.client().smembers(CANDIDATE_INDEX))
    except Exception:
        ids = []
    return [c for c in (get_candidate(cid) for cid in ids) if c]


def _default_directive(agent_id: str, version_label: str) -> str:
    return {
        "cfo": "Resolve dissent into explicit board conditions and cite the computed runway-impact basis before ruling.",
        "treasury": "Stress-test liquidity under the downside cash forecast and always state months of runway remaining.",
        "fpna": "Calibrate growth claims against cohort churn/NDR and flag forecast overconfidence explicitly.",
        "risk": "Enumerate every high-severity audit finding and security blocker and tie each to a control.",
        "procurement": "Quantify renewal leverage, switching cost, and termination notice for each vendor commitment.",
        "reliability": "Produce evaluator-only scorecards with replay cases and prompt directives; never take an approve/reject stance.",
    }.get(agent_id, f"Apply the {version_label} prompt revision rigorously and cite evidence.")


def upsert_candidates_from_prompt_versions() -> list[dict]:
    """Idempotently register a PromotionCandidate for each seeded prompt version."""
    company = R.get_json(f"{R.NS}:company:northwind") or {}
    try:
        from src.openai_council import prompt_versions_payload

        versions = prompt_versions_payload({"financials": company})
    except Exception:
        versions = company.get("prompt_versions") or []
    created: list[dict] = []
    for version in versions:
        agent_id = version.get("agent") or version.get("role")
        version_label = version.get("candidate")
        if not agent_id or not version_label:
            continue
        candidate_id = _candidate_id(agent_id, version_label)
        existing = get_candidate(candidate_id)
        if existing:
            created.append(existing)
            continue
        candidate = PromotionCandidate(
            id=candidate_id,
            agent_id=agent_id,
            version_label=version_label,
            incumbent_label=version.get("current") or f"{agent_id}.incumbent",
            prompt_hash=version.get("prompt_hash") or version.get("active_prompt_hash") or "",
            candidate_prompt_hash=version.get("candidate_prompt_hash") or "",
            prompt_adjustment=version.get("directive") or _default_directive(agent_id, version_label),
            promotion_gate=version.get("promotion_gate") or "",
            reliability_dimensions=list(version.get("reliability_dimensions") or []),
            gate_metric=version.get("gate_metric") or "",
            replay_set=version.get("replay_set") or DEFAULT_SLUG,
            status="proposed",
            created_at=_now(),
            updated_at=_now(),
        )
        save_candidate(candidate)
        created.append(candidate.model_dump())
    return created


# --------------------------------------------------------------------------- #
# Replay execution (live)
# --------------------------------------------------------------------------- #
def _eval_row(case: dict) -> dict:
    return {
        "case_id": case.get("id"),
        "decision": case.get("decision"),
        "expected_decision": case.get("expected_decision"),
        "expected_confidence": case.get("expected_confidence"),
        "tags": case.get("tags") or [],
        "context": case.get("context") or {},
    }


def _drill_mean(node: Any) -> float | None:
    if isinstance(node, (int, float)):
        return float(node)
    if isinstance(node, dict):
        if isinstance(node.get("mean"), (int, float)):
            return float(node["mean"])
        for value in node.values():
            found = _drill_mean(value)
            if found is not None:
                return found
    return None


def _aggregate_from_summary(summary: Any, scorers: list[_RubricScorer]) -> dict | None:
    if not isinstance(summary, dict):
        return None
    out: dict[str, float] = {}
    for scorer in scorers:
        key = getattr(scorer, "name", None) or scorer.__class__.__name__
        node = summary.get(key)
        if node is None:
            for summary_key, value in summary.items():
                if scorer.dimension in str(summary_key).lower():
                    node = value
                    break
        value = _drill_mean(node) if node is not None else None
        if value is None:
            return None  # incomplete summary → use the live direct fallback
        out[scorer.dimension] = _clamp_score(value)
    out["reliability"] = _composite_reliability(out)
    out["_invalid"] = 0
    return out


async def _score_direct(model: CandidatePromptModel, cases: list[dict], scorers: list[_RubricScorer]) -> dict:
    """Live fallback: run predict + scorers per case directly (still real Weave spans)."""
    dims: dict[str, list[float]] = {scorer.dimension: [] for scorer in scorers}
    invalid = 0
    for case in cases:
        row = _eval_row(case)
        output = await model.predict(**row)
        if not (output or {}).get("stance"):
            invalid += 1
        for scorer in scorers:
            result = scorer.score(output=output, **row)
            value = result.get("score", 0) if isinstance(result, dict) else result
            dims[scorer.dimension].append(float(value))
    out = {dim: _clamp_score(mean(values)) if values else 0.0 for dim, values in dims.items()}
    out["reliability"] = _composite_reliability(out)
    out["_invalid"] = invalid
    return out


async def _evaluate_model(
    model: CandidatePromptModel,
    cases: list[dict],
    scorers: list[_RubricScorer],
    label: str,
    use_weave_evaluation: bool,
) -> tuple[dict, dict]:
    weave_info: dict[str, Any] = {"ran": False, "mode": "direct"}
    scores: dict | None = None
    if use_weave_evaluation:
        try:
            import re

            dataset = weave.Dataset(
                name=f"atlas_replay_{re.sub(r'[^a-zA-Z0-9_]+', '_', label)}",
                rows=[_eval_row(case) for case in cases],
            )
            evaluation = weave.Evaluation(dataset=dataset, scorers=scorers)
            summary = await evaluation.evaluate(model)
            parsed = _aggregate_from_summary(summary, scorers)
            if parsed is not None:
                scores = parsed
                weave_info = {"ran": True, "mode": "weave.Evaluation"}
        except Exception as exc:
            weave_info = {"ran": False, "mode": "direct", "error": redact_secrets(exc)}
    if scores is None:
        scores = await _score_direct(model, cases, scorers)
    return scores, weave_info


# --------------------------------------------------------------------------- #
# Gate evaluation + board explanation
# --------------------------------------------------------------------------- #
def evaluate_promotion(
    *,
    candidate: dict,
    incumbent_scores: dict,
    candidate_scores: dict,
    replay_set_name: str | None,
    case_count: int,
    candidate_invalid: int,
) -> tuple[str, list[GateResult], dict[str, float]]:
    gates: list[GateResult] = []
    for dimension, label in _HARD_NO_REGRESS:
        inc = float(incumbent_scores.get(dimension, 0) or 0)
        cand = float(candidate_scores.get(dimension, 0) or 0)
        delta = round(cand - inc, 1)
        gates.append(
            GateResult(
                name=f"{dimension}_no_regression",
                label=f"{label} — no regression",
                kind="hard",
                passed=delta >= -REGRESSION_TOLERANCE,
                incumbent=inc,
                candidate=cand,
                delta=delta,
                threshold=0.0,
                detail=f"candidate {cand} vs incumbent {inc} (Δ {delta:+}).",
            )
        )
    gates.append(
        GateResult(
            name="coverage",
            label="Replay coverage",
            kind="hard",
            passed=case_count >= MIN_CASES,
            candidate=float(case_count),
            threshold=float(MIN_CASES),
            detail=f"{case_count} cases replayed (minimum {MIN_CASES}).",
        )
    )
    gates.append(
        GateResult(
            name="trace_quality",
            label="Trace quality — valid predictions",
            kind="hard",
            passed=candidate_invalid == 0,
            candidate=float(candidate_invalid),
            threshold=0.0,
            detail=f"{candidate_invalid} malformed/empty candidate predictions (no new high-severity trace issues allowed).",
        )
    )
    reliability_delta = round(
        float(candidate_scores.get("reliability", 0) or 0) - float(incumbent_scores.get("reliability", 0) or 0),
        1,
    )
    gates.append(
        GateResult(
            name="reliability_improvement",
            label="Reliability — demonstrable gain",
            kind="soft",
            passed=reliability_delta >= MIN_RELIABILITY_GAIN,
            incumbent=float(incumbent_scores.get("reliability", 0) or 0),
            candidate=float(candidate_scores.get("reliability", 0) or 0),
            delta=reliability_delta,
            threshold=MIN_RELIABILITY_GAIN,
            detail=f"reliability Δ {reliability_delta:+} (auto-approval bar Δ ≥ {MIN_RELIABILITY_GAIN}).",
        )
    )

    hard_failed = [gate for gate in gates if gate.kind == "hard" and not gate.passed]
    improvement = next(gate for gate in gates if gate.name == "reliability_improvement")
    if hard_failed:
        status = "blocked"
    elif improvement.passed:
        status = "approved"
    else:
        status = "needs_review"

    deltas = {
        dimension: round(
            float(candidate_scores.get(dimension, 0) or 0) - float(incumbent_scores.get(dimension, 0) or 0), 1
        )
        for dimension in ("reliability", "policy_compliance", "evidence_grounding", "calibration")
    }
    return status, gates, deltas


def board_explanation(
    *,
    candidate: dict,
    status: str,
    gates: list[GateResult],
    deltas: dict[str, float],
    case_count: int,
    replay_set_name: str | None,
) -> str:
    label = candidate.get("version_label", "candidate")
    incumbent = candidate.get("incumbent_label", "incumbent")
    failed = [gate for gate in gates if gate.kind == "hard" and not gate.passed]
    movement = ", ".join(
        f"{dimension.replace('_', ' ')} {value:+}" for dimension, value in deltas.items()
    )
    header = (
        f"Candidate **{label}** was replayed against {case_count} board decision(s) in "
        f"'{replay_set_name or 'replay set'}' and compared head-to-head with incumbent **{incumbent}**. "
        f"Score movement vs incumbent: {movement}. "
    )
    if status == "blocked":
        reasons = "; ".join(f"{gate.label} ({gate.detail})" for gate in failed) or "a hard gate failed"
        return (
            header
            + f"**Decision: BLOCKED.** The change regressed a protected dimension or lacked evidence — {reasons}. "
            "Per the AI council promotion policy, no prompt change ships while it regresses reliability, "
            "policy compliance, evidence grounding, or calibration, or lacks replay coverage."
        )
    if status == "approved":
        return (
            header
            + f"**Decision: APPROVED.** It cleared every no-regression gate and improved reliability by "
            f"{deltas.get('reliability', 0):+} points — above the +{MIN_RELIABILITY_GAIN} promotion bar — so {label} "
            "is cleared to replace the incumbent."
        )
    return (
        header
        + f"**Decision: HELD FOR REVIEW.** No regressions, but the reliability gain "
        f"({deltas.get('reliability', 0):+}) is below the +{MIN_RELIABILITY_GAIN} auto-approval bar. A human owner must "
        "decide whether the marginal improvement justifies promotion."
    )


# --------------------------------------------------------------------------- #
# Record / persist gate decisions
# --------------------------------------------------------------------------- #
def record_gate_decision(decision: GateDecision, *, publish: bool = True) -> dict[str, Any]:
    data = decision.model_dump()
    result: dict[str, Any] = {"gate_id": decision.id, "status": decision.status}
    try:
        R.set_json(f"{PROMO_PREFIX}{decision.id}", data)
        R.set_json(LATEST_PROMO, data)
        result["event_id"] = R.append_event("promotions", data)
        R.publish(
            "dashboard",
            {"event": "promotion", "status": decision.status, "candidate": decision.candidate_label},
        )
    except Exception as exc:
        result["redis_error"] = redact_secrets(exc)
    # Reflect the decision back onto the candidate record.
    candidate = get_candidate(decision.candidate_id)
    if candidate:
        candidate["status"] = decision.status
        candidate["updated_at"] = _now()
        candidate["last_gate_id"] = decision.id
        R.set_json(f"{CANDIDATE_PREFIX}{decision.candidate_id}", candidate)
    if publish:
        result["weave"] = publish_to_weave(decision, name=f"atlas-gate-{decision.id}")
    return result


async def run_promotion_replay(
    candidate_id: str,
    *,
    replay_set: str | None = None,
    max_cases: int = REPLAY_MAX_CASES,
    use_weave_evaluation: bool = True,
    publish: bool = True,
) -> dict[str, Any]:
    """Replay incumbent vs candidate over a replay set and produce a GateDecision (live)."""
    candidate = get_candidate(candidate_id)
    if not candidate:
        raise ValueError(f"Unknown promotion candidate: {candidate_id}")

    replay_set_name = replay_set or candidate.get("replay_set") or DEFAULT_SLUG
    replay_record = get_replay_set(replay_set_name) or ensure_default_replay_set(publish=publish)
    replay_set_name = replay_record.get("slug") or replay_set_name
    cases = (replay_record.get("cases") or [])[: max(1, max_cases)]

    scorers = _build_scorers()
    agent_id = candidate.get("agent_id", "cfo")
    incumbent_model = CandidatePromptModel(
        agent_id=agent_id, version_label=candidate.get("incumbent_label", "incumbent"), prompt_adjustment=""
    )
    candidate_model = CandidatePromptModel(
        agent_id=agent_id,
        version_label=candidate.get("version_label", "candidate"),
        prompt_adjustment=candidate.get("prompt_adjustment", ""),
    )

    incumbent_scores, incumbent_weave = await _evaluate_model(
        incumbent_model, cases, scorers, f"{agent_id}_incumbent", use_weave_evaluation
    )
    candidate_scores, candidate_weave = await _evaluate_model(
        candidate_model, cases, scorers, f"{agent_id}_candidate", use_weave_evaluation
    )

    candidate_invalid = int(candidate_scores.pop("_invalid", 0))
    incumbent_scores.pop("_invalid", 0)

    status, gates, deltas = evaluate_promotion(
        candidate=candidate,
        incumbent_scores=incumbent_scores,
        candidate_scores=candidate_scores,
        replay_set_name=replay_set_name,
        case_count=len(cases),
        candidate_invalid=candidate_invalid,
    )
    explanation = board_explanation(
        candidate=candidate,
        status=status,
        gates=gates,
        deltas=deltas,
        case_count=len(cases),
        replay_set_name=replay_set_name,
    )
    decision = GateDecision(
        id=_new_id("gate"),
        candidate_id=candidate_id,
        candidate_label=candidate.get("version_label", candidate_id),
        incumbent_label=candidate.get("incumbent_label", "incumbent"),
        agent_id=agent_id,
        replay_set=replay_set_name,
        status=status,
        decided_by="auto",
        gates=gates,
        score_deltas=deltas,
        incumbent_scores=incumbent_scores,
        candidate_scores=candidate_scores,
        case_count=len(cases),
        board_explanation=explanation,
        trace_quality_issues=(
            [] if candidate_invalid == 0 else [{"severity": "high", "node": agent_id, "summary": f"{candidate_invalid} malformed candidate predictions."}]
        ),
        weave={
            "project": weave_links().get("project"),
            "url": weave_links().get("url"),
            "incumbent": incumbent_weave,
            "candidate": candidate_weave,
        },
        created_at=_now(),
    )
    persisted = record_gate_decision(decision, publish=publish)
    return {"decision": decision.model_dump(), **persisted}


def block_unproven_candidate(candidate_id: str, *, replay_set: str | None = None, publish: bool = True) -> dict[str, Any]:
    """Record a BLOCKED gate for a candidate that has no live replay evidence yet."""
    candidate = get_candidate(candidate_id)
    if not candidate:
        raise ValueError(f"Unknown promotion candidate: {candidate_id}")
    replay_set_name = replay_set or candidate.get("replay_set") or DEFAULT_SLUG
    decision = GateDecision(
        id=_new_id("gate"),
        candidate_id=candidate_id,
        candidate_label=candidate.get("version_label", candidate_id),
        incumbent_label=candidate.get("incumbent_label", "incumbent"),
        agent_id=candidate.get("agent_id", "cfo"),
        replay_set=replay_set_name,
        status="blocked",
        decided_by="auto",
        gates=[
            GateResult(
                name="replay_evidence",
                label="Replay evidence required",
                kind="hard",
                passed=False,
                detail="No live W&B Weave replay has been run for this candidate.",
            )
        ],
        board_explanation=(
            f"Candidate **{candidate.get('version_label', candidate_id)}** is **BLOCKED** until a live W&B Weave "
            f"replay against '{replay_set_name}' demonstrates it beats incumbent "
            f"**{candidate.get('incumbent_label', 'incumbent')}** without regressing reliability, policy compliance, "
            "evidence grounding, or calibration. Unproven changes do not ship."
        ),
        weave={"project": weave_links().get("project"), "url": weave_links().get("url")},
        created_at=_now(),
    )
    persisted = record_gate_decision(decision, publish=publish)
    return {"decision": decision.model_dump(), **persisted}


def mark_candidate(
    candidate_id: str,
    status: str,
    *,
    decided_by: str = "human",
    note: str = "",
    publish: bool = True,
) -> dict[str, Any]:
    """Manually approve/block/hold a candidate (records a human GateDecision)."""
    candidate = get_candidate(candidate_id)
    if not candidate:
        raise ValueError(f"Unknown promotion candidate: {candidate_id}")
    status = status.lower().strip()
    if status not in {"approved", "blocked", "needs_review"}:
        raise ValueError("status must be one of: approved, blocked, needs_review")
    decision = GateDecision(
        id=_new_id("gate"),
        candidate_id=candidate_id,
        candidate_label=candidate.get("version_label", candidate_id),
        incumbent_label=candidate.get("incumbent_label", "incumbent"),
        agent_id=candidate.get("agent_id", "cfo"),
        replay_set=candidate.get("replay_set"),
        status=status,
        decided_by=decided_by,
        board_explanation=(
            note
            or f"{candidate.get('version_label', candidate_id)} manually marked {status.upper()} by a human owner."
        ),
        weave={"project": weave_links().get("project"), "url": weave_links().get("url")},
        created_at=_now(),
    )
    return record_gate_decision(decision, publish=publish)


# --------------------------------------------------------------------------- #
# Read API + summaries (used by REST, health, agent learning_report)
# --------------------------------------------------------------------------- #
def summarize_gates() -> list[dict]:
    """The enforced promotion gates, in human-readable board terms."""
    return [
        {"name": "reliability_no_regression", "kind": "hard", "rule": "Candidate reliability must not fall below the incumbent.", "threshold": "Δ ≥ 0"},
        {"name": "policy_compliance_no_regression", "kind": "hard", "rule": "Policy compliance must not regress.", "threshold": "Δ ≥ 0"},
        {"name": "evidence_grounding_no_regression", "kind": "hard", "rule": "Evidence grounding must not regress.", "threshold": "Δ ≥ 0"},
        {"name": "calibration_no_regression", "kind": "hard", "rule": "Decision calibration must not regress.", "threshold": "Δ ≥ 0"},
        {"name": "coverage", "kind": "hard", "rule": f"Replay set must contain at least {MIN_CASES} cases.", "threshold": f"≥ {MIN_CASES} cases"},
        {"name": "trace_quality", "kind": "hard", "rule": "No malformed/empty candidate predictions (no new high-severity trace issues).", "threshold": "0 issues"},
        {"name": "reliability_improvement", "kind": "soft", "rule": f"Auto-approval requires a reliability gain of at least {MIN_RELIABILITY_GAIN} points; otherwise held for human review.", "threshold": f"Δ ≥ {MIN_RELIABILITY_GAIN}"},
    ]


def list_promotions(limit: int = 25) -> list[dict]:
    try:
        return R.read_events("promotions", count=limit)
    except Exception:
        return []


def latest_promotion() -> dict | None:
    return R.get_json(LATEST_PROMO)


def promotion_status_summary() -> dict[str, Any]:
    """Latest gate status per candidate, for the learning report / health surfaces."""
    promotions = list_promotions(50)
    counts = {"approved": 0, "blocked": 0, "needs_review": 0}
    seen: set[str] = set()
    for promotion in promotions:
        candidate_id = promotion.get("candidate_id")
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        status = promotion.get("status", "needs_review")
        counts[status] = counts.get(status, 0) + 1
    candidates = list_candidates()
    return {
        "counts": counts,
        "candidate_count": len(candidates),
        "decided_candidates": len(seen),
        "latest": promotions[0] if promotions else None,
        "enforced_gates": summarize_gates(),
        "thresholds": {
            "min_reliability_gain": MIN_RELIABILITY_GAIN,
            "min_cases": MIN_CASES,
            "regression_tolerance": REGRESSION_TOLERANCE,
        },
        "weave": weave_links(),
    }
