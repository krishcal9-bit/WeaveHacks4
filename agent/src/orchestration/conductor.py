"""
orchestration/conductor.py — the Conductor: plans the debate TOPOLOGY per decision.

A structured OpenAI call (reusing the configured reasoning model via
``src.agent.llm``) decides which seats to convene (base committee + on-demand
specialists), how many rounds to debate, whether to fan out, loop back, or seat a
dedicated adversarial red-team, and the convergence threshold — grounded in the
company's live context and any recalled precedents from episodic memory. The plan
is then compiled into a concrete, versioned ``Topology`` (nodes + edges) that the
debate engine and graph execute and the UI renders.

Weave-instrumented so topology planning shows up as the ``orch_conductor`` span.
Honest degradation: if the model call fails, a deterministic default topology is
used (and flagged in telemetry) — never a faked model response.

``src.agent`` is imported lazily inside the call so this module stays import-safe
offline and never couples to a sibling editing agent.py at import time.
"""

import json
import time
from dataclasses import dataclass, field

import weave
from langchain_core.messages import HumanMessage, SystemMessage

from src.orchestration import models as M

BASE_ROLES = {"cfo", "treasury", "fpna", "risk", "procurement"}
SPECIALIST_ROLES = {"tax", "legal", "hedging", "mna"}

SYSTEM = """You are the Conductor of an AI finance committee at a venture-backed company. \
Given a financial decision and the company's live financial context, you DESIGN THE DEBATE — \
you do not make the decision yourself.

Decide:
- SEATS: always seat the CFO (chair). Seat the standing committee — Treasury (liquidity/runway), \
FP&A (growth/ROI/unit economics), Risk & Audit (downside/compliance), Procurement (vendor terms) — \
when relevant. Seat on-demand SPECIALISTS only when the decision genuinely requires them: \
tax (cross-border, equity comp, entity structure), legal (contracts, IP, litigation, M&A terms), \
hedging (FX, interest-rate, commodity exposure), mna (acquisitions, divestitures, fundraising structure).
- ROUNDS (1-5): more rounds for ambiguous, high-stakes, or contested decisions; fewer for routine ones.
- FAN_OUT: true to let seats analyze concurrently (default for speed).
- ALLOW_LOOPS: true to permit a re-debate round if the red-team is unsatisfied.
- REQUIRES_RED_TEAM: seat a dedicated adversarial seat that must be satisfied before the CFO rules — \
true for material, risky, or irreversible decisions.
- CONVERGENCE_THRESHOLD (0-1): the weighted agreement ratio that ends debate early; lower it for \
contentious decisions so they debate longer.

Optimize the cost/latency/rigor trade-off: lean topologies for small reversible decisions, deep \
topologies (more seats, more rounds, red-team, lower threshold) for large or irreversible ones. \
Ground every choice in the actual figures and any recalled precedent. Return ONLY the structured plan."""


@dataclass
class ConductorResult:
    ok: bool
    plan: M.ConductorPlan
    topology: M.Topology
    telemetry: dict = field(default_factory=dict)


def _context_digest(context: dict | None, company: str, stage: str, precedents=None) -> str:
    context = context or {}
    fin = context.get("financials") or {}
    vendors = context.get("vendors") or []
    policies = context.get("policies") or []
    parts = [f"Company: {company} ({stage})"]
    if fin:
        parts.append("Financials (live system of record): " + json.dumps(fin, default=str)[:1400])
    parts.append(f"Vendors on file: {len(vendors)}")
    if policies:
        titles = "; ".join(str(p.get("title") or p.get("kind") or "")[:60] for p in policies[:5])
        parts.append(f"Relevant policies/precedents: {titles}")
    if precedents:
        prec = "; ".join(
            f"{(p.get('decision') or '')[:50]} -> {p.get('recommendation') or '?'}" for p in precedents[:3]
        )
        parts.append(f"Recalled past decisions (episodic memory): {prec}")
    return "\n".join(parts)


def _fallback_plan(decision_type: str) -> M.ConductorPlan:
    return M.ConductorPlan(
        topology_name="balanced-committee",
        decision_type=decision_type or "general",
        seats=[
            M.SeatPlan(role=r, is_specialist=False, rationale="standing committee member")
            for r in ("cfo", "treasury", "fpna", "risk", "procurement")
        ],
        rounds=2,
        fan_out=True,
        allow_loops=False,
        requires_red_team=True,
        convergence_threshold=0.7,
        stop_conditions=["weighted agreement >= threshold", "max rounds reached", "red-team satisfied"],
        rationale="Deterministic default topology (conductor model call unavailable).",
    )


async def _structured_call(system: str, user: str, schema, config=None):
    from src.agent import llm  # lazy reuse of the configured reasoning model

    chat = llm(temperature=0.2).with_structured_output(schema, include_raw=True)
    t0 = time.time()
    res = await chat.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=user)], config=config
    )
    latency_ms = int((time.time() - t0) * 1000)
    usage = getattr(res.get("raw"), "usage_metadata", None) or {}
    telemetry = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "latency_ms": latency_ms,
        "error": str(res.get("parsing_error")) if res.get("parsing_error") else None,
    }
    return res.get("parsed"), telemetry


