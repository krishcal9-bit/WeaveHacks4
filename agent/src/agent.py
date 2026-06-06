"""
Atlas — the multi-agent finance department debate graph.

A user poses any financial decision. A committee of role-based agents each take a
grounded position (Treasury, FP&A, Risk & Audit, Procurement), cross-examine each
other, and the CFO synthesizes a board-ready, quantified recommendation. State
streams to the frontend (CopilotKit useCoAgent) to drive the boardroom view; every
node is a @weave.op so the committee appears as named spans in Weave.

Flow:  intake → treasury → fpna → risk → procurement → debate → synthesis
       → reliability → persist
"""

import inspect
import json
import os
import time

import weave
from copilotkit import CopilotKitState
from langchain.chat_models import init_chat_model
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from src.env import load_env
from src.health import require_live_ready, sponsor_health, weave_status
from src import redis_layer as R
from src.tools import (
    compute_runway,
    get_company_financials,
    list_vendors,
    search_finance_policies,
)

load_env()

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.5")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "xhigh")
LLM_TEXT_VERBOSITY = os.getenv("LLM_TEXT_VERBOSITY", "low")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")
OPENAI_REALTIME_REASONING_EFFORT = os.getenv("OPENAI_REALTIME_REASONING_EFFORT", "xhigh")

try:
    from copilotkit.langgraph import copilotkit_emit_state as _copilotkit_emit_state
except Exception:
    try:
        from copilotkit import copilotkit_emit_state as _copilotkit_emit_state
    except Exception:  # CopilotKit Python package versions expose different helpers.
        _copilotkit_emit_state = None


def llm(temperature: float = 0.3):
    if LLM_PROVIDER.lower() == "openai":
        return ChatOpenAI(
            model=LLM_MODEL,
            temperature=temperature,
            reasoning_effort=LLM_REASONING_EFFORT,
            verbosity=LLM_TEXT_VERBOSITY,
            output_version="responses/v1",
        )
    return init_chat_model(LLM_MODEL, model_provider=LLM_PROVIDER, temperature=temperature)


# --------------------------------------------------------------------------- #
# The committee — professional roles, not characters (org-chart, not mascots)
# --------------------------------------------------------------------------- #
ROSTER: dict[str, dict] = {
    "cfo": {
        "label": "Office of the CFO",
        "role": "Chief Financial Officer · Chair",
        "monogram": "CF",
        "mandate": "balancing growth, risk, and runway to make the final call",
    },
    "treasury": {
        "label": "Treasury",
        "role": "Treasury",
        "monogram": "TR",
        "mandate": "liquidity, cash position, runway, and financing risk",
    },
    "fpna": {
        "label": "FP&A",
        "role": "Financial Planning & Analysis",
        "monogram": "FP",
        "mandate": "growth, ROI, forecast, payback, and unit economics",
    },
    "risk": {
        "label": "Risk & Audit",
        "role": "Risk & Audit",
        "monogram": "RA",
        "mandate": "downside scenarios, compliance, controls, and policy adherence",
    },
    "procurement": {
        "label": "Procurement",
        "role": "Procurement",
        "monogram": "PR",
        "mandate": "vendor terms, cost efficiency, and negotiation leverage",
    },
    "reliability": {
        "label": "Reliability Auditor",
        "role": "Reliability & Learning",
        "monogram": "RL",
        "mandate": "scoring agent reliability, packaging W&B evals, and gating self-improvement",
    },
}
ANALYSTS = ["treasury", "fpna", "risk", "procurement"]


# --------------------------------------------------------------------------- #
# Structured outputs (reliable JSON from the model)
# --------------------------------------------------------------------------- #
class Position(BaseModel):
    stance: str = Field(description="one of: support, oppose, conditional")
    headline: str = Field(description="one-line position, <= 12 words")
    argument: str = Field(description="2-4 sentences citing specific figures")
    key_points: list[str] = Field(default_factory=list, description="2-3 crisp bullets")


class Exchange(BaseModel):
    from_role: str = Field(description="the function raising the challenge")
    to_role: str = Field(description="the function being challenged")
    point: str = Field(description="a sharp, specific, quantified challenge")


class Rebuttals(BaseModel):
    exchanges: list[Exchange] = Field(default_factory=list)


class Recommendation(BaseModel):
    decision: str = Field(description="one of: APPROVE, REJECT, CONDITIONAL, DEFER")
    confidence: int = Field(ge=0, le=100)
    rationale: str = Field(description="3-5 sentences, decisive and quantified")
    key_risks: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    estimated_monthly_cost: float = Field(default=0, description="incremental recurring monthly cost; 0 if none")
    estimated_one_time_cost: float = Field(default=0, description="upfront one-time cost; 0 if none")
    estimated_added_monthly_revenue: float = Field(default=0, description="incremental monthly revenue; 0 if none")


