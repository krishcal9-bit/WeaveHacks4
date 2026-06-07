"""
orchestration/selftest.py — fast, offline regression guard for the engine's pure logic.

Runs the deterministic invariants (models, topology compilation, convergence +
stance-migration, reliability-weighted voting, eval scoring + promotion gate, seat
control) with NO Redis and NO model calls, so it can run anywhere in well under a
second. The live/integration paths (Conductor, debate, store, graph) are verified
separately against the live stack.

    uv run --directory agent python -m src.orchestration.selftest
"""

import sys

from src.orchestration import conductor as CONDUCTOR
from src.orchestration import control as CONTROL
from src.orchestration import debate as DEBATE
from src.orchestration import eval as EVAL
from src.orchestration import models as M
from src.orchestration import namespace as ns


def _check(name: str, cond: bool, results: list) -> None:
    results.append((name, bool(cond)))


def run() -> list[tuple[str, bool]]:
    r: list[tuple[str, bool]] = []

    # namespace: ownership guard + key map
    _check("ns owns atlas:orch:*", ns.is_orch_key(ns.run_key("x")), r)
    _check("ns rejects foreign keys", not ns.is_orch_key("atlas:company:northwind"), r)
    _check("ns key_map complete", set(ns.key_map()) >= {"json", "vector", "search", "streams", "pubsub"}, r)

    # models: strict schema + round-trip
    plan = M.ConductorPlan(topology_name="t", decision_type="general",
        seats=[M.SeatPlan(role="cfo", is_specialist=False, rationale="chair")], rounds=2, fan_out=True,
        allow_loops=False, requires_red_team=True, convergence_threshold=0.75, stop_conditions=["x"], rationale="y")
    _check("ConductorPlan strict (additionalProperties false)",
           M.ConductorPlan.model_json_schema().get("additionalProperties") is False, r)
    topo = M.Topology(name="t", nodes=[M.NodeSpec(id="cfo", role="cfo")], edges=[M.EdgeSpec(source="cfo", target="x")])
    _check("Topology JSON round-trip", M.Topology(**topo.model_dump(mode="json")).id == topo.id, r)
    _check("EpisodicMemoryRecord.embedding_text", bool(
        M.EpisodicMemoryRecord(decision="d", recommendation="APPROVE").embedding_text()), r)

    # conductor: plan -> topology compilation
    topo2 = CONDUCTOR.plan_to_topology(CONDUCTOR._fallback_plan("general"))
    kinds = {n.kind.value for n in topo2.nodes}
    _check("plan_to_topology has conductor/red_team/vote/synthesis",
           {"conductor", "red_team", "vote", "synthesis"}.issubset(kinds), r)
    _check("plan_to_topology fan-out edges parallel",
           any(e.kind == M.EdgeKind.parallel for e in topo2.edges), r)
    mna = CONDUCTOR.plan_to_topology(M.ConductorPlan(topology_name="m", decision_type="acquisition",
        seats=[M.SeatPlan(role="mna", is_specialist=True, rationale="deal")], rounds=3, fan_out=True,
        allow_loops=True, requires_red_team=True, convergence_threshold=0.8, stop_conditions=["x"], rationale="y"))
    _check("specialist seat compiled", any(n.is_specialist for n in mna.nodes), r)
    _check("loop-back edge present when allow_loops", any(e.kind == M.EdgeKind.loop_back for e in mna.edges), r)

    # debate: convergence + stance migration + weighting
    def rs(role, stance, conf): return M.RoundStance(role=role, stance=stance, confidence=conf, headline="h")
    st = [rs("a", M.Stance.support, 80), rs("b", M.Stance.support, 70), rs("c", M.Stance.oppose, 60)]
    w1 = {"a": 1.0, "b": 1.0, "c": 1.0}
    cv = DEBATE.compute_convergence(st, w1, 1, 0.75)
    _check("convergence agreement 2/3 not converged", abs(cv.agreement_ratio - 2/3) < 0.01 and not cv.converged, r)
    prev = [rs("a", M.Stance.oppose, 50), rs("b", M.Stance.support, 70), rs("c", M.Stance.oppose, 60)]
    cv2 = DEBATE.compute_convergence(st, w1, 2, 0.6, prev)
    _check("stance migration detected + converges", cv2.stance_migrations == 1 and cv2.converged, r)
    cv3 = DEBATE.compute_convergence(st, {"a": 1.0, "b": 1.0, "c": 3.0}, 1, 0.75)
    _check("reliability weight flips modal stance", cv3.agreement_ratio == 0.6, r)

    # debate: reliability-weighted tally + minority reports
    votes = [M.Vote(role="a", value=M.Stance.support, confidence=80, weight=1.0, rationale="x"),
             M.Vote(role="b", value=M.Stance.support, confidence=70, weight=1.0, rationale="y"),
             M.Vote(role="c", value=M.Stance.oppose, confidence=60, weight=3.0, rationale="z")]
    tally = DEBATE.tally_votes(votes)
    _check("weighted tally -> REJECT", tally.decision == "REJECT", r)
    _check("minority reports for dissenters", len(tally.minority_reports) == 2, r)
    _check("conflict pair found", (DEBATE._conflict_pair(st) or (None,))[0].role == "a", r)
    _check("stance coercion", DEBATE._coerce_stance("APPROVE") == M.Stance.support
           and DEBATE._coerce_stance("nope") == M.Stance.abstain, r)

    # eval: scoring separates good/bad + promotion gate
    def mk(grounded, converged, decision, conf, margin, rt):
        rnd = M.DebateRound(index=1, convergence=M.ConvergenceSignal(round_index=1, converged=converged,
                agreement_ratio=(1.0 if converged else 0.5)),
            stances=[M.RoundStance(role=f"s{i}", stance=M.Stance.support, confidence=conf,
                cited_metrics=(["a", "b", "c"] if grounded else [])) for i in range(3)])
        return M.OrchestrationTrace(decision="d", rounds=[rnd], convergence=rnd.convergence,
            red_team=M.RedTeamReport(summary="s", challenges=[], satisfied=rt),
            tally=M.VoteTally(decision=decision, margin=margin),
            recommendation={"decision": decision, "confidence": conf}, cost_usd=0.1, latency_ms=60000)
    good, bad = mk(True, True, "CONDITIONAL", 90, 1.0, True), mk(False, False, "DEFER", 30, 0.1, False)
    _check("eval scores good > bad", EVAL._overall(EVAL._score_trace(good, 2)) > EVAL._overall(EVAL._score_trace(bad, 2)), r)
    ts = lambda o, g: M.TopologyScore(overall=o, grounding=g)
    _check("gate promotes clear winner", EVAL.gate_decision(ts(0.6, 0.8), ts(0.7, 0.8))[0], r)
    _check("gate blocks small gain", not EVAL.gate_decision(ts(0.7, 0.8), ts(0.71, 0.8))[0], r)
    _check("gate blocks grounding regression", not EVAL.gate_decision(ts(0.6, 0.9), ts(0.85, 0.5))[0], r)

    # control: seat directives (pure parts, no Redis)
    seats = [{"id": "treasury"}, {"id": "fpna"}, {"id": "procurement"}]
    kept = [p["id"] for p in CONTROL.apply_seats(seats, {"retire_seats": ["procurement"], "inject_seats": []})]
    _check("control retire drops seat", "procurement" not in kept and "treasury" in kept, r)
    _check("ABS_MAX_ROUNDS bounded", 1 <= CONTROL.ABS_MAX_ROUNDS <= 20, r)

    return r


def main() -> int:
    results = run()
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"orchestration selftest: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
