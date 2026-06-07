"""
orchestration/debate.py — the multi-round debate engine.

Executes a compiled ``Topology`` into a full committee debate:

  1. Multi-round, adaptive position-taking: each seat sees the prior round and may
     migrate its stance. Rounds run concurrently when the topology fans out.
  2. Deterministic convergence + stance-migration detection from the weighted
     stance vector — debate stops early once weighted agreement crosses the
     topology's threshold.
  3. An adversarial RED-TEAM gate that must be satisfied; with loop-back enabled, a
     single bounded re-debate round addresses its must-address challenges.
  4. Structured NEGOTIATION between the most-confident opposing pair, when present.
  5. RELIABILITY-WEIGHTED voting from the final stances → a tally + minority reports.
  6. CFO SYNTHESIS into a board-ready, quantified Recommendation (reused from src.agent).

Every model call flows through ``llm_io.structured_call`` (honest degradation, full
telemetry). Decorated ``@weave.op`` so the whole debate is one span tree in Weave.
"""

import asyncio
import inspect
from collections import defaultdict

import weave

from src.orchestration import llm_io as IO
from src.orchestration import models as M

_STANCE_TO_DECISION = {
    M.Stance.support: "APPROVE",
    M.Stance.oppose: "REJECT",
    M.Stance.conditional: "CONDITIONAL",
    M.Stance.abstain: "DEFER",
}


def _coerce_stance(value) -> M.Stance:
    v = (value or "").lower().strip()
    for stance in M.Stance:
        if stance.value == v:
            return stance
    if v in ("approve", "yes", "for", "in favor"):
        return M.Stance.support
    if v in ("reject", "no", "against", "oppose"):
        return M.Stance.oppose
    if v in ("conditional", "maybe", "qualified"):
        return M.Stance.conditional
    return M.Stance.abstain


def _round_summary(stances) -> str:
    if not stances:
        return ""
    bits = [f'{s.label or s.role}={s.stance.value}({s.confidence}) "{s.headline}"' for s in stances]
    return "Prior round positions:\n- " + "\n- ".join(bits)


async def _emit(emit, **patch) -> None:
    if emit is None:
        return
    try:
        result = emit(patch)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # streaming is best-effort, never fails the debate
        print(f"[orch debate] emit skipped: {exc}")


# --------------------------------------------------------------------------- #
# Deterministic signals (unit-testable offline, no model calls)
# --------------------------------------------------------------------------- #
def compute_convergence(stances, weights, round_index, threshold, prev_stances=None) -> M.ConvergenceSignal:
    weighted: dict = defaultdict(float)
    total = 0.0
    for s in stances:
        w = float(weights.get(s.role, 1.0))
        total += w
        weighted[s.stance] += w
    if total <= 0:
        return M.ConvergenceSignal(round_index=round_index, rationale="no weighted stances")
    modal, modal_w = max(weighted.items(), key=lambda kv: kv[1])
    agreement = modal_w / total
    prev = {p.role: p.stance for p in (prev_stances or [])}
    migrations = sum(1 for s in stances if s.role in prev and prev[s.role] != s.stance)
    confidences = [s.confidence for s in stances] or [0]
    spread = (max(confidences) - min(confidences)) / 100.0
    converged = agreement >= threshold
    return M.ConvergenceSignal(
        round_index=round_index,
        agreement_ratio=round(agreement, 3),
        divergence_score=round(1 - agreement, 3),
        stance_migrations=migrations,
        confidence_spread=round(spread, 3),
        converged=converged,
        rationale=f"{round(agreement * 100)}% weighted agreement on '{modal.value}'; {migrations} stance migration(s)",
    )