class ReliabilityScore(BaseModel):
    agent_id: str = Field(description="one of: cfo, treasury, fpna, risk, procurement")
    evidence_grounding: int = Field(ge=0, le=100)
    forecast_calibration: int = Field(ge=0, le=100)
    policy_compliance: int = Field(ge=0, le=100)
    debate_value: int = Field(ge=0, le=100)
    outcome_accuracy: int = Field(ge=0, le=100)
    confidence_calibration: int = Field(ge=0, le=100)
    trace_quality: int = Field(ge=0, le=100)
    reliability: int = Field(ge=0, le=100, description="weighted overall score")
    rationale: str = Field(description="specific evidence-backed reason for the score")
    known_weaknesses: list[str] = Field(default_factory=list)
    prompt_adjustment: str = Field(description="specific prompt or policy improvement to replay")
    promotion_gate: str = Field(description="how W&B Weave evals should decide whether this agent improves")


class ReliabilityReport(BaseModel):
    summary: str = Field(description="board-ready summary of council reliability")
    scores: list[ReliabilityScore] = Field(default_factory=list)
    eval_dataset: str = Field(description="W&B/Weave eval dataset or replay-set label")
    replay_plan: list[str] = Field(default_factory=list)
    promotion_gate: str = Field(description="global gate for accepting future prompt/model changes")


# --------------------------------------------------------------------------- #
# Shared state (extends CopilotKitState → streams to the frontend)
# --------------------------------------------------------------------------- #
class DebateState(CopilotKitState):
    decision: str
    phase: str
    current_phase: str
    context: dict
    positions: list
    transcript: list
    recommendation: dict
    agent_statuses: list
    observability_events: list
    trace_summary: dict
    redis_activity: list
    sponsor_health: dict
    reliability_scores: list
    learning_report: dict


def _extract_decision(messages: list) -> str:
    """Pull the most recent human message as the decision under review."""
    for m in reversed(messages or []):
        role = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else None)
        if role in ("human", "user"):
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _turn(role_key: str, **extra) -> dict:
    p = ROSTER[role_key]
    return {"agent": role_key, "label": p["label"], "role": p["role"], "monogram": p["monogram"], **extra}


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _company_name(context: dict | None = None) -> str:
    financials = (context or {}).get("financials") or {}
    return financials.get("name") or "Acme Corp"


def _event(sponsor: str, label: str, detail: str, tone: str = "info") -> dict:
    return {
        "id": f"{time.time_ns()}-{sponsor.lower().replace(' ', '-')}",
        "at": _now(),
        "sponsor": sponsor,
        "label": label,
        "detail": detail,
        "tone": tone,
    }


def _redis_activity(label: str, detail: str, kind: str) -> dict:
    return {"at": _now(), "label": label, "detail": detail, "kind": kind}


OBSERVABILITY_AGENTS = {
    **ROSTER,
    "system": {
        "label": "Atlas Runtime",
        "role": "Persistence & State Streaming",
        "monogram": "AT",
        "mandate": "publishing decisions, Redis activity, sponsor health, and AG-UI state",
    },
}

STREAM_STATE_KEYS = (
    "decision",
    "phase",
    "current_phase",
    "context",
    "positions",
    "transcript",
    "recommendation",
    "agent_statuses",
    "observability_events",
    "trace_summary",
    "redis_activity",
    "sponsor_health",
    "reliability_scores",
    "learning_report",
)


def _initial_agent_statuses() -> list[dict]:
    return [
        {
            "id": key,
            "label": meta["label"],
            "role": meta["role"],
            "monogram": meta["monogram"],
            "mandate": meta["mandate"],
            "status": "waiting",
            "detail": "Awaiting council turn",
            "last_update": _now(),
        }
        for key, meta in OBSERVABILITY_AGENTS.items()
    ]


def _set_agent_status(statuses: list | None, role_key: str, **updates) -> list[dict]:
    current = [dict(item) for item in (statuses or _initial_agent_statuses())]
    seen = False
    for item in current:
        if item.get("id") == role_key:
            item.update(updates)
            item["last_update"] = _now()
            seen = True
            break
    if not seen and role_key in OBSERVABILITY_AGENTS:
        meta = OBSERVABILITY_AGENTS[role_key]
        current.append({"id": role_key, **meta, **updates, "last_update": _now()})
    return current


def _trace_summary(node: str, status: str, model_calls: int = 0, tool_calls: int = 0) -> dict:
    weave = weave_status()
    return {
        "node": node,
        "status": status,
        "model": f"{LLM_PROVIDER}:{LLM_MODEL}",
        "reasoning_effort": LLM_REASONING_EFFORT,
        "text_verbosity": LLM_TEXT_VERBOSITY,
        "realtime_model": OPENAI_REALTIME_MODEL,
        "realtime_reasoning_effort": OPENAI_REALTIME_REASONING_EFFORT,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "weave_project": weave.get("project"),
        "weave_url": weave.get("url"),
        "state_streaming": "copilotkit_emit_state" if _copilotkit_emit_state else "langgraph_state_delta",
        "updated_at": _now(),
        "spans": _span_statuses(node, status),
    }


