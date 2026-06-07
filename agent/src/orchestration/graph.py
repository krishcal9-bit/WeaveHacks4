"""
orchestration/graph.py — the flag-gated orchestration graph (``finance_department``).

When ``ATLAS_ORCHESTRATOR`` is on, ``agent.py`` swaps its fixed linear graph for
this one (a single additive, flag-gated line at EOF). It runs the
Conductor → debate-engine pipeline and streams results onto the SAME
``DebateState`` the frontend already consumes — so the existing Decision Room
renders an orchestrated debate with no frontend change — plus a new
``orchestration`` state key carrying the live topology shape, per-round
convergence, and the reliability-weighted vote.

Durable in Redis: each round is checkpointed (branch / replay / time-travel), the
full trace is persisted, the decision is remembered as episodic precedent, and an
event is published on the orchestration bus. ``@weave.op`` nodes make the run a
named span tree.

The ``DebateState`` subclass is defined lazily inside ``build_orchestrator_graph``
so this module never imports (or races) the sibling-edited ``agent.py`` at import
time, and never edits it beyond the EOF swap.
"""

import inspect
import json
import time

import weave
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.orchestration import conductor as CONDUCTOR
from src.orchestration import debate as DEBATE
from src.orchestration import models as M
from src.orchestration import store as STORE

try:
    from copilotkit.langgraph import copilotkit_emit_state as _emit_state
except Exception:
    try:
        from copilotkit import copilotkit_emit_state as _emit_state
    except Exception:
        _emit_state = None


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _event(sponsor: str, label: str, detail: str, tone: str = "info") -> dict:
    return {
        "id": f"{time.time_ns()}-{sponsor.lower().replace(' ', '-')}",
        "at": _now(),
        "sponsor": sponsor,
        "label": label,
        "detail": detail,
        "tone": tone,
    }


async def _stream(config, patch: dict) -> None:
    """Resilient CopilotKit state emit (mirrors agent.py's version fallbacks)."""
    if _emit_state is None or config is None:
        return
    safe = json.loads(json.dumps(patch, default=str))
    try:
        result = _emit_state(config, safe)
    except TypeError:
        try:
            result = _emit_state(safe)
        except Exception as exc:
            print(f"[orch graph] emit skipped: {exc}")
            return
    except Exception as exc:
        print(f"[orch graph] emit skipped: {exc}")
        return
    try:
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        print(f"[orch graph] emit skipped: {exc}")


def _tool_body(tool_obj, *args, **kwargs):
    func = getattr(tool_obj, "func", None)
    if func is not None:
        return func(*args, **kwargs)
    payload = kwargs if kwargs else (args[0] if args else {})
    return tool_obj.invoke(payload)


def _load_context(decision: str) -> dict:
    from src.tools import get_company_financials, list_vendors, search_finance_policies

    return {
        "financials": json.loads(_tool_body(get_company_financials)),
        "vendors": json.loads(_tool_body(list_vendors)),
        "policies": json.loads(_tool_body(search_finance_policies, query=decision or "financial decision")),
    }


def _decision_from(state: dict) -> str:
    for m in reversed(state.get("messages", []) or []):
        role = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else None)
        if role in ("human", "user"):
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content", "")
            return content if isinstance(content, str) else str(content)
    return state.get("decision", "") or ""


def _company(context: dict) -> tuple[str, str, str]:
    fin = (context or {}).get("financials") or {}
    return fin.get("name") or "Acme Corp", fin.get("stage") or "Series A", fin.get("id") or "northwind"


def _stance_turn(stance: dict) -> dict:
    role = stance.get("role", "?")
    label = stance.get("label") or role
    return {
        "agent": role,
        "label": label,
        "role": label,
        "monogram": (role[:2] or "?").upper(),
        "type": "position",
        "stance": stance.get("stance"),
        "headline": stance.get("headline", ""),
        "argument": stance.get("argument", ""),
        "key_points": [],
        "cited_metrics": stance.get("cited_metrics", []),
        "changed": stance.get("changed", False),
        "at": _now(),
    }