def tally_votes(votes) -> M.VoteTally:
    weighted: dict = defaultdict(float)
    total = 0.0
    for v in votes:
        weighted[v.value] += v.weight
        total += v.weight
    order = [M.Stance.support, M.Stance.oppose, M.Stance.conditional, M.Stance.abstain]
    winner = max(order, key=lambda s: weighted.get(s, 0.0)) if total else M.Stance.abstain
    ranked = sorted((weighted.get(s, 0.0) for s in order), reverse=True)
    margin = round((ranked[0] - ranked[1]) / total, 3) if total > 0 else 0.0
    nonzero = sum(1 for s in order if weighted.get(s, 0.0) > 0)
    minority = [
        M.MinorityReport(
            role=v.role,
            dissent=f"voted {v.value.value} ({v.confidence})",
            weight=round(v.weight, 3),
            rationale=v.rationale,
        )
        for v in votes
        if v.value != winner
    ]
    return M.VoteTally(
        votes=votes,
        weighted_support=round(weighted.get(M.Stance.support, 0.0), 3),
        weighted_oppose=round(weighted.get(M.Stance.oppose, 0.0), 3),
        weighted_conditional=round(weighted.get(M.Stance.conditional, 0.0), 3),
        weighted_abstain=round(weighted.get(M.Stance.abstain, 0.0), 3),
        total_weight=round(total, 3),
        decision=_STANCE_TO_DECISION[winner],
        margin=margin,
        unanimous=(nonzero == 1),
        minority_reports=minority,
    )


def _conflict_pair(stances):
    supporters = [s for s in stances if s.stance == M.Stance.support]
    opposers = [s for s in stances if s.stance == M.Stance.oppose]
    if not supporters or not opposers:
        return None
    return max(supporters, key=lambda s: s.confidence), max(opposers, key=lambda s: s.confidence)


# --------------------------------------------------------------------------- #
# Model-backed steps
# --------------------------------------------------------------------------- #
async def _seat_position(persona, decision, digest, round_index, prev_summary, red_team_focus, config):
    system = persona["system_prompt"] + (
        " Return a decisive stance (support/oppose/conditional/abstain), a 0-100 confidence, a one-line "
        "headline, a 2-4 sentence argument citing specific figures, and the concrete metrics you cited."
    )
    parts = [f"DECISION: {decision}", f"\nCONTEXT:\n{digest}"]
    if prev_summary:
        parts.append("\n" + prev_summary + "\nRevise your stance only if the debate genuinely warrants it.")
    if red_team_focus:
        parts.append("\nADDRESS THESE RED-TEAM CHALLENGES head-on:\n" + red_team_focus)
    parts.append(f"\nThis is debate round {round_index}.")
    parsed, tel = await IO.structured_call(system, "\n".join(parts), M.SeatPosition, temperature=0.3, config=config)
    if parsed is None:
        return None, tel
    stance = M.RoundStance(
        role=persona["id"],
        label=persona.get("label", ""),
        stance=_coerce_stance(parsed.stance),
        confidence=int(parsed.confidence or 0),
        headline=parsed.headline,
        argument=parsed.argument,
        cited_metrics=list(parsed.cited_metrics or []),
    )
    return stance, tel


async def _run_round(seats, decision, digest, round_index, prev_stances, red_team_focus, fan_out, config):
    prev_summary = _round_summary(prev_stances)

    async def one(persona):
        return persona, await _seat_position(persona, decision, digest, round_index, prev_summary, red_team_focus, config)

    if fan_out:
        results = await asyncio.gather(*[one(p) for p in seats])
    else:
        results = [await one(p) for p in seats]

    prev_by_role = {p.role: p.stance for p in (prev_stances or [])}
    stances, telemetries = [], []
    for persona, (stance, tel) in results:
        telemetries.append(tel)
        if stance is None:  # honest degradation: keep prior stance, flag the gap
            stance = M.RoundStance(
                role=persona["id"],
                label=persona.get("label", ""),
                stance=prev_by_role.get(persona["id"], M.Stance.abstain),
                confidence=0,
                headline="(no position produced)",
                argument=(tel.get("error") or "model call failed"),
                cited_metrics=[],
            )
        else:
            stance.changed = persona["id"] in prev_by_role and prev_by_role[persona["id"]] != stance.stance
        stances.append(stance)
    return stances, telemetries