def _span_statuses(active_node: str, active_status: str) -> list[dict]:
    spans = [
        "intake",
        "analyst_treasury",
        "analyst_fpna",
        "analyst_risk",
        "analyst_procurement",
        "debate_round",
        "cfo_synthesis",
        "reliability_auditor",
        "persist_decision",
    ]
    active_index = spans.index(active_node) if active_node in spans else -1
    out: list[dict] = []
    for index, span in enumerate(spans):
        if span == active_node:
            span_status = active_status
        elif active_index >= 0 and index < active_index:
            span_status = "complete"
        else:
            span_status = "waiting"
        out.append({"node": span, "status": span_status})
    return out


def _append(items: list | None, *new_items: dict, keep: int = 48) -> list:
    return [*(items or []), *new_items][-keep:]


def _normalize_agent_id(value: str) -> str:
    normalized = (value or "").lower().replace("&", "and").replace("-", "_").replace(" ", "_")
    aliases = {
        "office_of_the_cfo": "cfo",
        "chief_financial_officer": "cfo",
        "financial_planning_and_analysis": "fpna",
        "fpa": "fpna",
        "fp&a": "fpna",
        "risk_audit": "risk",
        "risk_and_audit": "risk",
        "risk_&_audit": "risk",
    }
    return aliases.get(normalized, normalized)


def _weighted_reliability(score: dict) -> int:
    weights = {
        "outcome_accuracy": 0.30,
        "evidence_grounding": 0.20,
        "forecast_calibration": 0.15,
        "policy_compliance": 0.15,
        "debate_value": 0.10,
        "confidence_calibration": 0.05,
        "trace_quality": 0.05,
    }
    value = sum(float(score.get(key, 0) or 0) * weight for key, weight in weights.items())
    return max(0, min(100, round(value)))


def _default_reliability_score(agent_id: str) -> dict:
    return {
        "agent_id": agent_id,
        "evidence_grounding": 0,
        "forecast_calibration": 0,
        "policy_compliance": 0,
        "debate_value": 0,
        "outcome_accuracy": 0,
        "confidence_calibration": 0,
        "trace_quality": 0,
        "reliability": 0,
        "rationale": "Reliability auditor did not return a score for this agent; promotion is blocked until replay evidence exists.",
        "known_weaknesses": ["Missing reliability score"],
        "prompt_adjustment": "Require this role to cite evidence and replay outcomes in every recommendation.",
        "promotion_gate": "Blocked until W&B Weave replay eval produces a complete scorecard.",
    }


def _normalize_reliability_scores(scores: list[ReliabilityScore]) -> list[dict]:
    expected = ["cfo", *ANALYSTS]
    by_agent: dict[str, dict] = {}
    for score in scores:
        item = score.model_dump()
        agent_id = _normalize_agent_id(item.get("agent_id", ""))
        if agent_id not in expected:
            continue
        item["agent_id"] = agent_id
        item["reliability"] = _weighted_reliability(item)
        by_agent[agent_id] = item
    return [by_agent.get(agent_id) or _default_reliability_score(agent_id) for agent_id in expected]


def _attach_reliability_to_statuses(statuses: list | None, scores: list[dict]) -> list[dict]:
    current = [dict(item) for item in (statuses or _initial_agent_statuses())]
    by_agent = {item["agent_id"]: item for item in scores}
    updated: list[dict] = []
    for item in current:
        score = by_agent.get(item.get("id"))
        if score:
            item.update(
                reliability_score=score["reliability"],
                reliability_dimensions={
                    "outcome_accuracy": score["outcome_accuracy"],
                    "evidence_grounding": score["evidence_grounding"],
                    "forecast_calibration": score["forecast_calibration"],
                    "policy_compliance": score["policy_compliance"],
                    "debate_value": score["debate_value"],
                    "confidence_calibration": score["confidence_calibration"],
                    "trace_quality": score["trace_quality"],
                },
                reliability_rationale=score["rationale"],
                known_weaknesses=score["known_weaknesses"],
                prompt_adjustment=score["prompt_adjustment"],
                promotion_gate=score["promotion_gate"],
            )
        updated.append(item)
    return updated


def _tool_body(tool_obj, *args, **kwargs) -> str:
    """Run local Redis-backed LangChain tools without emitting AG-UI tool events."""
    func = getattr(tool_obj, "func", None)
    if func is not None:
        return func(*args, **kwargs)
    payload = kwargs if kwargs else (args[0] if args else {})
    return tool_obj.invoke(payload)


