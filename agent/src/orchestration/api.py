"""
orchestration/api.py — read-mostly REST surface for the orchestration engine.

Mounted additively onto ``src.api``'s ``/api`` router (the ``financial_api``
pattern), behind the ``ATLAS_ORCHESTRATOR`` flag, so ``/api/orchestration/*``
appears only when the engine is enabled. These endpoints bypass CopilotKit; the
dashboard reads them directly (CORS ``*``), exactly like the other dashboard data.

Every handler is defensive: a Redis/model failure returns 503 with a redacted
message rather than leaking secrets or crashing the shared app.
"""

from fastapi import APIRouter, Body, HTTPException, Query

from src.env import redact_secrets
from src.orchestration import store as STORE

# No prefix — src.api mounts this under its own "/api" router.
orchestration_router = APIRouter(tags=["orchestration"])


def _fail(exc: Exception) -> "HTTPException":
    return HTTPException(status_code=503, detail=redact_secrets(exc))


@orchestration_router.get("/orchestration/map")
def orchestration_map():
    """The atlas:orch:* key map + live counts + index doc counts."""
    try:
        return STORE.orch_overview()
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.get("/orchestration/topologies")
def list_topologies(decision_type: str | None = None):
    try:
        return [t.model_dump(mode="json") for t in STORE.list_topologies(decision_type)]
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.get("/orchestration/topologies/{topology_id}")
def get_topology(topology_id: str):
    try:
        topo = STORE.get_topology(topology_id)
    except Exception as exc:
        raise _fail(exc)
    if not topo:
        raise HTTPException(status_code=404, detail="topology not found")
    return topo.model_dump(mode="json")


@orchestration_router.get("/orchestration/runs")
def list_runs(limit: int = 25):
    try:
        return STORE.list_traces(limit)
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.get("/orchestration/runs/{run_id}")
def get_run(run_id: str):
    try:
        run = STORE.get_trace(run_id)
    except Exception as exc:
        raise _fail(exc)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@orchestration_router.get("/orchestration/memory")
def recall_memory(q: str = Query(..., description="query text"), k: int = 4, decision_type: str | None = None):
    """Vector recall of prior decisions from episodic memory (hybrid filter+KNN)."""
    try:
        return STORE.recall(q, k=k, decision_type=decision_type)
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.get("/orchestration/evals")
def list_evals(limit: int = 25):
    try:
        return STORE.list_evals(limit)
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.get("/orchestration/threads/{thread_id}/checkpoints")
def list_checkpoints(thread_id: str):
    """Checkpoint lineage for a debate thread (durable / branch / time-travel)."""
    try:
        return STORE.list_checkpoints(thread_id)
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.post("/orchestration/plan")
async def plan(body: dict = Body(...)):
    """Run the Conductor on a decision and return the plan + compiled topology (live)."""
    decision = (body or {}).get("decision", "")
    if not decision:
        raise HTTPException(status_code=400, detail="decision required")
    context = (body or {}).get("context") or {}
    company = (body or {}).get("company", "Northwind Robotics")
    stage = (body or {}).get("stage", "Series B")
    decision_type = (body or {}).get("decision_type", "general")
    try:
        from src.orchestration import conductor as CONDUCTOR

        result = await CONDUCTOR.plan_topology(
            decision, context, company=company, stage=stage, decision_type=decision_type
        )
        return {
            "ok": result.ok,
            "plan": result.plan.model_dump(mode="json"),
            "topology": result.topology.model_dump(mode="json"),
            "telemetry": result.telemetry,
        }
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.get("/orchestration/control/{thread_id}")
def get_control(thread_id: str):
    """Read the operator's standing directives for a live debate thread."""
    from src.orchestration import control as CONTROL

    try:
        return CONTROL.read_control(thread_id)
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.post("/orchestration/control/{thread_id}")
def set_control(thread_id: str, body: dict = Body(...)):
    """Steer a live debate (HITL): inject/retire seats, force rounds, override threshold."""
    from src.orchestration import control as CONTROL

    body = body or {}
    try:
        return CONTROL.set_control(
            thread_id,
            inject_seats=body.get("inject_seats"),
            retire_seats=body.get("retire_seats"),
            force_more_rounds=body.get("force_more_rounds"),
            override_threshold=body.get("override_threshold"),
            note=body.get("note"),
        )
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.get("/orchestration/observability")
def observability():
    """Aggregate analytics over real persisted runs (cost/latency/convergence/mix)."""
    try:
        return STORE.orch_analytics()
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.get("/orchestration/hierarchical")
def list_hierarchical(limit: int = 25):
    """Recent hierarchical (sub-debate) runs."""
    try:
        return STORE.list_hierarchical(limit)
    except Exception as exc:
        raise _fail(exc)


@orchestration_router.post("/orchestration/hierarchical")
async def run_hierarchical(body: dict = Body(...)):
    """Decompose a complex decision into concurrent sub-committees and aggregate (live)."""
    decision = (body or {}).get("decision", "")
    if not decision:
        raise HTTPException(status_code=400, detail="decision required")
    context = (body or {}).get("context") or {}
    company = (body or {}).get("company", "Northwind Robotics")
    stage = (body or {}).get("stage", "Series B")
    try:
        if not context:
            from src.orchestration.graph import _load_context

            context = _load_context(decision)
        from src.orchestration import subdebate as SUB

        htrace = await SUB.run_hierarchical(decision, context, company=company, stage=stage)
        return htrace.model_dump(mode="json")
    except Exception as exc:
        raise _fail(exc)