async def _red_team(decision, digest, stances, config):
    system = (
        "You are the adversarial Red-Team on a finance committee. Attack the committee's consensus with the "
        "strongest, most specific, quantified objections. For each seat, raise the sharpest challenge. Mark "
        "must_address=true for challenges that should block a ruling. Set satisfied=true ONLY if the positions "
        "already withstand your strongest attacks."
    )
    user = f"DECISION: {decision}\n\nCONTEXT:\n{digest}\n\n{_round_summary(stances)}"
    return await IO.structured_call(system, user, M.RedTeamReport, temperature=0.4, config=config)


async def _negotiate(decision, digest, seat_a, seat_b, config):
    system = (
        f"Facilitate a structured negotiation between {seat_a.label} (supports) and {seat_b.label} (opposes) on "
        "the decision. Produce the first seat's proposal, the second seat's counter, whether they reach a "
        "workable compromise, and the agreed terms or the remaining gap. Ground every point in the figures."
    )
    user = (
        f"DECISION: {decision}\n\nCONTEXT:\n{digest}\n\n"
        f"{seat_a.label}: {seat_a.argument}\n{seat_b.label}: {seat_b.argument}"
    )
    parsed, tel = await IO.structured_call(system, user, M.NegotiationOutcome, temperature=0.4, config=config)
    if parsed is None:
        return None, tel
    move = M.NegotiationMove(
        from_role=seat_a.role,
        to_role=seat_b.role,
        proposal=parsed.proposal,
        counter=parsed.counter,
        resolved=parsed.resolved,
        terms=parsed.terms,
    )
    return move, tel