async def _emit(config: RunnableConfig, state: dict) -> None:
    """Best-effort CopilotKit state streaming across package versions."""
    if _copilotkit_emit_state is None:
        return
    try:
        result = _copilotkit_emit_state(config, state)
    except TypeError as exc:
        try:
            result = _copilotkit_emit_state(state)
        except Exception:
            print(f"[observability] CopilotKit state emit skipped: {exc}")
            return
    except Exception as exc:
        print(f"[observability] CopilotKit state emit skipped: {exc}")
        return
    try:
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        print(f"[observability] CopilotKit state emit skipped: {exc}")


def _stream_state(state: DebateState, patch: dict) -> dict:
    merged = {}
    for key in STREAM_STATE_KEYS:
        if key in patch:
            merged[key] = patch[key]
        elif key in state:
            merged[key] = state.get(key)
    return json.loads(json.dumps(merged, default=str))


async def _emit_patch(state: DebateState, config: RunnableConfig, **patch) -> None:
    await _emit(config, _stream_state(state, patch))


def _sponsor_event(health: dict) -> dict:
    if health.get("ready"):
        return _event("Sponsors", "Strict-live health checked", "All required sponsors are ready", "positive")
    return _event(
        "Sponsors",
        "Strict-live blockers visible",
        "; ".join(health.get("blockers") or ["Sponsor health unavailable"]),
        "warning",
    )


def _redis_ping_activity(health: dict) -> dict:
    redis = next((item for item in health.get("sponsors", []) if item.get("id") == "redis"), {})
    return _redis_activity(
        "Redis PING",
        redis.get("detail") or "Redis health checked",
        "health-ok" if redis.get("ready") else "health-error",
    )


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
@weave.op(name="intake")
async def intake_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    decision = _extract_decision(state.get("messages", []))
    health = sponsor_health()
    agent_statuses = _set_agent_status(
        _initial_agent_statuses(),
        "cfo",
        status="speaking",
        detail="Convening the council and loading sponsor-backed context",
    )
    events = [
        _event("OpenAI", "GPT-5.5 reasoning selected", f"{LLM_PROVIDER}:{LLM_MODEL} · {LLM_REASONING_EFFORT}", "positive"),
        _event("OpenAI", "Realtime 2 voice armed", OPENAI_REALTIME_MODEL, "positive"),
        _event("W&B Weave", "Trace span opened", "intake", "positive"),
        _event("CopilotKit", "AG-UI state stream", "finance_department", "positive"),
        _sponsor_event(health),
    ]
    await _emit_patch(
        state,
        config,
        decision=decision,
        phase="intake",
        current_phase="Convening council",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("intake", "running", tool_calls=3),
        redis_activity=[_redis_ping_activity(health)],
        sponsor_health=health,
        reliability_scores=[],
        learning_report={},
    )
    context = {
        "financials": json.loads(_tool_body(get_company_financials)),
        "vendors": json.loads(_tool_body(list_vendors)),
        "policies": json.loads(_tool_body(search_finance_policies, query=decision or "financial decision")),
    }
    framing = _turn(
        "cfo",
        type="framing",
        headline="Convening the committee",
        argument=f"The committee will evaluate: “{decision}”. Treasury, FP&A, Risk & Audit, and Procurement will each weigh in before I rule.",
        key_points=[],
    )
    return {
        "decision": decision,
        "context": context,
        "phase": "analysis",
        "current_phase": "Council analysis",
        "positions": [],
        "transcript": [framing],
        "recommendation": {},
        "reliability_scores": [],
        "learning_report": {},
        "agent_statuses": _set_agent_status(
            agent_statuses,
            "cfo",
            status="done",
            headline="Council convened",
            detail="Context loaded from Redis and policy memory",
        ),
        "observability_events": _append(
            events,
            _event("Redis", "Financial context loaded", "JSON company record, vendor search, vector policy RAG", "positive"),
            _event("W&B Weave", "Trace span closed", "intake", "positive"),
        ),
        "trace_summary": _trace_summary("intake", "complete", tool_calls=3),
        "redis_activity": [
            _redis_ping_activity(health),
            _redis_activity("RedisJSON", f"Loaded {_company_name(context)} financial system of record", "json"),
            _redis_activity("RediSearch", f"Loaded {len(context['vendors'])} vendor contracts", "search"),
            _redis_activity("Vector RAG", f"Loaded {len(context['policies'])} policy/precedent hits", "vector"),
        ],
        "sponsor_health": health,
    }