def _agent_statuses(stances: list[dict]) -> list[dict]:
    out = []
    for s in stances:
        role = s.get("role", "?")
        out.append(
            {
                "id": role,
                "label": s.get("label") or role,
                "role": s.get("label") or role,
                "monogram": (role[:2] or "?").upper(),
                "status": "speaking",
                "stance": s.get("stance"),
                "headline": s.get("headline", ""),
                "detail": s.get("argument", ""),
                "last_update": _now(),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
@weave.op(name="orch_intake")
async def _intake_node(state: dict, config) -> dict:
    from src.health import require_live_ready, sponsor_health

    require_live_ready()
    decision = _decision_from(state)
    context = _load_context(decision)
    company, stage, _cid = _company(context)
    health = sponsor_health()

    precedents = []
    try:
        precedents = STORE.recall(decision, k=3) if decision else []
    except Exception as exc:
        print(f"[orch intake] precedent recall skipped: {exc}")

    framing = {
        "agent": "cfo",
        "label": "Office of the CFO",
        "role": "Conductor",
        "monogram": "CF",
        "type": "framing",
        "headline": "Convening an orchestrated committee",
        "argument": (
            f"The Conductor will design a debate tailored to: “{decision}”. "
            + (f"{len(precedents)} prior decision(s) recalled as precedent." if precedents else "")
        ),
        "key_points": [],
        "at": _now(),
    }
    orchestration = {
        "phase": "intake",
        "engine": "ATLAS_ORCHESTRATOR",
        "precedents": [{"decision": p.get("decision"), "recommendation": p.get("recommendation")} for p in precedents],
        "topology": {},
        "rounds": [],
        "convergence": {},
        "tally": {},
    }
    patch = {
        "decision": decision,
        "phase": "intake",
        "current_phase": "Conductor convening",
        "context": context,
        "positions": [],
        "transcript": [framing],
        "recommendation": {},
        "sponsor_health": health,
        "observability_events": [
            _event("OpenAI", "Orchestration engine active", "Conductor will plan the debate topology", "positive"),
            _event("Redis", "Episodic memory queried", f"{len(precedents)} precedent(s) recalled", "info"),
        ],
        "orchestration": orchestration,
    }
    await _stream(config, patch)
    patch["_precedents"] = precedents
    return patch


@weave.op(name="orch_conduct")
async def _conduct_node(state: dict, config) -> dict:
    decision = state.get("decision", "")
    context = state.get("context", {})
    company, stage, _cid = _company(context)
    precedents = state.get("_precedents") or (state.get("orchestration", {}) or {}).get("precedents") or []

    result = await CONDUCTOR.plan_topology(
        decision, context, company=company, stage=stage, precedents=precedents, config=config
    )
    topology = result.topology
    try:
        STORE.save_topology(topology)
    except Exception as exc:
        print(f"[orch conduct] topology save skipped: {exc}")

    seats = [n.role for n in topology.nodes if n.kind in (M.NodeKind.analyst, M.NodeKind.specialist)]
    orchestration = dict(state.get("orchestration") or {})
    orchestration.update(
        phase="conduct",
        topology={
            "id": topology.id,
            "name": topology.name,
            "decision_type": topology.decision_type,
            "max_rounds": topology.max_rounds,
            "requires_red_team": topology.requires_red_team,
            "allow_loops": topology.allow_loops,
            "fan_out": topology.fan_out,
            "convergence_threshold": topology.convergence_threshold,
            "nodes": [n.model_dump(mode="json") for n in topology.nodes],
            "edges": [e.model_dump(mode="json") for e in topology.edges],
            "seats": seats,
        },
    )
    patch = {
        "phase": "conduct",
        "current_phase": f"Topology planned: {topology.name} · {len(seats)} seats · {topology.max_rounds} rounds",
        "decision_type": topology.decision_type,
        "orchestration": orchestration,
        "observability_events": [
            _event("OpenAI", "Conductor planned topology", f"{topology.name} ({len(seats)} seats)", "positive"),
            _event("W&B Weave", "Span: orch_conductor", "topology planning traced", "info"),
        ],
    }
    should_decompose = _should_decompose(topology)
    orchestration["mode"] = "hierarchical" if should_decompose else "single"
    patch["orchestration"] = orchestration
    if should_decompose:
        patch["current_phase"] = f"Complex decision → hierarchical mode ({len(seats)} seats)"
    await _stream(config, patch)
    patch["_topology"] = topology.model_dump(mode="json")
    patch["_decompose"] = should_decompose
    return patch


@weave.op(name="orch_debate")
async def _debate_node(state: dict, config) -> dict:
    if state.get("_decompose"):
        return await _hierarchical_branch(state, config)
    decision = state.get("decision", "")
    context = state.get("context", {})
    company, stage, _cid = _company(context)
    topo_dict = state.get("_topology") or (state.get("orchestration", {}) or {}).get("topology") or {}
    topology = M.Topology(**topo_dict) if topo_dict else CONDUCTOR.plan_to_topology(CONDUCTOR._fallback_plan("general"))
    precedents = state.get("_precedents") or []

    thread_id = M.new_id("thread")
    orch_view = dict(state.get("orchestration") or {})
    orch_view["phase"] = "debate"
    orch_view["thread_id"] = thread_id
    rounds_view: list[dict] = []
    framing = (state.get("transcript") or [{}])[0]
    checkpoints: list[str] = []

    async def emit_cb(p: dict) -> None:
        phase = p.get("phase", "")
        orch_view["phase"] = phase
        patch = {"current_phase": phase}
        if "stances" in p:
            stances = p["stances"]
            patch["transcript"] = [framing] + [_stance_turn(s) for s in stances]
            patch["positions"] = [_stance_turn(s) for s in stances]
            patch["agent_statuses"] = _agent_statuses(stances)
        if "convergence" in p:
            conv = p["convergence"]
            orch_view["convergence"] = conv
            rounds_view.append({"index": p.get("round_index"), "convergence": conv, "stances": p.get("stances", [])})
            orch_view["rounds"] = rounds_view
            # durable checkpoint per round (branch / replay / time-travel)
            try:
                cid = STORE.save_checkpoint(thread_id, {"round": p.get("round_index"), "convergence": conv, "stances": p.get("stances", [])},
                                            label=f"round-{p.get('round_index')}", node="debate")
                checkpoints.append(cid)
            except Exception as exc:
                print(f"[orch debate] checkpoint skipped: {exc}")
        if "tally" in p:
            orch_view["tally"] = p["tally"]
        patch["orchestration"] = dict(orch_view)
        patch["observability_events"] = [_event("OpenAI", f"debate · {phase}", "orchestrated committee", "info")]
        await _stream(config, patch)

    trace = await DEBATE.run_debate(
        decision, context, topology, company=company, stage=stage,
        reliability_weights={}, precedents=precedents, control_thread=thread_id, emit=emit_cb, config=config,
    )
    trace.thread_id = thread_id
    trace.checkpoints = checkpoints

    orch_view.update(
        phase="debate-complete",
        convergence=trace.convergence.model_dump() if trace.convergence else {},
        tally=trace.tally.model_dump(mode="json") if trace.tally else {},
        red_team=trace.red_team.model_dump(mode="json") if trace.red_team else {},
        stop_reason=trace.stop_reason.value,
        cost_usd=trace.cost_usd,
        seats=trace.seats,
    )
    final_stances = trace.rounds[-1].stances if trace.rounds else []
    final_positions = [_stance_turn(s.model_dump(mode="json")) for s in final_stances]
    patch = {
        "phase": "debate-complete",
        "current_phase": f"Debate complete · {len(trace.rounds)} round(s) · {trace.stop_reason.value}",
        "positions": final_positions,
        "transcript": [framing, *final_positions],
        "orchestration": orch_view,
        "observability_events": [
            _event("W&B Weave", "Span: orch_debate", f"{len(trace.rounds)} round(s) traced", "positive"),
            _event("Redis", "Debate checkpointed", f"{len(checkpoints)} durable checkpoint(s)", "positive"),
        ],
    }
    await _stream(config, patch)
    patch["_trace"] = trace.model_dump(mode="json")
    return patch


@weave.op(name="orch_persist")
async def _persist_node(state: dict, config) -> dict:
    trace_dict = state.get("_trace") or {}
    trace = M.OrchestrationTrace(**trace_dict) if trace_dict else None
    decision = state.get("decision", "")
    context = state.get("context", {})
    _company_name, _stage, company_id = _company(context)
    recommendation = (trace.recommendation if trace else state.get("recommendation")) or {}

    saved = {"trace": False, "memory": False, "event": False}
    if trace:
        try:
            STORE.save_trace(trace)
            saved["trace"] = True
        except Exception as exc:
            print(f"[orch persist] trace save skipped: {exc}")
        try:
            mem = M.EpisodicMemoryRecord(
                company_id=company_id,
                decision=decision,
                decision_type=trace.decision_type,
                recommendation=str(recommendation.get("decision", "")),
                confidence=int(recommendation.get("confidence") or 0),
                key_metrics=list((recommendation.get("key_risks") or [])[:4]),
                lessons=list((recommendation.get("conditions") or [])[:4]),
                topology_id=trace.topology_id,
                run_id=trace.run_id,
            )
            STORE.remember(mem)
            saved["memory"] = True
        except Exception as exc:
            print(f"[orch persist] memory save skipped: {exc}")
        try:
            STORE.emit_event(
                {"label": "decision", "decision": decision, "ruling": recommendation.get("decision"),
                 "topology": trace.topology_name, "run_id": trace.run_id}
            )
            STORE.publish_bus({"event": "decision", "run_id": trace.run_id, "ruling": recommendation.get("decision")})
            saved["event"] = True
        except Exception as exc:
            print(f"[orch persist] event publish skipped: {exc}")

    final_turn = {
        "agent": "cfo", "label": "Office of the CFO", "role": "Conductor", "monogram": "CF",
        "type": "ruling", "headline": f"Ruling: {recommendation.get('decision', 'DEFER')}",
        "argument": recommendation.get("rationale", ""), "key_points": recommendation.get("conditions", []),
        "at": _now(),
    }
    transcript = list(state.get("transcript") or [])
    transcript.append(final_turn)

    orch_view = dict(state.get("orchestration") or {})
    orch_view["phase"] = "persisted"
    orch_view["persisted"] = saved
    orch_view["run_id"] = trace.run_id if trace else ""

    patch = {
        "phase": "complete",
        "current_phase": f"Decision recorded · {recommendation.get('decision', 'DEFER')}",
        "recommendation": recommendation,
        "transcript": transcript,
        "orchestration": orch_view,
        "observability_events": [
            _event("Redis", "Decision persisted", f"trace={saved['trace']} memory={saved['memory']} bus={saved['event']}", "positive"),
        ],
    }
    await _stream(config, patch)
    return patch


# --------------------------------------------------------------------------- #
# Complexity router → hierarchical sub-debates for big decisions
# --------------------------------------------------------------------------- #
COMPLEX_DECISION_TYPES = {"acquisition", "merger", "divestiture", "expansion", "restructuring", "fundraising"}


def _should_decompose(topology: M.Topology) -> bool:
    """Reuse the Conductor's own complexity judgment (encoded in the topology) to
    decide whether to run hierarchical sub-debates: complex decision type, a wide
    roster (>=5 seats), or a deep debate (>=4 rounds)."""
    seats = [n for n in topology.nodes if n.kind in (M.NodeKind.analyst, M.NodeKind.specialist)]
    return (
        (topology.decision_type or "").lower() in COMPLEX_DECISION_TYPES
        or len(seats) >= 5
        or (topology.max_rounds or 0) >= 4
    )


def _lean_sub_topology(_question: str) -> M.Topology:
    """Cost-bounded sub-committee for in-graph hierarchical mode (3 seats, 1 round)."""
    plan = M.ConductorPlan(
        topology_name="sub-committee", decision_type="sub",
        seats=[M.SeatPlan(role=r, is_specialist=False, rationale="sub-committee seat")
               for r in ("cfo", "treasury", "fpna", "risk")],
        rounds=1, fan_out=True, allow_loops=False, requires_red_team=False,
        convergence_threshold=0.75, stop_conditions=["converge"], rationale="lean sub-committee",
    )
    return CONDUCTOR.plan_to_topology(plan)


async def _hierarchical_branch(state: dict, config) -> dict:
    """Decompose a complex decision into concurrent sub-committees and aggregate
    (the subdebate engine), mapping the parent ruling onto the streamed state."""
    from src.orchestration import subdebate as SUB

    decision = state.get("decision", "")
    context = state.get("context", {})
    company, stage, _cid = _company(context)
    framing = (state.get("transcript") or [{}])[0]
    orch_view = dict(state.get("orchestration") or {})
    orch_view.update(mode="hierarchical", phase="decompose")
    await _stream(config, {"current_phase": "Complex decision — decomposing into sub-committees", "orchestration": orch_view})

    htrace = await SUB.run_hierarchical(
        decision, context, company=company, stage=stage,
        sub_topology_factory=_lean_sub_topology, persist=True, config=config,
    )
    parent_rec = htrace.parent_recommendation or {}
    turns = [framing]
    for question, ruling in zip(htrace.sub_questions, htrace.sub_rulings):
        turns.append({
            "agent": "cfo", "label": "Sub-committee", "role": "Sub-committee", "monogram": "SC",
            "type": "position", "stance": (ruling or {}).get("decision"), "headline": question[:90],
            "argument": (ruling or {}).get("rationale", ""), "key_points": [], "at": _now(),
        })
    orch_view.update(
        phase="aggregated", sub_questions=htrace.sub_questions, sub_rulings=htrace.sub_rulings,
        hierarchical_run_id=htrace.run_id, cost_usd=htrace.cost_usd,
    )
    patch = {
        "phase": "debate-complete",
        "current_phase": f"Aggregated {len(htrace.sub_questions)} sub-committees → {parent_rec.get('decision', 'DEFER')}",
        "recommendation": parent_rec,
        "transcript": turns,
        "positions": turns[1:],
        "orchestration": orch_view,
        "observability_events": [
            _event("OpenAI", "Hierarchical aggregation", f"{len(htrace.sub_questions)} sub-committees", "positive"),
            _event("Redis", "Sub-debates persisted", f"{len(htrace.sub_run_ids)} sub-traces + 1 hierarchical run", "positive"),
        ],
    }
    await _stream(config, patch)
    patch["_htrace"] = htrace.model_dump(mode="json")
    return patch


# --------------------------------------------------------------------------- #
# Graph builder
# --------------------------------------------------------------------------- #
def build_orchestrator_graph(base_graph=None):
    """Compile the orchestration graph over a DebateState subclass that adds the
    ``orchestration`` channel. ``base_graph`` is accepted for call-site symmetry
    with agent.py's EOF swap (the orchestration graph is self-contained)."""
    from src.agent import DebateState  # lazy: defined before agent.py's EOF runs

    class OrchestrationState(DebateState):  # type: ignore[misc, valid-type]
        orchestration: dict
        # internal hand-off channels (underscored; ignored by the UI)
        _precedents: list
        _topology: dict
        _trace: dict
        _decompose: bool
        _htrace: dict

    workflow = StateGraph(OrchestrationState)
    workflow.add_node("intake", _intake_node)
    workflow.add_node("conduct", _conduct_node)
    workflow.add_node("debate", _debate_node)
    workflow.add_node("persist", _persist_node)
    workflow.add_edge(START, "intake")
    workflow.add_edge("intake", "conduct")
    workflow.add_edge("conduct", "debate")
    workflow.add_edge("debate", "persist")
    workflow.add_edge("persist", END)
    return workflow.compile(checkpointer=MemorySaver())
