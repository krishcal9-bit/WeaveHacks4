"""
orchestration/whatif.py — what-if debate branching (time-travel + counterfactual).

Takes a persisted debate run, **branches** its thread at the head checkpoint
(`store.branch_checkpoint` — the durable/branchable/time-travel feature), then
**re-runs** the same decision on the same topology with altered inputs (e.g.,
different reliability weights), and **compares** the original ruling vs the branch
ruling. This is counterfactual analysis on a board decision — "what would the
committee have decided if Risk carried more weight?" — and it exercises the
checkpointer end-to-end. `@weave.op` span; the branch run persists like any other.
"""

import weave

from src.orchestration import debate as DEBATE
from src.orchestration import store as STORE


@weave.op(name="orch_whatif")
async def what_if(run_id, *, weight_overrides=None, company="Northwind Robotics", stage="Series B", config=None) -> dict:
    original = STORE.get_trace(run_id)
    if not original:
        return {"error": f"run {run_id} not found"}
    decision = original.get("decision", "")
    topology = STORE.get_topology(original.get("topology_id", ""))
    if topology is None:
        return {"error": "original topology not found (cannot reproduce the counterfactual)"}

    # Time-travel: branch the original thread at its head checkpoint (records lineage).
    thread_id = original.get("thread_id", "")
    branch_thread = None
    if thread_id:
        checkpoints = STORE.list_checkpoints(thread_id)
        head = checkpoints[-1]["checkpoint_id"] if checkpoints else None
        if head:
            branch_thread = STORE.branch_checkpoint(thread_id, head, label="what-if")

    # Counterfactual re-run: same decision + topology, altered reliability weights.
    from src.orchestration.graph import _load_context

    context = _load_context(decision)
    branch_trace = await DEBATE.run_debate(
        decision, context, topology, company=company, stage=stage,
        reliability_weights=(weight_overrides or {}), control_thread=branch_thread, config=config,
    )
    try:
        STORE.save_trace(branch_trace)
    except Exception as exc:
        print(f"[orch whatif] branch trace save skipped: {exc}")

    original_rec = original.get("recommendation", {}) or {}
    branch_rec = branch_trace.recommendation or {}
    return {
        "decision": decision,
        "topology": topology.name,
        "weight_overrides": weight_overrides or {},
        "original_run": run_id,
        "original_ruling": original_rec.get("decision"),
        "original_confidence": original_rec.get("confidence"),
        "branch_run": branch_trace.run_id,
        "branch_thread": branch_thread,
        "branch_ruling": branch_rec.get("decision"),
        "branch_confidence": branch_rec.get("confidence"),
        "changed": original_rec.get("decision") != branch_rec.get("decision"),
    }