def make_analyst_node(role_key: str):
    persona = ROSTER[role_key]

    @weave.op(name=f"analyst_{role_key}")
    async def node(state: DebateState, config: RunnableConfig) -> dict:
        require_live_ready()
        health = sponsor_health()
        company_name = _company_name(state.get("context"))
        agent_statuses = _set_agent_status(
            state.get("agent_statuses"),
            role_key,
            status="thinking",
            detail=f"{persona['label']} is forming a grounded position",
        )
        events = _append(
            state.get("observability_events"),
            _event("W&B Weave", "Trace span opened", f"analyst_{role_key}", "positive"),
            _event("OpenAI", "Structured position call", persona["label"], "info"),
            _event("Redis", "Redis-grounded context active", "Using intake financials, vendors, and policy hits", "positive"),
            _sponsor_event(health),
        )
        await _emit_patch(
            state,
            config,
            phase="analysis",
            current_phase=f"{persona['label']} analysis",
            agent_statuses=agent_statuses,
            observability_events=events,
            trace_summary=_trace_summary(f"analyst_{role_key}", "running", model_calls=1),
            redis_activity=_append(
                state.get("redis_activity"),
                _redis_ping_activity(health),
                _redis_activity("Redis context", f"{persona['label']} using intake context", "state"),
            ),
            sponsor_health=health,
        )
        model = llm(0.4).with_structured_output(Position)
        system = SystemMessage(
            content=(
                f"You are {persona['label']} at {company_name} (Series A), a member of its "
                f"investment committee. Your mandate is {persona['mandate']}. Evaluate the decision "
                f"strictly from your function's perspective. Cite specific figures from the company "
                f"context, including forecast, cohort, pipeline, audit, vendor, and outcome history when relevant. "
                f"Take a clear stance (support / oppose / conditional) and defend it crisply. "
                f"Speak like a senior finance executive in a boardroom — precise, quantified, no fluff. "
                f"Never mention being an AI or a model."
            )
        )
        human = HumanMessage(
            content=(
                f"DECISION UNDER REVIEW:\n{state['decision']}\n\n"
                f"COMPANY CONTEXT ({company_name}):\n{json.dumps(state['context'])}\n\n"
                f"Give your position."
            )
        )
        pos: Position = await model.ainvoke([system, human], config)
        entry = _turn(
            role_key,
            type="position",
            stance=pos.stance,
            headline=pos.headline,
            argument=pos.argument,
            key_points=pos.key_points,
        )
        agent_statuses = _set_agent_status(
            agent_statuses,
            role_key,
            status="speaking",
            stance=pos.stance,
            headline=pos.headline,
            detail=pos.argument,
        )
        return {
            "positions": state.get("positions", []) + [entry],
            "transcript": state.get("transcript", []) + [entry],
            "phase": "analysis",
            "current_phase": f"{persona['label']} position recorded",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("W&B Weave", "Trace span closed", f"analyst_{role_key}", "positive"),
            ),
            "trace_summary": _trace_summary(f"analyst_{role_key}", "complete", model_calls=1),
            "redis_activity": _append(
                state.get("redis_activity"),
                _redis_ping_activity(health),
                _redis_activity("Redis context", f"{persona['label']} position grounded in intake context", "state"),
            ),
            "sponsor_health": health,
        }

    return node


@weave.op(name="debate_round")
async def debate_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    health = sponsor_health()
    company_name = _company_name(state.get("context"))
    positions = state.get("positions", [])
    agent_statuses = state.get("agent_statuses") or _initial_agent_statuses()
    for role_key in ANALYSTS:
        agent_statuses = _set_agent_status(
            agent_statuses,
            role_key,
            status="thinking",
            detail="Cross-examining peer assumptions",
        )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "debate_round", "positive"),
        _event("OpenAI", "Cross-examination call", f"{len(positions)} positions", "info"),
        _event("Redis", "Redis-grounded positions active", "Debate uses analyst positions derived from intake context", "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="debate",
        current_phase="Committee cross-examination",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("debate_round", "running", model_calls=1),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Redis context", "Debate using Redis-grounded analyst positions", "state"),
        ),
        sponsor_health=health,
    )
    model = llm(0.55).with_structured_output(Rebuttals)
    system = SystemMessage(
        content=(
            f"You are moderating an investment-committee debate at {company_name}. Given each "
            "function's position, produce 3-4 sharp cross-examination exchanges where members "
            "challenge each other's reasoning with specific numbers and trade-offs. Keep it "
            "professional, substantive, and concrete — like a real boardroom, not small talk."
        )
    )
    slim = [{"role": p["role"], "stance": p.get("stance"), "headline": p.get("headline"), "key_points": p.get("key_points")} for p in positions]
    human = HumanMessage(content=f"DECISION:\n{state['decision']}\n\nPOSITIONS:\n{json.dumps(slim)}")
    reb: Rebuttals = await model.ainvoke([system, human], config)
    turns = [
        {"agent": "debate", "type": "rebuttal", "from_role": e.from_role, "to_role": e.to_role, "point": e.point}
        for e in reb.exchanges
    ]
    for role_key in ANALYSTS:
        agent_statuses = _set_agent_status(
            agent_statuses,
            role_key,
            status="speaking",
            detail="Challenge recorded in the debate transcript",
        )
    return {
        "transcript": state.get("transcript", []) + turns,
        "phase": "debate",
        "current_phase": "Cross-examination complete",
        "agent_statuses": agent_statuses,
        "observability_events": _append(
            events,
            _event("W&B Weave", "Trace span closed", "debate_round", "positive"),
        ),
        "trace_summary": _trace_summary("debate_round", "complete", model_calls=1),
        "redis_activity": _append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Redis context", "Cross-examination grounded in intake context", "state"),
        ),
        "sponsor_health": health,
    }


