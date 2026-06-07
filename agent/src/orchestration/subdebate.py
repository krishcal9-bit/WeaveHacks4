"""
orchestration/subdebate.py — hierarchical, parallel sub-debates.

Decomposes a complex decision into focused sub-decisions, runs a full committee on
each CONCURRENTLY (coordinated + logged on the atlas:orch bus + pub/sub), then
aggregates the sub-rulings into one parent ruling. This is committees spawning
committees — the deepest orchestration mode — and it puts the consumer-group bus
and pub/sub fan-out (built in store.py) to work.

``@weave.op`` spans (orch_hierarchical / orch_decompose / orch_aggregate); persists
a HierarchicalTrace to ``atlas:orch:hrun:*`` and each sub-debate's OrchestrationTrace
to ``atlas:orch:run:*``.
"""

import asyncio
import time

import weave

from src.orchestration import conductor as CONDUCTOR
from src.orchestration import debate as DEBATE
from src.orchestration import llm_io as IO
from src.orchestration import models as M
from src.orchestration import store as STORE


@weave.op(name="orch_decompose")
async def decompose(decision, context, *, company="Acme Corp", stage="Series A", config=None):
    system = (
        "You are a finance chief of staff. Break the decision into 2-4 focused, decision-relevant SUB-DECISIONS "
        "whose answers together determine the parent decision. Each must be independently analyzable by a finance "
        "committee (e.g., affordability/liquidity, ROI/payback, risk/compliance, vendor/deal terms). Do not just "
        "restate the parent decision."
    )
    user = f"DECISION: {decision}\n\nCONTEXT:\n{IO.context_digest(context, company, stage)}"
    parsed, tel = await IO.structured_call(system, user, M.Decomposition, temperature=0.3, config=config)
    return (list(parsed.sub_questions) if parsed else []), tel


@weave.op(name="orch_aggregate")
async def aggregate(parent_decision, sub_pairs, context, *, company="Acme Corp", stage="Series A", config=None):
    from src.agent import Recommendation  # reuse the board-ruling contract

    lines = [
        f"- SUB-DECISION: {q}\n  RULING: {(r or {}).get('decision')} ({(r or {}).get('confidence')}) — "
        f"{((r or {}).get('rationale') or '')[:180]}"
        for q, r in sub_pairs
    ]
    system = (
        "You are the CFO. Aggregate the sub-committee rulings into ONE board-ready, quantified ruling on the PARENT "
        "decision. Weigh the sub-rulings against each other, resolve tensions, and ground every number in the figures."
    )
    user = (
        f"PARENT DECISION: {parent_decision}\n\nSUB-COMMITTEE RULINGS:\n" + "\n".join(lines)
        + f"\n\nCONTEXT:\n{IO.context_digest(context, company, stage)}"
    )
    parsed, tel = await IO.structured_call(system, user, Recommendation, temperature=0.2, config=config)
    return (parsed.model_dump() if parsed else {}), tel


@weave.op(name="orch_hierarchical")
async def run_hierarchical(
    decision,
    context,
    *,
    company="Acme Corp",
    stage="Series A",
    sub_topology_factory=None,
    persist=True,
    config=None,
) -> M.HierarchicalTrace:
    t0 = time.time()
    try:
        STORE.ensure_bus_group()
    except Exception:
        pass

    sub_questions, tel0 = await decompose(decision, context, company=company, stage=stage, config=config)
    if not sub_questions:
        sub_questions = [decision]  # honest fallback: no decomposition -> a single committee

    async def run_one(question):
        try:
            STORE.bus_send({"event": "subdebate_start", "parent": decision[:80], "question": question})
            STORE.publish_bus({"event": "subdebate_start", "question": question})
        except Exception:
            pass
        if sub_topology_factory is not None:
            topology = sub_topology_factory(question)
        else:
            plan = await CONDUCTOR.plan_topology(question, context, company=company, stage=stage, config=config)
            topology = plan.topology
        trace = await DEBATE.run_debate(question, context, topology, company=company, stage=stage, config=config)
        if persist:
            try:
                STORE.save_trace(trace)
            except Exception:
                pass
        try:
            STORE.bus_send(
                {"event": "subdebate_done", "question": question, "ruling": (trace.recommendation or {}).get("decision")}
            )
        except Exception:
            pass
        return trace

    sub_traces = await asyncio.gather(*[run_one(q) for q in sub_questions])
    sub_pairs = list(zip(sub_questions, [t.recommendation or {} for t in sub_traces]))
    parent_rec, tel1 = await aggregate(decision, sub_pairs, context, company=company, stage=stage, config=config)

    from src.agent import LLM_MODEL

    overhead = IO.estimate_cost(
        LLM_MODEL,
        (tel0.get("input_tokens") or 0) + (tel1.get("input_tokens") or 0),
        (tel0.get("output_tokens") or 0) + (tel1.get("output_tokens") or 0),
    )
    htrace = M.HierarchicalTrace(
        parent_decision=decision,
        sub_questions=sub_questions,
        sub_run_ids=[t.run_id for t in sub_traces],
        sub_rulings=[t.recommendation or {} for t in sub_traces],
        parent_recommendation=parent_rec,
        cost_usd=round(sum(t.cost_usd for t in sub_traces) + overhead, 4),
        latency_ms=int((time.time() - t0) * 1000),
    )
    if persist:
        try:
            STORE.save_hierarchical(htrace)
            STORE.publish_bus(
                {"event": "hierarchical_done", "run_id": htrace.run_id, "ruling": parent_rec.get("decision")}
            )
        except Exception:
            pass
    return htrace
