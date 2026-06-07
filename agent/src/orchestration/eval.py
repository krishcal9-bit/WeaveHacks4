"""
orchestration/eval.py — the ORCHESTRATION is the evaluatable unit (W&B Weave).

Most eval harnesses score a model or a prompt. Here the unit under test is the
debate TOPOLOGY itself: we run a candidate topology across a replay set, score the
resulting traces (grounding, decisiveness, convergence speed, red-team robustness,
cost, latency), and A/B a challenger against the incumbent. A PROMOTION GATE then
ensures a worse orchestrator can never ship — a new topology is adopted only if it
beats the incumbent by a margin and never regresses the grounding guardrail.

Scorers are deterministic (cheap, reproducible); an optional LLM judge adds a
decision-quality signal. Eval + promotion ops are ``@weave.op`` so each run is a
named span tree, and results persist to ``atlas:orch:eval:*`` / ``:promotion:*``.
"""

from dataclasses import dataclass, field

import weave
from pydantic import Field

from src.orchestration import debate as DEBATE
from src.orchestration import llm_io as IO
from src.orchestration import models as M
from src.orchestration import store as STORE

# Sub-score weights → overall. Grounding + decision quality dominate; efficiency
# (cost/latency) is a tie-breaker, never allowed to outweigh decision quality.
WEIGHTS = {
    "grounding": 0.25,
    "decision_quality": 0.25,
    "convergence_speed": 0.15,
    "red_team": 0.15,
    "cost": 0.10,
    "latency": 0.10,
}


@dataclass
class EvalCase:
    decision: str
    context: dict
    decision_type: str = "general"
    company: str = "Northwind Robotics"
    stage: str = "Series B"
    weights: dict = field(default_factory=dict)
    expected: str = ""  # optional expected decision (APPROVE/REJECT/CONDITIONAL/DEFER)


class _JudgeScore(M.StrictModel):
    quality: int = Field(ge=0, le=100, description="overall quality of the ruling 0-100")
    grounded: bool = Field(description="true if the ruling is grounded in the cited figures")
    rationale: str = Field(description="one-sentence justification of the score")


# --------------------------------------------------------------------------- #
# Deterministic scorers (no model calls)
# --------------------------------------------------------------------------- #
def score_grounding(trace: M.OrchestrationTrace) -> float:
    final = trace.rounds[-1].stances if trace.rounds else []
    if not final:
        return 0.0
    avg_metrics = sum(len(s.cited_metrics) for s in final) / len(final)
    return round(min(1.0, avg_metrics / 3.0), 3)


def score_convergence_speed(trace: M.OrchestrationTrace, max_rounds: int) -> float:
    if trace.convergence and trace.convergence.converged:
        r = trace.convergence.round_index or 1
        return round(max(0.0, (max_rounds - r + 1) / max(1, max_rounds)), 3)
    return 0.2  # never converged → low (but non-zero) speed


def score_cost(trace: M.OrchestrationTrace, ref_cost: float = 0.5) -> float:
    return round(1.0 / (1.0 + (trace.cost_usd or 0.0) / ref_cost), 3)


def score_latency(trace: M.OrchestrationTrace, ref_ms: float = 120000.0) -> float:
    return round(1.0 / (1.0 + (trace.latency_ms or 0) / ref_ms), 3)


def score_decisiveness(trace: M.OrchestrationTrace) -> float:
    conf = float((trace.recommendation or {}).get("confidence") or 0) / 100.0
    margin = trace.tally.margin if trace.tally else 0.0
    return round(min(1.0, 0.6 * conf + 0.4 * margin), 3)


def score_red_team(trace: M.OrchestrationTrace) -> float:
    if not trace.red_team:
        return 0.7  # no red-team was required by the topology
    return 1.0 if trace.red_team.satisfied else 0.4


def _score_trace(trace: M.OrchestrationTrace, max_rounds: int) -> dict:
    return {
        "grounding": score_grounding(trace),
        "decision_quality": score_decisiveness(trace),
        "convergence_speed": score_convergence_speed(trace, max_rounds),
        "red_team": score_red_team(trace),
        "cost": score_cost(trace),
        "latency": score_latency(trace),
    }


def _overall(subs: dict) -> float:
    return round(sum(WEIGHTS[k] * subs.get(k, 0.0) for k in WEIGHTS), 4)


async def _judge(case: EvalCase, trace: M.OrchestrationTrace, config=None) -> float:
    rec = trace.recommendation or {}
    system = (
        "You are an impartial evaluator of a finance committee's ruling. Score the ruling's quality 0-100 "
        "on decisiveness, grounding in the cited figures, and risk awareness. Be strict."
    )
    user = (
        f"DECISION: {case.decision}\n\nRULING: {rec.get('decision')} (confidence {rec.get('confidence')})\n"
        f"RATIONALE: {rec.get('rationale')}\nKEY RISKS: {rec.get('key_risks')}"
    )
    parsed, _tel = await IO.structured_call(system, user, _JudgeScore, temperature=0.0, config=config)
    return (parsed.quality / 100.0) if parsed else 0.0