@weave.op(name="cfo_synthesis")
async def synthesis_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    health = sponsor_health()
    company_name = _company_name(state.get("context"))
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "cfo",
        status="thinking",
        detail="Reconciling positions into a board-ready recommendation",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "cfo_synthesis", "positive"),
        _event("OpenAI", "Structured CFO synthesis call", "recommendation + cost estimates", "info"),
        _event("Redis", "Runway model ready", "compute_runway will read the company cash record", "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="synthesis",
        current_phase="CFO synthesis",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("cfo_synthesis", "running", model_calls=1, tool_calls=1),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Runway model", "Ready to compute current vs scenario runway", "tool"),
        ),
        sponsor_health=health,
    )
    model = llm(0.3).with_structured_output(Recommendation)
    positions = state.get("positions", [])
    debate_turns = [t for t in state.get("transcript", []) if t.get("type") == "rebuttal"]
    system = SystemMessage(
        content=(
            f"You are the Chief Financial Officer of {company_name}, chairing the investment "
            "committee. You have heard each function's position and the cross-examination. Weigh "
            "them, resolve the disagreements, and issue a final, board-ready decision. Be decisive "
            "and quantified. Also estimate the decision's incremental monthly cost, one-time cost, "
            "and added monthly revenue (numbers only, 0 if none) so runway impact can be computed. "
            "Use the richer Acme operating data: forecast downside, churn cohorts, pipeline stage risk, "
            "vendor obligations, security incidents, audit findings, board constraints, and prior outcomes."
        )
    )
    human = HumanMessage(
        content=(
            f"DECISION:\n{state['decision']}\n\n"
            f"COMPANY CONTEXT:\n{json.dumps(state['context'])}\n\n"
            f"POSITIONS:\n{json.dumps([{'role': p['role'], 'stance': p.get('stance'), 'headline': p.get('headline'), 'argument': p.get('argument')} for p in positions])}\n\n"
            f"CROSS-EXAMINATION:\n{json.dumps([{'from': t['from_role'], 'to': t['to_role'], 'point': t['point']} for t in debate_turns])}"
        )
    )
    rec: Recommendation = await model.ainvoke([system, human], config)

    # Precise runway impact, computed (not hallucinated) from the CFO's estimates.
    impact = json.loads(
        _tool_body(
            compute_runway,
            extra_monthly_spend=rec.estimated_monthly_cost,
            one_time_cost=rec.estimated_one_time_cost,
            added_monthly_revenue=rec.estimated_added_monthly_revenue,
        )
    )
    recommendation = {
        "decision": rec.decision,
        "confidence": rec.confidence,
        "rationale": rec.rationale,
        "key_risks": rec.key_risks,
        "conditions": rec.conditions,
        "impact": impact,
    }
    closing = _turn(
        "cfo",
        type="decision",
        headline=f"{rec.decision} · {rec.confidence}% confidence",
        argument=rec.rationale,
        key_points=rec.conditions or rec.key_risks,
    )
    summary = (
        f"**Recommendation: {rec.decision}** ({rec.confidence}% confidence)\n\n{rec.rationale}"
    )
    agent_statuses = _set_agent_status(
        agent_statuses,
        "cfo",
        status="speaking",
        stance=rec.decision.lower(),
        headline=f"{rec.decision} · {rec.confidence}% confidence",
        detail=rec.rationale,
    )
    return {
        "recommendation": recommendation,
        "transcript": state.get("transcript", []) + [closing],
        "phase": "synthesis",
        "current_phase": "Committee resolution issued",
        "agent_statuses": agent_statuses,
        "observability_events": _append(
            events,
            _event("Redis", "Runway impact computed", "compute_runway tool returned scenario deltas", "positive"),
            _event("W&B Weave", "Trace span closed", "cfo_synthesis", "positive"),
        ),
        "trace_summary": _trace_summary("cfo_synthesis", "complete", model_calls=1, tool_calls=1),
        "redis_activity": _append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Runway model", "Computed current vs scenario runway", "tool"),
        ),
        "sponsor_health": health,
        "messages": [AIMessage(content=summary)],
    }