async def _synthesize(decision, digest, stances, convergence, red_team, tally, config):
    from src.agent import Recommendation  # reuse the committee's recommendation contract

    system = (
        "You are the CFO chairing the committee. Issue the final, board-ready, quantified ruling. Weigh the "
        "seats' positions, the convergence signal, the red-team's challenges, and the reliability-weighted vote. "
        "Be decisive and ground every number in the live figures."
    )
    extras = [
        _round_summary(stances),
        f"Convergence: {convergence.rationale if convergence else 'n/a'}",
        (
            f"Reliability-weighted vote: {tally.decision} (support {tally.weighted_support} / oppose "
            f"{tally.weighted_oppose} / conditional {tally.weighted_conditional}; margin {tally.margin})"
        ),
    ]
    if red_team:
        extras.append(f"Red-team satisfied={red_team.satisfied}; {len(red_team.challenges)} challenge(s)")
    user = f"DECISION: {decision}\n\nCONTEXT:\n{digest}\n\n" + "\n".join(extras)
    return await IO.structured_call(system, user, Recommendation, temperature=0.2, config=config)


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
@weave.op(name="orch_debate")
async def run_debate(
    decision: str,
    context: dict | None,
    topology: M.Topology,
    *,
    company: str = "Acme Corp",
    stage: str = "Series A",
    personas: list[dict] | None = None,
    reliability_weights: dict | None = None,
    precedents=None,
    emit=None,
    config=None,
) -> M.OrchestrationTrace:
    weights = {str(k).lower(): float(v) for k, v in (reliability_weights or {}).items()}
    digest = IO.context_digest(context, company, stage, precedents)

    if personas is None:
        from src.orchestration import registry as REG

        roles = [n.role for n in topology.nodes if n.kind in (M.NodeKind.analyst, M.NodeKind.specialist)]
        personas = REG.resolve_seats(roles)
    debate_seats = [p for p in personas if p.get("id") != "cfo"]
    if not debate_seats:
        from src.orchestration import registry as REG

        debate_seats = REG.resolve_seats(["treasury", "fpna", "risk", "procurement"])

    telemetry: dict = {}
    rounds: list[M.DebateRound] = []
    prev_stances: list = []
    convergence = None
    stop_reason = M.StopReason.max_rounds
    max_rounds = max(1, int(topology.max_rounds or 1))

    for r in range(1, max_rounds + 1):
        await _emit(emit, phase=f"debate round {r}/{max_rounds}", round_index=r)
        stances, tels = await _run_round(debate_seats, decision, digest, r, prev_stances, None, topology.fan_out, config)
        for tel in tels:
            telemetry = IO.merge_telemetry(telemetry, tel)
        convergence = compute_convergence(stances, weights, r, topology.convergence_threshold, prev_stances)
        rounds.append(M.DebateRound(index=r, stances=stances, convergence=convergence))
        await _emit(
            emit,
            phase=f"round {r} complete",
            round_index=r,
            convergence=convergence.model_dump(),
            stances=[s.model_dump(mode="json") for s in stances],
        )
        prev_stances = stances
        if convergence.converged:
            stop_reason = M.StopReason.converged
            break

    red_team = None
    if topology.requires_red_team:
        await _emit(emit, phase="red-team challenge")
        red_team, tel = await _red_team(decision, digest, prev_stances, config)
        telemetry = IO.merge_telemetry(telemetry, tel)
        if red_team and not red_team.satisfied and topology.allow_loops:
            focus = "; ".join(
                f"[{c.severity}] {c.target_role}: {c.attack}" for c in red_team.challenges if c.must_address
            ) or red_team.summary
            await _emit(emit, phase="loop-back round (addressing red-team)")
            next_index = len(rounds) + 1
            stances, tels = await _run_round(debate_seats, decision, digest, next_index, prev_stances, focus, topology.fan_out, config)
            for tel in tels:
                telemetry = IO.merge_telemetry(telemetry, tel)
            convergence = compute_convergence(stances, weights, next_index, topology.convergence_threshold, prev_stances)
            rounds.append(M.DebateRound(index=next_index, stances=stances, convergence=convergence, notes="loop-back addressing red-team"))
            prev_stances = stances
            red_team2, tel2 = await _red_team(decision, digest, prev_stances, config)
            telemetry = IO.merge_telemetry(telemetry, tel2)
            red_team = red_team2 or red_team
            if red_team and red_team.satisfied:
                stop_reason = M.StopReason.red_team_satisfied

    negotiations: list = []
    pair = _conflict_pair(prev_stances)
    if pair:
        await _emit(emit, phase="negotiation")
        move, tel = await _negotiate(decision, digest, pair[0], pair[1], config)
        telemetry = IO.merge_telemetry(telemetry, tel)
        if move:
            negotiations.append(move.model_dump(mode="json"))

    votes = [
        M.Vote(role=s.role, value=s.stance, confidence=s.confidence, weight=weights.get(s.role, 1.0), rationale=s.headline)
        for s in prev_stances
    ]
    tally = tally_votes(votes)
    await _emit(emit, phase="vote", tally=tally.model_dump(mode="json"))

    await _emit(emit, phase="synthesis")
    rec, tel = await _synthesize(decision, digest, prev_stances, convergence, red_team, tally, config)
    telemetry = IO.merge_telemetry(telemetry, tel)
    recommendation = rec.model_dump() if rec else {}

    from src.agent import LLM_MODEL  # the configured model id (lazy)

    cost = IO.estimate_cost(LLM_MODEL, telemetry.get("input_tokens"), telemetry.get("output_tokens"))

    return M.OrchestrationTrace(
        decision=decision,
        decision_type=topology.decision_type,
        topology_id=topology.id,
        topology_version=topology.version,
        topology_name=topology.name,
        seats=[p["id"] for p in debate_seats],
        rounds=rounds,
        convergence=convergence,
        red_team=red_team,
        tally=tally,
        negotiations=negotiations,
        recommendation=recommendation,
        precedents=[p.get("id", "") for p in (precedents or []) if p.get("id")],
        stop_reason=stop_reason,
        cost_usd=cost,
        input_tokens=telemetry.get("input_tokens") or 0,
        output_tokens=telemetry.get("output_tokens") or 0,
        latency_ms=telemetry.get("latency_ms") or 0,
        model=LLM_MODEL,
    )