# --------------------------------------------------------------------------- #
# Evaluate a topology across a replay set
# --------------------------------------------------------------------------- #
@weave.op(name="orch_eval_topology")
async def evaluate_topology(
    topology: M.Topology, dataset: list[EvalCase], *, use_judge: bool = False, config=None
) -> M.TopologyScore:
    accum: dict = {k: 0.0 for k in WEIGHTS}
    samples = 0
    for case in dataset:
        trace = await DEBATE.run_debate(
            case.decision,
            case.context,
            topology,
            company=case.company,
            stage=case.stage,
            reliability_weights=case.weights,
            config=config,
        )
        subs = _score_trace(trace, topology.max_rounds)
        if use_judge:
            judged = await _judge(case, trace, config=config)
            subs["decision_quality"] = round(0.5 * subs["decision_quality"] + 0.5 * judged, 3)
        for key in accum:
            accum[key] += subs[key]
        samples += 1

    if samples:
        averaged = {k: round(v / samples, 3) for k, v in accum.items()}
    else:
        averaged = {k: 0.0 for k in accum}
    overall = _overall(averaged)
    return M.TopologyScore(
        topology_id=topology.id,
        version=topology.version,
        name=topology.name,
        decision_quality=averaged["decision_quality"],
        grounding=averaged["grounding"],
        convergence_speed=averaged["convergence_speed"],
        cost_score=averaged["cost"],
        latency_score=averaged["latency"],
        overall=overall,
        samples=samples,
        rationale=(
            f"grounding {averaged['grounding']}, decision {averaged['decision_quality']}, "
            f"convergence {averaged['convergence_speed']}, red-team {averaged['red_team']}, "
            f"cost {averaged['cost']}, latency {averaged['latency']}"
        ),
    )


@weave.op(name="orch_eval_compare")
async def evaluate_topologies(
    topologies: list[M.Topology], dataset: list[EvalCase], *, use_judge: bool = False, config=None
) -> list[M.TopologyScore]:
    scores = []
    for topo in topologies:
        scores.append(await evaluate_topology(topo, dataset, use_judge=use_judge, config=config))
    scores.sort(key=lambda s: s.overall, reverse=True)
    return scores


# --------------------------------------------------------------------------- #
# Promotion gate — a worse orchestrator can never ship
# --------------------------------------------------------------------------- #
def gate_decision(
    incumbent: M.TopologyScore,
    challenger: M.TopologyScore,
    *,
    min_gain: float = 0.02,
    grounding_tolerance: float = 0.05,
) -> tuple[bool, str]:
    """Pure gate: promote only if the challenger beats the incumbent overall by
    ``min_gain`` AND does not regress grounding beyond ``grounding_tolerance``."""
    gain = round(challenger.overall - incumbent.overall, 4)
    grounding_ok = challenger.grounding >= incumbent.grounding - grounding_tolerance
    promoted = bool(gain >= min_gain and grounding_ok)
    rationale = (
        f"overall gain {gain} (need >= {min_gain}); grounding guard "
        f"{'OK' if grounding_ok else 'FAIL'} (chal {challenger.grounding} vs inc {incumbent.grounding}); "
        f"{'PROMOTED' if promoted else 'BLOCKED'}"
    )
    return promoted, rationale


@weave.op(name="orch_promotion_gate")
async def promote_if_better(
    incumbent: M.Topology,
    challenger: M.Topology,
    dataset: list[EvalCase],
    *,
    min_gain: float = 0.02,
    grounding_tolerance: float = 0.05,
    dataset_label: str = "default-replay",
    use_judge: bool = False,
    config=None,
) -> M.OrchestrationEvalResult:
    """A/B the challenger against the incumbent and adopt it ONLY if it beats the
    incumbent overall by ``min_gain`` and does not regress grounding beyond
    ``grounding_tolerance``. Persists the eval + (on promotion) the topology."""
    inc = await evaluate_topology(incumbent, dataset, use_judge=use_judge, config=config)
    chal = await evaluate_topology(challenger, dataset, use_judge=use_judge, config=config)

    promoted, rationale = gate_decision(inc, chal, min_gain=min_gain, grounding_tolerance=grounding_tolerance)

    result = M.OrchestrationEvalResult(
        dataset=dataset_label,
        scores=[inc, chal],
        incumbent=incumbent.id,
        challenger=challenger.id,
        winner=challenger.id if promoted else incumbent.id,
        promoted=promoted,
        gate_rationale=rationale,
    )
    try:
        STORE.save_eval(result)
        if promoted:
            STORE.save_topology(challenger)
            STORE.set_promotion(
                challenger.id,
                {
                    "promoted_at": M.now_iso(),
                    "over": incumbent.id,
                    "overall": chal.overall,
                    "eval_id": result.eval_id,
                },
            )
    except Exception as exc:  # persistence is best-effort; never crash the gate
        from src.env import redact_secrets

        print(f"[orch eval] persist skipped: {redact_secrets(exc)}")
    return result


# --------------------------------------------------------------------------- #
# A small default replay set (grounded in the Northwind demo context)
# --------------------------------------------------------------------------- #
def default_replay_set() -> list[EvalCase]:
    ctx = {
        "financials": {
            "name": "Northwind Robotics", "stage": "Series B", "cash_usd": 4200000,
            "monthly_burn_usd": 310000, "runway_months": 13.5, "arr_usd": 6800000,
            "gross_margin": 0.62, "burn_multiple": 2.04,
        },
        "vendors": [{"name": "Datadog", "annual_cost": 120000}, {"name": "AWS", "annual_cost": 540000}],
        "policies": [
            {"title": "Board approval required above $150k/yr", "kind": "policy"},
            {"title": "Maintain >12 months runway", "kind": "policy"},
        ],
    }
    weights = {"treasury": 1.2, "fpna": 1.0, "risk": 0.9, "procurement": 0.9}
    return [
        EvalCase(
            decision="Renew the Datadog observability contract at $180k/year on a 2-year term, paid annually.",
            context=ctx, decision_type="vendor_contract", weights=weights, expected="CONDITIONAL",
        ),
        EvalCase(
            decision="Hire 6 additional account executives (~$1.1M/yr fully loaded) to accelerate ARR growth.",
            context=ctx, decision_type="hiring", weights=weights, expected="CONDITIONAL",
        ),
    ]