@weave.op(name="reliability_auditor")
async def reliability_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    health = sponsor_health()
    company_name = _company_name(state.get("context"))
    persona = ROSTER["reliability"]
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "reliability",
        status="thinking",
        detail="Scoring council reliability and packaging W&B replay evals",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "reliability_auditor", "positive"),
        _event("W&B Weave", "Eval packet assembling", "Council reliability + prompt promotion gate", "positive"),
        _event("Redis", "Historical outcomes active", "Using prior decision outcomes and prompt-version gates", "positive"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="reliability",
        current_phase="Reliability audit and W&B eval packaging",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("reliability_auditor", "running", model_calls=1, tool_calls=2),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("W&B eval packet", "Preparing reliability scorecard for replay comparison", "eval"),
        ),
        sponsor_health=health,
    )

    model = llm(0.2).with_structured_output(ReliabilityReport)
    positions = state.get("positions", [])
    debate_turns = [t for t in state.get("transcript", []) if t.get("type") == "rebuttal"]
    system = SystemMessage(
        content=(
            f"You are {persona['label']} for {company_name}. Your job is not to re-decide the case; "
            "score the reliability of each decision-making agent: cfo, treasury, fpna, risk, procurement. "
            "Use a live self-improvement rubric: outcome_accuracy 30%, evidence_grounding 20%, "
            "forecast_calibration 15%, policy_compliance 15%, debate_value 10%, confidence_calibration 5%, "
            "trace_quality 5%. Cite concrete evidence from the decision, positions, debate, company context, "
            "prior outcomes, audit findings, board constraints, and W&B/Weave trace quality. "
            "For every agent, include a specific prompt_adjustment and promotion_gate that can be evaluated "
            "by W&B Weave replay runs. If current outcome accuracy cannot yet be observed, calibrate it from "
            "historical analogous outcomes and say so. Never invent external facts."
        )
    )
    human = HumanMessage(
        content=(
            f"DECISION:\n{state.get('decision')}\n\n"
            f"COMPANY CONTEXT:\n{json.dumps(state.get('context'))}\n\n"
            f"POSITIONS:\n{json.dumps([{'agent': p.get('agent'), 'role': p.get('role'), 'stance': p.get('stance'), 'headline': p.get('headline'), 'argument': p.get('argument'), 'key_points': p.get('key_points')} for p in positions])}\n\n"
            f"CROSS-EXAMINATION:\n{json.dumps([{'from': t.get('from_role'), 'to': t.get('to_role'), 'point': t.get('point')} for t in debate_turns])}\n\n"
            f"CFO RECOMMENDATION:\n{json.dumps(state.get('recommendation') or {})}\n\n"
            f"TRACE SUMMARY:\n{json.dumps(state.get('trace_summary') or {})}"
        )
    )
    report: ReliabilityReport = await model.ainvoke([system, human], config)
    scorecard = _normalize_reliability_scores(report.scores)
    average_score = round(sum(score["reliability"] for score in scorecard) / len(scorecard)) if scorecard else 0
    learning_report = {
        "summary": report.summary,
        "eval_dataset": report.eval_dataset,
        "replay_plan": report.replay_plan,
        "promotion_gate": report.promotion_gate,
        "score_formula": {
            "outcome_accuracy": 0.30,
            "evidence_grounding": 0.20,
            "forecast_calibration": 0.15,
            "policy_compliance": 0.15,
            "debate_value": 0.10,
            "confidence_calibration": 0.05,
            "trace_quality": 0.05,
        },
        "weave_project": weave_status().get("project"),
        "weave_url": weave_status().get("url"),
    }
    eval_packet = {
        "decision": state.get("decision"),
        "recommendation": state.get("recommendation"),
        "scores": scorecard,
        "learning_report": learning_report,
        "source": "reliability_auditor",
    }
    eval_event_id = None
    eval_warning = None
    try:
        eval_event_id = R.append_event("evals", eval_packet)
        R.set_json(f"{R.NS}:reliability:latest", eval_packet)
        R.publish("dashboard", {"event": "reliability", "average_score": average_score})
    except Exception as exc:
        eval_warning = str(exc)
        print(f"[reliability] warning: {exc}")

    agent_statuses = _attach_reliability_to_statuses(agent_statuses, scorecard)
    agent_statuses = _set_agent_status(
        agent_statuses,
        "reliability",
        status="done" if not eval_warning else "warning",
        headline=f"Reliability scorecard · {average_score}%",
        detail=report.summary,
        reliability_score=average_score,
    )
    audit_turn = _turn(
        "reliability",
        type="reliability",
        headline=f"Reliability scorecard · {average_score}%",
        argument=report.summary,
        key_points=report.replay_plan[:3],
    )
    return {
        "reliability_scores": scorecard,
        "learning_report": learning_report,
        "transcript": state.get("transcript", []) + [audit_turn],
        "phase": "reliability",
        "current_phase": "Reliability scorecard attached",
        "agent_statuses": agent_statuses,
        "observability_events": _append(
            events,
            _event("W&B Weave", "Reliability eval ready", report.promotion_gate, "positive"),
            _event(
                "Redis",
                "Reliability eval persisted" if eval_event_id else "Reliability eval persistence warning",
                f"atlas:stream:evals · {eval_event_id}" if eval_event_id else (eval_warning or "Unknown warning"),
                "positive" if eval_event_id else "warning",
            ),
            _event("W&B Weave", "Trace span closed", "reliability_auditor", "positive"),
        ),
        "trace_summary": _trace_summary("reliability_auditor", "complete" if not eval_warning else "warning", model_calls=1, tool_calls=2),
        "redis_activity": _append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity(
                "W&B eval packet",
                f"Persisted reliability scorecard {eval_event_id}" if eval_event_id else (eval_warning or "Persistence warning"),
                "eval" if eval_event_id else "warning",
            ),
        ),
        "sponsor_health": health,
    }