def plan_to_topology(plan: M.ConductorPlan, *, version: int = 1) -> M.Topology:
    """Compile a ConductorPlan into a concrete nodes+edges Topology.

    Shape: conductor → {analyst/specialist seats} → [red_team (with optional
    loop-back)] → vote → synthesis. Analyst edges are ``parallel`` when fan-out is
    on. The CFO is the conductor + synthesis (not a separate analyst seat).
    """
    nodes: list[M.NodeSpec] = [
        M.NodeSpec(
            id="conductor",
            kind=M.NodeKind.conductor,
            role="cfo",
            label="Conductor (CFO chair)",
            mandate="plans the debate topology and issues the final ruling",
        )
    ]
    analyst_ids: list[str] = []
    seen: set[str] = set()
    for seat in plan.seats:
        role = (seat.role or "").lower().strip()
        if not role or role == "cfo" or role in seen:
            continue
        seen.add(role)
        is_specialist = bool(seat.is_specialist) or role in SPECIALIST_ROLES
        kind = M.NodeKind.specialist if is_specialist else M.NodeKind.analyst
        node_id = f"seat_{role}"
        nodes.append(
            M.NodeSpec(
                id=node_id,
                kind=kind,
                role=role,
                label=role.upper(),
                mandate=seat.rationale,
                is_specialist=is_specialist,
            )
        )
        analyst_ids.append(node_id)

    edges: list[M.EdgeSpec] = []
    analyst_edge_kind = M.EdgeKind.parallel if plan.fan_out else M.EdgeKind.sequential
    for node_id in analyst_ids:
        edges.append(M.EdgeSpec(source="conductor", target=node_id, kind=analyst_edge_kind))

    if plan.requires_red_team:
        nodes.append(
            M.NodeSpec(
                id="red_team",
                kind=M.NodeKind.red_team,
                role="red_team",
                label="Adversarial Red-Team",
                mandate="raises the strongest objections; must be satisfied before the CFO rules",
            )
        )
        for node_id in analyst_ids:
            edges.append(M.EdgeSpec(source=node_id, target="red_team", kind=M.EdgeKind.sequential))
        if plan.allow_loops:
            for node_id in analyst_ids:
                edges.append(
                    M.EdgeSpec(
                        source="red_team",
                        target=node_id,
                        kind=M.EdgeKind.loop_back,
                        condition="red-team unsatisfied",
                    )
                )
        edges.append(M.EdgeSpec(source="red_team", target="vote", kind=M.EdgeKind.sequential))
    else:
        for node_id in analyst_ids:
            edges.append(M.EdgeSpec(source=node_id, target="vote", kind=M.EdgeKind.sequential))

    nodes.append(
        M.NodeSpec(
            id="vote",
            kind=M.NodeKind.vote,
            role="vote",
            label="Reliability-weighted vote",
            mandate="tally reliability-weighted votes and record minority reports",
        )
    )
    nodes.append(
        M.NodeSpec(
            id="synthesis",
            kind=M.NodeKind.synthesis,
            role="cfo",
            label="CFO synthesis",
            mandate="issue the board-ready, quantified ruling",
        )
    )
    edges.append(M.EdgeSpec(source="vote", target="synthesis", kind=M.EdgeKind.sequential))

    return M.Topology(
        name=plan.topology_name or "conductor-topology",
        version=version,
        decision_type=plan.decision_type or "general",
        nodes=nodes,
        edges=edges,
        max_rounds=plan.rounds,
        convergence_threshold=plan.convergence_threshold,
        requires_red_team=plan.requires_red_team,
        allow_loops=plan.allow_loops,
        fan_out=plan.fan_out,
        description=plan.rationale,
    )


@weave.op(name="orch_conductor")
async def plan_topology(
    decision: str,
    context: dict | None,
    *,
    company: str = "Acme Corp",
    stage: str = "Series A",
    decision_type: str = "general",
    precedents=None,
    config=None,
) -> ConductorResult:
    """Plan the debate topology for one decision (the ``orch_conductor`` Weave span)."""
    digest = _context_digest(context, company, stage, precedents)
    user = (
        f"DECISION TO EVALUATE:\n{decision}\n\nCOMPANY CONTEXT:\n{digest}\n\n"
        "Design the debate topology that best fits this decision."
    )
    try:
        plan, telemetry = await _structured_call(SYSTEM, user, M.ConductorPlan, config=config)
    except Exception as exc:  # honest degradation, never a faked response
        from src.env import redact_secrets

        plan, telemetry = None, {"error": redact_secrets(exc), "latency_ms": 0}

    if plan is None:
        fallback = _fallback_plan(decision_type)
        return ConductorResult(ok=False, plan=fallback, topology=plan_to_topology(fallback), telemetry=telemetry)

    # Guarantee the CFO is always seated as chair.
    if not any((s.role or "").lower() == "cfo" for s in plan.seats):
        plan.seats.insert(0, M.SeatPlan(role="cfo", is_specialist=False, rationale="committee chair"))

    return ConductorResult(ok=True, plan=plan, topology=plan_to_topology(plan), telemetry=telemetry)