@weave.op(name="persist_decision")
async def persist_node(state: DebateState, config: RunnableConfig) -> dict:
    require_live_ready()
    health = sponsor_health()
    rec = state.get("recommendation", {})
    agent_statuses = _set_agent_status(
        state.get("agent_statuses"),
        "system",
        status="persisting",
        detail="Appending the decision stream event and notifying the dashboard",
    )
    events = _append(
        state.get("observability_events"),
        _event("W&B Weave", "Trace span opened", "persist_decision", "positive"),
        _event("Redis", "Persisting decision", "Streams + Pub/Sub", "info"),
        _sponsor_event(health),
    )
    await _emit_patch(
        state,
        config,
        phase="persist",
        current_phase="Persisting decision",
        agent_statuses=agent_statuses,
        observability_events=events,
        trace_summary=_trace_summary("persist_decision", "running", tool_calls=2),
        redis_activity=_append(
            state.get("redis_activity"),
            _redis_ping_activity(health),
            _redis_activity("Redis Stream", "Preparing atlas:stream:decisions append", "stream"),
            _redis_activity("Redis Pub/Sub", "Preparing atlas:dashboard publish", "pubsub"),
        ),
        sponsor_health=health,
    )
    try:
        event_id = R.append_event("decisions", {
            "title": (state.get("decision") or "")[:140],
            "summary": (rec.get("rationale") or "")[:400],
            "decision": rec.get("decision"),
            "confidence": rec.get("confidence"),
            "reliability_scores": state.get("reliability_scores", []),
            "learning_report": state.get("learning_report", {}),
            "source": "debate",
        })
        R.publish("dashboard", {"event": "decision", "decision": rec.get("decision")})
        agent_statuses = _set_agent_status(
            agent_statuses,
            "system",
            status="done",
            detail=f"Decision persisted to Redis stream event {event_id}",
        )
        return {
            "phase": "done",
            "current_phase": "Decision persisted",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("Redis", "Decision appended", f"atlas:stream:decisions · {event_id}", "positive"),
                _event("Redis", "Dashboard notified", "atlas:dashboard Pub/Sub", "positive"),
                _event("W&B Weave", "Trace span closed", "persist_decision", "positive"),
            ),
            "redis_activity": _append(
                state.get("redis_activity"),
                _redis_activity("Redis Stream", f"Decision event {event_id}", "stream"),
                _redis_activity("Redis Pub/Sub", "Published dashboard update", "pubsub"),
            ),
            "trace_summary": _trace_summary("persist_decision", "complete", tool_calls=2),
            "sponsor_health": health,
        }
    except Exception as exc:  # persistence must not fail the run
        print(f"[persist] warning: {exc}")
        agent_statuses = _set_agent_status(
            agent_statuses,
            "system",
            status="warning",
            detail=f"Decision completed with Redis persistence warning: {exc}",
        )
        return {
            "phase": "done",
            "current_phase": "Decision completed; persistence warning",
            "agent_statuses": agent_statuses,
            "observability_events": _append(
                events,
                _event("Redis", "Persistence warning", str(exc), "warning"),
            ),
            "redis_activity": _append(
                state.get("redis_activity"),
                _redis_ping_activity(health),
                _redis_activity("Redis persistence warning", str(exc), "warning"),
            ),
            "trace_summary": _trace_summary("persist_decision", "warning", tool_calls=2),
            "sponsor_health": health,
        }


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
workflow = StateGraph(DebateState)
workflow.add_node("intake", intake_node)
for _a in ANALYSTS:
    workflow.add_node(_a, make_analyst_node(_a))
workflow.add_node("debate", debate_node)
workflow.add_node("synthesis", synthesis_node)
workflow.add_node("reliability", reliability_node)
workflow.add_node("persist", persist_node)

workflow.add_edge(START, "intake")
workflow.add_edge("intake", "treasury")
workflow.add_edge("treasury", "fpna")
workflow.add_edge("fpna", "risk")
workflow.add_edge("risk", "procurement")
workflow.add_edge("procurement", "debate")
workflow.add_edge("debate", "synthesis")
workflow.add_edge("synthesis", "reliability")
workflow.add_edge("reliability", "persist")
workflow.add_edge("persist", END)

checkpointer = MemorySaver()
graph = workflow.compile(checkpointer=checkpointer)
